"""递归隐私门 enforce_trace_privacy + 默认 sink 的嵌套防线 + 可复用的隐私 InvariantPack。"""

import dataclasses
from typing import NamedTuple

import pytest

from corespine.conformance.harness import ConformanceSuite
from corespine.conformance.packs import TRACE_PRIVACY_PACK
from corespine.observability.trace import (
    FORBIDDEN_KEYS,
    MAX_TRACE_DEPTH,
    MAX_TRACE_NODES,
    InProcessPrivacyTraceSink,
    InProcessTraceExporter,
    TraceError,
    enforce_trace_privacy,
)


@dataclasses.dataclass
class _Payload:
    prompt: str = "x"


class _Pair(NamedTuple):
    text: str
    n: int


@pytest.mark.parametrize(
    "key", ["text", "raw_text", "user_prompt", "contents", "question", "output", "messages"]
)
def test_new_and_old_keys_are_forbidden(key):
    assert key in FORBIDDEN_KEYS
    with pytest.raises(TraceError):
        enforce_trace_privacy({key: "x"})


@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"text": "正文"}},
        {"a": {"b": {"c": {"Answer": "x"}}}},
        {"calls": [{"ok": 1}, {"prompt": "x"}]},
        {"calls": ({"body": "x"},)},
        {"s": {frozenset({1}): 1, "k": [{"CONTENT": 1}]}},
        {"dc": _Payload()},
        {"nt": _Pair("x", 1)},
        {"set": {_Pair("x", 1)}},
    ],
)
def test_nested_forbidden_keys_rejected(payload):
    with pytest.raises(TraceError):
        enforce_trace_privacy(payload)


def test_error_message_has_path_but_no_value():
    with pytest.raises(TraceError) as ei:
        enforce_trace_privacy({"calls": [{"prompt": "SECRET-VALUE"}]})
    assert "calls[0].prompt" in str(ei.value)
    assert "SECRET-VALUE" not in str(ei.value)


def test_legit_events_pass():
    enforce_trace_privacy({"count": 3, "took_ms": 1.5, "hit": True, "none": None, "tag": "x"})
    enforce_trace_privacy({"stages": [1, 2, 3], "timings": {"a": 1, "b": 2}, "b": b"raw"})
    enforce_trace_privacy({})


def test_depth_limit():
    ok: dict = {}
    cur = ok
    for _ in range(MAX_TRACE_DEPTH - 1):  # 顶层 + 嵌套共 MAX_TRACE_DEPTH 层容器
        cur["n"] = {}
        cur = cur["n"]
    enforce_trace_privacy(ok)
    cur["n"] = {}
    with pytest.raises(TraceError):
        enforce_trace_privacy(ok)


def test_cycle_is_rejected_not_hung():
    a: list = []
    a.append(a)
    with pytest.raises(TraceError):
        enforce_trace_privacy({"a": a})


def test_node_limit():
    enforce_trace_privacy({"xs": list(range(MAX_TRACE_NODES - 10))})
    with pytest.raises(TraceError):
        enforce_trace_privacy({"xs": list(range(MAX_TRACE_NODES + 10))})


def test_uninspectable_object_fail_closed():
    class Opaque:
        pass

    with pytest.raises(TraceError):
        enforce_trace_privacy({"o": Opaque()})
    with pytest.raises(TraceError):
        enforce_trace_privacy({"o": lambda: 1})


def test_broken_container_fail_closed():
    class Boom(dict):
        def items(self):
            raise RuntimeError("boom")

    with pytest.raises(TraceError):
        enforce_trace_privacy({"o": Boom(a=1)})


def test_sink_rejects_nested_and_does_not_record_or_fan_out():
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    with pytest.raises(TraceError):
        sink.emit("evt", meta={"text": "正文"})
    assert sink.events == []
    assert exporter.events == []


def test_sink_accepts_legit_nested_metadata():
    sink = InProcessPrivacyTraceSink()
    sink.emit("evt", timings={"a": 1}, stages=[1, 2])
    assert sink.events[0].fields == {"timings": {"a": 1}, "stages": [1, 2]}


def test_privacy_pack_on_default_sink():
    suite = ConformanceSuite({"in_process": InProcessPrivacyTraceSink}, TRACE_PRIVACY_PACK)
    assert suite.passed(), [r for r in suite.run() if not r.passed]


def test_privacy_pack_detects_leaky_sink():
    class Leaky:
        def emit(self, code: str, **fields: object) -> None:
            for k in fields:
                if k in FORBIDDEN_KEYS:
                    raise TraceError("x")  # 只查顶层

    suite = ConformanceSuite({"leaky": Leaky}, TRACE_PRIVACY_PACK)
    assert not suite.passed()
