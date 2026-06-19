"""corespine 离线快速上手:三条缝各跑一遍,全程零网络 / 零 key / 确定性。

跑法:`python examples/quickstart.py`(或 `make demo`)。演示:
  1. Registry + make(spec):把"选哪个实现"降为一个 spec 字符串,解析两个内置实现;
  2. ConformanceSuite:给一条玩具缝绑一套不变量,跑 实现 × 不变量 的笛卡尔积;
  3. 隐私 TraceSink:正常元数据照记,携带正文(content)的载荷被【直接拒绝】。
全部通过后打印一行 "corespine OK"。
"""

from __future__ import annotations

from corespine import (
    ConformanceSuite,
    InProcessPrivacyTraceSink,
    InvariantPack,
    Registry,
    TraceError,
)


class Greeter:
    """玩具缝实现:greet(name) 按语言返回一句问候(domain-neutral、纯本地)。"""

    def __init__(self, *, lang: str) -> None:
        self.lang = lang

    def greet(self, name: str) -> str:
        templates = {"en": "Hello, {name}!", "zh": "你好,{name}!"}
        return templates[self.lang].format(name=name)


def _require(cond: bool, msg: str) -> None:
    """不变量断言助手:不满足即抛(由 ConformanceSuite 捕获并定位到具体格子)。"""
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    # 1) Registry + make(spec):登记两个实现,用 spec 字符串解析构造(大小写 / 连字符不敏感)。
    registry: Registry[Greeter] = Registry("greeter")
    registry.register("english", lambda: Greeter(lang="en"))
    registry.register("chinese", lambda: Greeter(lang="zh"))

    en = registry.make("English")  # 大小写不敏感
    zh = registry.make("chinese")
    assert en.greet("corespine") == "Hello, corespine!"
    assert zh.greet("corespine") == "你好,corespine!"
    print(
        f"[1/3] Registry.make → 可用实现 {registry.names()}:{en.greet('world')} / {zh.greet('世界')}"
    )

    # 2) ConformanceSuite:把 实现 × 不变量 绑成笛卡尔积,逐格执行;每格新建实例,杜绝串味。
    impls = {
        "english": lambda: Greeter(lang="en"),
        "chinese": lambda: Greeter(lang="zh"),
    }
    pack = (
        InvariantPack("greeter-contract")
        .add("greets-non-empty", lambda g: _require(bool(g.greet("x")), "问候不应为空"))
        .add("includes-name", lambda g: _require("Ada" in g.greet("Ada"), "问候应包含名字"))
    )
    suite = ConformanceSuite(impls, pack)
    results = suite.run()
    assert suite.passed(), [r for r in results if not r.passed]
    print(f"[2/3] ConformanceSuite → {len(results)} 格全过:{suite.ids()}")

    # 3) 隐私 TraceSink:元数据照记;携带正文的载荷必须被拒(隐私 by construction)。
    sink = InProcessPrivacyTraceSink()
    sink.emit("greeter.make", impl_count=len(impls), latency_ms=1)
    try:
        sink.emit("greeter.greet", content="你好,世界")  # 携带正文 → 必须被拒
    except TraceError:
        rejected = True
    else:
        rejected = False
    assert rejected, "TraceSink 必须拒绝携带正文(content)的载荷"
    print(f"[3/3] TraceSink → 已记录 {sink.codes()};正文载荷被拒绝")

    print("corespine OK")


if __name__ == "__main__":
    main()
