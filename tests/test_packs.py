"""核心自带的可复用 InvariantPack:queue / trace / llm,参数化跑默认实现,并验证能抓出坏实现。"""

import pytest

from corespine.conformance.harness import ConformanceSuite
from corespine.conformance.packs import (
    LLM_PROVIDER_PACK,
    QUEUE_PACK,
    STREAMING_PROVIDER_PACK,
    TRACE_PRIVACY_PACK,
)
from corespine.llm.provider import ChatCompletion, MockProvider
from corespine.observability.trace import InProcessPrivacyTraceSink
from corespine.queue.task_queue import FakeQueue

QUEUE_SUITE = ConformanceSuite({"fake": FakeQueue}, QUEUE_PACK)
TRACE_SUITE = ConformanceSuite({"in_process": InProcessPrivacyTraceSink}, TRACE_PRIVACY_PACK)
LLM_SUITE = ConformanceSuite({"mock": MockProvider}, LLM_PROVIDER_PACK)
STREAM_SUITE = ConformanceSuite({"mock": MockProvider}, STREAMING_PROVIDER_PACK)


@pytest.mark.parametrize(**QUEUE_SUITE.parametrize_kwargs())
def test_queue_pack(case):
    case()


@pytest.mark.parametrize(**TRACE_SUITE.parametrize_kwargs())
def test_trace_pack(case):
    case()


@pytest.mark.parametrize(**LLM_SUITE.parametrize_kwargs())
def test_llm_pack(case):
    case()


@pytest.mark.parametrize(**STREAM_SUITE.parametrize_kwargs())
def test_streaming_pack(case):
    case()


def test_queue_pack_catches_broken_queue():
    class NoopQueue:
        def enqueue(self, func, payload, *, job_id=None):
            return job_id or "x"

        def get(self, job_id):
            return None

    assert not ConformanceSuite({"noop": NoopQueue}, QUEUE_PACK).passed()


def test_llm_pack_catches_broken_provider():
    class Empty:
        def chat(self, messages, *, tools=None):
            return ChatCompletion(choices=())

    assert not ConformanceSuite({"empty": Empty}, LLM_PROVIDER_PACK).passed()


def test_streaming_pack_catches_mismatched_stream():
    class Mismatch(MockProvider):
        def stream_chat(self, messages, *, tools=None):
            yield from super().stream_chat([{"role": "user", "content": "other"}])

    assert not ConformanceSuite({"bad": Mismatch}, STREAMING_PROVIDER_PACK).passed()
