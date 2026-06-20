# corespine 演进方向(Roadmap)

> 这**不是** feature backlog。corespine 的增长由**证据**驱动(rule of three)——本文只
> 记录:**往哪个方向看、需要什么证据才动手、以及明确不做什么**。真正落一块,记一条 ADR。
> 先读 [`../CLAUDE.md`](../CLAUDE.md) 宪章与家族 `docs/adr/0001`。

## 立场(先于一切)

- **刻意地薄。** 只放"极小且明显稳定"的 domain-neutral 原语。
- **零领域泄漏。** 任何 RAG-/agent-特定的东西一律不准进。
- **离线可跑、import-clean、`dependencies` 永远为空。** 真实后端经可选 extra 延迟 import。
- **机制,不是保证。** 只给机制,具体不变量由各 app 自己绑。

增长 = rule of three:**≥2 个真实消费者**被证明在重复**同一块稳定面**,才把**恰好那块**
提上来。痛了再抽,带着证据抽。

## 增长判据(把"痛了再抽"落成 checklist)

提上来一块前,五问必须**全 yes**:

1. **证据** —— ≥2 个兄弟包(ragspine / agentspine / …)各自实现过同一块,且形状已收敛?
2. **domain-neutral** —— 换一个完全不同的后端引擎,这段代码还成立?
3. **离线默认** —— 能给出一个零依赖、确定性、可复现的默认实现?
4. **机制非保证** —— 只提供机制,不绑任何具体业务不变量?
5. **可记录** —— 动手即记一条 ADR?

任一为 no,就再等证据。**不预先造框架。**

## 现状基线(2026-06)

六条缝均已落地,各带离线确定性默认 + 参数化 conformance,测试全绿、import-clean、
`dependencies` 为空:`seam/registry` · `observability/trace` · `llm/provider` ·
`config/env` · `queue/task_queue` · `conformance/harness`。下文方向均以此为基线。

## 方向(三层,均附触发证据与落点)

### 一层:加固现有六缝(不扩面,优先)

| 缝 | 可加固点 | 触发证据 | 落点 |
|---|---|---|---|
| seam/registry | entry-point 发现的端到端示例与文档(测试已覆盖发现/未知/内置优先) | 已有机制,缺对外示范 | 核心(文档 / example) |
| observability/trace | 真实 sink(OTel 等)、采样 / 批量 | ≥2 app 需要导出 trace | 可选 extra / contrib,**不进核心默认路径** |
| llm/provider | streaming、批量、token 计量 | ≥2 app 在缝上重复同一形状 | 先扩 Protocol(最小),实现走 extra |
| config/env | list / enum / 嵌套 转型(已支持 str/int/float/bool 及 `X\|None`) | 出现真实配置项需要 | 核心(仅当确为通用) |
| queue/task_queue | 真实后端(RQ / Celery)adapter、重试 / 延迟语义 | ≥2 app 接同一类后端 | adapter 走 extra / contrib;协议扩不扩看证据 |
| conformance/harness | pytest 集成助手(`cases()` → `parametrize`) | ragspine + agentspine 都在重复这段胶水 | **独立插件包或 contrib,绝不把 pytest 引进核心** |

原则:**Protocol 可小步扩,真实实现一律走 extra。** 核心默认路径永远零重依赖、离线确定性。

### 二层:横切关注点(等痛点显现再动)

- **统一异常基类**(如 `CorespineError`):当多个 app 需要统一 `catch` corespine 抛出的
  错误时再引。当前 `TraceError` / `ValueError` / `TypeError` 分散且够用。
- **稳定性契约**:公开面冻结、SemVer、deprecation 流程 —— 等出现**依赖其稳定性的外部
  消费者**时正式化。
- **可选 extra 范式固化**:`lazy_extra_import` 已是机制;把 `[redis]` / `[openai]` 等 extra
  命名约定写成文档 —— 等**第一个真实 adapter** 落地时定形。

### 三层:家族证据闭环(最重要的前置)

- **让 ragspine / agentspine 真正消费 corespine。** 这是 rule-of-three 证据的**唯一来源**。
  在两个兄弟包真实依赖、并各自在某条缝上"痛"之前,一层 / 二层的多数条目都不该动。
- **跨包 conformance**:当多个 app 对同一缝各绑不变量时,用现有
  `ConformanceSuite × InvariantPack` 机制承接,反推机制是否够用。

## 明确不做(守宪章的护栏)

- 任何 RAG-/agent-特定概念:chunking / retrieval / provenance / anti-fabrication;
  MCP / A2A / 工具循环 / 编排。
- 预先造框架、大而全抽象、为"可能有用"而加 API(YAGNI)。
- 让核心 `dependencies` 非空。
- 把具体业务不变量写进核心。

## 决策记录

每真正落一块 → 在家族 `docs/adr/` 续记一条(编号接 0001)。**本文只记方向;决策落 ADR。**
