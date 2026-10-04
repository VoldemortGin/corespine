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

import copy
import dataclasses
from collections.abc import Mapping, Sequence
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
        # 下游(ragspine / spineagent / spinestudio)常见的正文承载键名;并入后默认 sink 与
        # enforce_trace_privacy 一并拦下(ADR 0006)。
        "raw_text",
        "user_prompt",
        "contents",
        "question",
        "output",
        "messages",
    }
)

# 递归校验的防御上限(对抗输入 / 自引用结构):容器层数(含顶层)与访问节点总数。
# 超限按可疑载荷拒绝,绝不截断放行。
MAX_TRACE_DEPTH = 8
MAX_TRACE_NODES = 10_000

# 允许直接出现在 trace 里的标量叶子(bool 是 int 子类;StrEnum / IntEnum 成员按 str / int 放行)。
_SCALARS = (str, bytes, int, float, bool, type(None))


class TraceError(CorespineError):
    """trace 载荷违反隐私约定(携带受限正文 / 取值字段)时抛出。

    继承家族统一基类 CorespineError,带稳定可 grep 的 code,便于跨缝统一捕获 / 归一。
    """

    code = "trace.forbidden"


def _children(value: object, path: str, offending: list[str]) -> list[tuple[str, object]] | None:
    """展开一层容器:返回 (路径, 子值) 列表并收集禁词键;标量返回 None;其余对象 fail-closed。

    - Mapping:按键检查,路径 `x.key`;
    - dataclass 实例(不含类本身):按字段名当键检查;
    - NamedTuple(有 `_asdict` 的 tuple):先 `_asdict()`,按字段名当键检查;
    - list / tuple:逐元素,路径 `x[i]`;set / frozenset:逐元素,路径 `x{*}`(无序,无下标)。
    """
    if isinstance(value, _SCALARS):
        return None
    if isinstance(value, tuple) and hasattr(value, "_asdict"):
        value = value._asdict()
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        items: list[tuple[str, object]] = []
        for key, item in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if str(key).strip().lower() in FORBIDDEN_KEYS:
                offending.append(key_path)
            items.append((key_path, item))
        return items
    if isinstance(value, list | tuple):
        return [(f"{path}[{i}]", item) for i, item in enumerate(value)]
    if isinstance(value, set | frozenset):
        return [(f"{path}{{*}}", item) for item in value]
    raise TraceError(
        f"trace 载荷含无法检查的非标量值({path}:{type(value).__qualname__}):只允许 str / bytes / "
        "int / float / bool / None 及 Mapping / list / tuple / set / dataclass / NamedTuple "
        "容器,其余一律拒绝。"
    )


def enforce_trace_privacy(fields: Mapping[str, object]) -> None:
    """隐私门:载荷任意一层的键命中 FORBIDDEN_KEYS 即抛 TraceError(归一小写后精确匹配)。

    递归检查 Mapping 的键、dataclass / NamedTuple 的字段名,并逐元素深入 list / tuple / set;
    无法检查的非标量对象(含遍历时抛错的容器)fail-closed 拒绝;容器层数(含顶层)超过
    MAX_TRACE_DEPTH、或访问节点数超过 MAX_TRACE_NODES 按可疑载荷拒绝(自引用结构也由此挡下)。
    报错给出路径(如 `calls[0].prompt`)而【不】含取值。

    默认 sink 的 emit 先调用它;自定义 / OTel sink 只要先调用它,就经同一条隐私门——扇出而不绕过。
    与 ragspine `enforce_trace_privacy`(ADR 0028)语义兼容,下游可直接改用本函数。
    """
    offending: list[str] = []
    budget = [MAX_TRACE_NODES]
    # 显式栈,避免对抗输入打爆 Python 递归深度;(值, 路径, 层数)。
    stack: list[tuple[object, str, int]] = [(fields, "", 1)]
    while stack:
        value, path, depth = stack.pop()
        try:
            items = _children(value, path, offending)
        except TraceError:
            raise
        except Exception:  # noqa: BLE001 — 任何遍历异常都按无法检查 fail-closed
            raise TraceError(
                f"trace 载荷无法安全遍历({path or '<root>'}:{type(value).__qualname__}):按可疑载荷拒绝。"
            ) from None
        if items is None:
            continue
        if depth > MAX_TRACE_DEPTH:
            raise TraceError(
                f"trace 载荷嵌套超过 {MAX_TRACE_DEPTH} 层({path}):按可疑载荷拒绝,不截断放行。"
            )
        budget[0] -= len(items)
        if budget[0] < 0:
            raise TraceError(f"trace 载荷节点数超过 {MAX_TRACE_NODES}:按可疑载荷拒绝,不截断放行。")
        stack.extend((item, item_path, depth + 1) for item_path, item in items)
    if offending:
        raise TraceError(
            f"trace 载荷含受限字段 {sorted(offending)}:trace 只记 code / 计数 / 耗时,"
            "不得携带答案正文 / 字段取值 / chunk 正文。"
        )


class _ReadOnlyDict(dict[str, object]):
    """只读 dict:是 dict 的子类(json.dumps / == / isinstance 照常),但一切就地修改都抛 TypeError。

    让已记录 / 已扇出的 TraceEvent.fields 无法事后被写入禁用键(绕过 emit 时的隐私校验)。
    """

    def _deny(self, *args: object, **kwargs: object) -> None:
        raise TypeError("TraceEvent.fields 是只读的:trace 事件记录后不可修改")

    __setitem__ = __delitem__ = __ior__ = _deny  # type: ignore[assignment]
    update = pop = popitem = clear = setdefault = _deny  # type: ignore[assignment]

    def __reduce__(self) -> tuple[type[_ReadOnlyDict], tuple[dict[str, object]]]:
        return (_ReadOnlyDict, (dict(self),))


@dataclass(frozen=True)
class TraceEvent:
    """一条被记录的 trace:事件 code + 非敏感字段快照(只读)。

    构造时把 fields 拷贝成只读 dict(顶层不可就地修改;与调用方传入的 dict 脱钩)。
    """

    code: str
    fields: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _ReadOnlyDict(self.fields))


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

    隐私 by construction:emit 时递归扫字段键(含嵌套容器),命中 FORBIDDEN_KEYS 立即抛 TraceError、
    绝不记录;通过校验的事件以 TraceEvent 追加到内部列表。只记 code / 计数 / 耗时 / 标志。

    可挂 0..N 个 TraceExporter 做扇出:扇出严格发生在隐私校验【之后】——受限载荷在校验阶段
    就被挡下,一条都到不了 exporter。故导出面与本地记录面等宽,隐私不变量不因扇出而放松。
    """

    def __init__(self, *, exporters: Sequence[TraceExporter] | None = None) -> None:
        self._events: list[TraceEvent] = []
        self._exporters: list[TraceExporter] = list(exporters) if exporters else []
        self._exporter_failures = 0

    @property
    def exporter_failures(self) -> int:
        """exporter 抛错的累计次数(只是计数,不含异常内容 / 载荷)。"""
        return self._exporter_failures

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
        exporter 抛错只累计到 `exporter_failures`(不含内容),不影响其余 exporter 与调用方。
        """
        enforce_trace_privacy(fields)
        try:
            snapshot = copy.deepcopy(dict(fields))
        except Exception:  # noqa: BLE001 — 无法快照即 fail-closed,绝不放行可变共享状态
            raise TraceError("trace 载荷无法安全快照:按可疑载荷拒绝。") from None
        event = TraceEvent(code=code, fields=snapshot)
        self._events.append(event)
        for exporter in self._exporters:
            # 每个 exporter 拿独立的深拷贝:互不影响,也碰不到本地记录;单个 exporter 故障
            # 只计数,不中断其余 exporter,也不冒泡给业务调用方。
            try:
                exporter.export(TraceEvent(code=code, fields=copy.deepcopy(dict(snapshot))))
            except Exception:  # noqa: BLE001
                self._exporter_failures += 1
