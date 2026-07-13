"""observability.trace 导出面合约:sink 扇出到 TraceExporter,但隐私不变量不放松。

核心钉死的不变量:【任何 exporter 都收不到正文字段】——导出发生在隐私校验【之后】,
受限载荷在 emit 阶段就被 TraceError 挡下、根本到不了 exporter。参数化跑遍 FORBIDDEN_KEYS
× 多个 exporter 实现,确保没有任何一条正文能从导出面泄露(Yuxi 记 prompt/response 正文,
我们刻意更窄:导出面只允许 code/计数/耗时,这是特性不是缺口)。
"""

import pytest

from corespine.conformance.harness import ConformanceSuite, InvariantPack
from corespine.observability.trace import (
    FORBIDDEN_KEYS,
    InProcessPrivacyTraceSink,
    InProcessTraceExporter,
    TraceError,
    TraceEvent,
    TraceExporter,
)

# --- 基础扇出 -----------------------------------------------------------------


def test_exporter_receives_validated_events():
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    sink.emit("retrieve", count=3, took_ms=12)
    sink.emit("rerank", count=2)
    assert [e.code for e in exporter.events] == ["retrieve", "rerank"]
    assert exporter.events[0].fields == {"count": 3, "took_ms": 12}


def test_fan_out_to_multiple_exporters():
    a, b = InProcessTraceExporter(), InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[a, b])
    sink.emit("evt", count=1)
    assert [e.code for e in a.events] == ["evt"]
    assert [e.code for e in b.events] == ["evt"]


def test_add_exporter_after_construction():
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink()
    sink.add_exporter(exporter)
    sink.emit("evt", count=1)
    assert [e.code for e in exporter.events] == ["evt"]


def test_no_exporters_still_records_locally():
    # 无 exporter 时行为与既有一致:本地仍记录。
    sink = InProcessPrivacyTraceSink()
    sink.emit("evt", count=1)
    assert sink.codes() == ["evt"]


def test_default_exporter_events_view_is_copy():
    exporter = InProcessTraceExporter()
    snapshot = exporter.events
    snapshot.append(TraceEvent(code="x"))  # 改副本不污染内部
    assert exporter.events == []


def test_default_exporter_satisfies_protocol():
    assert isinstance(InProcessTraceExporter(), TraceExporter)


# --- 隐私不变量:任何 exporter 都收不到正文字段 --------------------------------


class _SpyExporter:
    """记录它收到的每一条事件的 exporter(用来抓「正文是否泄露到导出面」)。"""

    def __init__(self) -> None:
        self.seen: list[TraceEvent] = []

    def export(self, event: TraceEvent) -> None:
        self.seen.append(event)


@pytest.mark.parametrize("bad_key", sorted(FORBIDDEN_KEYS) + ["ANSWER", "Body", " content "])
def test_forbidden_payload_never_reaches_exporter(bad_key):
    spy = _SpyExporter()
    sink = InProcessPrivacyTraceSink(exporters=[spy])
    with pytest.raises(TraceError):
        sink.emit("leak_attempt", **{bad_key: "敏感正文"})
    # 校验在扇出【之前】:被拒的事件既不入库,也一条都到不了 exporter。
    assert spy.seen == []
    assert sink.events == []


def test_exporter_only_ever_sees_allowed_fields():
    spy = _SpyExporter()
    sink = InProcessPrivacyTraceSink(exporters=[spy])
    # 混合发一批合法事件,断言 exporter 收到的字段键里绝无任一禁词。
    sink.emit("a", count=1, took_ms=5, hit=True)
    sink.emit("b", n=2, ok=False)
    for event in spy.seen:
        for key in event.fields:
            assert key.strip().lower() not in FORBIDDEN_KEYS


# --- 参数化 conformance:任一 exporter 实现都不得从导出面收到正文 ----------------


def _rejects_forbidden_at_export(exporter: TraceExporter) -> None:
    """契约:把 exporter 挂到隐私 sink 上,任何受限载荷都不得抵达它。"""
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    for bad_key in sorted(FORBIDDEN_KEYS):
        with pytest.raises(TraceError):
            sink.emit("x", **{bad_key: "正文"})
    # 无论 exporter 内部怎么实现,它都不该记录到任何东西。
    assert _exported_events(exporter) == []


def _passes_allowed_through_export(exporter: TraceExporter) -> None:
    """契约:合法元数据事件应原样扇出到 exporter,且不携带任何禁词键。"""
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    sink.emit("ok", count=7, took_ms=3)
    events = _exported_events(exporter)
    assert [e.code for e in events] == ["ok"]
    assert all(k.strip().lower() not in FORBIDDEN_KEYS for e in events for k in e.fields)


def _exported_events(exporter: TraceExporter) -> list[TraceEvent]:
    """从两种默认 exporter 取回它记录的事件(测试内省用)。"""
    if isinstance(exporter, InProcessTraceExporter):
        return exporter.events
    return exporter.seen  # _SpyExporter


PACK = (
    InvariantPack[TraceExporter]("trace_exporter")
    .add("rejects_forbidden_at_export", _rejects_forbidden_at_export)
    .add("passes_allowed_through_export", _passes_allowed_through_export)
)

SUITE = ConformanceSuite(
    {"in_process": InProcessTraceExporter, "spy": _SpyExporter},
    PACK,
)


def test_conformance_all_exporters():
    """2 exporter 实现 × 2 契约不变量 全绿。"""
    results = SUITE.run()
    failed = [f"{r.impl}/{r.invariant}: {r.error}" for r in results if not r.passed]
    assert not failed, failed
    assert len(results) == 2 * 2


@pytest.mark.parametrize(**SUITE.parametrize_kwargs())
def test_conformance(case):
    case()
