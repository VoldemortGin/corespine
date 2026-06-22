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
