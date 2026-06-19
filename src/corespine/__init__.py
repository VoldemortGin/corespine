"""corespine —— Spine 家族的【薄】共享核(ADR 0001 D3/D5)。

只装 domain-neutral 的底层原语:缝注册表 / 隐私安全 observability / LLM 缝 /
env 配置 / 任务队列 / conformance 基座。刻意保持极小,按证据(rule of three)增长——
绝不放任何 RAG- 或 agent-特定的东西。详见 CLAUDE.md 宪章与 docs/adr/0001。
"""

from corespine.config.env import env_key, load_from_env
from corespine.conformance.harness import CaseResult, ConformanceSuite, InvariantPack
from corespine.llm.provider import Completion, LLMProvider, MockProvider
from corespine.observability.trace import (
    FORBIDDEN_KEYS,
    InProcessPrivacyTraceSink,
    TraceError,
    TraceEvent,
    TraceSink,
)
from corespine.queue.task_queue import FakeQueue, JobStatus, TaskQueue
from corespine.seam.registry import Registry, lazy_extra_import

__version__ = "0.0.1"

__all__ = [
    # seam
    "Registry",
    "lazy_extra_import",
    # observability
    "TraceSink",
    "TraceEvent",
    "TraceError",
    "InProcessPrivacyTraceSink",
    "FORBIDDEN_KEYS",
    # llm
    "LLMProvider",
    "Completion",
    "MockProvider",
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
    "__version__",
]
