"""LLM 缝:LLMProvider 协议(chat/messages 形状)+ 确定性离线 MockProvider。

domain-neutral——约定当下 LLM 的【通用形状】:给一串 messages(role + content),拿回一次
对话结果(文本 + 可选工具调用 + 可选用量)。这正是 OpenAI / Anthropic / Gemini 等收敛到的
最广兼容接口(messages 进、message 出),取代旧的单 prompt `complete`。

`chat` 可选带 `tools`(function-calling 工具描述,采 OpenAI function-tool 形状作规范),结果
可带 `tool_calls`(模型请求的工具调用)。注意:这里只暴露 provider 的【原生 chat + tool-calling
能力】——它是 domain-neutral 的 LLM API 面;真正的「决定调哪个工具、执行、把结果喂回」的
tool-use 循环属各 app(agentspine)自己的缝,不在薄核。

核心 import 本模块零 SDK;真实 provider(Anthropic / OpenAI 等)由各 app 在自己的缝里延迟
import 接入。MockProvider 是离线确定性默认:同样的 messages 恒定产出同样的文本(纯函数、零
网络、零 key),让"装上即可端到端跑"成立,也让测试可复现;它【不】伪造 tool_calls(离线默认
不假装会推理)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    """一条对话消息(OpenAI 风格):role(system / user / assistant / tool)+ content。"""

    role: str
    content: str


@dataclass(frozen=True)
class ToolCall:
    """模型请求的一次工具调用(function-calling):id + 工具名 + 结构化参数。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatResult:
    """一次对话补全结果:文本 + 工具调用(可空)+ 可选 token 用量。"""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """provider 协议:给一串 messages(可选带 tools),拿回一次对话结果。"""

    def chat(
        self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatResult: ...


class MockProvider:
    """确定性离线 provider:零网络、零 key,输出由 messages 派生,可复现。

    回声 + 短指纹:回显最后一条 user 消息,并附一段由【整段对话】计算的稳定 hex 指纹——使不同
    对话(含不同 system / 历史)产出不同、但对同一对话恒定的文本;既便于断言,又不引入随机性。
    绝不伪造 tool_calls:离线默认不假装会 function-calling(真工具调用需接真实 provider)。
    """

    def __init__(self, *, prefix: str = "mock") -> None:
        self._prefix = prefix

    def chat(
        self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None
    ) -> ChatResult:
        # 指纹覆盖整段对话(role + content),\x00/\x01 作分隔杜绝拼接歧义;取前 12 位 hex。
        joined = "\x00".join(f"{m.role}\x01{m.content}" for m in messages)
        digest = hashlib.sha256(joined.encode()).hexdigest()[:12]
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        text = f"[{self._prefix}:{digest}] {last_user.strip()}"
        # 用量为确定性派生值(非真实计费),便于可观测链路联调。
        usage = {"input_tokens": len(joined), "output_tokens": len(text)}
        return ChatResult(text=text, usage=usage)
