"""隐私安全的可观测性原语:TraceSink 协议 + 默认的进程内实现。

约定("trace 按受限数据对待"):trace 只允许记【非敏感元数据】——事件 code、计数、
耗时、布尔标志这类。绝不记原始答案正文 / 字段取值 / chunk 正文——它们一旦进 trace
就成了受限数据的泄露面。

默认实现 InProcessPrivacyTraceSink 把这条约定做成"构造即保证":任何带禁词键
(answer / value / text / content / ...)的载荷会被【直接拒绝】(抛 TraceError),
而不是悄悄记下去。隐私 by construction,而非靠 reviewer 自觉。

domain-neutral:不预设任何具体事件 code;每个 app 用自己的 code 词表,harness 只管
"只记元数据、拒绝正文"这条机制。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from corespine.errors import CorespineError

# 禁止出现在 trace 载荷里的键(承载受限正文 / 取值的字段名,归一为小写后比对)。
# 命中任一即拒绝整条 trace——宁可报错,也不让正文流进可观测链路。
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "value",
        "text",
        "content",
        "prompt",
        "completion",
        "chunk",
        "chunk_text",
        "body",
    }
)


class TraceError(CorespineError):
    """trace 载荷违反隐私约定(携带受限正文 / 取值字段)时抛出。

    继承家族统一基类 CorespineError,带稳定可 grep 的 code,便于跨缝统一捕获 / 归一。
    """

    code = "trace.forbidden"


@dataclass(frozen=True)
class TraceEvent:
    """一条被记录的 trace:事件 code + 非敏感字段快照(只读)。"""

    code: str
    fields: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class TraceSink(Protocol):
    """trace 出口的最小结构接口:发射一条事件(code + 非敏感字段)。"""

    def emit(self, code: str, **fields: object) -> None: ...


class InProcessPrivacyTraceSink:
    """进程内默认 TraceSink:把事件存进内存列表,且拒绝任何携带受限内容的载荷。

    隐私 by construction:emit 时先扫字段键,命中 FORBIDDEN_KEYS 立即抛 TraceError、
    绝不记录;通过校验的事件以 TraceEvent 追加到内部列表。只记 code / 计数 / 耗时 / 标志。
    """

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> list[TraceEvent]:
        """已记录事件的只读视图(返回副本,外部改动不污染内部状态)。"""
        return list(self._events)

    def codes(self) -> list[str]:
        """已记录事件的 code 序列(按记录顺序)。"""
        return [event.code for event in self._events]

    def emit(self, code: str, **fields: object) -> None:
        """记一条 trace;载荷含受限字段即抛 TraceError(不记录)。"""
        offending = sorted(k for k in fields if k.strip().lower() in FORBIDDEN_KEYS)
        if offending:
            raise TraceError(
                f"trace 载荷含受限字段 {offending}:trace 只记 code / 计数 / 耗时,"
                "不得携带答案正文 / 字段取值 / chunk 正文。"
            )
        self._events.append(TraceEvent(code=code, fields=dict(fields)))
