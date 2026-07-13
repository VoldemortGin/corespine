# ADR 0005:Trigger 缝(机制部分)进 corespine(外部信号 -> 执行)

- 状态:已接受
- 日期:2026-07-14
- 上游:家族 ADR 0001(D3 薄核 / D5 零依赖 / D6 机制非保证)

## 背景

家族本轮对标 n8n 触发器体系的概念(cron / webhook / 事件 / 轮询 / 手动;n8n 是 fair-code
许可,只学概念、绝不抄代码),要把家族从「被调用的引擎」扩成「能被外部信号启动执行的系统」。
盘点到一处 domain-neutral 的最小机制缺口:**「外部信号源产出一批待处理事件」这条抽象**——
既不属于 RAG、也不属于 agent,换到任何后端引擎都成立。产品层落地(真实 HTTP webhook 端点、
真实 cron 守护)是 spinestudio 的活,本 ADR **只锁 corespine 的机制部分**。

## 决策

在 corespine 新增 `trigger` 缝(`src/corespine/trigger/`),照家族缝的元模式落地:

- `TriggerSource` **Protocol**:`poll() -> list[TriggerEvent]`。`TriggerEvent` 是 frozen
  dataclass:trigger 类型 code + 确定性且唯一的 id + payload(纯 dict)+ 发生时刻 at(epoch 秒)。
- **离线确定性默认**、零依赖实现:
  - `ManualTrigger`:显式 `fire(payload)` 入队、`poll()` 排空(测试 / 人工触发基座,也是拉模式
    消费语义的参照);事件 id 由单调序号派生 -> 同一发火序列可重放、不同 fire 互异。
  - `ScheduleTrigger`:**可注入时钟**(`now_fn` 构造注入,绝不直接 `time.time()`)+ 固定间隔;
    假时钟下发火序列完全确定可重放;每个时刻窗口(tick)只发一次,时钟跳跃跨多个边界时按边界补发。
- `Registry` 工厂:`TRIGGER_REGISTRY`(注册 `manual` / `schedule`)+ 便捷 `make_trigger(spec)`;
  真实信号源(webhook / 消息队列 / 文件监听等)只留 entry-point 扩展点(group
  `corespine.trigger`),**核心不实现任何监听器 / 守护进程**。
- 参数化 **conformance**(idle 不发火、arm 后 poll 得事件且形状正确、取出即消费、批内 id 唯一)
  在测试侧绑定(机制非保证);确定性重放、假时钟机制、隐私负向由专测钉死。

## 拉 vs 推裁决(取拉,与家族同步查询风格一致)

外部信号源有两种形状:拉模式 `poll() -> list[TriggerEvent]`(调用方主动取)与推模式(回调注册,
信号源反向调用)。家族既有缝一律**同步查询**:queue 的 `enqueue`/`get`、blob / credential 的
`get`,离线默认**无线程 / 无守护 / 无控制反转**。推模式要求核心持有回调并反向调用,会把调度环 /
事件环塞进核心、破坏离线确定性与可测性。故取**拉模式**:调用方自己驱动轮询环与时钟,core 只出
「一次 poll 收哪些新事件」这条纯函数式机制。真实的推(webhook 回调)由 spinestudio 在产品层把
外部推事件翻译成对某个 `TriggerSource` 的投递 / 轮询,核心不背推的复杂度。

## poll 消费语义裁决(取「取出即消费」)

`poll()` 只返回**尚未被观测过**的信号,观测即消费——再次 poll(无新信号)返回 `[]`。这是两个
默认共享的**唯一**语义:ManualTrigger 像队列一样 fire 入队、poll 排空;ScheduleTrigger 每个 tick
只发一次。它直接兑现 n8n「仅发现新数据才算一次执行」,并让「同一时刻窗口不重复发火」成为消费
语义的自然推论,而非另加的特例。选它而非「幂等 / 需显式 ack」:后者要求核心维护已确认游标 /
重投递机制,超出「刻意地薄」的机制边界——真需要 at-least-once 语义时,由消费者在产品层叠加。

## 隐私不变量

payload 正文**永不**进 trace / repr:本缝实现**自身不发射 trace**,故 payload 到不了任何
trace 事件;各实现的 `__repr__` 只暴露计数 / 配置,绝不载入 payload。`observability/trace` 的
`FORBIDDEN_KEYS`(含 body / value / content / ...)对「误把 payload 塞进 trace」再兜一层——
双重保障。要观测就记 trigger code / 计数 / 耗时。以上由 conformance 与专测钉死。

## 理由(rule of three 五问)

1. 证据:spinestudio(平台产品)需要「被 cron / webhook / 事件启动执行」,对标 n8n / Dify 均有
   触发器形状。**如实记:这是一次「需求先行的机制上提」**(同 ADR 0003/0004)——缝的形状取家族
   已验证的元模式(Protocol + 离线默认 + Registry + conformance),风险受控;广度(真实后端)
   留给 entry-point。
2. 恰好那块:只提「外部信号源产出一批待处理事件 + 一次 poll 收新事件」这一最小面,不含任何
   workspace / workflow / user 等产品概念,payload 是纯 dict、core 不解释其语义。
3. 零领域泄漏:纯「信号 -> 事件」抽象,换到任何后端引擎都成立;真实监听器 / 守护 / 网络一律不进核心。
4. 零依赖:两个默认实现纯标准库(dataclass / hashlib);时钟靠注入 `now_fn`,不碰真实墙钟。
   `dependencies` 仍为空。
5. 机制非保证:core 只给 Protocol + 离线默认 + 工厂;真实 cron 精度 / webhook 鉴权 / at-least-once
   投递 / 去重游标由各 app 自绑(ADR 0001 D6)。

## 后果

- corespine 0.3.0 → **0.4.0**(新增 trigger 缝,导入面扩大)。
- 家族缝再添一员:trigger(与 seam / observability / llm / config / queue / conformance /
  errors / blob / credential 并列)。
- 产品层(spinestudio)后续接真实 webhook 端点 / cron 调度时,把外部信号翻译成对 `TriggerSource`
  的投递,或新增 entry-point 注册的第三方信号源即可,核心默认路径不变。
