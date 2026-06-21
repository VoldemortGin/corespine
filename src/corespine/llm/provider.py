"""LLM 缝:LLMProvider 协议(OpenAI chat-completions 规范)+ 确定性离线 MockProvider。

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


@runtime_checkable
class LLMProvider(Protocol):
    """provider 协议(OpenAI 规范):给 OpenAI 形状的 messages(可选 tools),拿回 OpenAI ChatCompletion。"""

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion: ...


def _text_of(message: dict[str, Any]) -> str:
    """从一条 OpenAI message dict 取纯文本内容(content 为 None 时视作空串)。"""
    return str(message.get("content") or "")


class MockProvider:
    """确定性离线 provider:零网络、零 key,输出 OpenAI 形状的 ChatCompletion,由 messages 派生、可复现。

    回显最后一条 user 消息,并附一段由【整段对话】计算的稳定 hex 指纹——使不同对话(含不同 system /
    历史)产出不同、但对同一对话恒定的文本;既便于断言,又不引入随机性。绝不伪造 tool_calls:离线
    默认不假装会 function-calling(真工具调用需接真实 provider)。
    """

    def __init__(self, *, prefix: str = "mock") -> None:
        self._prefix = prefix

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion:
        # 指纹覆盖整段对话(role + content),\x00/\x01 作分隔杜绝拼接歧义;取前 12 位 hex。
        joined = "\x00".join(f"{m.get('role', '')}\x01{_text_of(m)}" for m in messages)
        digest = hashlib.sha256(joined.encode()).hexdigest()[:12]
        last_user = next((_text_of(m) for m in reversed(messages) if m.get("role") == "user"), "")
        text = f"[{self._prefix}:{digest}] {last_user.strip()}"
        usage = Usage(
            prompt_tokens=len(joined),
            completion_tokens=len(text),
            total_tokens=len(joined) + len(text),
        )
        choice = Choice(index=0, message=ResponseMessage(role="assistant", content=text), finish_reason="stop")
        return ChatCompletion(choices=(choice,), usage=usage, model=f"{self._prefix}", id=f"chatcmpl-{digest}")
