"""llm 流式协议合约:StreamingLLMProvider(独立 Protocol,不破坏既有)+ 流非流确定性等价。

核心不变量(conformance 钉死):【流式拼接结果 == 非流式结果】——stream_chat 逐块吐出的
delta.content 顺序拼接,必须逐字等于 chat().choices[0].message.content。MockProvider 是确定性
分块流式默认:同 messages 恒定产出同一串块,拼回同一段文本。
"""

import pytest

from corespine.conformance.harness import ConformanceSuite, InvariantPack
from corespine.llm.provider import (
    ChatCompletionChunk,
    LLMProvider,
    MockProvider,
    StreamingLLMProvider,
)


def _u(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def _accumulate(provider: StreamingLLMProvider, messages: list[dict]) -> str:
    """把一次流式的所有 delta.content 顺序拼接成整段文本。"""
    parts: list[str] = []
    for chunk in provider.stream_chat(messages):
        delta = chunk.choices[0].delta
        if delta.content:
            parts.append(delta.content)
    return "".join(parts)


# --- Protocol 分离:不破坏既有 LLMProvider ------------------------------------


def test_mock_is_both_provider_and_streaming():
    p = MockProvider()
    assert isinstance(p, LLMProvider)  # 既有能力不变
    assert isinstance(p, StreamingLLMProvider)  # 叠加流式能力


def test_non_streaming_provider_still_satisfies_llmprovider():
    # 只实现 chat 的老实现仍是合法 LLMProvider,且【不】被误判为 StreamingLLMProvider。
    class LegacyProvider:
        def chat(self, messages, *, tools=None):
            return MockProvider().chat(messages, tools=tools)

    legacy = LegacyProvider()
    assert isinstance(legacy, LLMProvider)
    assert not isinstance(legacy, StreamingLLMProvider)


# --- 流式输出形状(OpenAI chat.completion.chunk)------------------------------


def test_stream_yields_openai_shaped_chunks():
    chunks = list(MockProvider().stream_chat(_u("hello")))
    assert chunks, "至少产出一块"
    for ch in chunks:
        assert isinstance(ch, ChatCompletionChunk)
        assert ch.object == "chat.completion.chunk"
        assert ch.choices[0].index == 0
    # 首块带 role,末块带 finish_reason=stop(与 OpenAI 流式约定一致)。
    assert chunks[0].choices[0].delta.role == "assistant"
    assert chunks[-1].choices[0].finish_reason == "stop"


def test_stream_id_matches_non_stream():
    # 同 messages 下,流式与非流式共享同一确定性 id(同源派生)。
    msgs = _u("同一段对话")
    stream_id = next(iter(MockProvider().stream_chat(msgs))).id
    assert stream_id == MockProvider().chat(msgs).id


def test_stream_is_deterministic():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "Q"}]
    a = [(c.choices[0].delta.content) for c in MockProvider().stream_chat(msgs)]
    b = [(c.choices[0].delta.content) for c in MockProvider().stream_chat(msgs)]
    assert a == b  # 逐块逐字一致


def test_stream_never_fabricates_tool_calls():
    # 离线默认不假装 function-calling:给了 tools 也不吐 tool_calls,末块仍 finish_reason=stop。
    chunks = list(
        MockProvider().stream_chat(
            _u("用工具"), tools=[{"type": "function", "function": {"name": "calc"}}]
        )
    )
    assert chunks[-1].choices[0].finish_reason == "stop"


# --- 核心不变量:流式拼接 == 非流式 -------------------------------------------

_FIXTURES = [
    _u("hello"),
    _u("中国内地FY2024的REVENUE是多少"),
    [{"role": "system", "content": "sys"}, {"role": "user", "content": "带历史的多轮"}],
    _u(""),  # 空 user 也应等价
    _u("x" * 200),  # 长文本跨多块
]


@pytest.mark.parametrize("prefix", ["mock", "offline"])
@pytest.mark.parametrize("messages", _FIXTURES)
def test_stream_concat_equals_non_stream(prefix, messages):
    p = MockProvider(prefix=prefix)
    streamed = _accumulate(p, messages)
    full = p.chat(messages).choices[0].message.content
    assert streamed == full


# --- 参数化 conformance:确定性等价不变量 --------------------------------------


def _stream_concat_equals_chat(provider: StreamingLLMProvider) -> None:
    """契约:对多组 messages,流式拼接逐字等于非流式 content。"""
    # provider 同时是 LLMProvider(MockProvider 两者皆是);用 chat 取基准。
    chat = provider.chat  # type: ignore[attr-defined]
    for messages in _FIXTURES:
        streamed = _accumulate(provider, messages)
        full = chat(messages).choices[0].message.content
        assert streamed == full, messages


PACK = InvariantPack[StreamingLLMProvider]("streaming_llm").add(
    "stream_concat_equals_chat", _stream_concat_equals_chat
)

SUITE = ConformanceSuite(
    {"mock": MockProvider, "offline": lambda: MockProvider(prefix="offline")},
    PACK,
)


def test_conformance_streaming_equivalence():
    results = SUITE.run()
    failed = [f"{r.impl}/{r.invariant}: {r.error}" for r in results if not r.passed]
    assert not failed, failed


@pytest.mark.parametrize(**SUITE.parametrize_kwargs())
def test_conformance(case):
    case()
