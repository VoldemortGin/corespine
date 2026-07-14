"""credential 缝:CredentialStore 协议(namespace × name -> secret)+ 离线默认 + Registry 工厂。

多租户产品的现实缺口:每租户「自带 API key」需要加密落库,而此前只有「环境变量名」这种
间接引用。本缝把 domain-neutral 的那一小块提上来——按 (namespace, name) 存取一个 str 秘密值。
namespace 是纯字符串隔离维度(调用方拿它拼租户 / 环境 / 作用域,core 不认识任何产品概念);
name 是该 namespace 下的凭据名(如 "openai_api_key")。

【加密路线裁决(诚实优于自造密码学)】薄核宪章要求默认路径零三方依赖、import-clean,而 Python
标准库【没有】AEAD(hashlib/hmac/secrets 只够做完整性/派生,拼不出安全的认证加密),自造密码学
是众所周知的脚枪(家族铁律:不自造加密)。故采「诚实路线」:

  - 两个零依赖默认实现【不谎称加密】:
      · MemoryCredentialStore —— 进程内 dict,零落地(测试 / 临时);
      · InsecureLocalCredentialStore —— 明文本地文件 + 权限 0600 + 名字里直书 "Insecure"
        作命名警示,杜绝把「藏起来」误当「加密」。
  - 真实加密走可选 extra:EncryptedFileCredentialStore 经 `corespine[crypto]` 用
    cryptography 的 Fernet(AES-128-CBC + HMAC-SHA256,Apache-2.0/BSD 双许可,家族许可相容),
    在 __init__ 里【延迟 import】——没装 crypto 不影响核心离线默认路径,corespine 的
    `dependencies` 仍为空。见 docs/adr/0004。

隐私不变量(由 conformance 钉死):秘密值【永不】出现在 repr / str / 异常消息里;本缝的实现
自身【不】发射 trace,故秘密值也到不了任何 trace 事件(要观测就记 namespace/name,绝不记 value)。

真实外部 vault(1Password / HashiCorp 等)不进核心:只留 Registry 的 entry-point 扩展点
(group "corespine.credential"),第三方装包即扩展(rule of three,痛了再抽,不预造 vault 客户端)。
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from corespine.errors import CorespineError
from corespine.seam.registry import Registry, lazy_extra_import

# 落地文件的内部布局:{namespace: {name: value}} 的 JSON。
_Store = dict[str, dict[str, str]]

# 文件 store 是整文件 read-modify-write；同进程中即使构造了多个实例，也必须按同一路径
# 共用一把锁，否则两个更新会各自从旧快照出发、后写者覆盖先写者。
_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[str, threading.RLock] = {}


def _shared_file_lock(path: Path) -> threading.RLock:
    """按不跟随最终文件 symlink 的 canonical 路径复用进程内 RLock。"""
    canonical = os.path.normcase(str(path.parent.resolve() / path.name))
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(canonical)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[canonical] = lock
        return lock


class CredentialError(CorespineError):
    """credential 缝的边界异常基类(带稳定可 grep 的 code,便于跨缝统一捕获)。"""

    code = "credential.error"


class CredentialNotFound(CredentialError):
    """get 一个不存在的 (namespace, name) 时抛出。

    隐私:消息只带 namespace / name 定位符,【绝不】携带任何秘密值。
    """

    code = "credential.not_found"


@runtime_checkable
class CredentialStore(Protocol):
    """命名凭据存储的最小结构接口:按 (namespace, name) 存取一个 str 秘密值。

    契约(由 conformance 钉死):
      - set(namespace, name, value):写入 / 覆盖;
      - get(namespace, name) -> str:读回,缺失抛 CredentialNotFound;
      - delete(namespace, name):删除,幂等(删不存在的不报错);
      - list(namespace) -> list[str]:列该 namespace 下的凭据名(字典序;未知 namespace 返回 [])。

    namespace 是 domain-neutral 的隔离维度(调用方拼租户 / 作用域);不同 namespace 下的
    同名凭据互不可见、互不影响。秘密值永不出现在 repr / str / 异常消息 / trace 里。
    """

    def set(self, namespace: str, name: str, value: str) -> None: ...

    def get(self, namespace: str, name: str) -> str: ...

    def delete(self, namespace: str, name: str) -> None: ...

    def list(self, namespace: str) -> list[str]: ...


class MemoryCredentialStore:
    """进程内 dict 实现:零落地、离线确定性(测试 / 临时用)。

    repr 只暴露 namespace / 条目【计数】,绝不暴露任何名字或秘密值(隐私 by construction)。
    """

    def __init__(self) -> None:
        self._data: _Store = {}

    def set(self, namespace: str, name: str, value: str) -> None:
        self._data.setdefault(namespace, {})[name] = value

    def get(self, namespace: str, name: str) -> str:
        try:
            return self._data[namespace][name]
        except KeyError:
            raise CredentialNotFound(
                f"凭据不存在:namespace={namespace!r} name={name!r}",
                namespace=namespace,
                name=name,
            ) from None

    def delete(self, namespace: str, name: str) -> None:
        self._data.get(namespace, {}).pop(name, None)  # 幂等

    def list(self, namespace: str) -> list[str]:
        return sorted(self._data.get(namespace, {}))

    def __repr__(self) -> str:
        entries = sum(len(v) for v in self._data.values())
        return f"MemoryCredentialStore(namespaces={len(self._data)}, entries={entries})"


class _FileCredentialStore:
    """本地文件凭据存储的私有基类:整文件 {namespace:{name:value}} JSON 载入 -> 改 -> 落回。

    子类只需实现字节编解码钩子(_encode / _decode),据此分出「明文」与「Fernet 加密」两种落地;
    其余 set/get/delete/list 与「文件权限 0600」逻辑在此共用(避免两份近乎相同的载入/落回复制)。
    repr 只暴露落地路径(非秘密),绝不载入 / 暴露任何名字或秘密值。
    """

    def __init__(self, path: str | Path) -> None:
        requested = Path(path).expanduser()
        requested.parent.mkdir(parents=True, exist_ok=True)
        # 固定父目录的 canonical 位置，但保留最终文件名本身，绝不 resolve 最终 symlink。
        self._path = requested.parent.resolve() / requested.name
        self._lock = _shared_file_lock(self._path)

    # --- 子类钩子:明文直通 / Fernet 加解密 -----------------------------------
    def _encode(self, text: str) -> bytes:
        raise NotImplementedError

    def _decode(self, raw: bytes) -> str:
        raise NotImplementedError

    # --- 共用的整文件载入 / 落回 ----------------------------------------------
    def _load(self) -> _Store:
        try:
            before = os.lstat(self._path)
        except FileNotFoundError:
            return {}
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise CredentialError(
                f"凭据文件必须是普通文件且不能是 symlink:path={str(self._path)!r}",
                path=str(self._path),
            )

        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd: int | None = None
        try:
            try:
                fd = os.open(self._path, flags)
            except FileNotFoundError:
                return {}
            except OSError as exc:
                raise CredentialError(
                    f"凭据文件无法安全打开:path={str(self._path)!r}",
                    path=str(self._path),
                ) from exc

            opened = os.fstat(fd)
            try:
                after = os.lstat(self._path)
            except FileNotFoundError as exc:
                raise CredentialError(
                    f"凭据文件在读取期间被替换:path={str(self._path)!r}",
                    path=str(self._path),
                ) from exc
            same_file = (opened.st_dev, opened.st_ino) == (after.st_dev, after.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_ISLNK(after.st_mode)
                or not stat.S_ISREG(after.st_mode)
                or not same_file
            ):
                raise CredentialError(
                    f"凭据文件必须是普通文件且不能是 symlink:path={str(self._path)!r}",
                    path=str(self._path),
                )
            with os.fdopen(fd, "rb") as handle:
                fd = None  # fdopen 接管关闭责任
                raw = handle.read()
        finally:
            if fd is not None:
                os.close(fd)
        if not raw:
            return {}
        data: _Store = json.loads(self._decode(raw))
        return data

    def _save(self, data: _Store) -> None:
        payload = self._encode(json.dumps(data, ensure_ascii=False))
        # 同目录 0600 临时普通文件完整落盘后原子替换：写失败时旧文件保持完整，且 replace
        # 替换最终目录项而不跟随目标 symlink。Windows 上 chmod 语义有限，但不影响原子提交。
        fd: int | None = None
        temp_path: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
            )
            temp_path = Path(temp_name)
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(fd, 0o600)
            else:  # pragma: no cover - Windows 无 POSIX mode bits
                os.chmod(temp_path, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = None  # fdopen 接管关闭责任
                written = handle.write(payload)
                if written != len(payload):
                    raise OSError("credential 临时文件未完整写入")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
            temp_path = None
        finally:
            if fd is not None:
                os.close(fd)
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def set(self, namespace: str, name: str, value: str) -> None:
        with self._lock:
            data = self._load()
            data.setdefault(namespace, {})[name] = value
            self._save(data)

    def get(self, namespace: str, name: str) -> str:
        with self._lock:
            try:
                return self._load()[namespace][name]
            except KeyError:
                raise CredentialNotFound(
                    f"凭据不存在:namespace={namespace!r} name={name!r}",
                    namespace=namespace,
                    name=name,
                ) from None

    def delete(self, namespace: str, name: str) -> None:
        with self._lock:
            data = self._load()
            bucket = data.get(namespace)
            if bucket is not None and name in bucket:
                del bucket[name]
                self._save(data)  # 幂等:不存在则不落回

    def list(self, namespace: str) -> list[str]:
        with self._lock:
            return sorted(self._load().get(namespace, {}))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(path={str(self._path)!r})"


class InsecureLocalCredentialStore(_FileCredentialStore):
    """明文本地文件实现(零依赖)——名字里的 "Insecure" 是命名警示。

    秘密值以【明文】JSON 落盘,仅靠文件权限 0600 保护:任何能读该文件的进程 / 用户都能看到
    秘密。这【不是加密】,只适合离线 / 开发 / 单机可信环境。真加密请用 EncryptedFileCredentialStore
    (`corespine[crypto]`)。
    """

    def _encode(self, text: str) -> bytes:
        return text.encode("utf-8")

    def _decode(self, raw: bytes) -> str:
        return raw.decode("utf-8")


class EncryptedFileCredentialStore(_FileCredentialStore):
    """Fernet 对称加密的本地文件实现(经 `corespine[crypto]` 可选 extra)。

    整个 {namespace:{name:value}} JSON 用 Fernet(AES-128-CBC + HMAC-SHA256)加密后落盘,
    密文文件同样收紧到 0600。cryptography 在 __init__ 里【延迟 import】——没装 crypto 时构造
    本类才会抛友好的安装指引,核心离线默认路径不受影响。

    key 必须是 Fernet 密钥(urlsafe-base64 的 32 字节);可用 `EncryptedFileCredentialStore
    .generate_key()` 生成。key 的保管 / 轮换是【调用方】的事(core 不落地 key、不暴露 key）。
    """

    def __init__(self, path: str | Path, *, key: bytes | str) -> None:
        super().__init__(path)
        fernet_mod = lazy_extra_import("cryptography.fernet", pkg="corespine", extra="crypto")
        # key 只进 Fernet 实例、绝不作实例属性存明,repr / str 无从暴露。
        self._fernet = fernet_mod.Fernet(key)

    @staticmethod
    def generate_key() -> bytes:
        """生成一个新的 Fernet 密钥(urlsafe-base64 的 32 字节)。"""
        fernet_mod = lazy_extra_import("cryptography.fernet", pkg="corespine", extra="crypto")
        # cryptography 经 lazy_extra_import 返回 Any(核心不硬依赖其类型),显式 cast 回精确类型。
        return cast("bytes", fernet_mod.Fernet.generate_key())

    def _encode(self, text: str) -> bytes:
        return cast("bytes", self._fernet.encrypt(text.encode("utf-8")))

    def _decode(self, raw: bytes) -> str:
        return cast("str", self._fernet.decrypt(raw).decode("utf-8"))


def _make_insecure_local(*, path: str | Path) -> InsecureLocalCredentialStore:
    """insecure_local 工厂:要求显式 path(离线默认不猜落地位置)。"""
    return InsecureLocalCredentialStore(path)


def _make_encrypted_file(*, path: str | Path, key: bytes | str) -> EncryptedFileCredentialStore:
    """encrypted_file 工厂:要求显式 path 与 Fernet key(cryptography 在构造时才延迟 import)。"""
    return EncryptedFileCredentialStore(path, key=key)


# credential 缝的注册表:内置 memory / insecure_local / encrypted_file;真实外部 vault
# (1Password / HashiCorp 等)经 entry-point group "corespine.credential" 自动发现
# (第三方装包即扩展,无需改核心,也不在核心实现任何 vault 客户端)。
CREDENTIAL_REGISTRY: Registry[CredentialStore] = Registry("credential")
CREDENTIAL_REGISTRY.register("memory", MemoryCredentialStore)
CREDENTIAL_REGISTRY.register("insecure_local", _make_insecure_local)
CREDENTIAL_REGISTRY.register("encrypted_file", _make_encrypted_file)


def make_credential_store(spec: str, **kwargs: object) -> CredentialStore:
    """按 spec 构造一个 CredentialStore(大小写 / 连字符 / 留白不敏感)。

    内置:"memory"(无参)、"insecure_local"(需 path=...)、"encrypted_file"(需 path=... 与
    key=...,且装了 `corespine[crypto]`);未知 spec 抛 ValueError 并列清可用名。真实 vault
    后端由第三方经 entry-point 注册后,同样以 spec 名选用。
    """
    return CREDENTIAL_REGISTRY.make(spec, **kwargs)
