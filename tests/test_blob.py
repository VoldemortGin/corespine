"""blob 缝合约:BlobStore Protocol 结构匹配 + 参数化 conformance(round-trip / 删后不存在 / key 隔离)。

conformance 用 ConformanceSuite × InvariantPack 把「两个离线默认实现 × 三条契约不变量」绑成
笛卡尔积——这正是家族「机制非保证」:不变量在测试侧绑,core 只出 Protocol + 默认 + 工厂。
"""

import pytest

from corespine.blob.store import (
    BLOB_REGISTRY,
    BlobNotFound,
    BlobStore,
    FileSystemBlobStore,
    MemoryBlobStore,
    make_blob_store,
)
from corespine.conformance.harness import ConformanceSuite, InvariantPack

# --- conformance 不变量(domain-neutral 字节存储契约)--------------------------


def _round_trip(store: BlobStore) -> None:
    store.put("k/1", b"hello bytes")
    assert store.exists("k/1")
    assert store.get("k/1") == b"hello bytes"


def _overwrite_replaces(store: BlobStore) -> None:
    store.put("k", b"v1")
    store.put("k", b"v2")
    assert store.get("k") == b"v2"


def _delete_then_absent(store: BlobStore) -> None:
    store.put("gone", b"x")
    store.delete("gone")
    assert not store.exists("gone")
    with pytest.raises(BlobNotFound):
        store.get("gone")


def _delete_missing_is_idempotent(store: BlobStore) -> None:
    store.delete("never-existed")  # 幂等:删不存在的 key 不报错


def _key_isolation(store: BlobStore) -> None:
    store.put("a", b"AAA")
    store.put("b", b"BBB")
    assert store.get("a") == b"AAA"
    assert store.get("b") == b"BBB"
    store.delete("a")
    assert not store.exists("a")
    assert store.exists("b")  # 删 a 不影响 b


def _get_missing_raises(store: BlobStore) -> None:
    with pytest.raises(BlobNotFound):
        store.get("absent")


def _empty_bytes_round_trip(store: BlobStore) -> None:
    store.put("empty", b"")
    assert store.exists("empty")  # 空字节也是存在,不是缺失
    assert store.get("empty") == b""


PACK = (
    InvariantPack[BlobStore]("blob")
    .add("round_trip", _round_trip)
    .add("overwrite_replaces", _overwrite_replaces)
    .add("delete_then_absent", _delete_then_absent)
    .add("delete_missing_is_idempotent", _delete_missing_is_idempotent)
    .add("key_isolation", _key_isolation)
    .add("get_missing_raises", _get_missing_raises)
    .add("empty_bytes_round_trip", _empty_bytes_round_trip)
)


def _make_impls(tmp_path):
    """两个离线默认实现的工厂表(filesystem 各拿独立临时根)。"""
    counter = {"n": 0}

    def _fs() -> FileSystemBlobStore:
        counter["n"] += 1
        return FileSystemBlobStore(tmp_path / f"fs{counter['n']}")

    return {"memory": MemoryBlobStore, "filesystem": _fs}


def test_conformance_all_defaults(tmp_path):
    """2 实现 × 7 不变量 全绿(离线自检:run() 不抛,逐格通过)。"""
    suite = ConformanceSuite(_make_impls(tmp_path), PACK)
    results = suite.run()
    failed = [f"{r.impl}/{r.invariant}: {r.error}" for r in results if not r.passed]
    assert not failed, failed
    assert len(results) == 2 * 7


# 端到端:消费者用法只两行——参数化跑满全格。
def test_conformance_parametrized(tmp_path):
    suite = ConformanceSuite(_make_impls(tmp_path), PACK)
    kwargs = suite.parametrize_kwargs()
    for thunk in kwargs["argvalues"]:
        thunk()  # 每格满足则静默返回


# --- Protocol 结构匹配 --------------------------------------------------------


def test_defaults_satisfy_protocol(tmp_path):
    assert isinstance(MemoryBlobStore(), BlobStore)
    assert isinstance(FileSystemBlobStore(tmp_path / "fs"), BlobStore)


# --- Registry 工厂 ------------------------------------------------------------


def test_registry_make_memory():
    store = make_blob_store("memory")
    assert isinstance(store, MemoryBlobStore)
    store.put("k", b"v")
    assert store.get("k") == b"v"


def test_registry_make_filesystem(tmp_path):
    store = make_blob_store("filesystem", root=tmp_path / "root")
    assert isinstance(store, FileSystemBlobStore)
    store.put("k", b"v")
    assert store.get("k") == b"v"


def test_registry_spec_is_normalized():
    # 大小写 / 连字符 / 留白不敏感(复用 Registry 归一)。
    assert isinstance(make_blob_store("  MEMORY "), MemoryBlobStore)


def test_registry_unknown_spec_lists_available():
    with pytest.raises(ValueError) as ei:
        make_blob_store("s3-not-installed")
    msg = str(ei.value)
    assert "memory" in msg and "filesystem" in msg  # 报错列清可用名


def test_registry_names_include_builtins():
    assert "memory" in BLOB_REGISTRY.names()
    assert "filesystem" in BLOB_REGISTRY.names()


# --- FileSystem 具体行为:确定性映射 + 跨实例持久 + 路径穿越无害化 ----------------


def test_filesystem_persists_across_instances(tmp_path):
    root = tmp_path / "shared"
    FileSystemBlobStore(root).put("doc", b"payload")
    # 同 root 新实例(模拟跨进程):同 key 读回同字节。
    assert FileSystemBlobStore(root).get("doc") == b"payload"


def test_filesystem_key_with_slashes_is_flat_and_safe(tmp_path):
    root = tmp_path / "safe"
    store = FileSystemBlobStore(root)
    # 含斜杠 / 相对穿越的 key 不得逃出 root——扁平化到 root 下单层文件。
    store.put("../../etc/passwd", b"nope")
    store.put("a/b/c", b"nested-key")
    assert store.get("../../etc/passwd") == b"nope"
    assert store.get("a/b/c") == b"nested-key"
    # root 下只应有扁平文件,绝无逃逸出去的目录树。
    children = list(root.rglob("*"))
    assert all(p.is_file() for p in children)
