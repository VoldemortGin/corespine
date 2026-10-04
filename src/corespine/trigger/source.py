"""trigger 缝:TriggerSource 协议(外部信号 -> TriggerEvent)+ 离线确定性默认 + Registry 工厂。

把家族从「被调用的引擎」扩成「能被外部信号启动执行的系统」——对标 n8n 触发器体系的概念
(cron / webhook / 事件 / 轮询 / 手动;n8n 是 fair-code 许可,只学概念、绝不抄代码)。本缝只做
corespine 的 **domain-neutral 机制**:产品层落地(真实 HTTP webhook 端点 / 真实 cron 守护)
是 spinestudio 的活,不进核心。

【拉 vs 推裁决(取拉,与家族同步查询风格一致)】外部信号源有两种形状——拉模式
`poll() -> list[TriggerEvent]`(调用方主动取)与推模式(回调注册,信号源反向调用)。家族既有缝
一律同步查询:queue 的 `enqueue`/`get`、blob 的 `get`、credential 的 `get`,离线默认无线程 /
无守护 / 无控制反转。推模式要求核心持有并回调,破坏离线确定性、把调度环塞进核心。故取【拉模式】:
调用方自己驱动轮询环与时钟,core 只出「一次 poll 收哪些新事件」这条纯函数式机制。

【poll 消费语义裁决(取「取出即消费」)】poll() 只返回【尚未被观测过】的信号,观测即消费——
再次 poll(无新信号)返回 []。两个默认都据此实现:ManualTrigger 像队列一样 fire 入队、poll 排空;
ScheduleTrigger 每个时刻窗口(tick)只发一次,同一时刻重复 poll 不重复发火。这直接兑现 n8n
「仅发现新数据才算一次执行」的语义,也让「同一时刻窗口不重复发火」成为消费语义的自然推论。

隐私(由 conformance / 专测钉死):本缝实现【自身不发射 trace】,故 payload 正文到不了任何 trace
事件;各实现的 __repr__ 只暴露计数 / 配置,绝不暴露 payload。要观测就记 trigger code / 计数 / 耗时,
绝不记 payload——`observability/trace` 的 FORBIDDEN_KEYS 已含 body/value/... 作双重保障。

真实信号源(webhook / 消息队列 / 文件监听等)不进核心:只留 Registry 的 entry-point 扩展点
(group "corespine.trigger"),第三方装包即扩展(rule of three,痛了再抽,不预造监听器)。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from corespine.seam.registry import Registry

# 内置 trigger 类型 code(domain-neutral;调用方可用它做路由 / 计数)。
TRIGGER_MANUAL = "manual"
TRIGGER_SCHEDULE = "schedule"

# tick 边界比较的相对容差(ulp 数,按时间戳量级取 math.ulp):吸收 `k * interval` 与手写时间字面量
# 之间的浮点舍入误差(如 3 * 0.1 == 0.30000000000000004 对 0.3),又不让量级 1.7e9 的时间戳
# 提前约微秒级发火。
_TICK_TOLERANCE_ULPS = 4


def _event_id(trigger: str, *parts: object) -> str:
    """据 trigger code + 判别部件产出确定性、唯一的事件 id(纯函数,可重放)。

    id 是输入的纯函数:同一构造 / 同一发火序列 -> 同一 id 序列(可重放);判别部件互异 ->
    id 互异(唯一)。用 sha256 十六进制前 16 位——够低碰撞,又短便于 grep / 落库。
    """
    raw = ":".join([trigger, *(str(p) for p in parts)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TriggerEvent:
    """一次外部信号产出的事件(只读):trigger 类型 code + 确定性 id + payload + 发生时刻。

    - trigger:产出它的 trigger 类型 code(如 "manual" / "schedule");
    - id:确定性的事件 id(见 _event_id;同一实例内唯一;跨实例 / 重启是否撞见各实现的说明);
    - payload:该信号携带的 domain-neutral 数据(纯 dict;core 不解释其语义);
    - at:发生时刻(epoch 秒;ScheduleTrigger 下由注入时钟给出,假时钟即完全确定)。
    """

    trigger: str
    id: str
    # repr=False:默认 repr 会暴露 payload 正文,与本模块的隐私声明相悖。
    payload: dict[str, Any] = field(default_factory=dict, repr=False)
    at: float = 0.0


@runtime_checkable
class TriggerSource(Protocol):
    """外部信号源的最小结构接口:拉一次,收下自上次以来的新事件。

    契约(由 conformance 钉死):
      - poll() -> list[TriggerEvent]:返回【尚未被观测过】的事件,观测即消费(再次 poll
        无新信号返回 []);空闲(无信号)时返回 []。事件按发生顺序排列。
    """

    def poll(self) -> list[TriggerEvent]: ...


class ManualTrigger:
    """手动 / 测试触发基座:显式 fire(payload) 入队,poll 排空(取出即消费)。

    离线确定性、零依赖:是「测试 / 人工触发」的最小实现,也是拉模式消费语义的参照实现。
    每次 fire 用单调递增的序号派生事件 id——同一发火序列可重放(id 序列稳定),【同一实例内】
    不同 fire 互异(即便 payload 相同)。id 只由 (trigger, namespace, 序号) 决定,故【跨实例 /
    重启后序号从 0 重来会得到相同 id】——这是离线确定性(可重放)的代价,不是「全局唯一」。
    需要跨实例 / 重启不撞时,给每个实例传不同的 `namespace`(如实例名 / 租户名);缺省空
    namespace 的 id 与此前完全一致。repr 只暴露 pending 计数,绝不暴露任何 payload。
    """

    def __init__(self, *, trigger: str = TRIGGER_MANUAL, namespace: str = "") -> None:
        self._trigger = trigger
        self._namespace = namespace
        self._pending: list[TriggerEvent] = []
        self._seq = 0

    def fire(self, payload: dict[str, Any] | None = None, *, at: float = 0.0) -> TriggerEvent:
        """入队一个事件并返回它;下次 poll 会把它(及此后入队的)一并取出。"""
        event = TriggerEvent(
            trigger=self._trigger,
            id=_event_id(
                self._trigger, *((self._namespace,) if self._namespace else ()), self._seq
            ),
            payload=dict(payload or {}),
            at=at,
        )
        self._seq += 1
        self._pending.append(event)
        return event

    def poll(self) -> list[TriggerEvent]:
        drained = self._pending
        self._pending = []  # 取出即消费:排空后再 poll 返回 []
        return drained

    def __repr__(self) -> str:
        return f"ManualTrigger(trigger={self._trigger!r}, pending={len(self._pending)})"


class ScheduleTrigger:
    """固定间隔调度触发:可注入时钟(now_fn),假时钟下完全确定性可测。

    绝不直接 time.time():时钟经 now_fn 构造注入,单元测试拿假时钟即得可重放的发火序列。
    自锚点(start_at,缺省取构造时的 now())起,每跨过一个 interval_s 边界记一次 tick 并发一个
    事件(边界时刻 = anchor + k*interval,k>=1)。消费语义:同一时刻重复 poll 不重复发火;
    时钟跳跃跨过多个边界时,按边界逐个补发(「发现了 N 个新窗口」)。不实现任何真实 cron 守护。

    事件 id 由边界时刻派生 -> 同一 (anchor, interval, 时钟序列) 可完全重放。

    【补发上限 max_catchup / 策略 catchup】缺省 `max_catchup=None`:不限,时钟大跳时一次 poll
    补发全部积压 tick(与此前行为一致;大跳时耗时与内存随跳跃量线性增长)。需要限量时显式选一种
    策略,二者语义【不同】:

    - `catchup="drop_oldest"`(缺省策略):一次 poll 只保留【最近】的 `max_catchup` 个 tick,
      更早的被合并跳过并【永久丢弃】(状态直接前移到当前,之后不再涌出)。适合「只关心最新」。
      丢弃数可观察:累计值见 `skipped_ticks`,本次有丢弃时首个事件的 payload 带 `skipped_ticks`。
      注意:长期持有同一实例的调用方在积压超过上限时会【丢 tick】,请按需选择。
    - `catchup="batch"`:一次 poll 最多返回 `max_catchup` 个【最旧的】未发 tick,状态只前移到已返回
      的位置,其余留给后续 poll——大跳后连续多次 poll 恰好补完全部 tick、一个不丢、每次有界。
      `skipped_ticks` 恒为 0。适合「必须补齐」(如按批消费积压)。

    tick 序号的计算对浮点稳健:以锚点为原点,取「使 `anchor + k*interval` 不晚于 now」的最大 k,
    边界比较用 `_TICK_TOLERANCE_ULPS` 个 ulp(按时间戳量级)的相对容差,而不用绝对 epsilon;
    故既不会因 `3*0.1 != 0.3` 漏发,量级 1.7e9 的时间戳也不会提前发火(至多提前数个 ulp,
    即该量级下不到 1 微秒,比 now 自身的分辨率还小)。
    """

    def __init__(
        self,
        *,
        interval_s: float,
        now_fn: Callable[[], float],
        trigger: str = TRIGGER_SCHEDULE,
        start_at: float | None = None,
        max_catchup: int | None = None,
        catchup: Literal["drop_oldest", "batch"] = "drop_oldest",
    ) -> None:
        if interval_s <= 0:
            raise ValueError(f"interval_s 必须为正:{interval_s!r}")
        if max_catchup is not None and max_catchup < 1:
            raise ValueError(f"max_catchup 必须 >= 1(或 None 表示不限):{max_catchup!r}")
        if catchup not in ("drop_oldest", "batch"):
            raise ValueError(f"catchup 只能是 'drop_oldest' 或 'batch':{catchup!r}")
        self._max_catchup = max_catchup
        self._catchup = catchup
        self._skipped = 0
        self._interval = float(interval_s)
        self._now = now_fn
        self._trigger = trigger
        self._anchor = float(start_at) if start_at is not None else float(now_fn())
        self._last_tick = 0  # 已发火到的 tick 序号(0 = 仅锚点,尚未发过)

    def _tick_time(self, tick: int) -> float:
        return self._anchor + tick * self._interval

    def _due(self, tick: int, now: float) -> bool:
        """第 tick 个边界是否已到(含 _TICK_TOLERANCE_ULPS 个 ulp 的相对容差)。"""
        boundary = self._tick_time(tick)
        return boundary <= now + _TICK_TOLERANCE_ULPS * math.ulp(max(abs(now), abs(boundary)))

    def _current_tick(self, now: float) -> int:
        """已到的最大 tick 序号(锚点之前 / 当刻为 0)。"""
        elapsed = now - self._anchor
        if elapsed <= 0:
            return 0
        tick = int(elapsed / self._interval)  # 估计值,下面按真实边界时刻修正(至多差 1)
        while self._due(tick + 1, now):
            tick += 1
        while tick > 0 and not self._due(tick, now):
            tick -= 1
        return tick

    def poll(self) -> list[TriggerEvent]:
        now = float(self._now())
        current_tick = self._current_tick(now)
        first_tick = self._last_tick + 1
        skipped = 0
        last_tick = current_tick  # 本次发到哪个 tick(batch 模式可能小于 current_tick)
        backlog = current_tick - self._last_tick
        if self._max_catchup is not None and backlog > self._max_catchup:
            if self._catchup == "batch":
                last_tick = self._last_tick + self._max_catchup
            else:
                skipped = backlog - self._max_catchup
                first_tick += skipped
                self._skipped += skipped
        events: list[TriggerEvent] = []
        for tick in range(first_tick, last_tick + 1):
            tick_time = self._tick_time(tick)
            payload: dict[str, Any] = {"tick": tick, "scheduled_at": tick_time}
            if skipped and tick == first_tick:
                payload["skipped_ticks"] = skipped
            events.append(
                TriggerEvent(
                    trigger=self._trigger,
                    id=_event_id(self._trigger, tick_time),
                    payload=payload,
                    at=tick_time,
                )
            )
        if last_tick > self._last_tick:
            self._last_tick = last_tick  # 取出即消费:窗口一旦观测即不再重发
        return events

    @property
    def skipped_ticks(self) -> int:
        """因 max_catchup 截断而被跳过(未发出)的 tick 累计数。"""
        return self._skipped

    def __repr__(self) -> str:
        return (
            f"ScheduleTrigger(trigger={self._trigger!r}, "
            f"interval_s={self._interval}, last_tick={self._last_tick})"
        )


def _make_schedule(
    *,
    interval_s: float,
    now_fn: Callable[[], float],
    trigger: str = TRIGGER_SCHEDULE,
    start_at: float | None = None,
    max_catchup: int | None = None,
    catchup: Literal["drop_oldest", "batch"] = "drop_oldest",
) -> ScheduleTrigger:
    """schedule 工厂:要求显式 interval_s 与注入时钟 now_fn(离线默认不猜真实墙钟)。"""
    return ScheduleTrigger(
        interval_s=interval_s,
        now_fn=now_fn,
        trigger=trigger,
        start_at=start_at,
        max_catchup=max_catchup,
        catchup=catchup,
    )


# trigger 缝的注册表:内置 manual / schedule;真实信号源(webhook / 消息队列 / 文件监听等)
# 经 entry-point group "corespine.trigger" 自动发现(第三方装包即扩展,核心不实现任何监听器)。
TRIGGER_REGISTRY: Registry[TriggerSource] = Registry("trigger")
TRIGGER_REGISTRY.register("manual", ManualTrigger)
TRIGGER_REGISTRY.register("schedule", _make_schedule)


def make_trigger(spec: str, /, **kwargs: object) -> TriggerSource:
    """按 spec 构造一个 TriggerSource(大小写 / 连字符 / 留白不敏感)。

    内置:"manual"(无参)、"schedule"(需 interval_s=... 与注入时钟 now_fn=...);未知 spec
    抛 ValueError 并列清可用名。真实信号源后端由第三方经 entry-point 注册后,同样以 spec 名选用。
    """
    return TRIGGER_REGISTRY.make(spec, **kwargs)
