"""observability.trace 合约:记元数据 / 拒绝正文载荷 / Protocol 结构匹配。"""

import pytest

from corespine.observability.trace import (
    InProcessPrivacyTraceSink,
    TraceError,
    TraceSink,
)


def test_records_codes_counts_and_timings():
    sink = InProcessPrivacyTraceSink()
    sink.emit("retrieve", count=3, took_ms=12, hit=True)
    sink.emit("rerank", count=2)
    assert sink.codes() == ["retrieve", "rerank"]
    first = sink.events[0]
    assert first.code == "retrieve"
    assert first.fields == {"count": 3, "took_ms": 12, "hit": True}


def test_rejects_forbidden_content_payload():
    sink = InProcessPrivacyTraceSink()
    with pytest.raises(TraceError):
        sink.emit("answer_emitted", answer="敏感的真实答案正文")
    # 被拒绝的事件绝不入库。
    assert sink.events == []


@pytest.mark.parametrize("bad_key", ["answer", "value", "text", "content", "CHUNK", "Body"])
def test_forbidden_keys_are_case_insensitive(bad_key):
    sink = InProcessPrivacyTraceSink()
    with pytest.raises(TraceError):
        sink.emit("evt", **{bad_key: "x"})
    assert sink.events == []


def test_events_view_is_a_copy():
    sink = InProcessPrivacyTraceSink()
    sink.emit("evt", count=1)
    snapshot = sink.events
    snapshot.clear()  # 改副本不应污染内部状态。
    assert sink.codes() == ["evt"]


def test_default_satisfies_protocol():
    sink = InProcessPrivacyTraceSink()
    assert isinstance(sink, TraceSink)
