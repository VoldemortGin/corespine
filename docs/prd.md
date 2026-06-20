# corespine PRD(现状与待做)

> 本文记**现状清单 + 可执行的待做 backlog**;**方向与增长判据**见
> [`roadmap.md`](roadmap.md),宪章见 [`../CLAUDE.md`](../CLAUDE.md)。
> corespine 刻意地薄:多数"待做"被**有意推迟**,需满足 rule of three 才动手(见 roadmap 五问)。

## 现状(已完成)

六条缝均已落地,各带 Protocol + 离线确定性默认 + 工厂 + 参数化 conformance;
`make ci` 全绿(ruff + 47 tests)、import-clean、核心 `dependencies` 为空。

| 缝 | 公开面 | 离线默认 | 状态 |
|---|---|---|---|
| seam/registry | `Registry` / `lazy_extra_import` | 内置注册 + entry-point 发现 | ✅ |
| observability/trace | `TraceSink` / `TraceEvent` / `TraceError` / `FORBIDDEN_KEYS` | `InProcessPrivacyTraceSink`(拒正文) | ✅ |
| llm/provider | `LLMProvider` / `Completion` | `MockProvider`(确定性) | ✅ |
| config/env | `load_from_env` / `env_key` | env→frozen dataclass(含 `X\|None`) | ✅ |
| queue/task_queue | `TaskQueue` / `JobStatus` | `FakeQueue`(同步内联) | ✅ |
| conformance/harness | `ConformanceSuite` / `InvariantPack` / `CaseResult` | 实现×不变量笛卡尔积 | ✅ |

测试覆盖含:解析归一、entry-point 发现、**内置优先于 entry-point**、未知名报错、缺 extra 友好提示、
trace 拒正文、Mock 确定性、env 类型转换 / 可选 / 缺失校验、queue 终态 / 幂等、conformance 笛卡尔积。

## 待做 backlog

### A. 可立即做(纯增量,不需新证据,charter-safe)

| # | 项 | 内容 | 落点 |
|---|---|---|---|
| A1 | entry-point 端到端示例 | 一个"第三方装包即扩展"的最小可跑 demo + 文档(`pyproject` entry-points → `Registry.make` 发现) | example / 文档 |
| A2 | 可选 extra 范式文档 | 把 `[redis]`/`[openai]` 等 extra 命名约定 + `lazy_extra_import` 用法写成一页 | 文档 |
| A3 | conformance 使用范例 | 展示 app 如何用 `InvariantPack` 给某缝绑自己的不变量(机制示范,不含具体业务不变量) | example / 文档 |

### B. 待证据(rule of three:≥2 个真实消费者重复同一稳定面才动)

| # | 项 | 触发条件 | 落点 |
|---|---|---|---|
| B1 | trace 真实 sink(OTel 等) | ≥2 app 需要导出 trace | 可选 extra / contrib,**不进核心默认路径** |
| B2 | llm streaming / 批量 / token 计量 | ≥2 app 在缝上重复同一形状 | 先扩 Protocol(最小),实现走 extra |
| B3 | queue 真实后端 adapter(RQ/Celery)+ 重试/延迟 | ≥2 app 接同一类后端 | adapter 走 extra/contrib;协议扩不扩看证据 |
| B4 | conformance × pytest 集成助手 | ragspine + agentspine 都在重复 `cases()`→`parametrize` 胶水 | **独立插件包或 contrib,绝不把 pytest 引进核心** |
| B5 | config 扩展转型(list / enum / 嵌套) | 出现真实通用配置项需要 | 核心(仅当确为通用) |
| B6 | 统一异常基类 `CorespineError` | 多个 app 需统一 `catch` corespine 错误 | 核心 |
| B7 | 稳定性契约(公开面冻结 / SemVer / deprecation) | 出现依赖其稳定性的外部消费者 | 流程 + 文档 |

### C. 家族前置(最重要,解锁 B 类证据)

| # | 项 | 说明 |
|---|---|---|
| C1 | ragspine / agentspine 真正依赖 corespine | rule-of-three 证据的**唯一来源**;在此之前 B 类多数不该动 |
| C2 | 跨包 conformance 验证 | 多 app 各绑不变量时,验证现有机制是否够用,反推增强 |

## 不做(护栏)

RAG-/agent-特定概念(chunking / retrieval / provenance;MCP / A2A / 工具循环 / 编排)、
预造框架、核心非空依赖、把具体业务不变量写进核心。详见 roadmap「明确不做」。

## 跟踪

- 每落一项 → 在家族 `docs/adr/` 记一条 ADR(编号接 0001),并回填本表状态。
- A 类可随时排期;B 类须先在 PR / ADR 里附上"≥2 消费者重复"的证据;C 类是 B 类的前置。
