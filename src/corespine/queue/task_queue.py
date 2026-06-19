"""任务队列缝:TaskQueue 协议 + 同步内联的 FakeQueue 默认实现。

domain-neutral 的 worker / job 队列抽象。Protocol 只约定 enqueue / get 两件事;真实
后端(RQ+Redis / Celery 等)由各 app 在自己的缝里延迟 import 接入,核心零依赖。

FakeQueue 是离线默认:enqueue 时【同步内联】执行 job 并记录结果 / 失败,不需要任何
外部服务,让测试与离线流程可复现。job 函数签名约定为 fn(payload: dict) -> dict;
失败被捕获记进 JobStatus(不外抛),与真实异步后端"失败也是一种终态"的语义一致。
job 既可传可调用对象,也可传点路径字符串(末段为可调用对象)。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol, cast, runtime_checkable

# job 终态常量(与常见异步后端语义对齐)。
JOB_FINISHED = "finished"
JOB_FAILED = "failed"

# job 函数:纯可序列化 dict 进、dict 出。
JobFunc = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class JobStatus:
    """一个 job 的状态快照:id + 状态 + 结果 / 错误(成功记 result,失败记 error)。"""

    id: str
    status: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@runtime_checkable
class TaskQueue(Protocol):
    """任务队列的最小结构接口:投递一个 job、按 id 查状态。"""

    def enqueue(
        self,
        func: JobFunc | str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> str: ...

    def get(self, job_id: str) -> JobStatus | None: ...


def _resolve(func: JobFunc | str) -> JobFunc:
    """可调用对象原样;点路径字符串 -> 末段可调用对象。"""
    if callable(func):
        return func
    module_path, _, attr = func.rpartition(".")
    if not module_path:
        raise ValueError(f"func 必须是可调用对象或点路径:{func!r}")
    mod = import_module(module_path)
    return cast("JobFunc", getattr(mod, attr))


class FakeQueue:
    """同步内存队列:enqueue 时内联执行 job(离线 / 测试用,无需任何外部服务)。"""

    def __init__(self) -> None:
        self._jobs: dict[str, JobStatus] = {}

    def enqueue(
        self,
        func: JobFunc | str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> str:
        # 幂等:显式 job_id 且已知 -> 直接返回,不重跑。
        if job_id is not None and job_id in self._jobs:
            return job_id
        jid = job_id or uuid.uuid4().hex[:12]
        try:
            result = _resolve(func)(payload)
            self._jobs[jid] = JobStatus(id=jid, status=JOB_FINISHED, result=result)
        except Exception as exc:  # noqa: BLE001 — 内联失败记进状态,不外抛
            self._jobs[jid] = JobStatus(
                id=jid,
                status=JOB_FAILED,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        return jid

    def get(self, job_id: str) -> JobStatus | None:
        return self._jobs.get(job_id)
