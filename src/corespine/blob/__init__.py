"""blob 缝:domain-neutral 的 key -> bytes 制品存储(见 store.py 与 docs/adr/0003)。"""

from corespine.blob.store import (
    BLOB_REGISTRY,
    BlobError,
    BlobNotFound,
    BlobStore,
    FileSystemBlobStore,
    MemoryBlobStore,
    make_blob_store,
)

__all__ = [
    "BlobStore",
    "MemoryBlobStore",
    "FileSystemBlobStore",
    "BlobError",
    "BlobNotFound",
    "make_blob_store",
    "BLOB_REGISTRY",
]
