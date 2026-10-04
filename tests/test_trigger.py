"""trigger 缝合约:TriggerSource Protocol 结构匹配 + 参数化 conformance + 默认实现具体行为。

conformance 用 ConformanceSuite × InvariantPack 把「离线默认实现 × 拉模式契约不变量」绑成笛卡尔积
(家族「机制非保证」:不变量在测试侧绑,core 只出 Protocol + 默认 + 工厂)。两个默认(manual /
schedule)驱动方式不同(前者 fire、后者推进假时钟),故用一个统一的 _Driver 适配层把「令其恰好
多产出一个事件」抽象成 arm(),让同一套不变量参数化跑满两实现。

确定性 / 可重放、假时钟机制、隐私(payload 不进 trace / 不进 repr)另有专测钉死。
"""

import pytest

from corespine.conformance.harness import ConformanceSuite, InvariantPack
from corespine.observability.trace import FORBIDDEN_KEYS, InProcessPrivacyTraceSink, TraceError
from corespine.trigger.source import (
    TRIGGER_REGISTRY,
    ManualTrigger,
    ScheduleTrigger,
    TriggerEvent,
    TriggerSource,
    make_trigger,
)


class _Driver:
    """统一驱动适配:把「令信号源恰好多产出一个事件」抽象成 arm(),抹平 manual/schedule 差异。"""

    def __init__(self, source: TriggerSource, arm) -> None:
        self.source = source
        self.arm = arm


# --- conformance 不变量(domain-neutral 拉模式契约)---------------------------


def _idle_poll_is_empty(d: _Driver) -> None:
    # 空闲(无信号)时 poll 返回 []——绝不无中生有地发火。
    assert d.source.poll() == []


def _arm_then_poll_yields_event(d: _Driver) -> None:
    d.arm()
    events = d.source.poll()
    assert len(events) == 1
    e = events[0]
    assert isinstance(e, TriggerEvent)
    assert isinstance(e.trigger, str) and e.trigger  # trigger code 非空
    assert isinstance(e.id, str) and e.id  # 事件 id 非空
    assert isinstance(e.payload, dict)
    assert isinstance(e.at, float)


def _poll_consumes_events(d: _Driver) -> None:
    # 取出即消费:排空后再 poll 无新信号返回 []。
    d.arm()
    assert len(d.source.poll()) == 1
    assert d.source.poll() == []


def _event_ids_unique(d: _Driver) -> None:
    for _ in range(3):
        d.arm()
    events = d.source.poll()
    assert d.source.poll() == []  # 一次排空
    assert len(events) == 3
    assert len({e.id for e in events}) == 3  # id 互异


PACK = (
    InvariantPack[_Driver]("trigger")
    .add("idle_poll_is_empty", _idle_poll_is_empty)
    .add("arm_then_poll_yields_event", _arm_then_poll_yields_event)
    .add("poll_consumes_events", _poll_consumes_events)
    .add("event_ids_unique", _event_ids_unique)
)


def _make_impls() -> dict[str, object]:
    """离线默认实现的驱动工厂表(manual 用 fire;schedule 推进各自独立的假时钟)。"""

    def _manual() -> _Driver:
        t = ManualTrigger()

        def _arm() -> None:
            t.fire({"k": "v"})

        return _Driver(t, _arm)

    def _schedule() -> _Driver:
        clock = {"t": 1000.0}
        t = ScheduleTrigger(interval_s=60.0, now_fn=lambda: clock["t"], start_at=1000.0)

        def _arm() -> None:
            clock["t"] += 60.0  # 跨一个 interval 边界 -> 下次 poll 多一个 tick

        return _Driver(t, _arm)

    return {"manual": _manual, "schedule": _schedule}


def test_conformance_all_defaults():
    """2 实现 × 4 不变量 全绿(离线自检:run() 不抛,逐格通过)。"""
    impls = _make_impls()
    suite = ConformanceSuite(impls, PACK)
    results = suite.run()
    failed = [f"{r.impl}/{r.invariant}: {r.error}" for r in results if not r.passed]
    assert not failed, failed
    assert len(results) == len(impls) * 4


def test_conformance_parametrized():
    # 端到端:消费者用法只两行——参数化跑满全格。
    suite = ConformanceSuite(_make_impls(), PACK)
    for thunk in suite.parametrize_kwargs()["argvalues"]:
        thunk()


# --- Protocol 结构匹配 --------------------------------------------------------


def test_defaults_satisfy_protocol():
    assert isinstance(ManualTrigger(), TriggerSource)
    assert isinstance(ScheduleTrigger(interval_s=1.0, now_fn=lambda: 0.0), TriggerSource)


# --- Registry 工厂 ------------------------------------------------------------


def test_registry_make_manual():
    t = make_trigger("manual")
    assert isinstance(t, ManualTrigger)
    t.fire({"n": 1})
    assert [e.payload for e in t.poll()] == [{"n": 1}]


def test_registry_make_schedule():
    clock = {"t": 0.0}
    t = make_trigger("schedule", interval_s=5.0, now_fn=lambda: clock["t"], start_at=0.0)
    assert isinstance(t, ScheduleTrigger)
    clock["t"] = 5.0
    assert len(t.poll()) == 1


def test_registry_spec_is_normalized():
    # 大小写 / 留白不敏感(复用 Registry 归一)。
    assert isinstance(make_trigger("  MANUAL "), ManualTrigger)


def test_registry_unknown_spec_lists_available():
    with pytest.raises(ValueError) as ei:
        make_trigger("webhook-not-installed")
    msg = str(ei.value)
    assert "manual" in msg and "schedule" in msg


def test_registry_names_include_builtins():
    assert {"manual", "schedule"} <= set(TRIGGER_REGISTRY.names())


# --- ManualTrigger 具体行为:入队 / 排空 / id 可重放 ------------------------------


def test_manual_fire_then_poll_drains():
    t = ManualTrigger()
    t.fire({"a": 1})
    t.fire({"a": 2})
    events = t.poll()
    assert [e.payload for e in events] == [{"a": 1}, {"a": 2}]  # 按发火顺序
    assert t.poll() == []  # 取出即消费


def test_manual_ids_are_replayable_and_unique():
    def run() -> list[str]:
        t = ManualTrigger()
        t.fire({"a": 1})
        t.fire({"a": 1})  # 相同 payload,仍应得不同 id
        return [e.id for e in t.poll()]

    a, b = run(), run()
    assert a == b  # 同一发火序列 -> 同一 id 序列(可重放)
    assert len(set(a)) == len(a) == 2  # 同批次内唯一


# --- ScheduleTrigger 具体行为:注入时钟 / 同刻不重发 / 补发 / 可重放 -----------------


def test_schedule_no_fire_before_first_boundary():
    clock = {"t": 100.0}
    t = ScheduleTrigger(interval_s=5.0, now_fn=lambda: clock["t"], start_at=100.0)
    assert t.poll() == []  # 锚点当刻不发火
    clock["t"] = 104.9
    assert t.poll() == []  # 未满一个 interval,不发火


def test_schedule_same_instant_does_not_refire():
    clock = {"t": 100.0}
    t = ScheduleTrigger(interval_s=5.0, now_fn=lambda: clock["t"], start_at=100.0)
    clock["t"] = 105.0
    first = t.poll()
    assert len(first) == 1
    assert first[0].at == 105.0
    assert first[0].payload == {"tick": 1, "scheduled_at": 105.0}
    assert t.poll() == []  # 同一时刻窗口不重复发火


def test_schedule_catch_up_emits_per_boundary():
    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0)
    clock["t"] = 3.0  # 一次跨过 3 个边界
    events = t.poll()
    assert [e.payload["tick"] for e in events] == [1, 2, 3]  # 逐边界补发
    assert t.poll() == []


def test_schedule_fire_sequence_replayable():
    def run() -> list[str]:
        clock = {"t": 0.0}
        t = ScheduleTrigger(interval_s=10.0, now_fn=lambda: clock["t"], start_at=0.0)
        seq: list[str] = []
        for _ in range(3):
            clock["t"] += 10.0
            seq.extend(e.id for e in t.poll())
        return seq

    a, b = run(), run()
    assert a == b  # 同一 (anchor, interval, 时钟序列) -> 同一 id 序列(假时钟完全确定)
    assert len(set(a)) == len(a) == 3


def test_schedule_rejects_nonpositive_interval():
    with pytest.raises(ValueError):
        ScheduleTrigger(interval_s=0.0, now_fn=lambda: 0.0)
    with pytest.raises(ValueError):
        ScheduleTrigger(interval_s=-1.0, now_fn=lambda: 0.0)


def test_schedule_default_anchor_is_construction_now():
    clock = {"t": 500.0}
    t = ScheduleTrigger(interval_s=10.0, now_fn=lambda: clock["t"])  # 无 start_at -> 取当刻
    assert t.poll() == []
    clock["t"] = 510.0
    assert len(t.poll()) == 1


# --- 跨缝隐私:payload 到不了 trace,也不进 repr ------------------------------------


def test_payload_never_reaches_trace_or_repr():
    """触发器实现自身零 trace,payload 到不了任何 trace 事件;

    且即便调用方误把 payload 正文塞进隐私 trace,InProcessPrivacyTraceSink 也按 FORBIDDEN_KEYS
    直接拒绝——payload 不泄露到可观测链路是双重保障。合法元数据(trigger code / 计数)可照记。
    """
    t = ManualTrigger()
    sink = InProcessPrivacyTraceSink()
    secret = "secret-payload-should-not-leak"
    t.fire({"body": secret})  # payload 里甚至用了受限键名 body
    events = t.poll()
    # 存取 / 发火过程零 trace:sink 依旧为空(实现不发射)。
    assert sink.events == []
    # payload 不出现在 source 的 repr / str 里。
    assert secret not in repr(t) and secret not in str(t)
    # "body" 本就在 FORBIDDEN_KEYS 中:任何想把 payload 正文塞进 trace 的尝试都会被挡下。
    assert "body" in FORBIDDEN_KEYS
    with pytest.raises(TraceError):
        sink.emit("trigger.fire", body=secret)
    assert sink.events == []
    # 合法观测:只记 trigger code + 计数,可照记。
    sink.emit("trigger.poll", trigger=events[0].trigger, count=len(events))
    assert sink.codes() == ["trigger.poll"]


# --- ScheduleTrigger:max_catchup 截断 / 浮点边界 --------------------------------------


def test_schedule_big_clock_jump_is_capped_and_fast():
    import time

    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, max_catchup=5)
    clock["t"] = 2_000_000.0
    started = time.perf_counter()
    events = t.poll()
    assert time.perf_counter() - started < 1.0
    assert len(events) == 5
    # 保留【最近】的 5 个 tick,更早的被合并跳过。
    assert [e.payload["tick"] for e in events] == [
        1_999_996,
        1_999_997,
        1_999_998,
        1_999_999,
        2_000_000,
    ]
    # 被跳过的数量可观察(只是计数,不含内容)。
    assert events[0].payload["skipped_ticks"] == 1_999_995
    assert t.skipped_ticks == 1_999_995


def test_schedule_after_cap_old_ticks_do_not_resurface():
    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, max_catchup=3)
    clock["t"] = 1000.0
    t.poll()
    assert t.poll() == []
    clock["t"] = 1002.0
    assert [e.payload["tick"] for e in t.poll()] == [1001, 1002]
    assert t.skipped_ticks == 997  # 未再增加


def test_schedule_within_cap_has_no_skipped_marker():
    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, max_catchup=3)
    clock["t"] = 3.0
    events = t.poll()
    assert len(events) == 3
    assert all("skipped_ticks" not in e.payload for e in events)
    assert t.skipped_ticks == 0


def test_schedule_default_is_unbounded_like_main():
    # 缺省 max_catchup=None(不限,与 main 一致):小规模下全部补发、无跳过、无 skipped 标记。
    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0)
    clock["t"] = 250.0
    events = t.poll()
    assert [e.payload["tick"] for e in events] == list(range(1, 251))
    assert t.skipped_ticks == 0
    assert all("skipped_ticks" not in e.payload for e in events)


def test_schedule_max_catchup_none_restores_unbounded():
    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, max_catchup=None)
    clock["t"] = 500.0
    events = t.poll()
    assert [e.payload["tick"] for e in events] == list(range(1, 501))
    assert t.skipped_ticks == 0


def test_schedule_max_catchup_validation():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            ScheduleTrigger(interval_s=1.0, now_fn=lambda: 0.0, max_catchup=bad)


def test_schedule_float_interval_boundary_not_late():
    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=0.1, now_fn=lambda: clock["t"], start_at=0.0)
    clock["t"] = 0.3  # 0.3 // 0.1 == 2.0 的浮点坑:应已跨过 3 个边界
    assert [e.payload["tick"] for e in t.poll()] == [1, 2, 3]


def test_schedule_registry_passes_max_catchup_and_catchup():
    clock = {"t": 0.0}
    t = make_trigger(
        "schedule", interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, max_catchup=7
    )
    assert isinstance(t, ScheduleTrigger)
    clock["t"] = 100.0
    assert [e.payload["tick"] for e in t.poll()] == list(range(94, 101))  # drop_oldest 缺省
    assert t.skipped_ticks == 93

    b = make_trigger(
        "schedule",
        interval_s=1.0,
        now_fn=lambda: clock["t"],
        start_at=0.0,
        max_catchup=7,
        catchup="batch",
    )
    assert [e.payload["tick"] for e in b.poll()] == list(range(1, 8))  # batch:最旧的 7 个
    assert b.skipped_ticks == 0


def test_schedule_catchup_validation():
    with pytest.raises(ValueError):
        ScheduleTrigger(interval_s=1.0, now_fn=lambda: 0.0, catchup="nope")  # type: ignore[arg-type]


# --- catchup="batch":分批补发、不丢 tick ----------------------------------------------


def test_schedule_batch_mode_catches_up_exactly_in_bounded_batches():
    clock = {"t": 0.0}
    t = ScheduleTrigger(
        interval_s=1.0,
        now_fn=lambda: clock["t"],
        start_at=0.0,
        max_catchup=100,
        catchup="batch",
    )
    clock["t"] = 1050.0  # 一次大跳:1050 个 tick 待补
    seen: list[int] = []
    sizes: list[int] = []
    while batch := t.poll():
        sizes.append(len(batch))
        seen.extend(e.payload["tick"] for e in batch)
    assert seen == list(range(1, 1051))  # 恰好补完、不丢、不重、有序
    assert max(sizes) <= 100 and sizes == [100] * 10 + [50]
    assert t.skipped_ticks == 0
    assert t.poll() == []
    clock["t"] = 1052.0  # 补完后正常推进
    assert [e.payload["tick"] for e in t.poll()] == [1051, 1052]


def test_schedule_batch_mode_new_ticks_queue_behind_backlog():
    clock = {"t": 0.0}
    t = ScheduleTrigger(
        interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, max_catchup=3, catchup="batch"
    )
    clock["t"] = 5.0
    assert [e.payload["tick"] for e in t.poll()] == [1, 2, 3]
    clock["t"] = 7.0  # 又来了新 tick;仍从最旧的未发 tick 继续
    assert [e.payload["tick"] for e in t.poll()] == [4, 5, 6]
    assert [e.payload["tick"] for e in t.poll()] == [7]


def test_schedule_batch_mode_big_jump_is_fast_per_poll():
    import time

    clock = {"t": 0.0}
    t = ScheduleTrigger(
        interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, max_catchup=100, catchup="batch"
    )
    clock["t"] = 2_000_000.0
    started = time.perf_counter()
    assert len(t.poll()) == 100
    assert time.perf_counter() - started < 0.5


def test_schedule_batch_mode_without_max_catchup_is_unbounded():
    clock = {"t": 0.0}
    t = ScheduleTrigger(interval_s=1.0, now_fn=lambda: clock["t"], start_at=0.0, catchup="batch")
    clock["t"] = 300.0
    assert len(t.poll()) == 300


def test_schedule_drop_oldest_skip_count_is_observable_across_polls():
    clock = {"t": 0.0}
    t = ScheduleTrigger(
        interval_s=1.0,
        now_fn=lambda: clock["t"],
        start_at=0.0,
        max_catchup=10,
        catchup="drop_oldest",
    )
    clock["t"] = 100.0
    first = t.poll()
    assert first[0].payload["skipped_ticks"] == 90 and t.skipped_ticks == 90
    clock["t"] = 300.0
    second = t.poll()
    assert second[0].payload["skipped_ticks"] == 190 and t.skipped_ticks == 280


# --- tick 计算对浮点稳健:真实量级时间戳,不漏发 / 不重发 / 不提前 ---------------------------


@pytest.mark.parametrize("interval", [0.1, 1.0, 3600.0])
@pytest.mark.parametrize("anchor", [0.0, 1.7e9, 1_700_000_000.123])
def test_schedule_steps_tick_by_tick_without_miss_dup_or_early(interval, anchor):
    clock = {"t": anchor}
    t = ScheduleTrigger(interval_s=interval, now_fn=lambda: clock["t"], start_at=anchor)
    ids: list[str] = []
    for k in range(1, 2001):
        clock["t"] = anchor + k * interval  # 恰好到达第 k 个边界
        events = t.poll()
        assert [e.payload["tick"] for e in events] == [k], (interval, anchor, k)
        ids.extend(e.id for e in events)
        assert t.poll() == []  # 同刻不重发
    assert len(set(ids)) == 2000


@pytest.mark.parametrize("interval", [0.1, 1.0, 3600.0])
def test_schedule_never_fires_early_at_real_timestamp_magnitude(interval):
    anchor = 1.7e9
    clock = {"t": anchor}
    t = ScheduleTrigger(interval_s=interval, now_fn=lambda: clock["t"], start_at=anchor)
    boundary = anchor + interval
    clock["t"] = boundary - 3e-6  # 差 3 微秒(远大于该量级的几个 ulp):不得发火
    assert t.poll() == []
    clock["t"] = boundary
    assert [e.payload["tick"] for e in t.poll()] == [1]


@pytest.mark.parametrize("interval", [0.1, 1.0, 3600.0])
def test_schedule_big_jump_at_real_magnitude_counts_exact_ticks(interval):
    anchor = 1.7e9
    clock = {"t": anchor}
    t = ScheduleTrigger(interval_s=interval, now_fn=lambda: clock["t"], start_at=anchor)
    clock["t"] = anchor + 1000 * interval
    assert [e.payload["tick"] for e in t.poll()] == list(range(1, 1001))


# --- repr 不含 payload / ManualTrigger namespace ---------------------------------------


def test_trigger_event_repr_hides_payload():
    event = TriggerEvent(trigger="manual", id="abc", payload={"secret": "SECRET-PAYLOAD"}, at=1.0)
    assert "SECRET-PAYLOAD" not in repr(event) and "secret" not in repr(event)
    assert "abc" in repr(event) and "manual" in repr(event)
    assert event.payload == {"secret": "SECRET-PAYLOAD"}  # 数据本身不变


def test_manual_ids_default_unchanged_and_namespace_disambiguates():
    default_a, default_b = ManualTrigger(), ManualTrigger()
    assert default_a.fire().id == default_b.fire().id  # 缺省:确定性可重放(与此前一致)
    ns_a, ns_b = ManualTrigger(namespace="a"), ManualTrigger(namespace="b")
    ids = {ns_a.fire().id, ns_b.fire().id, default_a.fire().id}
    assert len(ids) == 3  # 命名空间不同 -> 同序号也不撞
    assert ManualTrigger(namespace="a").fire().id == ManualTrigger(namespace="a").fire().id


def test_manual_namespace_via_registry():
    t = make_trigger("manual", namespace="x")
    assert t.fire().id != ManualTrigger().fire().id  # type: ignore[attr-defined]


# --- ManualTrigger id 不撞:trigger 与 namespace 的拼接无歧义 -------------------------------


def test_manual_default_id_is_byte_identical_to_main():
    # main 的缺省 id = sha256("manual:0")[:16];逐字节不变。
    assert ManualTrigger().fire().id == "3e0c11a26341fee7"
    assert ManualTrigger(trigger="a:b").fire().id == _legacy_id("a:b:0")


def _legacy_id(raw: str) -> str:
    import hashlib

    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def test_manual_trigger_and_namespace_with_colons_do_not_collide():
    a = ManualTrigger(trigger="manual:x").fire().id
    b = ManualTrigger(namespace="x").fire().id
    assert a != b
    combos = [
        ("manual", ""),
        ("manual:x", ""),
        ("manual", "x"),
        ("manual:x", "y"),
        ("manual", "x:y"),
        ("manual:x", "y:z"),
        ("manual", "x:y:0"),
        ("manual:x:y", "0"),
        ("", "manual"),
    ]
    ids = {ManualTrigger(trigger=t, namespace=n).fire().id for t, n in combos}
    assert len(ids) == len(combos)
    # 带 namespace 的 id 也不会等于任何缺省 id(缺省 id 恒为 sha256(trigger + ":" + 序号))。
    assert ManualTrigger(trigger="manual", namespace="x").fire().id != _legacy_id("manual:x:0")


def test_manual_namespace_ids_are_deterministic_and_sequenced():
    one, two = ManualTrigger(namespace="n"), ManualTrigger(namespace="n")
    assert [one.fire().id for _ in range(3)] == [two.fire().id for _ in range(3)]
    assert len({e.id for e in [ManualTrigger(namespace="n").fire()]}) == 1
