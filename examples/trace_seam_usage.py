"""trace 缝用法范例:两种真实消费形态 + 自定义导出 sink(导出走 app 侧,不进核心)。

跑法:`python examples/trace_seam_usage.py`(全程零网络 / 零依赖 / 确定性)。演示家族里
**两个真实消费者**(ragspine / spineagent)各自用 trace 缝的两种形态,外加"接真实导出后端"
的形状(此处用纯标准库假 exporter 站位,真实 OTel sink 在各 app 自己的可选 extra 里):

  1. 隐私闸门形态(ragspine 式):把 InProcessPrivacyTraceSink 包成自家薄封装,每条 trace
     先过禁词键校验(命中正文即抛 TraceError),再落自己的日志;
  2. 注入点形态(spineagent 式):函数/方法以 `trace: TraceSink | None` 形参接收任意 sink,
     在关键步骤 `.emit(code, **非敏感字段)`;host 决定塞哪个 sink;
  3. 自定义导出 sink:第三方实现 TraceSink 协议、把事件转发到外部系统(真实场景是 OTel,
     这里用 stdlib 假 exporter 站位)——【导出实现一律在 app/contrib 侧,corespine 核心
     dependencies 恒空,绝不 import 任何后端 SDK】。

末行打印 "trace seam demo OK"。
"""

from __future__ import annotations

from corespine import (
    FORBIDDEN_KEYS,
    InProcessPrivacyTraceSink,
    TraceError,
    TraceSink,
)


# ── 形态 1:隐私闸门(ragspine 式)───────────────────────────────────────────
class PrivacyGatedTrace:
    """把 corespine 隐私 sink 包成自家薄封装:每条 trace 先过禁词键校验,再落自己的渠道。

    ragspine 的 common/observability 即此形态——corespine sink 当"落盘前的强制隐私闸门"
    (privacy-by-construction),正文字段命中即抛、绝不外泄。
    """

    def __init__(self) -> None:
        self._gate = InProcessPrivacyTraceSink()
        self.records: list[str] = []  # 站位"自家日志渠道"(真实场景是 stdlib logging)

    def emit_trace(self, code: str, **fields: object) -> None:
        # 先过 corespine 隐私闸门:命中 FORBIDDEN_KEYS 直接抛 TraceError,不会落到下游。
        self._gate.emit(code, **fields)
        # 过闸后才落自家渠道(此处只记 code + 字段个数,演示用)。
        self.records.append(f"{code}({len(fields)} fields)")


# ── 形态 2:注入点(spineagent 式)──────────────────────────────────────────
def run_step(task: str, *, trace: TraceSink | None = None) -> str:
    """一个"带可观测注入点"的步骤:host 传入任意 TraceSink,步骤只发非敏感元数据。

    spineagent 的 agent.step / orchestration 即此形态——trace 形参类型是 TraceSink 协议,
    实现由 host 注入;步骤只 emit code + 计数 + 耗时,绝不 emit 正文。
    """
    output = task.upper()  # 玩具"处理":domain-neutral
    if trace is not None:
        # 只发非敏感元数据:任务/输出的【长度】,不是任务/输出【正文】。
        trace.emit("step", task_chars=len(task), output_chars=len(output))
    return output


# ── 形态 3:自定义导出 sink(真实场景=OTel,导出实现在 app/contrib 侧)──────────
class ForwardingTraceSink:
    """实现 TraceSink 协议、把事件转发到外部系统的形状。

    真实场景里这会是一个 OTel sink(span / metric 导出),其后端 SDK 由各 app 经
    **可选 extra** 声明 + lazy_extra_import 延迟 import——corespine 核心绝不依赖它。
    这里用纯标准库的假 exporter(一个内存列表)站位,保证范例离线确定性、零依赖。
    """

    def __init__(self) -> None:
        self.exported: list[tuple[str, dict[str, object]]] = []

    def emit(self, code: str, **fields: object) -> None:
        # 真实 OTel sink 在这里建 span / 记 metric;导出前【自己】仍要守隐私约定。
        # 守约最省心的做法:复用 corespine 的 FORBIDDEN_KEYS 做同一套禁词键校验。
        offending = sorted(k for k in fields if k.strip().lower() in FORBIDDEN_KEYS)
        if offending:
            raise TraceError(f"导出 sink 同样拒绝受限字段 {offending}")
        self.exported.append((code, dict(fields)))


def main() -> None:
    # 形态 1:隐私闸门——正常元数据照记,携带正文(content)的载荷被闸门拒绝。
    gate = PrivacyGatedTrace()
    gate.emit_trace("request", route_chars=12, latency_ms=3)
    try:
        gate.emit_trace("answer", content="不该出现的答案正文")  # 命中禁词键
    except TraceError:
        rejected = True
    else:
        rejected = False
    assert rejected, "隐私闸门必须拒绝携带正文的载荷"
    assert gate.records == ["request(2 fields)"], gate.records
    print(f"[1/3] 隐私闸门(ragspine 式) → 已落 {gate.records};正文载荷被拒")

    # 形态 2:注入点——同一步骤可塞不同 sink;此处塞隐私 sink,只见元数据。
    sink = InProcessPrivacyTraceSink()
    out = run_step("hello corespine", trace=sink)
    assert out == "HELLO CORESPINE"
    assert sink.codes() == ["step"]
    assert sink.events[0].fields == {"task_chars": 15, "output_chars": 15}
    print(f"[2/3] 注入点(spineagent 式) → emit {sink.codes()};只见长度,不见正文")

    # 形态 3:自定义导出 sink——同一注入点改塞转发 sink,事件流向"外部系统"(站位)。
    forwarder = ForwardingTraceSink()
    assert isinstance(forwarder, TraceSink)  # 鸭子类型即满足 TraceSink 协议
    run_step("export me", trace=forwarder)
    assert forwarder.exported == [("step", {"task_chars": 9, "output_chars": 9})]
    print(f"[3/3] 导出 sink(真实=OTel,走 extra) → 已导出 {forwarder.exported}")

    print("trace seam demo OK")


if __name__ == "__main__":
    main()
