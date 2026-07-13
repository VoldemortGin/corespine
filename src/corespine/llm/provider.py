"""LLM 缝:LLMProvider 协议(OpenAI chat-completions 规范)+ 确定性离线 MockProvider。

流式能力作【独立叠加协议】StreamingLLMProvider 提供(见其 docstring:为何不给 LLMProvider
直接加方法),MockProvider 同时满足两者、给出确定性分块流式默认(流式拼接逐字等于非流式)。


【对外唯一规范 = OpenAI chat completions 形状】——输入是 OpenAI 风格的 messages(list[dict]:
role / content / 可带 tool_calls / tool_call_id)与 OpenAI function-tool 形状的 tools;输出是
OpenAI 形状的 ChatCompletion(choices[].message.{content, tool_calls[].function.{name, arguments}}、
finish_reason、usage.{prompt_tokens, completion_tokens, total_tokens})。

为什么以 OpenAI 形状作规范:它已是事实标准(LiteLLM / OpenRouter / vLLM / Ollama / Together /
Groq 等都吐这个),所以"规范化到 OpenAI"= 兼容面最广;用户永远只按 OpenAI 规范写代码。后端是
Anthropic 或其它非 OpenAI 兼容模型时,由各 app 的适配器【在内部转成 OpenAI 形状再吐出】,用户无感。
domain-neutral:这是当下 LLM 的通用 wire 形状,不含任何 RAG / agent 特定概念;tool-use 循环仍归 app。

核心 import 本模块零 SDK;真实 provider(Anthropic / OpenAI 等)由各 app 在自己的缝里延迟 import
接入。MockProvider 是离线确定性默认:同样的 messages 恒定产出同样的响应(纯函数、零网络、零 key),
让"装上即可端到端跑"成立、测试可复现;它【不】伪造 tool_calls(离线默认不假装会 function-calling)。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class FunctionCall:
    """工具调用的函数部分(OpenAI 形状):name + arguments(JSON 字符串,与 OpenAI 完全一致)。"""

    name: str
    arguments: str = "{}"


@dataclass(frozen=True)
class ToolCall:
    """一次工具调用(OpenAI 形状):id + type + function。"""

    id: str
    function: FunctionCall
    type: str = "function"


@dataclass(frozen=True)
class ResponseMessage:
    """模型返回的消息(OpenAI 形状):role + content(可空)+ tool_calls(可空)。"""

    role: str = "assistant"
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] | None = None


@dataclass(frozen=True)
class Choice:
    """一个候选(OpenAI 形状):index + message + finish_reason(stop / tool_calls / length …)。"""

    index: int
    message: ResponseMessage
    finish_reason: str = "stop"


@dataclass(frozen=True)
class Usage:
    """token 用量(OpenAI 形状):prompt / completion / total。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ChatCompletion:
    """一次对话补全结果(OpenAI 形状):choices + usage + 元数据,字段与 OpenAI 完全一致。"""

    choices: tuple[Choice, ...]
    usage: Usage | None = None
    model: str = ""
    id: str = ""
    created: int = 0
    object: str = "chat.completion"


@dataclass(frozen=True)
class ChoiceDelta:
    """流式增量消息(OpenAI 形状):role / content 都可空(首块给 role,后续块给 content 片段)。"""

    role: str | None = None
    content: str | None = None


@dataclass(frozen=True)
class ChunkChoice:
    """流式候选(OpenAI 形状):index + delta + finish_reason(仅末块非空)。"""

    index: int
    delta: ChoiceDelta
    finish_reason: str | None = None


@dataclass(frozen=True)
class ChatCompletionChunk:
    """一块流式补全(OpenAI 形状):choices + 元数据,object 固定 "chat.completion.chunk"。

    与 ChatCompletion 同源:同一次对话流式吐出的所有 chunk 的 delta.content 顺序拼接,
    应逐字等于对应 ChatCompletion.choices[0].message.content(确定性等价不变量)。
    """

    choices: tuple[ChunkChoice, ...]
    model: str = ""
    id: str = ""
    created: int = 0
    object: str = "chat.completion.chunk"


@runtime_checkable
class LLMProvider(Protocol):
    """provider 协议(OpenAI 规范):给 OpenAI 形状的 messages(可选 tools),拿回 OpenAI ChatCompletion。"""

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion: ...


@runtime_checkable
class StreamingLLMProvider(Protocol):
    """流式 provider 协议(OpenAI 规范):逐块吐 ChatCompletionChunk。

    【为何独立于 LLMProvider 而非给它加方法】:LLMProvider 是 runtime_checkable 的结构协议,
    给它直接加 stream_chat 会让【所有既有只实现 chat 的实现者】瞬间不再满足协议——破坏兼容。
    故把流式作为【可选叠加能力】单列一个协议:非流式实现继续是合法 LLMProvider;支持流式的
    实现同时满足两者。消费者用 `isinstance(p, StreamingLLMProvider)` 探测能力再决定走哪条路,
    不必强迫每个 provider 都实现流式。tools 签名与 chat 对齐,便于同一调用点二选一。
    """

    def stream_chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> Iterator[ChatCompletionChunk]: ...


def _text_of(message: dict[str, Any]) -> str:
    """从一条 OpenAI message dict 取纯文本内容(content 为 None 时视作空串)。"""
    return str(message.get("content") or "")


class MockProvider:
    """确定性离线 provider:零网络、零 key,输出 OpenAI 形状的 ChatCompletion,由 messages 派生、可复现。

    回显最后一条 user 消息,并附一段由【整段对话】计算的稳定 hex 指纹——使不同对话(含不同 system /
    历史)产出不同、但对同一对话恒定的文本;既便于断言,又不引入随机性。绝不伪造 tool_calls:离线
    默认不假装会 function-calling(真工具调用需接真实 provider)。
    """

    # 流式分块宽度(字符):把整段文本切成若干 content 片段逐块吐出。仅影响块的粒度,
    # 不影响拼接结果——保证「流式拼接 == 非流式」这条确定性等价不变量。
    _CHUNK_WIDTH = 16

    def __init__(self, *, prefix: str = "mock") -> None:
        self._prefix = prefix

    def _derive(self, messages: list[dict[str, Any]]) -> tuple[str, str, str]:
        """从整段对话确定性地派生 (整段拼串, 12 位 hex 指纹, 回复文本);chat 与 stream_chat 共用同一源。

        共用同一派生逻辑,是「流式拼接 == 非流式」得以成立的根:两条路的文本、id 皆同源。
        """
        # 指纹覆盖整段对话(role + content),\x00/\x01 作分隔杜绝拼接歧义;取前 12 位 hex。
        joined = "\x00".join(f"{m.get('role', '')}\x01{_text_of(m)}" for m in messages)
        digest = hashlib.sha256(joined.encode()).hexdigest()[:12]
        last_user = next((_text_of(m) for m in reversed(messages) if m.get("role") == "user"), "")
        text = f"[{self._prefix}:{digest}] {last_user.strip()}"
        return joined, digest, text

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion:
        joined, digest, text = self._derive(messages)
        usage = Usage(
            prompt_tokens=len(joined),
            completion_tokens=len(text),
            total_tokens=len(joined) + len(text),
        )
        choice = Choice(index=0, message=ResponseMessage(role="assistant", content=text), finish_reason="stop")
        return ChatCompletion(choices=(choice,), usage=usage, model=f"{self._prefix}", id=f"chatcmpl-{digest}")

    def stream_chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> Iterator[ChatCompletionChunk]:
        """确定性分块流式:首块吐 role,随后按 _CHUNK_WIDTH 逐块吐 content 片段,末块吐 stop。

        所有 content 片段顺序拼接 == chat().choices[0].message.content(等价不变量)。绝不伪造
        tool_calls:离线默认不假装 function-calling,末块 finish_reason 恒为 "stop"。
        """
        _joined, digest, text = self._derive(messages)
        cid = f"chatcmpl-{digest}"

        def _chunk(delta: ChoiceDelta, finish_reason: str | None = None) -> ChatCompletionChunk:
            return ChatCompletionChunk(
                choices=(ChunkChoice(index=0, delta=delta, finish_reason=finish_reason),),
                model=self._prefix,
                id=cid,
            )

        # 首块:仅 role(与 OpenAI 流式约定一致,role 块不含 content)。
        yield _chunk(ChoiceDelta(role="assistant"))
        # 中间块:把整段文本切成宽度 _CHUNK_WIDTH 的片段逐块吐出。
        for start in range(0, len(text), self._CHUNK_WIDTH):
            yield _chunk(ChoiceDelta(content=text[start : start + self._CHUNK_WIDTH]))
        # 末块:空 delta + finish_reason=stop。
        yield _chunk(ChoiceDelta(), finish_reason="stop")
