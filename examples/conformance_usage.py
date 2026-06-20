"""conformance 用法范例:app 如何给一条缝绑【自己的】不变量并跑全套。

跑法:`python examples/conformance_usage.py`(全程零网络 / 零依赖 / 确定性)。演示:
  1. 为一条玩具缝(Counter:加计数器)定义两个实现 —— 一个【守约】、一个【违约】;
  2. 用 InvariantPack 装上 app 自己的不变量(机制示范,corespine 核心不含任何具体不变量);
  3. ConformanceSuite 把 实现 × 不变量 绑成笛卡尔积,遍历 cases() 逐格跑,打印每格 PASS/FAIL;
  4. 违约实现必然在对应格子 FAIL —— 证明"没过 conformance 的实现会被逮住"。
末行打印 "conformance demo OK"。
"""

from __future__ import annotations

from corespine import ConformanceSuite, InvariantPack


class Counter:
    """玩具缝:从 0 起累加,add(n) 返回累加后的当前值(domain-neutral、纯本地)。"""

    def __init__(self) -> None:
        self._total = 0

    def add(self, n: int) -> int:
        self._total += n
        return self._total


class BrokenCounter:
    """故意违约的实现:add() 永远返回 0(用来证明 conformance 能逮住坏实现)。"""

    def add(self, n: int) -> int:  # noqa: ARG002 — 故意忽略入参以制造违约
        return 0


def _require(cond: bool, msg: str) -> None:
    """不变量断言助手:不满足即抛(由 ConformanceSuite 捕获并定位到具体格子)。"""
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    # 1) 实现注册表:名字 -> 无参工厂(每格各新建一个实例,杜绝实现间状态串味)。
    impls = {
        "counter": Counter,
        "broken": BrokenCounter,
    }

    # 2) app 自己的不变量包(corespine 只给机制,这些不变量由 app 绑)。
    pack = (
        InvariantPack("counter-contract")
        .add("first-add-returns-n", lambda c: _require(c.add(3) == 3, "首次 add(3) 应返回 3"))
        .add("accumulates", lambda c: _require((c.add(2), c.add(5)) == (2, 7), "应连续累加"))
    )

    # 3) 绑成笛卡尔积,遍历 cases() 逐格跑并打印 PASS/FAIL。
    suite = ConformanceSuite(impls, pack)
    for impl, invariant in suite.cases():
        try:
            suite.check(impl, invariant)
            print(f"PASS  {impl}/{invariant}")
        except Exception as exc:  # noqa: BLE001 — 逐格报告,不中断其余格子
            print(f"FAIL  {impl}/{invariant}  ({type(exc).__name__}: {exc})")

    # 4) 守约实现全过、违约实现全挂 —— 验证机制确实在"逮坏实现"。
    results = {(r.impl, r.invariant): r.passed for r in suite.run()}
    assert all(p for (impl, _), p in results.items() if impl == "counter"), "counter 应全过"
    assert not any(p for (impl, _), p in results.items() if impl == "broken"), "broken 应全挂"

    print("conformance demo OK")


if __name__ == "__main__":
    main()
