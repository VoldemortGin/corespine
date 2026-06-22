# corespine 陷阱(给 AI 消费者)

> 真实易错点,按踩坑频率排序。每条给【错误写法】→【正确写法】→ 原因。配方见 [recipes.md](recipes.md),
> 签名见 [api.md](api.md)。

## 1 · LLM 缝是 `chat`,不是 `complete`(最常见)

LLM 缝最近被重构为 **OpenAI chat-completions 形状**。旧的 `complete(prompt) -> Completion` 已被
**移除**——它在当前代码里**根本不存在**(`MockProvider.complete` 是 `AttributeError`,`Completion`
不在 `corespine.__all__` 里)。

```python
# ❌ 旧 API,已移除,会报错
out = provider.complete("hello")            # AttributeError: ... has no attribute 'complete'
from corespine import Completion            # ImportError: cannot import name 'Completion'

# ✅ 当前 API:chat(messages) -> ChatCompletion
out = provider.chat([{"role": "user", "content": "hello"}])
text = out.choices[0].message.content       # 取文本:choices[0].message.content
```

- 输入是 OpenAI 风格的 `messages: list[dict]`(`role` / `content` / 可带 `tool_calls` / `tool_call_id`),
  **不是**一个裸 prompt 字符串。
- `tools` 是**关键字参数**(在 `*` 之后):`chat(messages, tools=[...])`,不能位置传。
- `MockProvider` **绝不伪造 tool_calls**:离线默认不假装会 function-calling,`message.tool_calls` 恒为
  `None`。要真工具调用得接真实 provider。
- 导出的响应类型是 `ChatCompletion / Choice / ResponseMessage / ToolCall / FunctionCall / Usage`
  (全是 OpenAI 形状)。**没有** `Completion`。

## 2 · TraceSink 拒绝正文字段(隐私 by construction)

`InProcessPrivacyTraceSink.emit` 会扫描载荷键,命中 `FORBIDDEN_KEYS` 就**直接抛 `TraceError` 且不
记录**——不是警告、不是静默丢弃,而是抛异常。

```python
# ❌ 携带正文字段 -> raise TraceError(code="trace.forbidden"),整条不记录
sink.emit("answer.made", content="模型答案正文…")
sink.emit("retrieve", text="chunk 正文…")
sink.emit("score", value=0.97)              # 连 "value" 也被禁!

# ✅ 只记非敏感元数据:code / 计数 / 耗时 / 布尔标志
sink.emit("answer.made", char_count=128, took_ms=42)
sink.emit("retrieve", count=3, hit=True)
sink.emit("score", confidence_bucket="high")   # 换个不在禁词表的键名
```

被禁的键(归一为小写后比对):`answer / body / chunk / chunk_text / completion / content / prompt /
text / value`。`"score"` 里含 "value" 子串不会误伤——比对的是**完整键名**,不是子串。`TraceError`
继承 `CorespineError`,可按 `code == "trace.forbidden"` 统一捕获。

## 3 · Registry spec 名称会被归一(别依赖原样大小写)

`make(spec)` 与 `register(name)` 都先**归一**:去首尾留白 + 转小写 + 把连字符 / 空格统一成下划线。

```python
reg.register("In-Process", factory)
reg.make("in_process")    # ✅ 命中:"In-Process" 与 "in_process" 归一到同一个键
reg.make("  IN PROCESS ") # ✅ 命中:同上
reg.make("inProcess")     # ❌ 不命中:驼峰里没有分隔符,归一后是 "inprocess" ≠ "in_process"
```

- 同名重复 `register` 后者覆盖前者。
- **内置注册优先于 entry-point 发现**:同名时内置胜出(便于测试覆盖第三方实现)。
- 未知 spec 抛标准库 **`ValueError`**(不是 `SeamError`),消息里列清当前全部可用名。
- `names()` 返回【内置 + entry-point 发现】去重排序后的全集;entry-point 发现是延迟的,只在
  `make` / `names` 时才扫(import corespine 时不付代价)。

## 4 · 真实后端走可选 extra + `lazy_extra_import`(核心永远不背 SDK)

corespine 核心 `dependencies` 恒空,只用标准库。真实后端(Redis / OpenAI SDK / OTel / 数据库)**不在
corespine 里**——由各 app 在自己的缝里声明可选 extra 并**延迟 import**。

```python
# ✅ adapter 工厂里延迟 import:只在真正构造该 adapter 时才 import,缺依赖给友好提示
from corespine import lazy_extra_import

def make_redis_queue(**kwargs):
    redis = lazy_extra_import("redis", pkg="myadapter", extra="redis")  # 缺 redis -> 友好 ImportError
    return RedisQueue(redis.Redis(**kwargs))
```

未装 `redis` 时:`ImportError: 缺少可选依赖 'redis':请先 \`pip install myadapter[redis]\` 再重试。`
——而不是裸 `ModuleNotFoundError` 让人自己猜该装哪个 extra。**不要 import corespine 后就指望它带了
Redis/OpenAI**——它没有,也永远不会有。

## 5 · 核心 `dependencies` 恒空 —— 别往核心加重依赖

宪章(ADR 0001 D5):`pyproject.toml` 的 `[project].dependencies` **永远为空**,默认路径零重依赖、
离线确定性、import-clean。这意味着:

- `import corespine` 不需要联网、不需要任何 API key、不会拉起任何外部进程;
- 所有「真实后端」是各 app 经 extra 接的,不是 corespine 的职责;
- corespine 是**机制,不是保证**:conformance harness 不含任何具体业务不变量,具体不变量由各 app
  用自己的 `InvariantPack` 绑(ADR 0001 D6)。别在 corespine 里找 RAG/agent 的领域功能——一律不在这。

## 6 · 其它易错的小点

- **`MockProvider(prefix=...)` 的 `prefix` 是关键字参数**(`*` 之后):`MockProvider("m")` 报错,得
  `MockProvider(prefix="m")`。
- **`ConformanceSuite` 的 `implementations` 是「无参工厂」`Callable[[], T]`**,不是实例:传 `Counter`
  (类本身可无参构造)而非 `Counter()`。每格各新建实例以杜绝状态串味。空 implementations 抛 `ValueError`。
- **`InvariantPack` 的不变量「通过则返回 None、违反则抛异常」**,不是返回 bool。`return c.add(3) == 3`
  是错的(返回 bool 但从不抛),要 `if c.add(3) != 3: raise AssertionError(...)`。
- **`load_from_env` 的 `cls` 必须是 dataclass**,否则 `TypeError`;无默认值字段缺 env 抛 `ValueError`;
  bool 取值不在真 / 假词表抛 `ValueError`。
- **`FakeQueue` 的 job 失败不外抛**——被捕获记进 `JobStatus.error`(`status="failed"`)。要判成败看
  `get(jid).status` 是 `"finished"` 还是 `"failed"`,而不是靠 try/except。
- **`error_to_dict` 吃 `BaseException`**:`CorespineError` 走其 `to_dict()`,其余异常给保守默认
  (`code="error"`、`retryable=False`)。
