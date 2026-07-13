"""corespine —— Spine 家族的【薄】共享核(ADR 0001 D3/D5)。

只装 domain-neutral 的底层原语:缝注册表 / 隐私安全 observability / LLM 缝 /
env 配置 / 任务队列 / conformance 基座。刻意保持极小,按证据(rule of three)增长——
绝不放任何 RAG- 或 agent-特定的东西。详见 CLAUDE.md 宪章与 docs/adr/0001。
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from corespine.blob.store import (
    BLOB_REGISTRY,
    BlobError,
    BlobNotFound,
    BlobStore,
    FileSystemBlobStore,
    MemoryBlobStore,
    make_blob_store,
)
from corespine.config.env import env_key, load_from_env
from corespine.conformance.harness import CaseResult, ConformanceSuite, InvariantPack
from corespine.errors import (
    ConfigError,
    CorespineError,
    ProviderError,
    SeamError,
    error_to_dict,
)
from corespine.llm.provider import (
    ChatCompletion,
    ChatCompletionChunk,
    Choice,
    ChoiceDelta,
    ChunkChoice,
    FunctionCall,
    LLMProvider,
    MockProvider,
    ResponseMessage,
    StreamingLLMProvider,
    ToolCall,
    Usage,
)
from corespine.llm.rate_limit import RateLimitedProvider
from corespine.observability.trace import (
    FORBIDDEN_KEYS,
    InProcessPrivacyTraceSink,
    InProcessTraceExporter,
    TraceError,
    TraceEvent,
    TraceExporter,
    TraceSink,
)
from corespine.queue.task_queue import FakeQueue, JobStatus, TaskQueue
from corespine.seam.registry import Registry, lazy_extra_import

try:
    __version__ = _pkg_version("corespine")
except PackageNotFoundError:  # 纯源码 / 未安装场景
    __version__ = "0.0.0+unknown"

__all__ = [
    # seam
    "Registry",
    "lazy_extra_import",
    # observability
    "TraceSink",
    "TraceExporter",
    "TraceEvent",
    "TraceError",
    "InProcessPrivacyTraceSink",
    "InProcessTraceExporter",
    "FORBIDDEN_KEYS",
    # llm(OpenAI chat-completions 规范)
    "LLMProvider",
    "StreamingLLMProvider",
    "MockProvider",
    "ChatCompletion",
    "Choice",
    "ResponseMessage",
    "ToolCall",
    "FunctionCall",
    "Usage",
    "ChatCompletionChunk",
    "ChunkChoice",
    "ChoiceDelta",
    "RateLimitedProvider",
    # blob(key -> bytes 制品存储)
    "BlobStore",
    "MemoryBlobStore",
    "FileSystemBlobStore",
    "BlobError",
    "BlobNotFound",
    "make_blob_store",
    "BLOB_REGISTRY",
    # config
    "load_from_env",
    "env_key",
    # queue
    "TaskQueue",
    "JobStatus",
    "FakeQueue",
    # conformance
    "ConformanceSuite",
    "InvariantPack",
    "CaseResult",
    # errors
    "CorespineError",
    "error_to_dict",
    "ConfigError",
    "SeamError",
    "ProviderError",
    "__version__",
]
