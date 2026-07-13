# corespine

Spine 家族的**薄共享核**(见 [ADR 0001](../docs/adr/0001-spine-family-boundaries-and-dependency-direction.md))。
只装 **domain-neutral 的底层原语**——既不属于 RAG 也不属于 agent 的稳定地基,被 `ragspine` /
`spineagent` 兄弟包各自依赖,**不**含任何它们的领域概念。

> 刻意地薄。按证据(rule of three)增长,不预先造框架。详见 [`CLAUDE.md`](CLAUDE.md) 宪章。
>
> **🤖 给 AI / LLM:** 用本库前先读 [`llms.txt`](llms.txt)(精简索引)与 [`docs/llms/`](docs/llms/)(完整 API / recipes / 陷阱);`pip install` 后这些文档随包位于 `site-packages/corespine/_llms/`。

## 缝的元模式

每条缝都长一个样,核心 import 零 SDK、离线可跑:

**Protocol + 离线确定性默认 + `Registry` / `make_*` 工厂 + 参数化 conformance**

## 里面有什么

| 模块 | 原语 |
|---|---|
| `seam/registry.py` | `Registry`:name→factory 解析(大小写/留白/连字符不敏感)+ entry-point 自动发现(`corespine.<seam>` group)+ 未知 spec 列清可用名 + `lazy_extra_import`(缺 extra 给"pip install …"友好提示) |
| `observability/trace.py` | `TraceSink` 协议 + `InProcessPrivacyTraceSink`:只记 code/计数/耗时,**拒绝**任何携带正文(answer/value/text/content…)的载荷 |
| `llm/provider.py` | `LLMProvider` 协议 + 离线确定性 `MockProvider`(零网络、零 key、可复现) |
| `config/env.py` | `load_from_env`:把 `PREFIX_*` 环境变量读进一个 frozen dataclass(范式同 ragspine `from_env`) |
| `queue/task_queue.py` | `TaskQueue` 协议 + `FakeQueue`:同步内联执行 + 记录,离线/测试用 |
| `conformance/harness.py` | `ConformanceSuite` × `InvariantPack`:把"实现 × 不变量"绑成笛卡尔积逐格执行(**机制**,具体不变量由各 app 自己绑) |
| `credential/store.py` | `CredentialStore` 协议(namespace × name → secret)+ `Memory` / `InsecureLocal`(明文 + 0600)零依赖默认 + `EncryptedFile`(经 `[crypto]` extra 用 Fernet);秘密值永不入 repr/str/异常/trace |

## 本地开发(始终从包根)

```bash
uv venv .venv
VIRTUAL_ENV="$(pwd)/.venv" uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/python -c "import corespine"
```

## 30 秒上手

```python
from corespine import Registry, MockProvider, InProcessPrivacyTraceSink, FakeQueue

# 缝:一个 spec 选实现(大小写/留白不敏感;找不到列清可用名;还能 entry-point 自动发现)
reg: Registry = Registry("llm")
reg.register("mock", lambda **kw: MockProvider(**kw))
provider = reg.make("  MOCK ")
# OpenAI chat-completions 规范:messages 进,OpenAI 形状的 ChatCompletion 出(确定性可复现)
out = provider.chat([{"role": "user", "content": "hello"}])
print(out.choices[0].message.content)

# 隐私 trace:只记元数据;塞正文会被直接拒绝(raise TraceError)
sink = InProcessPrivacyTraceSink()
sink.emit("retrieve", count=3, took_ms=12)

# 任务队列:同步内联执行
q = FakeQueue()
jid = q.enqueue(lambda p: {"doubled": p["n"] * 2}, {"n": 21})
print(q.get(jid).result)                 # {'doubled': 42}
```
