"""可复用的 InvariantPack:把核心缝的契约做成下游可一行绑定的不变量包(机制,非保证)。

目前提供 trace / queue / llm(含流式)三条缝;blob / credential / trigger 的契约包暂在各自
测试里(tests/test_blob.py 等),按需再上提。

harness 本身不含任何具体不变量;这里按缝提供【最小、domain-neutral】的契约包,下游(或核心
自己的测试)用 `ConformanceSuite({...impls}, XXX_PACK)` 即可对自己的实现跑同一套。
本模块 pytest-free:违反不变量一律抛 AssertionError / 对应领域异常。
"""

from __future__ import annotations

from typing import Any

from corespine.conformance.harness import InvariantPack
from corespine.llm.provider import ChatCompletion, LLMProvider, StreamingLLMProvider
from corespine.observability.trace import FORBIDDEN_KEYS, TraceError, TraceSink
from corespine.queue.task_queue import JOB_FAILED, JOB_FINISHED, TaskQueue


def _expect_rejected(sink: TraceSink, **fields: object) -> None:
    try:
        sink.emit("conformance_probe", **fields)
    except TraceError:
        return
    raise AssertionError(f"trace sink 放行了含受限内容的载荷:{sorted(fields)}")


def _trace_rejects_top_level_forbidden_keys(sink: TraceSink) -> None:
    for key in sorted(FORBIDDEN_KEYS):
        _expect_rejected(sink, **{key: "x"})
    _expect_rejected(sink, **{"Answer": "x"})  # 大小写不敏感


def _trace_rejects_nested_forbidden_keys(sink: TraceSink) -> None:
    _expect_rejected(sink, meta={"text": "x"})
    _expect_rejected(sink, calls=[{"ok": 1}, {"prompt": "x"}])
    _expect_rejected(sink, deep={"a": {"b": ({"body": "x"},)}})


def _trace_rejects_uninspectable_objects(sink: TraceSink) -> None:
    _expect_rejected(sink, obj=object())


def _trace_accepts_metadata_events(sink: TraceSink) -> None:
    sink.emit("stage_done", count=3, took_ms=1.5, ok=True, timings={"a": 1}, stages=[1, 2])


TRACE_PRIVACY_PACK: InvariantPack[TraceSink] = (
    InvariantPack("trace_privacy")
    .add("rejects_top_level_forbidden_keys", _trace_rejects_top_level_forbidden_keys)
    .add("rejects_nested_forbidden_keys", _trace_rejects_nested_forbidden_keys)
    .add("rejects_uninspectable_objects", _trace_rejects_uninspectable_objects)
    .add("accepts_metadata_events", _trace_accepts_metadata_events)
)


# --- queue --------------------------------------------------------------------------


def _double(payload: dict[str, Any]) -> dict[str, Any]:
    return {"doubled": payload["n"] * 2}


def _boom(payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("job 故意失败")


def _queue_runs_job_and_records_result(queue: TaskQueue) -> None:
    status = queue.get(queue.enqueue(_double, {"n": 21}))
    assert status is not None, "enqueue 返回的 id 必须能 get 到状态"
    assert status.status == JOB_FINISHED and status.result == {"doubled": 42}
    assert status.error is None


def _queue_failure_is_recorded_not_raised(queue: TaskQueue) -> None:
    status = queue.get(queue.enqueue(_boom, {}))
    assert status is not None
    assert status.status == JOB_FAILED and status.result is None
    assert status.error is not None and status.error["type"] == "RuntimeError"


def _queue_explicit_job_id_is_idempotent(queue: TaskQueue) -> None:
    runs: list[int] = []

    def counting(payload: dict[str, Any]) -> dict[str, Any]:
        runs.append(1)
        return {"runs": len(runs)}

    assert queue.enqueue(counting, {}, job_id="fixed") == "fixed"
    assert queue.enqueue(counting, {}, job_id="fixed") == "fixed"
    assert len(runs) == 1, "已知 job_id 不得重跑"


def _queue_unknown_id_is_none(queue: TaskQueue) -> None:
    assert queue.get("no-such-job") is None


def _queue_dotted_path_func(queue: TaskQueue) -> None:
    status = queue.get(queue.enqueue(f"{__name__}._double", {"n": 5}))
    assert status is not None and status.result == {"doubled": 10}


# 适用范围:要求 enqueue 后【同步内联执行】(get 立即得到终态)、job 在【当前进程】内执行(用了闭包
# job 并读取其副作用)。FakeQueue 等同步实现可绑;异步后端(如 RQ:worker 在别的进程、get 先给
# queued)满足不了这些不变量,不要直接绑——请自写等待终态的不变量包。
QUEUE_PACK: InvariantPack[TaskQueue] = (
    InvariantPack("task_queue")
    .add("runs_job_and_records_result", _queue_runs_job_and_records_result)
    .add("failure_is_recorded_not_raised", _queue_failure_is_recorded_not_raised)
    .add("explicit_job_id_is_idempotent", _queue_explicit_job_id_is_idempotent)
    .add("unknown_id_is_none", _queue_unknown_id_is_none)
    .add("dotted_path_func", _queue_dotted_path_func)
)


# --- llm ----------------------------------------------------------------------------

_MESSAGES: list[dict[str, Any]] = [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "ping"},
]


def _llm_returns_openai_shaped_completion(provider: LLMProvider) -> None:
    result = provider.chat(_MESSAGES)
    assert isinstance(result, ChatCompletion)
    assert len(result.choices) >= 1, "至少一个 choice"
    message = result.choices[0].message
    assert message.role == "assistant"
    assert message.content is not None or message.tool_calls, "content 与 tool_calls 不能都空"


def _llm_usage_is_consistent_when_present(provider: LLMProvider) -> None:
    usage = provider.chat(_MESSAGES).usage
    if usage is None:
        return
    assert usage.prompt_tokens >= 0 and usage.completion_tokens >= 0
    assert usage.total_tokens >= max(usage.prompt_tokens, usage.completion_tokens)


def _llm_does_not_mutate_input_messages(provider: LLMProvider) -> None:
    messages = [dict(m) for m in _MESSAGES]
    provider.chat(messages)
    assert messages == _MESSAGES, "provider 不得修改调用方传入的 messages"


LLM_PROVIDER_PACK: InvariantPack[LLMProvider] = (
    InvariantPack("llm_provider")
    .add("returns_openai_shaped_completion", _llm_returns_openai_shaped_completion)
    .add("usage_is_consistent_when_present", _llm_usage_is_consistent_when_present)
    .add("does_not_mutate_input_messages", _llm_does_not_mutate_input_messages)
)


def _stream_text(provider: StreamingLLMProvider) -> tuple[str, str | None]:
    text, finish = "", None
    for chunk in provider.stream_chat(_MESSAGES):
        for choice in chunk.choices:
            text += choice.delta.content or ""
            finish = choice.finish_reason or finish
    return text, finish


def _stream_ends_with_finish_reason(provider: StreamingLLMProvider) -> None:
    _, finish = _stream_text(provider)
    assert finish is not None, "流的末块必须带 finish_reason"


def _stream_concat_equals_non_stream(provider: StreamingLLMProvider) -> None:
    # 仅适用于【确定性】实现(同一输入恒定输出,如 MockProvider);真实非确定性 provider 勿绑本包。
    assert isinstance(provider, LLMProvider), "流式 provider 必须同时满足 LLMProvider"
    text, _ = _stream_text(provider)
    assert text == provider.chat(_MESSAGES).choices[0].message.content


STREAMING_PROVIDER_PACK: InvariantPack[StreamingLLMProvider] = (
    InvariantPack("streaming_llm_provider")
    .add("ends_with_finish_reason", _stream_ends_with_finish_reason)
    .add("concat_equals_non_stream", _stream_concat_equals_non_stream)
)
