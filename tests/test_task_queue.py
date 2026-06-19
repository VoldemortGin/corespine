"""queue.task_queue 合约:FakeQueue 同步执行 / 失败记录 / 幂等 / Protocol。"""

from corespine.queue.task_queue import (
    JOB_FAILED,
    JOB_FINISHED,
    FakeQueue,
    TaskQueue,
)


def _double(payload):
    return {"doubled": payload["n"] * 2}


def _boom(payload):
    raise RuntimeError("job 故意失败")


def test_enqueue_runs_synchronously_and_records_result():
    q = FakeQueue()
    jid = q.enqueue(_double, {"n": 21})
    status = q.get(jid)
    assert status is not None
    assert status.status == JOB_FINISHED
    assert status.result == {"doubled": 42}
    assert status.error is None


def test_failure_is_recorded_not_raised():
    q = FakeQueue()
    jid = q.enqueue(_boom, {})
    status = q.get(jid)
    assert status.status == JOB_FAILED
    assert status.result is None
    assert status.error["type"] == "RuntimeError"
    assert "故意失败" in status.error["message"]


def test_explicit_job_id_is_idempotent():
    q = FakeQueue()
    runs = {"count": 0}

    def _counting(payload):
        runs["count"] += 1
        return {"runs": runs["count"]}

    first = q.enqueue(_counting, {}, job_id="fixed")
    second = q.enqueue(_counting, {}, job_id="fixed")
    assert first == second == "fixed"
    # 已知 job_id 不重跑。
    assert runs["count"] == 1


def test_dotted_path_resolution():
    q = FakeQueue()
    # 解析点路径到本模块的 _double。
    jid = q.enqueue(f"{__name__}._double", {"n": 5})
    assert q.get(jid).result == {"doubled": 10}


def test_get_unknown_returns_none():
    assert FakeQueue().get("missing") is None


def test_fake_queue_satisfies_protocol():
    assert isinstance(FakeQueue(), TaskQueue)
