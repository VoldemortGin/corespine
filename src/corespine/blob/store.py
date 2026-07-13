"""blob 缝:BlobStore 协议(key -> bytes)+ 两个离线确定性默认 + Registry 工厂。

domain-neutral 的制品存储抽象:按 key 存取字节 blob。Protocol 只承诺【字节 round-trip】
(put / get / delete / exists),元数据最小化——核心不背 content-type / 大小 / 时间戳,
需要元数据的 app 自行在 key 命名空间或外层包装里绑(守薄核宪章:只放极小且明显稳定的原语)。

两个默认实现纯标准库、离线确定性:
  - MemoryBlobStore:进程内 dict,零落地,测试 / 临时缓存用;
  - FileSystemBlobStore:本地文件系统,key 经 sha256 摊平成扁平文件名——杜绝路径穿越、
    跨平台文件名安全、同 key 跨进程确定映射(同 root 新实例读回同字节)。

真实后端(S3 / MinIO)不进核心:经 Registry 的 entry-point 自动发现 + lazy_extra_import
延迟接入(装 corespine[s3] 之类的可选 extra 即扩展),corespine 的 dependencies 永远为空。
见 docs/adr/0003。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from corespine.errors import CorespineError
from corespine.seam.registry import Registry


class BlobError(CorespineError):
    """blob 缝的边界异常基类(带稳定可 grep 的 code,便于跨缝统一捕获)。"""

    code = "blob.error"


class BlobNotFound(BlobError):
    """get 一个不存在的 key 时抛出。"""

    code = "blob.not_found"


@runtime_checkable
class BlobStore(Protocol):
    """制品存储的最小结构接口:按 key(str)存取字节 blob。

    契约(由 conformance 钉死):
      - put(key, data):写入 / 覆盖;
      - get(key) -> bytes:读回字节,缺失抛 BlobNotFound;
      - delete(key):删除,幂等(删不存在的 key 不报错);
      - exists(key) -> bool:是否存在(空字节也算存在,不是缺失)。
    """

    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


class MemoryBlobStore:
    """进程内 dict 实现:零落地、离线确定性(测试 / 临时缓存用)。"""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self._data[key] = bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._data[key]
        except KeyError:
            raise BlobNotFound(f"blob 不存在:{key!r}", key=key) from None

    def delete(self, key: str) -> None:
        self._data.pop(key, None)  # 幂等

    def exists(self, key: str) -> bool:
        return key in self._data


class FileSystemBlobStore:
    """本地文件系统实现:key 经 sha256 摊平成 root 下扁平文件名。

    用 sha256(key) 的 hex 作文件名,而非把 key 直接当路径——这样:
      - 杜绝路径穿越(含 ".." / 斜杠的 key 也逃不出 root);
      - 跨平台文件名安全(hex 全在 [0-9a-f],无平台非法字符、无长度 / 大小写敏感问题);
      - 同 key 跨进程确定映射(同 root 新实例读回同字节),支撑离线可复现。
    不追求可逆(存储无需列举 / 反查 key);需要枚举语义的 app 走真实后端。
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._root / digest

    def put(self, key: str, data: bytes) -> None:
        self._path(key).write_bytes(bytes(data))

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise BlobNotFound(f"blob 不存在:{key!r}", key=key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)  # 幂等

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


def _make_filesystem(*, root: str | Path) -> FileSystemBlobStore:
    """filesystem 工厂:要求显式 root(离线默认不猜落地位置)。"""
    return FileSystemBlobStore(root)


# blob 缝的注册表:内置 memory / filesystem;真实后端经 entry-point group "corespine.blob"
# 自动发现(第三方装包即扩展,无需改核心)。
BLOB_REGISTRY: Registry[BlobStore] = Registry("blob")
BLOB_REGISTRY.register("memory", MemoryBlobStore)
BLOB_REGISTRY.register("filesystem", _make_filesystem)


def make_blob_store(spec: str, **kwargs: object) -> BlobStore:
    """按 spec 构造一个 BlobStore(大小写 / 连字符 / 留白不敏感)。

    内置:"memory"(无参)、"filesystem"(需 root=...);未知 spec 抛 ValueError 并列清可用名。
    真实后端(s3 / minio)由第三方经 entry-point 注册后,同样以 spec 名选用。
    """
    return BLOB_REGISTRY.make(spec, **kwargs)
