"""可复用的 InvariantPack:把核心缝的契约做成下游可一行绑定的不变量包(机制,非保证)。

harness 本身不含任何具体不变量;这里按缝提供【最小、domain-neutral】的契约包,下游(或核心
自己的测试)用 `ConformanceSuite({...impls}, XXX_PACK)` 即可对自己的实现跑同一套。
本模块 pytest-free:违反不变量一律抛 AssertionError / 对应领域异常。
"""

from __future__ import annotations

from corespine.conformance.harness import InvariantPack
from corespine.observability.trace import FORBIDDEN_KEYS, TraceError, TraceSink


def _expect_rejected(sink: TraceSink, **fields: object) -> None:
    try:
        sink.emit("conformance_probe", **fields)
    except TraceError:
        return
    raise AssertionError(f"trace sink 放行了含受限内容的载荷:{sorted(fields)}")


def _trace_rejects_top_level_forbidden_keys(sink: TraceSink) -> None:
    for key in sorted(FORBIDDEN_KEYS):
        _expect_rejected(sink, **{key: "x"})
    _expect_rejected(sink, **{"Answer": "x"})  # 大小写不敏感


def _trace_rejects_nested_forbidden_keys(sink: TraceSink) -> None:
    _expect_rejected(sink, meta={"text": "x"})
    _expect_rejected(sink, calls=[{"ok": 1}, {"prompt": "x"}])
    _expect_rejected(sink, deep={"a": {"b": ({"body": "x"},)}})


def _trace_rejects_uninspectable_objects(sink: TraceSink) -> None:
    _expect_rejected(sink, obj=object())


def _trace_accepts_metadata_events(sink: TraceSink) -> None:
    sink.emit("stage_done", count=3, took_ms=1.5, ok=True, timings={"a": 1}, stages=[1, 2])


TRACE_PRIVACY_PACK: InvariantPack[TraceSink] = (
    InvariantPack("trace_privacy")
    .add("rejects_top_level_forbidden_keys", _trace_rejects_top_level_forbidden_keys)
    .add("rejects_nested_forbidden_keys", _trace_rejects_nested_forbidden_keys)
    .add("rejects_uninspectable_objects", _trace_rejects_uninspectable_objects)
    .add("accepts_metadata_events", _trace_accepts_metadata_events)
)
