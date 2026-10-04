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
import datetime
import decimal
import enum
import fractions
import functools
import types
import unicodedata
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePath
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

# 允许直接出现在 trace 里的【不可变标量叶子】白名单(子类按其基类处理,如 StrEnum / IntEnum /
# str 子类)。放行标量不等于放行正文:隐私门按【键名】把关,值的内容本来就不检查。
# 白名单之外的不可检查对象(自定义类实例、生成器、bytearray、memoryview ...)仍 fail-closed。
_LEAF_TYPES = (
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
    enum.Enum,
    PurePath,
    datetime.date,  # datetime.datetime 是其子类
    datetime.time,
    datetime.timedelta,
    decimal.Decimal,
    fractions.Fraction,
    uuid.UUID,
    range,
)


class TraceError(CorespineError):
    """trace 载荷违反隐私约定(携带受限正文 / 取值字段)时抛出。

    继承家族统一基类 CorespineError,带稳定可 grep 的 code,便于跨缝统一捕获 / 归一。
    消息只含被命中的禁用键名(来自固定集合)与路径的形状(见 enforce_trace_privacy),
    绝不含用户数据键名 / 取值;异常的属性与 to_dict() 同样不含它们。
    """

    code = "trace.forbidden"


@functools.lru_cache(maxsize=4096)
def _normalize_key(key: str) -> str:
    """键归一:NFKC(全角 -> 半角)、去 Unicode 格式 / 控制字符(Cf / Cc,含零宽字符)、
    去首尾空白、casefold。仅用于与 FORBIDDEN_KEYS 比对。"""
    folded = unicodedata.normalize("NFKC", key)
    kept = "".join(c for c in folded if unicodedata.category(c) not in ("Cf", "Cc"))
    return kept.strip().casefold()


class _Scan:
    """一次隐私扫描的共享状态。"""

    __slots__ = ("budget", "flat", "offending")

    def __init__(self) -> None:
        self.offending: list[str] = []
        self.budget = MAX_TRACE_NODES
        self.flat = True  # 除顶层外没遇到任何容器 -> 可走快路径


def _reject_uninspectable(path: str, value: object) -> TraceError:
    return TraceError(
        f"trace 载荷含无法检查的非标量值({path or '<root>'}:{type(value).__qualname__}):只允许"
        "不可变标量(None / bool / int / float / complex / str / bytes / Enum / Path / "
        "datetime 家族 / Decimal / Fraction / UUID / range)及 dict / MappingProxyType / list / "
        "tuple / set / dataclass / NamedTuple 容器,其余一律拒绝。"
    )


def _copy_value(value: object, path: str, depth: int, st: _Scan) -> object:
    """叶子原样返回(不可变);容器递归校验并返回【独立的干净拷贝】。"""
    if isinstance(value, _LEAF_TYPES):
        return value
    st.flat = False
    return _guarded_expand(value, path, depth, st)


def _guarded_expand(value: object, path: str, depth: int, st: _Scan) -> object:
    failed = False
    try:
        return _expand(value, path, depth, st)
    except TraceError:
        raise
    except Exception:  # noqa: BLE001 — 任何遍历异常都按无法检查 fail-closed
        failed = True
    # 在 except 块外抛:异常上下文里不留下原始异常(它可能带数据)。
    assert failed
    raise TraceError(
        f"trace 载荷无法安全遍历({path or '<root>'}:{type(value).__qualname__}):按可疑载荷拒绝。"
    )


def _expand(value: object, path: str, depth: int, st: _Scan) -> object:
    """展开一层容器,校验键并返回拷贝。

    - dict(含子类,按其【真实内容】读取,不信任被覆写的 items / __iter__)与 MappingProxyType:
      按键检查,拷贝为普通 dict;其它自定义 Mapping 一律 fail-closed(其内容可被覆写欺骗);
    - dataclass 实例(不含类本身):按字段名当键检查;NamedTuple:按字段名当键检查;
    - list / tuple:逐元素,路径 `x[i]`;set / frozenset:逐元素,路径 `x{*}`(无序,无下标)。
    键必须是 str(子类取其真实字符串值,不信任 __str__),其它类型的键 fail-closed。
    """
    pairs: list[tuple[object, object]] | None = None
    seq: list[object] | None = None
    if isinstance(value, dict):
        pairs = list(dict.items(value))
    elif isinstance(value, types.MappingProxyType):
        inner = value.copy()
        if not isinstance(inner, dict):
            raise _reject_uninspectable(path, value)
        pairs = list(dict.items(inner))
    elif isinstance(value, tuple) and hasattr(value, "_asdict"):
        as_dict = value._asdict()
        if not isinstance(as_dict, dict):
            raise _reject_uninspectable(path, value)
        pairs = list(dict.items(as_dict))
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        pairs = [(f.name, getattr(value, f.name)) for f in dataclasses.fields(value)]
    elif isinstance(value, list):
        seq = list(list.__iter__(value))
    elif isinstance(value, tuple):
        seq = list(tuple.__iter__(value))
    elif isinstance(value, set):
        seq = list(set.__iter__(value))
    elif isinstance(value, frozenset):
        seq = list(frozenset.__iter__(value))
    else:
        raise _reject_uninspectable(path, value)

    if depth > MAX_TRACE_DEPTH:
        raise TraceError(
            f"trace 载荷嵌套超过 {MAX_TRACE_DEPTH} 层({path}):按可疑载荷拒绝,不截断放行。"
        )
    st.budget -= len(pairs) if pairs is not None else len(seq or ())
    if st.budget < 0:
        raise TraceError(f"trace 载荷节点数超过 {MAX_TRACE_NODES}:按可疑载荷拒绝,不截断放行。")

    if seq is not None:
        if isinstance(value, list | tuple):
            copied = [
                _copy_value(item, f"{path}[{i}]", depth + 1, st) for i, item in enumerate(seq)
            ]
            return copied if isinstance(value, list) else tuple(copied)
        set_path = f"{path}{{*}}"
        members = [_copy_value(item, set_path, depth + 1, st) for item in seq]
        return frozenset(members) if isinstance(value, frozenset) else set(members)

    assert pairs is not None
    result: dict[str, object] = {}
    for key, item in pairs:
        if not isinstance(key, str):
            raise TraceError(
                f"trace 载荷含非 str 键({path or '<root>'}:{type(key).__qualname__}):按可疑载荷拒绝。"
            )
        real = key if type(key) is str else str.__str__(key)
        norm = _normalize_key(real)
        if norm in FORBIDDEN_KEYS:
            seg = norm  # 来自固定集合,可安全展示
            st.offending.append(f"{path}.{seg}" if path else seg)
        else:
            seg = "<key>"
        if isinstance(item, _LEAF_TYPES):
            result[real] = item
        else:
            st.flat = False
            result[real] = _guarded_expand(item, f"{path}.{seg}" if path else seg, depth + 1, st)

    if isinstance(value, tuple):  # NamedTuple
        return value._make(result.values())  # type: ignore[attr-defined]
    if dataclasses.is_dataclass(value):
        clone = copy.deepcopy(value)
        for name, item in result.items():
            object.__setattr__(clone, name, item)
        return clone
    return result


def _sanitize(fields: Mapping[str, object]) -> tuple[dict[str, object], bool]:
    """隐私校验 + 取独立拷贝(单次物化,校验的就是将被扇出的);返回 (拷贝, 是否全标量扁平)。"""
    st = _Scan()
    snapshot = _guarded_expand(fields, "", 1, st)
    if st.offending:
        raise TraceError(
            f"trace 载荷含受限字段 {sorted(st.offending)}:trace 只记 code / 计数 / 耗时,"
            "不得携带答案正文 / 字段取值 / chunk 正文。"
        )
    assert isinstance(snapshot, dict)
    return snapshot, st.flat


def enforce_trace_privacy(fields: Mapping[str, object]) -> None:
    """隐私门:载荷任意一层的键命中 FORBIDDEN_KEYS 即抛 TraceError。

    键匹配:键必须是 `str`(子类取其真实字符串值,不信任 `__str__`),其它类型的键 fail-closed;
    比对前做 NFKC 归一(全角 -> 半角)、去 Unicode 格式 / 控制字符(Cf / Cc,含零宽字符)、
    去首尾空白、casefold。

    递归检查 dict(含子类)/ `MappingProxyType` 的键、dataclass / NamedTuple 的字段名,并逐元素
    深入 list / tuple / set。**放行**的值:不可变标量叶子——None / bool / int / float / complex /
    str / bytes / Enum 成员 / Path 与 PurePath / datetime 家族(datetime / date / time /
    timedelta)/ Decimal / Fraction / UUID / range(子类按基类);值的内容不检查,隐私门只按键名把关。
    **fail-closed**:白名单外的对象(自定义类实例、生成器、bytearray ...)、自定义 Mapping(其
    `items()` 可被覆写欺骗)、非 str 键、遍历时抛错的容器;容器层数(含顶层)超过
    MAX_TRACE_DEPTH、或访问节点数超过 MAX_TRACE_NODES 也按可疑载荷拒绝(自引用结构由此挡下)。

    报错只含【被命中的禁用键名】(来自固定集合)与路径的形状:其余键名用 `<key>` 占位、序列
    下标保留数字(如 `<key>[0].prompt`),所以以用户数据(问题、文档名)作 dict 键也不会进日志;
    绝不含取值。异常的 args / context / to_dict() 同样如此。

    默认 sink 的 emit 先调用它(同一次扫描产出独立拷贝再扇出);自定义 / OTel sink 只要先调用它,
    就经同一条隐私门。与 ragspine `enforce_trace_privacy`(ADR 0028)语义兼容,下游可直接改用本函数
    (路径里非禁用键名变为 `<key>` 占位)。
    """
    _sanitize(fields)


class _ReadOnlyDict(dict[str, object]):
    """只读 dict:是 dict 的子类(json.dumps / == / isinstance 照常),但一切就地修改都抛 TypeError。

    让已记录 / 已扇出的 TraceEvent.fields 无法事后被写入禁用键(绕过 emit 时的隐私校验)。
    覆写了 `__init__` 的重复调用;**防误用,不防恶意**:`dict.__setitem__(f, ...)` 这类直接调用
    基类方法的绕过在 Python 里挡不住。exporter 拿到的始终是独立拷贝,改它不影响本地记录。
    """

    __slots__ = ("_sealed",)

    def __init__(self, *args: object, **kwargs: object) -> None:
        if getattr(self, "_sealed", False):
            self._deny()
        super().__init__(*args, **kwargs)
        self._sealed = True

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


def _check_max_events(max_events: int | None) -> int | None:
    if max_events is not None and max_events < 1:
        raise ValueError(f"max_events 必须 >= 1(或 None 表示不限):{max_events!r}")
    return max_events


class InProcessTraceExporter:
    """进程内默认 TraceExporter:把扇出到的事件收集进内存列表(离线 / 测试用)。

    是「核心只带进程内收集器默认」的兑现:真实 OTel / Langfuse 导出走各 app 的可选 extra,
    core 只给一个零依赖、可自检的收集器。它只可能收到已过隐私校验的事件(见 TraceExporter)。

    缺省【无界】累积(与此前一致);**长驻服务应设置 `max_events`**:超出后丢弃最旧的事件
    (保留最新的 max_events 条,丢弃数见 `dropped_events`),否则内存随事件数无限增长。
    """

    def __init__(self, *, max_events: int | None = None) -> None:
        self._events: deque[TraceEvent] = deque(maxlen=_check_max_events(max_events))
        self._dropped = 0

    @property
    def events(self) -> list[TraceEvent]:
        """已导出事件的只读视图(返回副本,外部改动不污染内部状态)。"""
        return list(self._events)

    @property
    def dropped_events(self) -> int:
        """因 max_events 上限被丢弃的最旧事件累计数(缺省不限则恒为 0)。"""
        return self._dropped

    def export(self, event: TraceEvent) -> None:
        if self._events.maxlen is not None and len(self._events) == self._events.maxlen:
            self._dropped += 1
        self._events.append(event)


class InProcessPrivacyTraceSink:
    """进程内默认 TraceSink:把事件存进内存列表,且拒绝任何携带受限内容的载荷。

    隐私 by construction:emit 时递归扫字段键(含嵌套容器),命中 FORBIDDEN_KEYS 立即抛 TraceError、
    绝不记录;通过校验的事件以 TraceEvent 追加到内部列表。只记 code / 计数 / 耗时 / 标志。

    可挂 0..N 个 TraceExporter 做扇出:扇出严格发生在隐私校验【之后】——受限载荷在校验阶段
    就被挡下,一条都到不了 exporter。故导出面与本地记录面等宽,隐私不变量不因扇出而放松。

    缺省本地记录【无界】累积(与此前一致);**长驻服务应设置 `max_events`**:超出后丢弃最旧的
    事件(保留最新 max_events 条,丢弃数见 `dropped_events`),否则内存随事件数无限增长。
    """

    def __init__(
        self, *, exporters: Sequence[TraceExporter] | None = None, max_events: int | None = None
    ) -> None:
        self._events: deque[TraceEvent] = deque(maxlen=_check_max_events(max_events))
        self._dropped = 0
        self._exporters: list[TraceExporter] = list(exporters) if exporters else []
        self._exporter_failures = 0

    @property
    def dropped_events(self) -> int:
        """因 max_events 上限被丢弃的最旧事件累计数(缺省不限则恒为 0)。"""
        return self._dropped

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
        # 单次扫描同时完成校验与取独立拷贝(含容器时才递归 / 深拷贝;全标量的扁平事件走快路径)。
        snapshot, flat = _sanitize(fields)
        event = TraceEvent(code=code, fields=snapshot)  # TraceEvent 构造时再浅拷贝一层
        if self._events.maxlen is not None and len(self._events) == self._events.maxlen:
            self._dropped += 1
        self._events.append(event)
        for exporter in self._exporters:
            # 每个 exporter 拿独立拷贝:互不影响,也碰不到本地记录;单个 exporter 故障
            # 只计数,不中断其余 exporter,也不冒泡给业务调用方。扁平事件的值全是不可变标量,
            # TraceEvent 的浅拷贝即独立;含容器才深拷贝。
            try:
                payload = snapshot if flat else copy.deepcopy(snapshot)
                exporter.export(TraceEvent(code=code, fields=payload))
            except Exception:  # noqa: BLE001
                self._exporter_failures += 1
