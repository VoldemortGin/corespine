# corespine PRD(现状与待做)

> 本文记**现状清单 + 可执行的待做 backlog**;**方向与增长判据**见
> [`roadmap.md`](roadmap.md),宪章见 [`../CLAUDE.md`](../CLAUDE.md)。
> corespine 刻意地薄:多数"待做"被**有意推迟**,需满足 rule of three 才动手(见 roadmap 五问)。

## 现状(已完成)

六条缝 + errors 缝均已落地,各带 Protocol + 离线确定性默认 + 工厂 + 参数化 conformance;
`make ci` 全绿(ruff + pytest)、import-clean、核心 `dependencies` 为空。

| 缝 | 公开面 | 离线默认 | 状态 |
|---|---|---|---|
| seam/registry | `Registry` / `lazy_extra_import` | 内置注册 + entry-point 发现 | ✅ |
| observability/trace | `TraceSink` / `TraceEvent` / `TraceError` / `FORBIDDEN_KEYS` | `InProcessPrivacyTraceSink`(拒正文) | ✅ |
| llm/provider | `LLMProvider.chat`(OpenAI chat-completions 规范:messages+tools 进、`ChatCompletion` 出) | `MockProvider`(确定性,OpenAI 形状) | ✅ |
| llm/rate_limit | `RateLimitedProvider`(包装任意 LLMProvider,每分钟 token 上限,超限阻塞等待) | 纯标准库滑动窗口(threading + deque,事后按 usage 累计) | ✅ |
| config/env | `load_from_env` / `env_key` | env→frozen dataclass(含 `X\|None`) | ✅ |
| queue/task_queue | `TaskQueue` / `JobStatus` | `FakeQueue`(同步内联) | ✅ |
| blob/store | `BlobStore` / `BlobError` / `BlobNotFound` | `MemoryBlobStore` / `FileSystemBlobStore` | ✅ |
| credential/store | `CredentialStore` / `CredentialError` / `CredentialNotFound` | `MemoryCredentialStore` / `InsecureLocalCredentialStore`(明文+0600) | ✅ |
| trigger/source | `TriggerSource` / `TriggerEvent` | `ManualTrigger` / `ScheduleTrigger`(注入时钟) | ✅ |
| conformance/harness | `ConformanceSuite` / `InvariantPack` / `CaseResult` + `parametrize_kwargs` | 实现×不变量笛卡尔积 | ✅ |
| errors | `CorespineError` / `error_to_dict` / `ConfigError` / `SeamError` | 统一基类 + 任意异常归一 dict | ✅ |

测试覆盖含:解析归一、entry-point 发现、**内置优先于 entry-point**、未知名报错、缺 extra 友好提示、
trace 拒正文、Mock 确定性、env 类型转换 / 可选 / 缺失校验、queue 终态 / 幂等、conformance 笛卡尔积、
errors 基类 code / retryable / 归一 dict、rate limit 滑动窗口 / 超限阻塞 / token 累计 / 线程安全
(全部用离线 `MockProvider` / 本地 fake provider,**零真实 API 调用、零网络、零 key**)。`TraceError`
已改继承 `CorespineError`(code=`trace.forbidden`),家族异常统一基类。

> **新增 llm/rate_limit 的证据(rule of three):** ragspine + spineagent 两消费者都需对 LLM 调用限流,
> 且 openai / anthropic SDK 只有被动重试(撞 429 退避)、无主动 TPM 节流——满足"≥2 消费者重复同一
> 稳定面"。限流是 domain-neutral 通用机制(不属 RAG/agent)、纯标准库可实现(符合零依赖宪章),故提上
> 核心,provider-agnostic 包装任意 LLMProvider;超限【阻塞等待】平滑限速,与 SDK 的 max_retries 互补。

## 待做 backlog

### A. 可立即做(纯增量,不需新证据,charter-safe)

A 类已全部落地(✅),示例 / 文档见 `examples/` 与 `docs/`:

| # | 项 | 内容 | 落点 | 状态 |
|---|---|---|---|---|
| A1 | entry-point 端到端示例 | 一个"第三方装包即扩展"的最小可跑 demo + 文档(`pyproject` entry-points → `Registry.make` 发现) | example / 文档 | ✅ |
| A2 | 可选 extra 范式文档 | 把 `[redis]`/`[openai]` 等 extra 命名约定 + `lazy_extra_import` 用法写成一页 | 文档 | ✅ |
| A3 | conformance 使用范例 | 展示 app 如何用 `InvariantPack` 给某缝绑自己的不变量(机制示范,不含具体业务不变量) | example / 文档 | ✅ |
| A4 | trace 缝使用范例 | 展示两个真实消费形态(ragspine 隐私闸门 / spineagent `TraceSink` 注入点)+ 接真实导出后端(OTel)走 app/contrib 侧的形状(范例用纯标准库假 exporter 站位) | example / 文档 | ✅ |

### B. 待证据(rule of three:≥2 个真实消费者重复同一稳定面才动)

> **watch list(单一消费者证据,暂不实现):** `import_string`(字符串→对象解析)、
> PEP 562 惰性子模块、并行 fan-out 执行器、资源限额——目前仅一个消费者出现过,证据不足
> rule of three,只观察不动手。方向口径见 [`roadmap.md`](roadmap.md) 增长五问。

| # | 项 | 触发条件 | 落点 | 状态 |
|---|---|---|---|---|
| B1 | trace 真实 sink(OTel 等) | ≥2 app 需要**导出** trace | 可选 extra / contrib,**不进核心默认路径** | 部分完成(`TraceExporter` + `InProcessTraceExporter` 已落地;OTel exporter 未做) |
| B2 | llm streaming / 批量 / token 计量 | ≥2 app 在缝上重复同一形状 | 先扩 Protocol(最小),实现走 extra | 部分完成(`StreamingLLMProvider` 已落地;batch 未做) |
| B3 | queue 真实后端 adapter(RQ/Celery)+ 重试/延迟 | ≥2 app 接同一类后端 | adapter 走 extra/contrib;协议扩不扩看证据 | 待证据 |
| B4 | conformance × pytest 集成助手 | ragspine + spineagent 都在重复 `cases()`→`parametrize` 胶水 | `harness.parametrize_kwargs`(纯标准库胶水,不把 pytest 引进核心) | ✅ |
| B5 | config 扩展转型(list / enum / 嵌套) | 出现真实通用配置项需要 | 核心(仅当确为通用) | 待证据 |
| B6 | 统一异常基类 `CorespineError` | 多个 app 需统一 `catch` corespine 错误 | 核心(`errors.py`:基类 + `error_to_dict` + `ConfigError`/`SeamError`) | ✅ |
| B7 | 稳定性契约(公开面冻结 / SemVer / deprecation) | 出现依赖其稳定性的外部消费者 | 流程 + 文档 | 待证据 |

> **B1 证据复核(2026-06-23):** trace **缝本身**(隐私 sink 机制)已被 **2 个真实消费者**用上——
> ragspine(`common/observability.py`:把 `InProcessPrivacyTraceSink` 包成隐私闸门)与 spineagent
> (`agent.step` / orchestration:以 `TraceSink` 形参做注入点,并在 conformance 复用 `FORBIDDEN_KEYS`)。
> **但 B1 的触发条件是"≥2 app 需要【导出】trace"**,而当下两者都只用进程内默认 sink:ragspine 仅把
> `TraceSink → OTel` 列在 P2 路线(规划未落地),spineagent 不导出。**导出证据不足,故不实现任何真实
> 导出 sink(OTel)**;只把"接导出后端走 app/contrib 侧 extra"的形状写进 [`extending.md` 四](extending.md)
> + [`examples/trace_seam_usage.py`](../examples/trace_seam_usage.py)(范例用纯标准库假 exporter 站位)。
> 待第 2 个 app 真正需要导出时再按"可选 extra / contrib,不进核心默认路径"落地。

### C. 家族前置(最重要,解锁 B 类证据)

| # | 项 | 说明 | 状态 |
|---|---|---|---|
| C1 | ragspine / spineagent 真正依赖 corespine | rule-of-three 证据的**唯一来源**;在此之前 B 类多数不该动 | ✅ |
| C2 | 跨包 conformance 验证 | 多 app 各绑不变量时,验证现有机制是否够用,反推增强 | ✅ |

## 不做(护栏)

RAG-/agent-特定概念(chunking / retrieval / provenance;MCP / A2A / 工具循环 / 编排)、
预造框架、核心非空依赖、把具体业务不变量写进核心。详见 roadmap「明确不做」。

## 跟踪

- 每落一项 → 在家族 `docs/adr/` 记一条 ADR(编号接 0001),并回填本表状态。
- A 类可随时排期;B 类须先在 PR / ADR 里附上"≥2 消费者重复"的证据;C 类是 B 类的前置。
