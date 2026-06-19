"""seam.registry 合约:解析 + entry-point 发现 + 未知名报错 + 缺 extra 友好提示。"""

import pytest

from corespine.seam import registry as reg_mod
from corespine.seam.registry import Registry, lazy_extra_import


def test_resolves_builtin_case_and_whitespace_insensitive():
    r: Registry = Registry("toy")
    r.register("In-Process", lambda **kw: {"kind": "memory", **kw})
    # 大小写 / 首尾留白 / 连字符<->下划线 都应解析到同一实现。
    assert r.make("  in_process ")["kind"] == "memory"
    assert r.make("IN-PROCESS")["kind"] == "memory"
    # 关键字参数透传给工厂。
    assert r.make("in_process", tag="x")["tag"] == "x"


class _FakeEntryPoint:
    """模拟 importlib.metadata.EntryPoint:有 name,且 load() 返回工厂可调用对象。"""

    name = "plugin_impl"

    def load(self):
        return lambda **kw: {"kind": "plugin", **kw}


def test_discovers_entry_point(monkeypatch):
    r: Registry = Registry("toy")

    def fake_entry_points(*, group):
        return [_FakeEntryPoint()] if group == "corespine.toy" else []

    monkeypatch.setattr(reg_mod.metadata, "entry_points", fake_entry_points)

    # 内置里没有,回落到 entry-point 自动发现并构造。
    assert r.make("Plugin-Impl")["kind"] == "plugin"
    # names() 把内置 + 发现合并去重。
    assert "plugin_impl" in r.names()


def test_unknown_spec_lists_available_names():
    r: Registry = Registry("vector_store")
    r.register("memory", lambda **kw: object())
    r.register("sqlite_vec", lambda **kw: object())
    with pytest.raises(ValueError) as ei:
        r.make("nope")
    msg = str(ei.value)
    assert "nope" in msg
    assert "memory" in msg and "sqlite_vec" in msg
    # 报错带上 entry-point group,提示可装包扩展。
    assert "corespine.vector_store" in msg


def test_lazy_extra_import_friendly_message():
    with pytest.raises(ImportError) as ei:
        lazy_extra_import(
            "corespine_definitely_missing_dep_xyz", pkg="corespine", extra="toy"
        )
    assert "pip install corespine[toy]" in str(ei.value)


def test_lazy_extra_import_returns_module_when_present():
    # 标准库一定在:应原样返回该模块对象,不抛。
    mod = lazy_extra_import("json", pkg="corespine", extra="toy")
    assert mod.dumps({"a": 1}) == '{"a": 1}'
