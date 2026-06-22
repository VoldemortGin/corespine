"""LLM 限流缝:RateLimitedProvider —— 主动客户端 TPM 限流(包装任意 LLMProvider)。

domain-neutral:openai / anthropic 等 SDK 只有【被动重试】(撞 429 后指数退避),并没有主动的
"每分钟 token 预算"节流。本包装器在 provider 层【预先平滑限速】——超出每分钟 token 上限就阻塞
等待到滑动窗口腾出预算,从源头尽量不触发 429;与 SDK 的 max_retries 互补(主动 + 被动两层)。
provider-agnostic:包装 Mock / OpenAI / Anthropic / 任意 LLMProvider。纯标准库
(threading + time.monotonic + deque),符合 corespine 零依赖宪章。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from corespine.llm.provider import ChatCompletion, LLMProvider


class RateLimitedProvider:
    """包装任意 LLMProvider,按每分钟 token 上限主动限流;超限【阻塞等待】(平滑限速)。

    滑动窗口:记录过去 window_seconds 内每次调用的 (monotonic 时刻, token 数)。每次 chat 前,
    若窗口内已用 token 达到上限,就 sleep 到最老一条记录滑出窗口(腾出预算)再重查,直到可发;
    预算够(含窗口为空的首次)则直接放行。token 数取 ChatCompletion.usage.total_tokens 在调用后
    【事后累计】——token 用量须等响应才知道,故为软限流:并发下可能小幅超额,但长期收敛到上限。

    与 provider SDK 的 max_retries 互补:SDK 撞 429 后被动退避,本包装器预先主动节流。
    """

    def __init__(
        self, inner: LLMProvider, *, tokens_per_minute: int, window_seconds: float = 60.0
    ) -> None:
        if tokens_per_minute <= 0:
            raise ValueError(f"tokens_per_minute 必须为正:{tokens_per_minute}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds 必须为正:{window_seconds}")
        self._inner = inner
        self._tpm = tokens_per_minute
        self._window = window_seconds
        self._lock = threading.Lock()
        # 滑动窗口内的 (monotonic 时刻, token 数);只保留过去 window_seconds 的记录。
        self._events: deque[tuple[float, int]] = deque()

    def _used_tokens(self, now: float) -> int:
        """清理窗口外旧记录,返回窗口内已用 token 之和(调用方须持锁)。"""
        cutoff = now - self._window
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()
        return sum(tokens for _, tokens in self._events)

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion:
        """限流后转发给 inner.chat;超出每分钟预算时阻塞等待至窗口腾出空间。"""
        # 阻塞等待:窗口内已用达上限就 sleep 到最老记录过期再重查;预算够则放行。
        # sleep 不持锁(避免串行化所有线程);每轮重新取锁判断当前预算。
        while True:
            with self._lock:
                now = time.monotonic()
                if self._used_tokens(now) < self._tpm:
                    break
                wait = (self._events[0][0] + self._window) - now
            if wait > 0:
                time.sleep(wait)

        result = self._inner.chat(messages, tools=tools)

        # 事后累计本次 token 用量(usage 可能为 None,记 0)。
        tokens = result.usage.total_tokens if result.usage is not None else 0
        with self._lock:
            self._events.append((time.monotonic(), tokens))
        return result
