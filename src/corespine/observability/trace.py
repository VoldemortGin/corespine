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

from collections.abc import Sequence
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


@runtime_checkable
class TraceExporter(Protocol):
    """trace 导出面的最小结构接口:接收一条【已通过隐私校验的】事件,扇出到外部。

    让 sink 能把 trace 扇出到进程外(OTel / Langfuse 形状的适配器等)。核心【只有这个
    Protocol + 进程内收集器默认】;真实导出后端(opentelemetry / langfuse SDK)由各 app
    在自己的适配器里经可选 extra 延迟 import,绝不进核心。

    隐私契约(不可放松):exporter 只会收到 TraceEvent,而 TraceEvent 是在 sink 的 emit
    里【先过 FORBIDDEN_KEYS 校验、再扇出】——受限正文在校验阶段就被 TraceError 挡下,
    根本到不了任何 exporter。故导出面天然只承载 code / 计数 / 耗时,与本地记录面等宽。
    """

    def export(self, event: TraceEvent) -> None: ...


class InProcessTraceExporter:
    """进程内默认 TraceExporter:把扇出到的事件收集进内存列表(离线 / 测试用)。

    是「核心只带进程内收集器默认」的兑现:真实 OTel / Langfuse 导出走各 app 的可选 extra,
    core 只给一个零依赖、可自检的收集器。它只可能收到已过隐私校验的事件(见 TraceExporter)。
    """

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> list[TraceEvent]:
        """已导出事件的只读视图(返回副本,外部改动不污染内部状态)。"""
        return list(self._events)

    def export(self, event: TraceEvent) -> None:
        self._events.append(event)


class InProcessPrivacyTraceSink:
    """进程内默认 TraceSink:把事件存进内存列表,且拒绝任何携带受限内容的载荷。

    隐私 by construction:emit 时先扫字段键,命中 FORBIDDEN_KEYS 立即抛 TraceError、
    绝不记录;通过校验的事件以 TraceEvent 追加到内部列表。只记 code / 计数 / 耗时 / 标志。

    可挂 0..N 个 TraceExporter 做扇出:扇出严格发生在隐私校验【之后】——受限载荷在校验阶段
    就被挡下,一条都到不了 exporter。故导出面与本地记录面等宽,隐私不变量不因扇出而放松。
    """

    def __init__(self, *, exporters: Sequence[TraceExporter] | None = None) -> None:
        self._events: list[TraceEvent] = []
        self._exporters: list[TraceExporter] = list(exporters) if exporters else []

    @property
    def events(self) -> list[TraceEvent]:
        """已记录事件的只读视图(返回副本,外部改动不污染内部状态)。"""
        return list(self._events)

    def codes(self) -> list[str]:
        """已记录事件的 code 序列(按记录顺序)。"""
        return [event.code for event in self._events]

    def add_exporter(self, exporter: TraceExporter) -> None:
        """追加一个导出面(扇出目标);后续 emit 的合法事件都会扇出到它。"""
        self._exporters.append(exporter)

    def emit(self, code: str, **fields: object) -> None:
        """记一条 trace;载荷含受限字段即抛 TraceError(不记录、不扇出)。

        校验通过后先本地记录,再扇出到每个 exporter——扇出在校验之后,受限正文到不了导出面。
        """
        offending = sorted(k for k in fields if k.strip().lower() in FORBIDDEN_KEYS)
        if offending:
            raise TraceError(
                f"trace 载荷含受限字段 {offending}:trace 只记 code / 计数 / 耗时,"
                "不得携带答案正文 / 字段取值 / chunk 正文。"
            )
        event = TraceEvent(code=code, fields=dict(fields))
        self._events.append(event)
        for exporter in self._exporters:
            exporter.export(event)
