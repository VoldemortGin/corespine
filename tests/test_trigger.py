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
