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
from typing import Generic, TypeVar

T = TypeVar("T")

# 一个不变量检查:拿到一个【新构造的实现实例】,验证通过则正常返回、违反则抛异常。
# 检查应只读、与实现内部无关(只验外部可观测行为)。
Invariant = Callable[[T], None]

# 一个工厂:无参构造一个全新实例(每个格子各拿一个,杜绝实现间状态串味)。
Factory = Callable[[], T]


@dataclass(frozen=True)
class InvariantPack(Generic[T]):
    """一组具名不变量。app 用它装自己的保证;harness 只负责跑。

    add() 返回自身,便于链式登记:InvariantPack("x").add(...).add(...)。
    """

    name: str
    invariants: dict[str, Invariant[T]] = field(default_factory=dict)

    def add(self, name: str, check: Invariant[T]) -> InvariantPack[T]:
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


class ConformanceSuite(Generic[T]):
    """把 实现注册表 × 不变量包 绑成可执行 / 可参数化的 conformance 套件。"""

    def __init__(self, implementations: dict[str, Factory[T]], pack: InvariantPack[T]) -> None:
        if not implementations:
            raise ValueError("conformance 套件至少需要一个实现")
        self._impls = dict(implementations)
        self._pack = pack

    def cases(self) -> list[tuple[str, str]]:
        """全部 (实现名, 不变量名) 组合——可直接喂给 pytest.mark.parametrize。"""
        return [(impl, inv) for impl in self._impls for inv in self._pack.names()]

    def ids(self) -> list[str]:
        """与 cases() 对齐的可读用例 id(形如 impl/invariant)。"""
        return [f"{impl}/{inv}" for impl, inv in self.cases()]

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
        ids = [f"{impl}-{inv}" for impl, inv in self.cases()]
        return {"argnames": "case", "argvalues": argvalues, "ids": ids}

    def check(self, impl: str, invariant: str) -> None:
        """跑单个格子:新建该实现实例,对其执行该不变量(失败则原样抛出)。

        每个格子都【新建实例】,杜绝实现间状态串味——与 ragspine 每用例新空库一致。
        """
        instance = self._impls[impl]()
        self._pack.invariants[invariant](instance)

    def run(self) -> list[CaseResult]:
        """跑全部格子并收集结果(不抛;用于离线自检 / 报告)。"""
        results: list[CaseResult] = []
        for impl, inv in self.cases():
            try:
                self.check(impl, inv)
                results.append(CaseResult(impl, inv, True))
            except Exception as exc:  # noqa: BLE001 — 收集失败,不中断其余格子
                results.append(CaseResult(impl, inv, False, f"{type(exc).__name__}: {exc}"))
        return results

    def passed(self) -> bool:
        """便捷:全部格子是否通过(离线自检用)。"""
        return all(result.passed for result in self.run())
