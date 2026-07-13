"""credential 缝:多租户命名凭据存储(namespace × name -> secret,见 store.py 与 docs/adr/0004)。"""

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

__all__ = [
    "CredentialStore",
    "MemoryCredentialStore",
    "InsecureLocalCredentialStore",
    "EncryptedFileCredentialStore",
    "CredentialError",
    "CredentialNotFound",
    "make_credential_store",
    "CREDENTIAL_REGISTRY",
]
