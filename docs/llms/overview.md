# corespine 总览(给 AI 消费者)

> 本篇建立心智模型:corespine 是什么、解决什么、何时用 / 不用、六条缝与统一元模式。
> API 速查见 [api.md](api.md),可跑示例见 [recipes.md](recipes.md),易错点见 [gotchas.md](gotchas.md)。

## 是什么

corespine 是 **Spine 家族的薄共享核**:一组 **domain-neutral 的底层原语**——那些既不属于
RAG、也不属于 agent 的稳定地基。它被 `ragspine` / `agentspine` 等兄弟包各自依赖,但**不**反向
依赖任何一个,也**不**含任何它们的领域概念。

它由六条「缝」(seam)+ 一个 errors 缝构成,每条缝只装【极小且明显稳定】的机制。核心
import **零第三方 SDK、零网络、零运行时依赖**(`dependencies` 恒空),装上即可离线、确定性地
端到端跑。

## 解决什么

- **「选哪个实现」从改代码降为一个字符串。** `Registry` 把 `name -> factory` 解析成 `make(spec)`,
  并能经 entry-point 自动发现第三方实现——第三方装包即扩展,核心一行不改。
- **隐私安全的可观测性 by construction。** `InProcessPrivacyTraceSink` 在 `emit` 时直接**拒绝**任何
  携带正文(answer/value/text/content…)的载荷,把「trace 只记元数据」做成构造即保证,而非靠
  reviewer 自觉。
- **离线即可端到端跑。** 每条缝都带一个零网络、零 key、确定性的默认实现(`MockProvider` /
  `FakeQueue` / …),让测试可复现、demo 不依赖任何外部服务。
- **声明式 env 配置。** `load_from_env` 把 `PREFIX_*` 环境变量按字段类型读进一个 frozen dataclass。
- **跨缝统一的错误形状。** `CorespineError`(带稳定可 grep 的 `code` + `retryable`)+ `error_to_dict`
  把任意异常拍成可序列化 dict。
- **「实现 × 不变量」笛卡尔积的 conformance 基座。** `ConformanceSuite` 让「敢放手让第三方填广度、
  却让脊柱不变量烂不掉」成立——没过 conformance 的实现直接 CI 红,而非生产事故。

## 何时用 / 不用

**用 corespine 当你需要:**

- 一个 domain-neutral 的「选实现」注册表 + entry-point 扩展点(`Registry`);
- 一个会拒绝正文泄露的 trace 出口(`InProcessPrivacyTraceSink`);
- 一个离线确定性的 LLM 桩(`MockProvider`,OpenAI chat-completions 形状),用于测试 / demo;
- env→dataclass 的声明式配置(`load_from_env`);
- 一个同步内联的任务队列桩(`FakeQueue`)用于离线 / 测试;
- 一套 Protocol × 不变量的 conformance 机制(`ConformanceSuite` + `InvariantPack`);
- 一个家族统一的异常基类 + 错误归一(`CorespineError` / `error_to_dict`)。

**不要用 corespine 找:**

- **任何 RAG-特定功能**:chunking / retrieval / anti-fabrication / provenance —— 不在这里,也永远不会进。
- **任何 agent-特定功能**:MCP / A2A / 工具调用循环 / 编排 —— 不在这里。corespine 给的是 LLM 的
  通用 wire 形状(含 `tool_calls` 数据结构),但 **tool-use 循环本身归各 app**。
- **真实后端 / 生产实现**:Redis 队列、OpenAI/Anthropic 真实 provider、OTel sink —— 这些由各 app
  在自己的缝里经**可选 extra 延迟 import** 接入(见 [gotchas.md](gotchas.md) `lazy_extra_import`)。
  corespine 只给 Protocol + 离线默认。
- **具体业务不变量**:conformance harness 只是**机制**,具体不变量由各 app 自己绑(ADR 0001 D6)。

判据:这段代码换到一个完全不同的后端引擎里还成立吗?不成立就不属于 corespine。

## 核心概念

### 缝的元模式(家族统一)

每条缝都长一个样,这是理解 corespine 的钥匙:

```
Protocol(只定结构接口,绝不 import 任何 SDK)
   + 离线确定性默认实现(零网络 / 零 key / 可复现)
   + 工厂 / Registry(把「选实现」变成一个 spec 字符串或一次工厂调用)
   + 参数化 conformance(同一套不变量跑遍所有实现)
```

**机制,不是保证。** corespine 提供机制(conformance 基座、缝注册表、隐私 trace 形状);具体
**不变量由各 app 自己绑**。这里不出现任何具体业务不变量。

### 六条缝 + errors 缝

| 缝(模块) | Protocol / 核心类型 | 离线默认实现 | 一句话 |
|---|---|---|---|
| `seam/registry` | — | `Registry` + `lazy_extra_import` | `name->factory` 解析(归一)+ entry-point 自动发现(group `corespine.<seam>`)+ 未知 spec 列清可用名 |
| `observability/trace` | `TraceSink` | `InProcessPrivacyTraceSink` | 只记 code/计数/耗时;载荷含正文字段(见 `FORBIDDEN_KEYS`)即 raise `TraceError` |
| `llm/provider` | `LLMProvider` | `MockProvider` | OpenAI chat-completions 规范:`chat(messages, *, tools=None) -> ChatCompletion`;Mock 确定性、不伪造 tool_calls |
| `config/env` | — | — | `load_from_env`:`PREFIX_*` env → frozen dataclass(支持 str/int/float/bool 及 `X\|None`) |
| `queue/task_queue` | `TaskQueue` | `FakeQueue` | `enqueue` 同步内联执行 job、`get` 查 `JobStatus`;失败记进状态(不外抛) |
| `conformance/harness` | — | — | `ConformanceSuite` × `InvariantPack`:实现 × 不变量 笛卡尔积逐格执行(机制,无具体不变量) |
| `errors` | — | — | `CorespineError`(`code` + `retryable`)+ `error_to_dict`:任意异常 → 可序列化 dict |

三个 Protocol(`TraceSink` / `LLMProvider` / `TaskQueue`)都是 `@runtime_checkable`——可用
`isinstance(obj, TraceSink)` 做结构性检查。

### LLM 缝的规范 = OpenAI chat-completions 形状

对外唯一规范是 OpenAI chat completions 的形状:输入 OpenAI 风格 `messages`(`list[dict]`,带
`role` / `content` / 可带 `tool_calls` / `tool_call_id`)与 OpenAI function-tool 形状的 `tools`;输出
OpenAI 形状的 `ChatCompletion`(`choices[].message.{content, tool_calls[].function.{name, arguments}}`、
`finish_reason`、`usage`)。后端是 Anthropic 等非 OpenAI 模型时,由各 app 的适配器在内部转成
OpenAI 形状再吐出,用户无感。**注意:旧的 `complete(prompt) -> Completion` 已被移除**(见
[gotchas.md](gotchas.md))。

### 离线默认 vs 真实后端

核心 import 零 SDK、`dependencies` 恒空。真实后端(Redis / OpenAI SDK / OTel / 数据库)由各 app
在自己的缝里经**可选 extra 延迟 import** 接入,`lazy_extra_import` 在缺依赖时把裸 `ImportError`
翻译成 `pip install <pkg>[<extra>]` 的友好提示。corespine 的默认路径永远离线、确定性、可复现。
