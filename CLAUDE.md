# CLAUDE.md — corespine(宪章)

Spine 家族的 AI / 人类协作契约。先读家族 `../README.md` 与
`../docs/adr/0001-spine-family-boundaries-and-dependency-direction.md`,本文件是 corespine 的操作指南。

## 这是什么

**corespine —— Spine 家族的「薄」共享核**(ADR 0001 D3/D5)。只装 **domain-neutral 的底层原语**:
那些**既不属于 RAG、也不属于 agent** 的稳定地基。它被 `ragspine` / `spineagent` 等兄弟包各自依赖,
但**不**反向依赖任何一个,也**不**含任何它们的领域概念。

## 宪章(不可违背)

- **刻意地薄。** corespine 只放"极小且明显稳定"的原语。新增一块**必须先有证据**——
  rule of three:当**两个以上**真实消费者都被证明在重复同一块稳定面,才把**恰好那块**提上来,
  并记一条新 ADR。**痛了再抽,带着证据抽。** 不预先造大而全的框架。
- **零领域泄漏。** 任何 RAG-特定(chunking / retrieval / anti-fabrication / provenance)或
  agent-特定(MCP / A2A / 工具循环 / 编排)的代码**一律不准进**。判据:这段代码换到一个完全
  不同的后端引擎里还成立吗?不成立就不属于 corespine。
- **离线可跑、import-clean、零重依赖默认路径。** 核心只用标准库;真实后端(SDK / 数据库 / Redis)
  经**可选 extra 延迟 import**,由各 app 在自己的缝里接,corespine 的 `dependencies` 永远为空。
- **机制,不是保证。** corespine 只提供**机制**(conformance 基座、缝注册表、隐私 trace 形状);
  具体**不变量由各 app 自己绑**(ADR 0001 D6)。这里不准出现任何具体业务不变量。

## 缝的元模式(家族统一)

每条缝都长一个样:**Protocol + 离线确定性默认 + `make_*` / Registry 工厂 + 参数化 conformance**。
core 只 import Protocol,**绝不** import 任何 SDK。

## 模块地图(按文件夹定位)

```
src/corespine/
  seam/registry.py         Registry(name->factory) + make(spec) + entry-point 发现 + lazy_extra_import
  blob/store.py            BlobStore 协议(key->bytes)+ Memory/FileSystem 默认 + make_blob_store 工厂
  observability/trace.py   TraceSink 协议 + InProcessPrivacyTraceSink(只记 code/计数/耗时,拒绝正文)
                           + TraceExporter 扇出协议(导出面与本地面等宽,校验后才扇出)
  llm/provider.py          LLMProvider 协议 + StreamingLLMProvider 叠加协议 + 离线确定性 MockProvider
  config/env.py            load_from_env:PREFIX_* env -> frozen dataclass(范式同 ragspine from_env)
  queue/task_queue.py      TaskQueue 协议 + 同步内联 FakeQueue 默认
  conformance/harness.py   ConformanceSuite × InvariantPack:实现 × 不变量 笛卡尔积(机制,无具体不变量)
```

## 跑(始终从包根)

```bash
uv venv .venv
VIRTUAL_ENV="$(pwd)/.venv" uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # 期望 GREEN
.venv/bin/python -c "import corespine"  # 期望 import-clean
```

## 约定

- Python **3.10+** 类型注解;import 顺序 **stdlib > 三方 > 本地**;简体中文 docstring/注释,匹配家族风格。
- **TDD**——测试即规格;**最小改动**——只改需求要求的部分。
- **深层、按领域分组**的布局:文件路径先定位职责,再读文件名。
