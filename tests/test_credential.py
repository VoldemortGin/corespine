"""credential 缝合约:CredentialStore Protocol 结构匹配 + 参数化 conformance。

conformance 用 ConformanceSuite × InvariantPack 把「离线默认实现 × 契约不变量」绑成笛卡尔积
(家族「机制非保证」:不变量在测试侧绑,core 只出 Protocol + 默认 + 工厂)。核心隐私不变量
——秘密值永不出现在 repr / str / 异常消息里——由负向不变量钉死;跨缝的 trace 不泄露另有专测。

EncryptedFileCredentialStore 需要 `corespine[crypto]`(cryptography);dev extra 已含它,
故 CI 下三实现全跑。未装 crypto 时仅跳过加密实现,import-clean 与其余门不受影响。
"""

import importlib.util
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from corespine.conformance.harness import ConformanceSuite, InvariantPack
from corespine.credential.store import (
    CREDENTIAL_REGISTRY,
    CredentialError,
    CredentialNotFound,
    CredentialStore,
    EncryptedFileCredentialStore,
    InsecureLocalCredentialStore,
    MemoryCredentialStore,
    make_credential_store,
)
from corespine.observability.trace import FORBIDDEN_KEYS, InProcessPrivacyTraceSink, TraceError

_CRYPTO = importlib.util.find_spec("cryptography") is not None

# 负向测试用的哨兵秘密:任何实现都不得让它出现在 repr / str / 异常里。
_SECRET = "sk-super-secret-TOKEN-do-not-leak-42"

# --- conformance 不变量(domain-neutral 命名凭据契约)-------------------------


def _set_get_round_trip(store: CredentialStore) -> None:
    store.set("tenant-a", "openai_api_key", "sk-1")
    assert store.get("tenant-a", "openai_api_key") == "sk-1"


def _overwrite_replaces(store: CredentialStore) -> None:
    store.set("ns", "k", "v1")
    store.set("ns", "k", "v2")
    assert store.get("ns", "k") == "v2"


def _delete_then_absent(store: CredentialStore) -> None:
    store.set("ns", "gone", "x")
    store.delete("ns", "gone")
    with pytest.raises(CredentialNotFound):
        store.get("ns", "gone")


def _delete_missing_is_idempotent(store: CredentialStore) -> None:
    store.delete("ns", "never-existed")  # 幂等:删不存在的不报错
    store.delete("no-such-ns", "x")


def _get_missing_raises(store: CredentialStore) -> None:
    with pytest.raises(CredentialNotFound):
        store.get("ns", "absent")


def _namespace_isolation(store: CredentialStore) -> None:
    # 不同 namespace 下的同名凭据互不可见、互不影响。
    store.set("tenant-a", "key", "AAA")
    store.set("tenant-b", "key", "BBB")
    assert store.get("tenant-a", "key") == "AAA"
    assert store.get("tenant-b", "key") == "BBB"
    store.delete("tenant-a", "key")
    with pytest.raises(CredentialNotFound):
        store.get("tenant-a", "key")
    assert store.get("tenant-b", "key") == "BBB"  # 删 a 不影响 b


def _list_names_scoped(store: CredentialStore) -> None:
    assert store.list("empty-ns") == []  # 未知 namespace 返回空
    store.set("ns", "b_key", "1")
    store.set("ns", "a_key", "2")
    store.set("other", "z_key", "3")
    assert store.list("ns") == ["a_key", "b_key"]  # 字典序,仅本 namespace
    assert store.list("other") == ["z_key"]


def _secret_never_leaks(store: CredentialStore) -> None:
    # 隐私不变量:秘密值绝不出现在 repr / str / 异常消息里。
    store.set("ns", "cred", _SECRET)
    assert _SECRET not in repr(store)
    assert _SECRET not in str(store)
    with pytest.raises(CredentialNotFound) as ei:
        store.get("ns", "absent")
    assert _SECRET not in str(ei.value)
    assert _SECRET not in repr(ei.value)
    # 异常虽携带定位符,但只带 namespace / name,绝无 value。
    assert ei.value.context == {"namespace": "ns", "name": "absent"}


PACK = (
    InvariantPack[CredentialStore]("credential")
    .add("set_get_round_trip", _set_get_round_trip)
    .add("overwrite_replaces", _overwrite_replaces)
    .add("delete_then_absent", _delete_then_absent)
    .add("delete_missing_is_idempotent", _delete_missing_is_idempotent)
    .add("get_missing_raises", _get_missing_raises)
    .add("namespace_isolation", _namespace_isolation)
    .add("list_names_scoped", _list_names_scoped)
    .add("secret_never_leaks", _secret_never_leaks)
)


def _make_impls(tmp_path):
    """离线默认实现的工厂表(文件实现各拿独立临时路径;加密实现按 crypto 可用性纳入)。"""
    counter = {"n": 0}

    def _next(prefix: str):
        counter["n"] += 1
        return tmp_path / f"{prefix}{counter['n']}.json"

    def _insecure() -> InsecureLocalCredentialStore:
        return InsecureLocalCredentialStore(_next("insecure"))

    impls = {"memory": MemoryCredentialStore, "insecure_local": _insecure}

    if _CRYPTO:

        def _encrypted() -> EncryptedFileCredentialStore:
            return EncryptedFileCredentialStore(
                _next("encrypted"), key=EncryptedFileCredentialStore.generate_key()
            )

        impls["encrypted_file"] = _encrypted
    return impls


def test_conformance_all_defaults(tmp_path):
    """N 实现 × 8 不变量 全绿(离线自检:run() 不抛,逐格通过)。"""
    impls = _make_impls(tmp_path)
    suite = ConformanceSuite(impls, PACK)
    results = suite.run()
    failed = [f"{r.impl}/{r.invariant}: {r.error}" for r in results if not r.passed]
    assert not failed, failed
    assert len(results) == len(impls) * 8


def test_conformance_parametrized(tmp_path):
    # 端到端:消费者用法只两行——参数化跑满全格。
    suite = ConformanceSuite(_make_impls(tmp_path), PACK)
    kwargs = suite.parametrize_kwargs()
    for thunk in kwargs["argvalues"]:
        thunk()  # 每格满足则静默返回


# --- Protocol 结构匹配 --------------------------------------------------------


def test_defaults_satisfy_protocol(tmp_path):
    assert isinstance(MemoryCredentialStore(), CredentialStore)
    assert isinstance(InsecureLocalCredentialStore(tmp_path / "c.json"), CredentialStore)


# --- Registry 工厂 ------------------------------------------------------------


def test_registry_make_memory():
    store = make_credential_store("memory")
    assert isinstance(store, MemoryCredentialStore)
    store.set("ns", "k", "v")
    assert store.get("ns", "k") == "v"


def test_registry_make_insecure_local(tmp_path):
    store = make_credential_store("insecure_local", path=tmp_path / "creds.json")
    assert isinstance(store, InsecureLocalCredentialStore)
    store.set("ns", "k", "v")
    assert store.get("ns", "k") == "v"


def test_registry_spec_is_normalized():
    # 大小写 / 连字符 / 留白不敏感(复用 Registry 归一)。
    assert isinstance(make_credential_store("  MEMORY "), MemoryCredentialStore)


def test_registry_unknown_spec_lists_available():
    with pytest.raises(ValueError) as ei:
        make_credential_store("vault-not-installed")
    msg = str(ei.value)
    assert "memory" in msg and "insecure_local" in msg and "encrypted_file" in msg


def test_registry_names_include_builtins():
    names = CREDENTIAL_REGISTRY.names()
    assert {"memory", "insecure_local", "encrypted_file"} <= set(names)


# --- InsecureLocal 具体行为:明文落地 + 权限 0600 + 跨实例持久 --------------------


def test_insecure_persists_across_instances(tmp_path):
    path = tmp_path / "shared.json"
    InsecureLocalCredentialStore(path).set("ns", "k", "payload")
    # 同 path 新实例(模拟跨进程):读回同值。
    assert InsecureLocalCredentialStore(path).get("ns", "k") == "payload"


def test_file_stores_same_path_serialize_load_modify_save(tmp_path):
    """同进程的不同实例不能因并发整文件更新而丢失彼此写入。"""

    class SlowDecodeStore(InsecureLocalCredentialStore):
        def _decode(self, raw: bytes) -> str:
            # 让旧实现的两个实例稳定地读到同一份快照，再分别覆盖落盘。
            time.sleep(0.05)
            return super()._decode(raw)

    path = tmp_path / "shared-concurrent.json"
    InsecureLocalCredentialStore(path).set("ns", "seed", "0")
    stores = [SlowDecodeStore(path), SlowDecodeStore(path)]
    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def write(store: SlowDecodeStore, name: str) -> None:
        start.wait()
        try:
            store.set("ns", name, name)
        except BaseException as exc:  # noqa: BLE001 - 线程异常回传主测试
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=(stores[0], "a")),
        threading.Thread(target=write, args=(stores[1], "b")),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    final = InsecureLocalCredentialStore(path)
    assert final.get("ns", "a") == "a"
    assert final.get("ns", "b") == "b"


def test_file_store_failed_replace_preserves_previous_data_and_cleans_temp(tmp_path, monkeypatch):
    """原子提交失败时，旧凭据仍可读且同目录不遗留临时文件。"""
    path = tmp_path / "atomic.json"
    store = InsecureLocalCredentialStore(path)
    store.set("ns", "key", "old")
    before = {item.name for item in tmp_path.iterdir()}

    def fail_replace(src, dst):
        raise OSError("simulated replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="simulated replace failure"):
            store.set("ns", "key", "new")

    assert store.get("ns", "key") == "old"
    assert {item.name for item in tmp_path.iterdir()} == before


def test_file_store_rejects_symlink_on_read(tmp_path):
    """凭据读取拒绝最终路径 symlink，不能静默跟随到另一文件。"""
    target = tmp_path / "target.json"
    target.write_text('{"ns": {"key": "target-secret"}}', encoding="utf-8")
    link = tmp_path / "credentials.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("当前平台不允许创建 symlink")

    store = InsecureLocalCredentialStore(link)
    with pytest.raises(CredentialError, match="普通文件"):
        store.get("ns", "key")


def test_file_store_rejects_symlink_on_write_without_clobbering_target(tmp_path):
    """凭据写入拒绝既有 symlink，不能截断或改写其目标。"""
    target = tmp_path / "victim.txt"
    target.write_text("do-not-clobber", encoding="utf-8")
    link = tmp_path / "credentials.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("当前平台不允许创建 symlink")

    store = InsecureLocalCredentialStore(link)
    with pytest.raises(CredentialError, match="普通文件"):
        store.set("ns", "key", "secret")
    assert target.read_text(encoding="utf-8") == "do-not-clobber"
    assert link.is_symlink()


def test_file_store_atomic_replace_does_not_follow_racing_symlink(tmp_path, monkeypatch):
    """load 后才出现的目标 symlink 会被替换为凭据文件，而不是跟随并改写目标。"""
    target = tmp_path / "victim.txt"
    target.write_text("do-not-clobber", encoding="utf-8")
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(target)
        probe.unlink()
    except (NotImplementedError, OSError):
        pytest.skip("当前平台不允许创建 symlink")

    path = tmp_path / "credentials.json"
    store = InsecureLocalCredentialStore(path)
    real_replace = os.replace
    swapped = False

    def swap_then_replace(src, dst):
        nonlocal swapped
        Path(dst).symlink_to(target)
        swapped = True
        real_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", swap_then_replace)
        store.set("ns", "key", "secret")

    assert swapped
    assert not path.is_symlink()
    assert target.read_text(encoding="utf-8") == "do-not-clobber"
    assert store.get("ns", "key") == "secret"


def test_file_store_rejects_non_regular_file(tmp_path):
    """目录等非普通文件不能被当作 credential 文件读取。"""
    path = tmp_path / "credentials-dir"
    path.mkdir()
    store = InsecureLocalCredentialStore(path)

    with pytest.raises(CredentialError, match="普通文件"):
        store.list("ns")


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位在 Windows 无意义")
def test_insecure_file_mode_is_0600(tmp_path):
    path = tmp_path / "perm.json"
    InsecureLocalCredentialStore(path).set("ns", "k", "v")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


# --- Encrypted 具体行为:密文落地不含明文 + 换 key 读不回 -------------------------


@pytest.mark.skipif(not _CRYPTO, reason="需要 corespine[crypto](cryptography)")
def test_encrypted_ciphertext_has_no_plaintext(tmp_path):
    path = tmp_path / "enc.json"
    key = EncryptedFileCredentialStore.generate_key()
    store = EncryptedFileCredentialStore(path, key=key)
    store.set("tenant", "api_key", _SECRET)
    assert store.get("tenant", "api_key") == _SECRET
    # 落盘密文里绝无明文秘密。
    raw = path.read_bytes()
    assert _SECRET.encode() not in raw
    assert b"api_key" not in raw  # 名字也在密文内,不泄露


@pytest.mark.skipif(not _CRYPTO, reason="需要 corespine[crypto](cryptography)")
def test_encrypted_wrong_key_cannot_read(tmp_path):
    from cryptography.fernet import InvalidToken

    path = tmp_path / "enc.json"
    EncryptedFileCredentialStore(path, key=EncryptedFileCredentialStore.generate_key()).set(
        "ns", "k", "v"
    )
    other = EncryptedFileCredentialStore(path, key=EncryptedFileCredentialStore.generate_key())
    with pytest.raises(InvalidToken):
        other.get("ns", "k")


# --- 跨缝隐私:秘密值到不了 trace --------------------------------------------------


def test_secret_never_reaches_trace():
    """本缝实现自身不发射 trace,故秘密值到不了任何 trace 事件;

    且即便调用方误把 value 塞进隐私 trace,InProcessPrivacyTraceSink 也按 FORBIDDEN_KEYS
    直接拒绝——凭据值不泄露到可观测链路是双重保障。
    """
    store = MemoryCredentialStore()
    sink = InProcessPrivacyTraceSink()
    store.set("ns", "cred", _SECRET)
    assert store.get("ns", "cred") == _SECRET
    # 存取过程零 trace:sink 依旧为空(实现不发射)。
    assert sink.events == []
    # "value" 本就在 FORBIDDEN_KEYS 中:任何想把秘密塞进 trace 的尝试都会被挡下。
    assert "value" in FORBIDDEN_KEYS
    with pytest.raises(TraceError):
        sink.emit("credential.get", value=_SECRET)
    assert sink.events == []
