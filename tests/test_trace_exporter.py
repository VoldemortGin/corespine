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


# --- 事后改写 / exporter 故障隔离 ------------------------------------------------------


def test_event_fields_cannot_be_mutated_after_emit():
    sink = InProcessPrivacyTraceSink(exporters=[InProcessTraceExporter()])
    sink.emit("evt", count=1)
    event = sink.events[0]
    with pytest.raises(TypeError):
        event.fields["text"] = "正文"
    with pytest.raises(TypeError):
        event.fields.update({"text": "x"})  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        del event.fields["count"]
    assert event.fields == {"count": 1}


def test_emit_snapshot_is_independent_of_callers_mutable_payload():
    sink = InProcessPrivacyTraceSink()
    meta = {"a": 1}
    sink.emit("evt", meta=meta)
    meta["text"] = "后来才塞进去的正文"
    assert sink.events[0].fields == {"meta": {"a": 1}}


def test_exporters_do_not_share_one_event_object():
    first, second = InProcessTraceExporter(), InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[first, second])
    sink.emit("evt", stages=[1, 2])
    sink.events[0].fields["stages"].append(3)  # 嵌套容器可变,但各方互不影响
    assert first.events[0].fields["stages"] == [1, 2]
    assert second.events[0].fields["stages"] == [1, 2]


def test_traceevent_constructed_directly_is_also_readonly_and_eq_dict():
    event = TraceEvent("c", {"n": 1})
    assert event.fields == {"n": 1}
    with pytest.raises(TypeError):
        event.fields["x"] = 1
    import json

    assert json.dumps(event.fields) == '{"n": 1}'


def test_failing_exporter_does_not_break_others_or_caller():
    class Boom:
        def export(self, event):
            raise RuntimeError("exporter 挂了")

    good = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[Boom(), good])
    sink.emit("evt", count=1)  # 不抛
    sink.emit("evt2", count=2)
    assert sink.codes() == ["evt", "evt2"]
    assert [e.code for e in good.events] == ["evt", "evt2"]
    assert sink.exporter_failures == 2
    assert InProcessPrivacyTraceSink().exporter_failures == 0


def test_exporter_failure_count_never_holds_content():
    class Boom:
        def export(self, event):
            raise RuntimeError("SECRET-in-error")

    sink = InProcessPrivacyTraceSink(exporters=[Boom()])
    sink.emit("evt", count=1)
    assert isinstance(sink.exporter_failures, int)
    assert all("SECRET-in-error" not in repr(e) for e in sink.events)


# --- 内存型 sink / exporter 的可选容量上限(缺省不限) --------------------------------------


def test_in_memory_sink_and_exporter_default_unbounded():
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    for i in range(300):
        sink.emit("e", n=i)
    assert len(sink.events) == 300 and len(exporter.events) == 300


def test_in_memory_sink_and_exporter_max_events_keeps_latest():
    exporter = InProcessTraceExporter(max_events=3)
    sink = InProcessPrivacyTraceSink(exporters=[exporter], max_events=5)
    for i in range(10):
        sink.emit("e", n=i)
    assert [e.fields["n"] for e in sink.events] == [5, 6, 7, 8, 9]
    assert [e.fields["n"] for e in exporter.events] == [7, 8, 9]
    assert sink.codes() == ["e"] * 5
    assert sink.dropped_events == 5 and exporter.dropped_events == 7


def test_max_events_validation():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            InProcessPrivacyTraceSink(max_events=bad)
        with pytest.raises(ValueError):
            InProcessTraceExporter(max_events=bad)
