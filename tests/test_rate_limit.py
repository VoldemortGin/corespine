"""rate_limit 合约:TPM 滑动窗口 + 阻塞等待 + 事后 token 累计 + provider 兼容 + 线程安全。"""

import threading
import time

import pytest

from corespine import RateLimitedProvider
from corespine.llm.provider import (
    ChatCompletion,
    Choice,
    LLMProvider,
    MockProvider,
    ResponseMessage,
    StreamingLLMProvider,
    Usage,
)


class _FixedProvider:
    """每次 chat 返回固定 total_tokens 的 provider(便于精确测限流逻辑)。"""

    def __init__(self, tokens):
        self._tokens = tokens
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        return ChatCompletion(
            choices=(Choice(index=0, message=ResponseMessage(content="ok")),),
            usage=Usage(total_tokens=self._tokens),
        )


def test_under_limit_does_not_block():
    inner = _FixedProvider(tokens=10)
    rl = RateLimitedProvider(inner, tokens_per_minute=10_000, window_seconds=60.0)
    start = time.monotonic()
    for _ in range(5):
        rl.chat([{"role": "user", "content": "hi"}])
    assert time.monotonic() - start < 0.1  # 远低于上限,不阻塞
    assert inner.calls == 5


def test_over_limit_blocks_until_window_frees():
    # tpm=10、每次用 10 token:第 1 次放行用满,第 2 次须等窗口(0.2s)腾出预算。
    inner = _FixedProvider(tokens=10)
    rl = RateLimitedProvider(inner, tokens_per_minute=10, window_seconds=0.2)
    rl.chat([{"role": "user", "content": "a"}])  # 放行
    start = time.monotonic()
    rl.chat([{"role": "user", "content": "b"}])  # 须等第一条滑出窗口
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15  # 约等一个 0.2s 窗口(留误差)
    assert inner.calls == 2


def test_tokens_accumulate_from_usage():
    inner = _FixedProvider(tokens=7)
    rl = RateLimitedProvider(inner, tokens_per_minute=1000, window_seconds=60.0)
    rl.chat([{"role": "user", "content": "x"}])
    rl.chat([{"role": "user", "content": "y"}])
    with rl._lock:
        assert rl._used_tokens(time.monotonic()) == 14  # 窗口内累计 7+7


def test_satisfies_llmprovider_protocol():
    rl = RateLimitedProvider(MockProvider(), tokens_per_minute=1000)
    assert isinstance(rl, LLMProvider)  # runtime_checkable Protocol
    out = rl.chat([{"role": "user", "content": "hello"}])
    assert isinstance(out, ChatCompletion)


def test_invalid_params_raise():
    with pytest.raises(ValueError):
        RateLimitedProvider(MockProvider(), tokens_per_minute=0)
    with pytest.raises(ValueError):
        RateLimitedProvider(MockProvider(), tokens_per_minute=100, window_seconds=0)


def test_concurrent_calls_thread_safe():
    inner = _FixedProvider(tokens=5)
    rl = RateLimitedProvider(inner, tokens_per_minute=10_000, window_seconds=60.0)
    errors = []

    def worker():
        try:
            for _ in range(10):
                rl.chat([{"role": "user", "content": "c"}])
        except Exception as e:  # noqa: BLE001 — 测试只关心是否抛错
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert inner.calls == 80  # 8 线程 × 10 次,全部完成


# --- 流式能力透传 ---------------------------------------------------------------------


def test_wrapping_streaming_inner_keeps_streaming_capability():
    rl = RateLimitedProvider(MockProvider(), tokens_per_minute=10_000)
    assert isinstance(rl, StreamingLLMProvider)
    assert isinstance(rl, LLMProvider)
    assert isinstance(rl, RateLimitedProvider)


def test_wrapping_non_streaming_inner_does_not_pretend_to_stream():
    rl = RateLimitedProvider(_FixedProvider(tokens=1), tokens_per_minute=10_000)
    assert not isinstance(rl, StreamingLLMProvider)
    assert isinstance(rl, LLMProvider)
    assert isinstance(rl, RateLimitedProvider)
    assert not hasattr(rl, "stream_chat")


def test_stream_chat_passes_through_chunks_unchanged():
    inner = MockProvider()
    rl = RateLimitedProvider(inner, tokens_per_minute=10_000)
    messages = [{"role": "user", "content": "你好,流式限流"}]
    assert list(rl.stream_chat(messages)) == list(inner.stream_chat(messages))


def test_stream_chat_is_metered_and_throttles_next_call():
    # tpm 很小:一次流式调用的(估算)用量即用满预算,下一次 chat 必须等窗口腾出。
    rl = RateLimitedProvider(MockProvider(), tokens_per_minute=1, window_seconds=0.2)
    list(rl.stream_chat([{"role": "user", "content": "hello"}]))
    start = time.monotonic()
    rl.chat([{"role": "user", "content": "again"}])
    assert time.monotonic() - start >= 0.15


def test_stream_chat_uses_reported_usage_when_chunks_carry_it():
    from dataclasses import dataclass

    @dataclass
    class _Chunk:
        choices: tuple = ()
        usage: Usage | None = None

    class _UsageStream:
        def chat(self, messages, *, tools=None):
            raise AssertionError

        def stream_chat(self, messages, *, tools=None):
            yield _Chunk()
            yield _Chunk(usage=Usage(total_tokens=500))

    rl = RateLimitedProvider(_UsageStream(), tokens_per_minute=100, window_seconds=0.2)
    list(rl.stream_chat([{"role": "user", "content": "x"}]))
    assert rl._used_tokens(time.monotonic()) == 500


def test_stream_chat_gate_blocks_when_over_budget():
    rl = RateLimitedProvider(MockProvider(), tokens_per_minute=1, window_seconds=0.2)
    rl.chat([{"role": "user", "content": "a"}])  # 用满预算
    start = time.monotonic()
    list(rl.stream_chat([{"role": "user", "content": "b"}]))
    assert time.monotonic() - start >= 0.15


def test_non_streaming_chat_behaviour_unchanged_on_streaming_wrapper():
    inner = MockProvider()
    rl = RateLimitedProvider(inner, tokens_per_minute=10_000)
    msgs = [{"role": "user", "content": "hi"}]
    assert rl.chat(msgs) == inner.chat(msgs)


# --- 流式估算只统计文本,不被多模态大载荷放大 ------------------------------------------


class _SilentStream:
    """只吐一个空 chunk 的流式 inner(不带 usage),让估算只取决于输入消息。"""

    def chat(self, messages, *, tools=None):
        raise AssertionError

    def stream_chat(self, messages, *, tools=None):
        return iter(())


def _stream_estimate(messages) -> int:
    rl = RateLimitedProvider(_SilentStream(), tokens_per_minute=10**9)
    list(rl.stream_chat(messages))
    return rl._used_tokens(time.monotonic())


def _image_msg(size: int, text: str = "describe"):
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * size}},
            ],
        }
    ]


def test_stream_estimate_is_independent_of_image_size():
    small, big = _stream_estimate(_image_msg(10)), _stream_estimate(_image_msg(400_000))
    assert small == big
    assert big < 100  # 绝不是约 10 万 token 的量级


def test_stream_estimate_counts_text_parts_and_str_content():
    assert _stream_estimate([{"role": "user", "content": "x" * 400}]) == 100
    assert _stream_estimate(_image_msg(10, text="y" * 400)) > _stream_estimate(
        _image_msg(10, text="")
    )
    # 文本片段按字符数计:多 400 字符 = 多 100 token。
    assert (
        _stream_estimate(_image_msg(10, text="y" * 400)) - _stream_estimate(_image_msg(10, text=""))
        == 100
    )
    assert _stream_estimate([{"role": "user", "content": None}]) == 0
