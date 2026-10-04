"""可复用的 conformance 基座(机制,不含任何具体不变量)。

一个 Protocol -> 一套共享测试:把"每个实现都必须跑同一套不变量"做成可参数化的机制。
给定 (1) 一份实现注册表(名字 -> 工厂)与 (2) 一个不变量包(名字 -> 检查函数),
ConformanceSuite 把二者【绑成笛卡尔积】,逐 (实现 × 不变量) 执行,任一失败即定位到
具体格子。

这正是 ragspine tests/conformance 的泛化:让"敢放手让第三方填广度、却让脊柱不变量
烂不掉"成立——没过 conformance 的 adapter 直接 CI 红,而非生产事故。

【机制,非保证】:本模块【不】定义任何具体不变量。anti-fabrication / provenance /
isolation 这些是各 app 自己的事(ADR 0001 D6),由 app 把自己的 InvariantPack 喂进来;
harness 只负责"跑全套 + 报告哪个格子坏了"。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TypeVar

T = TypeVar("T")

# 一个不变量检查:拿到一个【新构造的实现实例】,验证通过则正常返回、违反则抛异常。
# 检查应只读、与实现内部无关(只验外部可观测行为)。
Invariant = Callable[[T], None]

# 一个工厂:无参构造一个全新实例(每个格子各拿一个,杜绝实现间状态串味)。
Factory = Callable[[], T]


def _is_pytest_module(module: str) -> bool:
    return module in ("pytest", "_pytest") or module.startswith(("pytest.", "_pytest."))


def _classify(exc: BaseException) -> str | None:
    """run() 该如何对待这个异常:"failed" / "skipped" 收集进结果;None 表示原样外抛。

    pytest 把 Failed / Skipped 的 `__module__` 改写成 "builtins",故类名之外还要核对其基类
    `OutcomeException` 确实来自 pytest(`pytest` / `_pytest.` 模块)——任何同名的假类(自己定义的
    `SkipTest` / `Skipped` / `Failed` ...)都不会被当成 skip / fail。不 import pytest / unittest。

    - pytest.skip(`Skipped`)与 unittest 的 `SkipTest`(模块 `unittest.` 前缀):skipped;
    - pytest.fail(`Failed`):failed;`pytest.xfail`(`XFailed`,是 `Failed` 子类)同样记为 failed——
      一个在 conformance 里主动 xfail 的不变量没有验证通过,不能算绿;
    - pytest.exit(`Exit`)与认不出来的 BaseException(含假的 outcome 类):None,原样外抛。
    """
    mro = type(exc).__mro__
    names = [c.__name__ for c in mro]
    if any(c.__name__ == "OutcomeException" and _is_pytest_module(c.__module__) for c in mro):
        if "Skipped" in names:
            return "skipped"
        return "failed" if "Failed" in names else None
    if type(exc).__name__ == "Exit" and _is_pytest_module(type(exc).__module__):
        return None  # pytest.exit:有意中止整个会话
    if any(c.__name__ == "SkipTest" and c.__module__.startswith("unittest") for c in mro):
        return "skipped"
    return "failed" if isinstance(exc, Exception) else None


@dataclass(frozen=True)
class InvariantPack[T]:
    """一组具名不变量。app 用它装自己的保证;harness 只负责跑。

    add() 返回自身,便于链式登记:InvariantPack("x").add(...).add(...)。
    同名不变量重复登记抛 ValueError(静默覆盖会让一条保证悄悄消失);空包不能喂给
    ConformanceSuite(0 个格子会静默全绿)。
    """

    name: str
    invariants: dict[str, Invariant[T]] = field(default_factory=dict)

    def add(self, name: str, check: Invariant[T]) -> InvariantPack[T]:
        if name in self.invariants:
            raise ValueError(f"InvariantPack {self.name!r} 已有同名不变量 {name!r}")
        self.invariants[name] = check
        return self

    def names(self) -> list[str]:
        return list(self.invariants)


@dataclass(frozen=True)
class CaseResult:
    """单个 (实现 × 不变量) 格子的执行结果。"""

    impl: str
    invariant: str
    passed: bool
    error: str | None = None
    # 不变量内部 pytest.skip():既没通过也没失败。passed 为 False(不静默算绿),由本字段区分。
    skipped: bool = False


class ConformanceSuite[T]:
    """把 实现注册表 × 不变量包 绑成可执行 / 可参数化的 conformance 套件。"""

    def __init__(self, implementations: dict[str, Factory[T]], pack: InvariantPack[T]) -> None:
        if not implementations:
            raise ValueError("conformance 套件至少需要一个实现")
        if not pack.invariants:
            raise ValueError(
                f"InvariantPack {pack.name!r} 没有任何不变量:空包会让套件静默全绿(0 个格子)"
            )
        self._impls = dict(implementations)
        self._pack = pack

    def cases(self) -> list[tuple[str, str]]:
        """全部 (实现名, 不变量名) 组合——可直接喂给 pytest.mark.parametrize。"""
        return [(impl, inv) for impl in self._impls for inv in self._pack.names()]

    def ids(self) -> list[str]:
        """与 cases() 对齐的可读用例 id(形如 impl-invariant,与 parametrize_kwargs 的 ids 一致)。"""
        return [f"{impl}-{inv}" for impl, inv in self.cases()]

    def parametrize_kwargs(self) -> dict[str, object]:
        """产出可直接喂给 pytest.mark.parametrize(**...) 的 kwargs(pytest-free)。

        本方法【只返回纯数据】(str / list[Callable] / list[str]),core 不 import
        pytest——pytest 依赖留在消费者的测试里。返回三键:

        - argnames: 固定为 "case"(单形参,值是一个【已绑定好该格子的零参 thunk】);
        - argvalues: 每格一个 thunk,调用即跑 check(impl, inv)——满足则静默返回,违反则
          原样抛出(通常是 AssertionError);
        - ids: 与 argvalues 对齐的可读用例 id,形如 "impl-inv"。

        这样消费者的整套 glue 收敛成两行,无需手写 cases() 遍历或 fixture(params=...):

            @pytest.mark.parametrize(**suite.parametrize_kwargs())
            def test_conformance(case):
                case()
        """
        # partial 立即绑定 impl/inv(规避 lambda 闭包晚绑定),且类型明确(mypy --strict 友好)。
        argvalues: list[Callable[[], None]] = [
            partial(self.check, impl, inv) for impl, inv in self.cases()
        ]
        return {"argnames": "case", "argvalues": argvalues, "ids": self.ids()}

    def check(self, impl: str, invariant: str) -> None:
        """跑单个格子:新建该实现实例,对其执行该不变量(失败则原样抛出)。

        每个格子都【新建实例】,杜绝实现间状态串味——与 ragspine 每用例新空库一致。
        """
        instance = self._impls[impl]()
        self._pack.invariants[invariant](instance)

    def run(self) -> list[CaseResult]:
        """跑全部格子并收集结果(不因单格失败而中断;用于离线自检 / 报告)。

        捕获范围:`Exception`(含 AssertionError),以及 pytest 的 `Failed`(pytest.fail / pytest.xfail,
        记为失败)与 `Skipped`(pytest.skip / unittest.SkipTest,记为 skipped、passed=False)——
        前两者继承 BaseException,否则会中断整轮。本模块不 import pytest,按类名 + 模块识别
        (见 `_classify`)。`KeyboardInterrupt` / `SystemExit` / `pytest.exit`(Exit)等「有意中止」
        一律原样外抛。
        """
        results: list[CaseResult] = []
        for impl, inv in self.cases():
            try:
                self.check(impl, inv)
                results.append(CaseResult(impl, inv, True))
            except BaseException as exc:  # noqa: BLE001 — 收集失败,不中断其余格子
                kind = _classify(exc)
                if kind is None:
                    raise
                results.append(
                    CaseResult(
                        impl, inv, False, f"{type(exc).__name__}: {exc}", skipped=kind == "skipped"
                    )
                )
        return results

    def passed(self) -> bool:
        """便捷:全部格子是否通过(离线自检用)。

        【跳过不算通过】任一格子失败或被 skip(`CaseResult.skipped`)都返回 False——全是 skip
        的套件什么也没验证,与「空 pack 不得静默全绿」同一原则。(main 上不变量里的 pytest.skip
        会从本方法透传出去、让调用方的测试变成 skipped;现在改为记录并返回 False。)想容忍
        skip 的调用方自己遍历 `run()`,按 `skipped` 过滤。
        """
        return all(result.passed for result in self.run())
