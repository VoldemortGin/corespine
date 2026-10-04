"""递归隐私门 enforce_trace_privacy + 默认 sink 的嵌套防线 + 可复用的隐私 InvariantPack。"""

import dataclasses
from typing import NamedTuple

import pytest

from corespine.conformance.harness import ConformanceSuite
from corespine.conformance.packs import TRACE_PRIVACY_PACK
from corespine.observability.trace import (
    FORBIDDEN_KEYS,
    MAX_TRACE_DEPTH,
    MAX_TRACE_NODES,
    InProcessPrivacyTraceSink,
    InProcessTraceExporter,
    TraceError,
    enforce_trace_privacy,
)


@dataclasses.dataclass
class _Payload:
    prompt: str = "x"


class _Pair(NamedTuple):
    text: str
    n: int


@pytest.mark.parametrize(
    "key", ["text", "raw_text", "user_prompt", "contents", "question", "output", "messages"]
)
def test_new_and_old_keys_are_forbidden(key):
    assert key in FORBIDDEN_KEYS
    with pytest.raises(TraceError):
        enforce_trace_privacy({key: "x"})


@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"text": "正文"}},
        {"a": {"b": {"c": {"Answer": "x"}}}},
        {"calls": [{"ok": 1}, {"prompt": "x"}]},
        {"calls": ({"body": "x"},)},
        {"s": {frozenset({1}): 1, "k": [{"CONTENT": 1}]}},
        {"dc": _Payload()},
        {"nt": _Pair("x", 1)},
        {"set": {_Pair("x", 1)}},
    ],
)
def test_nested_forbidden_keys_rejected(payload):
    with pytest.raises(TraceError):
        enforce_trace_privacy(payload)


def test_error_message_has_path_but_no_value():
    with pytest.raises(TraceError) as ei:
        enforce_trace_privacy({"calls": [{"prompt": "SECRET-VALUE"}]})
    # 非禁用键名以 <key> 占位(防止数据键进日志),序列下标与被命中的禁用键名保留。
    assert "<key>[0].prompt" in str(ei.value)
    assert "SECRET-VALUE" not in str(ei.value)


def test_legit_events_pass():
    enforce_trace_privacy({"count": 3, "took_ms": 1.5, "hit": True, "none": None, "tag": "x"})
    enforce_trace_privacy({"stages": [1, 2, 3], "timings": {"a": 1, "b": 2}, "b": b"raw"})
    enforce_trace_privacy({})


def test_depth_limit():
    ok: dict = {}
    cur = ok
    for _ in range(MAX_TRACE_DEPTH - 1):  # 顶层 + 嵌套共 MAX_TRACE_DEPTH 层容器
        cur["n"] = {}
        cur = cur["n"]
    enforce_trace_privacy(ok)
    cur["n"] = {}
    with pytest.raises(TraceError):
        enforce_trace_privacy(ok)


def test_cycle_is_rejected_not_hung():
    a: list = []
    a.append(a)
    with pytest.raises(TraceError):
        enforce_trace_privacy({"a": a})


def test_node_limit():
    enforce_trace_privacy({"xs": list(range(MAX_TRACE_NODES - 10))})
    with pytest.raises(TraceError):
        enforce_trace_privacy({"xs": list(range(MAX_TRACE_NODES + 10))})


def test_uninspectable_object_fail_closed():
    class Opaque:
        pass

    with pytest.raises(TraceError):
        enforce_trace_privacy({"o": Opaque()})
    with pytest.raises(TraceError):
        enforce_trace_privacy({"o": lambda: 1})


def test_broken_container_fail_closed():
    class BadTuple(tuple):
        def _asdict(self):
            raise RuntimeError("boom")

    @dataclasses.dataclass
    class BadProp:
        n: int = 1

        def __getattribute__(self, name):
            if name == "n":
                raise RuntimeError("boom")
            return super().__getattribute__(name)

    with pytest.raises(TraceError):
        enforce_trace_privacy({"o": BadTuple((1,))})
    with pytest.raises(TraceError):
        enforce_trace_privacy({"o": BadProp()})


def test_sink_rejects_nested_and_does_not_record_or_fan_out():
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    with pytest.raises(TraceError):
        sink.emit("evt", meta={"text": "正文"})
    assert sink.events == []
    assert exporter.events == []


def test_sink_accepts_legit_nested_metadata():
    sink = InProcessPrivacyTraceSink()
    sink.emit("evt", timings={"a": 1}, stages=[1, 2])
    assert sink.events[0].fields == {"timings": {"a": 1}, "stages": [1, 2]}


def test_privacy_pack_on_default_sink():
    suite = ConformanceSuite({"in_process": InProcessPrivacyTraceSink}, TRACE_PRIVACY_PACK)
    assert suite.passed(), [r for r in suite.run() if not r.passed]


def test_privacy_pack_detects_leaky_sink():
    class Leaky:
        def emit(self, code: str, **fields: object) -> None:
            for k in fields:
                if k in FORBIDDEN_KEYS:
                    raise TraceError("x")  # 只查顶层

    suite = ConformanceSuite({"leaky": Leaky}, TRACE_PRIVACY_PACK)
    assert not suite.passed()


# --- 评审第二轮:合法标量放行 / 键匹配不被绕过 / 报错不带内容 / 映射 / 快路径 -------------------

import copy  # noqa: E402
import datetime  # noqa: E402
import decimal  # noqa: E402
import enum  # noqa: E402
import fractions  # noqa: E402
import pathlib  # noqa: E402
import types  # noqa: E402
import uuid  # noqa: E402
from collections import abc as cabc  # noqa: E402

from corespine.observability import trace as trace_mod  # noqa: E402


class _Color(enum.Enum):
    RED = "secret-red"


class _Mode(enum.StrEnum):
    A = "a"


class _MyStr(str):
    pass


class _MyInt(int):
    pass


_LEGIT_SCALARS = [
    None,
    True,
    1,
    1.5,
    1 + 2j,
    "s",
    b"raw",
    _Color.RED,
    _Mode.A,
    pathlib.Path("/a/b"),
    pathlib.PurePosixPath("a"),
    datetime.datetime(2020, 1, 1),
    datetime.date(2020, 1, 1),
    datetime.time(1, 2),
    datetime.timedelta(seconds=3),
    decimal.Decimal("1.5"),
    fractions.Fraction(1, 3),
    uuid.UUID(int=1),
    range(3),
    _MyStr("x"),
    _MyInt(3),
]


@pytest.mark.parametrize("value", _LEGIT_SCALARS, ids=lambda v: type(v).__name__)
def test_legit_scalar_leaves_pass_top_level_and_nested(value):
    enforce_trace_privacy({"v": value})
    enforce_trace_privacy({"m": {"v": value}, "xs": [value], "t": (value,)})
    sink = InProcessPrivacyTraceSink()
    sink.emit("evt", v=value, nested={"v": [value]})
    assert sink.events[0].fields["v"] == value


@pytest.mark.parametrize(
    "value",
    [object(), lambda: 1, bytearray(b"x"), (x for x in [1]), memoryview(b"x"), type, ...],
    ids=lambda v: type(v).__name__,
)
def test_non_whitelisted_objects_still_fail_closed(value):
    with pytest.raises(TraceError):
        enforce_trace_privacy({"v": value})


class _LyingStr(str):
    def __str__(self) -> str:
        return "ok"


@pytest.mark.parametrize(
    "key",
    [
        b"text",
        ("text",),
        1,
        None,
        _Color.RED,
        frozenset({"text"}),
    ],
    ids=repr,
)
def test_non_str_keys_fail_closed(key):
    with pytest.raises(TraceError):
        enforce_trace_privacy({"meta": {key: "x"}})


@pytest.mark.parametrize(
    "key",
    [
        "ｔｅｘｔ",  # 全角
        "te​xt",  # 零宽空格
        "te⁠xt",  # word joiner
        "﻿text",  # BOM / 零宽不换行
        "  TEXT\n",
        "TeXt",
        "Ｕｓｅｒ_Ｐｒｏｍｐｔ",
        "te\x00xt",
        _LyingStr("text"),  # __str__ 说谎,真实字符串值是 text
    ],
    ids=repr,
)
def test_key_matching_cannot_be_bypassed(key):
    with pytest.raises(TraceError):
        enforce_trace_privacy({"meta": {key: "x"}})
    with pytest.raises(TraceError):
        enforce_trace_privacy({key: "x"})


def test_innocent_keys_with_odd_chars_pass():
    enforce_trace_privacy({"计数": 1, "ｃｏｕｎｔ": 2, "context": {"texture": 1}})


def test_error_message_has_forbidden_name_and_shape_but_no_data_keys():
    payload = {"用户问题-SECRET-QUESTION": {"文档名-SECRET-DOC": [{"prompt": 1}, {"calls": 2}]}}
    with pytest.raises(TraceError) as ei:
        enforce_trace_privacy(payload)
    err = ei.value
    shown = [str(err), repr(err), *map(str, err.args), str(err.to_dict()), str(err.context)]
    for s in shown:
        assert "SECRET" not in s
        assert "用户问题" not in s
    assert "<key>.<key>[0].prompt" in str(err)


def test_error_message_for_forbidden_key_normalized_name_only():
    with pytest.raises(TraceError) as ei:
        enforce_trace_privacy({"a": {"ｔｅｘｔ​": 1}})
    assert "<key>.text" in str(ei.value)


def test_error_messages_for_other_rejections_do_not_leak_keys():
    class Opaque:
        pass

    for payload in (
        {"SECRET-KEY": Opaque()},
        {"SECRET-KEY": {b"SECRET-BYTES": 1}},
        {"SECRET-KEY": {("SECRET",): 1}},
        {"SECRET-KEY": types.MappingProxyType({"a": Opaque()})},
    ):
        with pytest.raises(TraceError) as ei:
            enforce_trace_privacy(payload)
        assert "SECRET" not in str(ei.value)
        assert "SECRET" not in str(ei.value.to_dict())


class _HiddenMapping(cabc.Mapping):
    def __init__(self) -> None:
        self.hidden = {"text": "SECRET"}

    def __getitem__(self, key):
        return self.hidden[key]

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def items(self):
        return []


def test_custom_mapping_fail_closed_and_never_fanned_out():
    with pytest.raises(TraceError):
        enforce_trace_privacy({"m": _HiddenMapping()})
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    with pytest.raises(TraceError):
        sink.emit("evt", m=_HiddenMapping())
    assert sink.events == [] and exporter.events == []


def test_mappingproxy_accepted_and_fanned_out_as_plain_dict():
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    sink.emit("evt", m=types.MappingProxyType({"a": 1}))
    assert type(sink.events[0].fields["m"]) is dict
    assert type(exporter.events[0].fields["m"]) is dict
    with pytest.raises(TraceError):
        enforce_trace_privacy({"m": types.MappingProxyType({"text": 1})})
    with pytest.raises(TraceError):
        enforce_trace_privacy({"m": types.MappingProxyType(_HiddenMapping())})


def test_dict_subclass_with_lying_items_is_read_by_real_contents():
    class Liar(dict):
        def items(self):
            return []

        def __iter__(self):
            return iter(())

    with pytest.raises(TraceError):
        enforce_trace_privacy({"m": Liar(text="SECRET")})
    sink = InProcessPrivacyTraceSink()
    sink.emit("evt", m=Liar(a=1))
    assert type(sink.events[0].fields["m"]) is dict
    assert sink.events[0].fields["m"] == {"a": 1}


def test_recorded_fields_reinit_is_denied():
    sink = InProcessPrivacyTraceSink()
    sink.emit("evt", count=1)
    fields = sink.events[0].fields
    with pytest.raises(TypeError):
        fields.__init__({"text": "x"})  # type: ignore[misc]
    assert fields == {"count": 1}


# --- 评审指出的三处变异缺口 -----------------------------------------------------------------


@dataclasses.dataclass
class _Legit:
    count: int = 1
    nested: dict | None = None


@dataclasses.dataclass
class _Outer:
    inner: _Legit


def test_legit_dataclass_passes_and_is_fanned_out_as_independent_copy():
    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    original = _Legit(count=2, nested={"a": [1]})
    sink.emit("evt", d=original, outer=_Outer(_Legit()))
    got = exporter.events[0].fields["d"]
    assert got == original and got is not original
    got.nested["a"].append(2)  # type: ignore[index]
    assert original.nested == {"a": [1]}
    assert sink.events[0].fields["d"].nested == {"a": [1]}  # type: ignore[attr-defined]


def test_dataclass_field_names_are_key_checked_at_any_depth():
    @dataclasses.dataclass
    class Bad:
        answer: str = "x"

    for payload in ({"d": Bad()}, {"o": [{"deep": (Bad(),)}]}):
        with pytest.raises(TraceError):
            enforce_trace_privacy(payload)
    with pytest.raises(TraceError):
        enforce_trace_privacy({"d": _Outer(inner=_Legit(nested={"text": 1}))})


def test_snapshot_failure_fail_closed_nothing_recorded_or_fanned_out():
    @dataclasses.dataclass
    class NoCopy:
        n: int = 1

        def __deepcopy__(self, memo):
            raise RuntimeError("cannot copy")

    exporter = InProcessTraceExporter()
    sink = InProcessPrivacyTraceSink(exporters=[exporter])
    with pytest.raises(TraceError):
        sink.emit("evt", d=NoCopy())
    assert sink.events == [] and exporter.events == []


def test_node_limit_exact_boundary():
    # 顶层 1 个键 + 列表 MAX-1 个元素 = 恰好 MAX_TRACE_NODES 个节点:放行;多 1 个:拒绝。
    enforce_trace_privacy({"xs": list(range(MAX_TRACE_NODES - 1))})
    with pytest.raises(TraceError):
        enforce_trace_privacy({"xs": list(range(MAX_TRACE_NODES))})


# --- 快路径:全标量扁平事件不递归、不深拷贝 -------------------------------------------------


def test_flat_event_takes_fast_path_without_deepcopy(monkeypatch):
    calls: list[object] = []
    real = copy.deepcopy

    def counting(x, memo=None, _nil=[]):  # noqa: B006
        calls.append(x)
        return real(x, memo)

    monkeypatch.setattr(trace_mod.copy, "deepcopy", counting)
    exporters = [InProcessTraceExporter() for _ in range(3)]
    sink = InProcessPrivacyTraceSink(exporters=exporters)
    sink.emit(
        "evt", count=1, took_ms=2.5, hit=True, tag="x", mode=_Mode.A, at=datetime.date(2020, 1, 1)
    )
    assert calls == []
    assert all(len(e.events) == 1 for e in exporters)
    # 独立:一个 exporter 拿到的事件与本地记录不是同一对象。
    assert exporters[0].events[0] is not sink.events[0]
    assert exporters[0].events[0].fields == sink.events[0].fields

    sink.emit("evt2", nested={"a": [1]})
    assert calls  # 含容器才走深拷贝
