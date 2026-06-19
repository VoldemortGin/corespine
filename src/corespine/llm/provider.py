"""LLM 缝:LLMProvider 协议 + 确定性离线 MockProvider。

domain-neutral——只约定最小公约数:给一段 prompt(可带 system),拿回一段文本。
【不】含任何 RAG / agent 特定的 tool-use 循环或意图解析(那些属各 app 自己的缝)。
核心 import 本模块零 SDK;真实 provider(Anthropic / OpenAI 等)由各 app 在自己的
缝里延迟 import 接入。

MockProvider 是离线确定性默认:同样的输入恒定产出同样的文本(纯函数,零网络、
零 key),让"装上即可端到端跑"成立,也让测试可复现。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Completion:
    """一次补全的结果:文本 + 可选 token 用量(provider 未回传则 None)。"""

    text: str
    usage: dict[str, int] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """provider 协议:给 system + prompt,拿回一次补全。"""

    def complete(self, prompt: str, *, system: str = "") -> Completion: ...


class MockProvider:
    """确定性离线 provider:零网络、零 key,输出由输入派生,可复现。

    回声 + 短指纹:把 prompt 规整后回显,并附一段由 (system, prompt) 计算的稳定 hex
    指纹——使不同输入产出不同、但【对同一输入恒定】的文本;既便于断言,又不引入随机性。
    """

    def __init__(self, *, prefix: str = "mock") -> None:
        self._prefix = prefix

    def complete(self, prompt: str, *, system: str = "") -> Completion:
        # \x00 作分隔符,杜绝 (system, prompt) 拼接歧义;取前 12 位 hex 作短指纹。
        digest = hashlib.sha256(f"{system}\x00{prompt}".encode()).hexdigest()[:12]
        text = f"[{self._prefix}:{digest}] {prompt.strip()}"
        # 用量为确定性派生值(非真实计费),便于可观测链路联调。
        usage = {"input_tokens": len(prompt), "output_tokens": len(text)}
        return Completion(text=text, usage=usage)
