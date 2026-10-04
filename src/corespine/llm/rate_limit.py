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
from collections.abc import Iterator
from typing import Any

from corespine.llm.provider import (
    ChatCompletion,
    ChatCompletionChunk,
    LLMProvider,
    StreamingLLMProvider,
)


class RateLimitedProvider:
    """包装任意 LLMProvider,按每分钟 token 上限主动限流;超限【阻塞等待】(平滑限速)。

    滑动窗口:记录过去 window_seconds 内每次调用的 (monotonic 时刻, token 数)。每次 chat 前,
    若窗口内已用 token 达到上限,就 sleep 到最老一条记录滑出窗口(腾出预算)再重查,直到可发;
    预算够(含窗口为空的首次)则直接放行。token 数取 ChatCompletion.usage.total_tokens 在调用后
    【事后累计】——token 用量须等响应才知道,故为软限流:并发下可能小幅超额,但长期收敛到上限。

    与 provider SDK 的 max_retries 互补:SDK 撞 429 后被动退避,本包装器预先主动节流。

    【流式能力如实透传】inner 同时满足 StreamingLLMProvider 时,`RateLimitedProvider(...)` 实际
    构造出本类的子类(带 `stream_chat`,同样限流计量),故 `isinstance(wrapped,
    StreamingLLMProvider)` 为真;inner 只会 chat 时则不带 stream_chat、isinstance 为假——
    绝不假装支持。构造方式不变,两种结果都满足 `isinstance(x, RateLimitedProvider)`。
    """

    def __new__(cls, inner: LLMProvider, **kwargs: Any) -> RateLimitedProvider:
        if cls is RateLimitedProvider and isinstance(inner, StreamingLLMProvider):
            cls = _StreamingRateLimitedProvider
        return super().__new__(cls)

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

    def _acquire(self) -> None:
        """阻塞等待:窗口内已用达上限就 sleep 到最老记录过期再重查;预算够则放行。

        sleep 不持锁(避免串行化所有线程);每轮重新取锁判断当前预算。
        """
        while True:
            with self._lock:
                now = time.monotonic()
                if self._used_tokens(now) < self._tpm:
                    return
                wait = (self._events[0][0] + self._window) - now
            if wait > 0:
                time.sleep(wait)

    def _record(self, tokens: int) -> None:
        with self._lock:
            self._events.append((time.monotonic(), tokens))

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion:
        """限流后转发给 inner.chat;超出每分钟预算时阻塞等待至窗口腾出空间。"""
        self._acquire()
        result = self._inner.chat(messages, tools=tools)
        # 事后累计本次 token 用量(usage 可能为 None,记 0)。
        self._record(result.usage.total_tokens if result.usage is not None else 0)
        return result


def _chars_to_tokens(chars: int) -> int:
    """无 usage 可用时的粗略估算:约 4 字符 / token(向上取整)。"""
    return (chars + 3) // 4


# 非文本内容片段(图片 / 音频 / 文件等)估算时按的固定字符数:不看其载荷大小(base64 图片可达数百
# KB,按字符估会把 tpm 撑爆),只给一个确定性的小常数。
_NON_TEXT_PART_CHARS = 32


def _estimate_input_chars(messages: list[dict[str, Any]]) -> int:
    """估算输入消息的「文本字符数」(确定性,与多模态载荷大小无关)。

    - `content` 是 `str`:按其长度;`None` / 缺省:0;
    - `content` 是 list:`str` 片段与 `{"type": "text", "text": ...}` 片段按文本长度累计,
      其它类型的片段(图片 / 音频 / 文件 ...)每个固定计 `_NON_TEXT_PART_CHARS`;
    - 其它类型的 `content`:同样按一个非文本片段计。
    """
    total = 0
    for m in messages:
        content = m.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    total += len(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text") or "")
                else:
                    total += _NON_TEXT_PART_CHARS
        else:
            total += _NON_TEXT_PART_CHARS
    return total


class _StreamingRateLimitedProvider(RateLimitedProvider):
    """inner 支持流式时的包装类:在 RateLimitedProvider 之上叠加 `stream_chat`(见其 docstring)。

    token 计量:chunk 若携带 `usage`(如开启 stream usage 的 OpenAI 兼容后端)就用其
    total_tokens;否则按「提示词 + 已吐出内容」的字符数粗估(约 4 字符 / token)——流式 chunk
    协议本身不带用量,估算是软限流下的诚实近似。输入只统计【文本部分】(见 `_estimate_input_chars`:
    多模态的图片等非文本片段按固定小常数计,与载荷大小无关)。迭代被中途放弃时,按已消费部分计量。
    """

    def stream_chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> Iterator[ChatCompletionChunk]:
        """限流(首次迭代时检查预算,必要时阻塞)后转发给 inner.stream_chat,并事后计量。"""
        self._acquire()
        inner = self._inner
        assert isinstance(inner, StreamingLLMProvider)  # __new__ 已保证
        out_chars = 0
        reported = 0
        try:
            for chunk in inner.stream_chat(messages, tools=tools):
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    reported = max(reported, usage.total_tokens)
                for choice in chunk.choices:
                    out_chars += len(choice.delta.content or "")
                yield chunk
        finally:
            in_chars = _estimate_input_chars(messages)
            self._record(reported or _chars_to_tokens(in_chars + out_chars))
