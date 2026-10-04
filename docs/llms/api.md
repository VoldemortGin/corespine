# corespine API 参考(给 AI 消费者)

> `corespine.__all__` 每个导出的【真实签名】+ 契约 + 一句话,按缝分节。所有签名经
> `inspect.signature` 核对、所有行为经实跑核对(见 [recipes.md](recipes.md))。心智模型见
> [overview.md](overview.md),易错点见 [gotchas.md](gotchas.md)。
>
> 全部从顶层包导入:`from corespine import Registry, MockProvider, ...`。
> `corespine.__version__` 取已安装包版本(当前 0.5.1)。`__all__` 共 62 项(含 `__version__`)。
> 另有未进 `__all__` 的子模块公开面:`corespine.conformance.packs`(可复用 InvariantPack,见 conformance 节)。
> 尚未发版的行为变化见仓根 `CHANGELOG.md` 的 `[Unreleased]`。

## seam —— `corespine.seam.registry`

### `class Registry(Generic[T])`

一条缝的 `name -> factory` 注册表 + spec 解析 + entry-point 自动发现。

```python
Registry(seam: str) -> None          # seam 名既用于报错,也用于 entry-point group "corespine.<seam>"

# 属性(只读 property)
registry.seam   -> str               # 缝名(构造时传入)
registry.group  -> str               # entry-point 发现使用的 group:f"corespine.{seam}"

# 方法
register(name: str, factory: Factory[T]) -> None
    # 登记一个内置实现;名字归一后入表(同名后者覆盖)。factory 签名:(**kwargs) -> T。

make(spec: str, /, **kwargs: Any) -> T
    # 把 spec 解析到工厂并构造实例。spec 是【仅位置】参数,工厂自己的 spec= 关键字可经 kwargs 透传。
    # 解析顺序:内置注册 -> entry-point 发现 -> 抛 SeamError(ValueError 子类,code="seam.unknown")。
    # spec 归一:去首尾留白 + 转小写 + 连字符/空格统一为下划线("In-Process"=="in_process")。
    # kwargs 原样透传给工厂。未知 spec 抛 SeamError(同时是 ValueError)并列清【当前全部可用名】。

names() -> list[str]
    # 当前全部可用名:内置 + entry-point 发现,去重后按字典序。
```

契约要点:`Factory[T] = Callable[..., T]`(任意 kwargs → 实例)。内置注册**优先于** entry-point
(同名时内置胜出,便于测试覆盖)。entry-point 发现是**延迟**的——只在 `make` / `names` 时才扫。

### `lazy_extra_import(module: str, *, pkg: str, extra: str) -> Any`

延迟 import 一个可选依赖;缺失时把裸 `ImportError` 翻译成
`缺少可选依赖 '<module>':请先 \`pip install <pkg>[<extra>]\` 再重试。` 支撑「离线精简默认 + 可选
重依赖」范式——核心默认路径零重依赖,真实后端 SDK 仅在选用时才 import。返回成功 import 的模块对象。

## observability —— `corespine.observability.trace`

### `class TraceSink(Protocol)`(`@runtime_checkable`)

trace 出口的最小结构接口。

```python
emit(self, code: str, **fields: object) -> None    # 发射一条事件(code + 非敏感字段)
```

### `class TraceExporter(Protocol)`(`@runtime_checkable`)

trace 导出面:`export(self, event: TraceEvent) -> None`。只会收到【已通过隐私校验】的事件。真实导出
后端(OTel / Langfuse)由各 app 经可选 extra 接入,核心只给进程内收集器 `InProcessTraceExporter`
(`.events -> list[TraceEvent]`)。

### `class InProcessPrivacyTraceSink`

进程内默认 `TraceSink`:把事件存进内存列表,且**拒绝**任何携带受限内容的载荷。

```python
InProcessPrivacyTraceSink(*, exporters: Sequence[TraceExporter] | None = None) -> None

emit(self, code: str, **fields: object) -> None
    # 先调 enforce_trace_privacy(fields)(递归,含嵌套容器),命中禁词键 / 无法检查的对象即抛
    # TraceError、不记录不扇出;通过后深拷贝快照、本地记录,再给每个 exporter 一份独立深拷贝。
    # 单个 exporter 抛错不影响其余 exporter 与调用方,只累计 exporter_failures。

codes(self) -> list[str]                            # 已记录事件的 code 序列(按记录顺序)
add_exporter(self, exporter: TraceExporter) -> None # 追加扇出目标

# 属性(只读 property)
events -> list[TraceEvent]                          # 已记录事件的只读副本(外部改动不污染内部)
exporter_failures -> int                            # exporter 抛错累计次数(仅计数,不含内容)
```

### `enforce_trace_privacy(fields: Mapping[str, object]) -> None`

公开的递归隐私门(ADR 0006)。递归检查 Mapping 的键、dataclass / NamedTuple 的字段名、list /
tuple / set 的元素;任意一层键命中 `FORBIDDEN_KEYS`(小写后精确匹配)、遇到无法检查的非标量对象、
容器层数(含顶层)超过 `MAX_TRACE_DEPTH`(8)、访问节点数超过 `MAX_TRACE_NODES`(10000)都抛
`TraceError`(fail-closed)。报错只给路径(如 `calls[0].prompt`),不含取值。自定义 sink 先调它即
经同一条隐私门。

### `class TraceEvent`(frozen dataclass)

```python
TraceEvent(code: str, fields: dict[str, object] = {})   # 一条被记录的 trace(只读)
```

`fields` 在构造时被拷贝成只读 `dict` 子类:`==` / `json.dumps` 照常,就地修改抛 `TypeError`。

### `class TraceError(CorespineError)`

trace 载荷违反隐私约定时抛出。`code = "trace.forbidden"`,`retryable = False`。继承 `CorespineError`。

### `FORBIDDEN_KEYS: frozenset[str]`

禁止出现在 trace 载荷里的键(归一为小写后比对,命中任一即拒整条;嵌套同样检查):
`{answer, body, chunk, chunk_text, completion, content, contents, messages, output, prompt,
question, raw_text, text, user_prompt, value}`。要记正文规模请记计数(如 `answer_chars`)。

## llm —— `corespine.llm.provider`

> 规范 = OpenAI chat-completions 形状。**只有 `chat`,没有 `complete`**(见 [gotchas.md](gotchas.md))。

### `class LLMProvider(Protocol)`(`@runtime_checkable`)

```python
chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> ChatCompletion
    # 给 OpenAI 形状的 messages(可选 OpenAI function-tool 形状的 tools),拿回 OpenAI ChatCompletion。
    # tools 是关键字参数(* 之后),必须 tools=... 传。
```

### `class MockProvider`

确定性离线 provider:零网络、零 key,输出由 messages 派生、可复现。

```python
MockProvider(*, prefix: str = "mock") -> None       # prefix 仅关键字参数

chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> ChatCompletion
    # 回显最后一条 user 消息,附一段由【整段对话】计算的稳定 12 位 hex 指纹。
    # 同一对话恒定产出同一文本(纯函数);finish_reason 恒为 "stop"。
    # 【绝不伪造 tool_calls】——离线默认不假装会 function-calling(返回 message.tool_calls 为 None)。
```

### 响应数据类(均 frozen dataclass,字段与 OpenAI 完全一致)

```python
ChatCompletion(
    choices: tuple[Choice, ...],                    # 必填
    usage: Usage | None = None,
    model: str = "", id: str = "", created: int = 0, object: str = "chat.completion",
)
Choice(index: int, message: ResponseMessage, finish_reason: str = "stop")     # index/message 必填
ResponseMessage(role: str = "assistant", content: str | None = None, tool_calls: tuple[ToolCall, ...] | None = None)
ToolCall(id: str, function: FunctionCall, type: str = "function")             # id/function 必填
FunctionCall(name: str, arguments: str = "{}")                                # arguments 是 JSON 字符串(与 OpenAI 一致)
Usage(prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0)
```

取文本的惯用路径:`completion.choices[0].message.content`。

### 流式:`StreamingLLMProvider`(叠加协议)与 chunk 类型

```python
class StreamingLLMProvider(Protocol):   # @runtime_checkable,独立于 LLMProvider(不破坏只实现 chat 的实现者)
    def stream_chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> Iterator[ChatCompletionChunk]: ...

ChatCompletionChunk(choices: tuple[ChunkChoice, ...], model: str = "", id: str = "", created: int = 0, object: str = "chat.completion.chunk")
ChunkChoice(index: int, delta: ChoiceDelta, finish_reason: str | None = None)
ChoiceDelta(role: str | None = None, content: str | None = None)
```

`MockProvider` 同时满足两者:首块 role、中间块 content 片段、末块 `finish_reason="stop"`,所有 content
片段拼接逐字等于 `chat()` 的内容。用 `isinstance(p, StreamingLLMProvider)` 探测能力。

### `class RateLimitedProvider`(`corespine.llm.rate_limit`)

```python
RateLimitedProvider(inner: LLMProvider, *, tokens_per_minute: int, window_seconds: float = 60.0)
chat(messages, *, tools=None) -> ChatCompletion      # 超出每分钟 token 预算时阻塞等待(滑动窗口,软限流)
```

主动客户端 TPM 限流,与 SDK 的被动重试互补。`tokens_per_minute` / `window_seconds` 非正抛
`ValueError`。**流式能力如实透传**:inner 满足 `StreamingLLMProvider` 时结果也满足它(带限流计量
的 `stream_chat`;chunk 无 `usage` 时按约 4 字符 / token 估算);inner 只会 `chat` 时
`isinstance(x, StreamingLLMProvider)` 为 False。两种结果都是 `RateLimitedProvider` 的实例。

## config —— `corespine.config.env`

### `load_from_env(cls: type[T], *, prefix: str, env: Mapping[str, str] | None = None) -> T`

按 dataclass 字段从 `PREFIX_*` 读取并构造实例。

- `cls` 必须是 dataclass(否则 `TypeError`)。
- 每个字段从 `env_key(prefix, field)` 读取;命中则按字段注解类型转换,缺失则用 dataclass 字段默认值。
- 字段**无默认值且 env 缺失** → 抛 `ConfigError`(`ValueError` 子类,把缺失的 env 名报清楚)。
- 支持类型:`str` / `int` / `float` / `bool` / `pathlib.Path` / `list[str]`(逗号分隔,去空白、丢空项),
  及其 `X | None` 形式。bool:`{1,true,yes,on}` 为真、`{0,false,no,off,""}` 为假(大小写不敏感)。
  `int` / `float` / `Path` / `list[str]` 的 `X | None` 遇空串得 `None`(`str | None` / `bool | None` 保持旧行为)。
- 转型失败 / 不支持的注解(如 `int | str`、`list[int]`、`object`,仅当对应 env 存在时)抛
  `ConfigError`,消息只含环境变量名、**不含取值**(取值可能是密钥)。`init=False` 字段不读 env。
- `env` 可注入(默认读 `os.environ`)——测试传入自己的 mapping,不碰进程环境。

### `env_key(prefix: str, field_name: str) -> str`

字段名 → 环境变量名:`f"{prefix.rstrip('_')}_{field_name.upper()}"`;空前缀不加下划线
(`env_key("", "port") == "PORT"`)。例:`env_key("APP", "port") == "APP_PORT"`。

## queue —— `corespine.queue.task_queue`

### `class TaskQueue(Protocol)`(`@runtime_checkable`)

```python
enqueue(self, func: JobFunc | str, payload: dict[str, Any], *, job_id: str | None = None) -> str
get(self, job_id: str) -> JobStatus | None
```

`JobFunc = Callable[[dict[str, Any]], dict[str, Any]]`——job 函数签名约定为 `fn(payload: dict) -> dict`。

### `class FakeQueue`

同步内存队列:`enqueue` 时**内联执行** job(离线 / 测试用,无需任何外部服务)。

```python
FakeQueue() -> None                                 # 无参构造

enqueue(self, func: JobFunc | str, payload: dict[str, Any], *, job_id: str | None = None) -> str
    # func 可传可调用对象,也可传点路径字符串(如 "pkg.mod.fn",末段为可调用对象)。
    # 内联执行 func(payload):成功记 status="finished" + result;
    # 抛异常则【捕获】记 status="failed" + error={"type","message"}(不外抛)。
    # 幂等:显式 job_id 且已知 -> 直接返回该 id,不重跑。返回 job id(未给则取 12 位 hex)。

get(self, job_id: str) -> JobStatus | None          # 按 id 查状态;未知 id 返回 None
```

### `class JobStatus`(dataclass,非 frozen)

```python
JobStatus(id: str, status: str, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None)
    # status 取值:"finished"(成功,看 result) / "failed"(失败,看 error)。
```

## conformance —— `corespine.conformance.harness`

### `class InvariantPack(Generic[T])`(frozen dataclass)

一组具名不变量。app 用它装自己的保证;harness 只负责跑。

```python
InvariantPack(name: str, invariants: dict[str, Invariant[T]] = {})

add(self, name: str, check: Invariant[T]) -> InvariantPack[T]    # 登记一个不变量;返回自身(可链式);同名重复抛 ValueError
names(self) -> list[str]                                         # 全部不变量名
```

`Invariant[T] = Callable[[T], None]`——拿到一个【新构造的实现实例】,通过则正常返回、违反则抛异常。
检查应只读、只验外部可观测行为。

### `class ConformanceSuite(Generic[T])`

把 实现注册表 × 不变量包 绑成可执行 / 可参数化的套件。

```python
ConformanceSuite(implementations: dict[str, Factory[T]], pack: InvariantPack[T]) -> None
    # implementations: 名字 -> 无参工厂 Callable[[], T](每格各新建实例,杜绝状态串味)。
    # implementations 为空、或 pack 没有任何不变量 -> 抛 ValueError(空包会静默全绿)。

cases(self) -> list[tuple[str, str]]                # 全部 (实现名, 不变量名) 组合
ids(self) -> list[str]                              # 与 cases() 对齐的可读 id,形如 "impl-invariant"(与 parametrize_kwargs 的 ids 相同)
check(self, impl: str, invariant: str) -> None      # 跑单格:新建该实现实例,执行该不变量(失败原样抛)
run(self) -> list[CaseResult]                       # 跑全部格子并收集结果;Exception / pytest.fail 记失败、pytest.skip 记 skipped(passed=False),KeyboardInterrupt / SystemExit / pytest.exit 原样外抛
passed(self) -> bool                                # 便捷:全部格子是否通过(跳过不算通过)

parametrize_kwargs(self) -> dict[str, object]
    # 产出可直接喂 pytest.mark.parametrize(**...) 的 kwargs(core 不 import pytest,只返回纯数据)。
    # 三键:argnames="case"(单形参,值是已绑定该格的零参 thunk);
    #       argvalues=每格一个 thunk(调用即跑 check,违反则原样抛);
    #       ids=与 argvalues 对齐的可读 id,形如 "impl-invariant"。
    # 消费者侧:
    #     @pytest.mark.parametrize(**suite.parametrize_kwargs())
    #     def test_conformance(case): case()
```

### `class CaseResult`(frozen dataclass)

```python
CaseResult(impl: str, invariant: str, passed: bool, error: str | None = None, skipped: bool = False)
    # 单个 (实现 × 不变量) 格子的执行结果;error 形如 "AssertionError: <msg>"(passed=False 时)。
```

### 可复用的 InvariantPack —— `corespine.conformance.packs`

下游一行绑定:`ConformanceSuite({"mine": MySink}, TRACE_PRIVACY_PACK)`。

| 常量 | 适用的实现 | 内容 |
|---|---|---|
| `TRACE_PRIVACY_PACK: InvariantPack[TraceSink]` | 任意 `TraceSink` | 顶层 / 嵌套禁词键被拒、不可检查对象被拒、合法元数据放行 |
| `QUEUE_PACK: InvariantPack[TaskQueue]` | 【同步内联执行】的 `TaskQueue`(如 `FakeQueue`;异步后端如 RQ 不适用:要求 enqueue 后 `get` 立即终态、闭包 job 在当前进程跑) | 同步执行记结果、失败入状态不外抛、显式 job_id 幂等、未知 id 为 None、点路径 func |
| `LLM_PROVIDER_PACK: InvariantPack[LLMProvider]` | 任意 `LLMProvider` | OpenAI 形状、usage 自洽、不改写入参 |
| `STREAMING_PROVIDER_PACK: InvariantPack[StreamingLLMProvider]` | 【确定性】流式 provider | 末块有 finish_reason、流式拼接 == 非流式 |

blob / credential / trigger 的契约包目前在各自测试里(`tests/test_blob.py` 等),尚未上提。

## errors —— `corespine.errors`

### `class CorespineError(Exception)`

Spine 家族统一异常基类。

```python
CorespineError(message: str = "", *, code: str | None = None, retryable: bool | None = None, **context: object)
    # code/retryable:既可在子类作类属性覆盖,也可构造时实例级覆盖(仅显式传入才覆盖)。
    # 任意 kwargs 收进 self.context(普通 dict),携带可序列化诊断上下文。

# 类属性默认
code: str = "error"
retryable: bool = False

to_dict(self) -> dict[str, object]
    # 归一为可被 json.dumps 序列化的 dict:
    # {"type": 类名, "code": code, "message": str(self), "retryable": bool, "context": dict}
    # context 里的 Path -> str、set -> 排序 list、未知对象 -> str,保证可 json.dumps;self.context 不变。
```

### `error_to_dict(exc: BaseException) -> dict[str, object]`

把【任意】异常归一为可序列化 dict。`CorespineError` 走其 `to_dict()`;其余异常给同形状的保守默认
(`code="error"`、`retryable=False`、`context={}`)。

### 内置子类

```python
class ConfigError(CorespineError, ValueError):
    code = "config.invalid"  # 配置非法(缺必填、转型失败、不支持的注解);context 含 key(env 名)


class SeamError(CorespineError, ValueError):
    code = "seam.unknown"  # 未知 spec / 缝构造失败;context 含 seam / spec


class ProviderError(CorespineError):
    code = "provider.error"  # provider / 外部服务调用失败的统一边界异常(网络 / 超时 / API 错误归一到此)
```

`ConfigError` / `SeamError` 同时继承 `ValueError`:核心在缺必填 env / 转型失败(`load_from_env`)与未知
spec(`Registry.make`、`make_*`)处抛它们,既有的 `except ValueError` 不受影响。`ProviderError`
只包裹 SDK / 外部服务的网络类异常,程序错误(KeyError / TypeError 等)照常向上抛(ADR 0002)。

## blob —— `corespine.blob.store`

```python
class BlobStore(Protocol):                      # @runtime_checkable
    def put(self, key: str, data: bytes) -> None        # 写入 / 覆盖;非 bytes-like(如 5、[1,2])抛 TypeError
    def get(self, key: str) -> bytes                    # 缺失抛 BlobNotFound
    def delete(self, key: str) -> None                  # 幂等
    def exists(self, key: str) -> bool                  # 空字节也算存在

MemoryBlobStore()                               # 进程内 dict
FileSystemBlobStore(root: str | Path)           # key 经 sha256 摊平成文件名,防路径穿越
make_blob_store(spec: str, /, **kwargs) -> BlobStore    # "memory" / "filesystem"(需 root=...);BLOB_REGISTRY 为其注册表
BlobError(CorespineError)  code="blob.error";  BlobNotFound(BlobError)  code="blob.not_found"
```

## credential —— `corespine.credential.store`

```python
class CredentialStore(Protocol):                # @runtime_checkable;namespace × name -> str 秘密
    def set(self, namespace: str, name: str, value: str) -> None
    def get(self, namespace: str, name: str) -> str     # 缺失抛 CredentialNotFound
    def delete(self, namespace: str, name: str) -> None # 幂等
    def list(self, namespace: str) -> list[str]         # 字典序

MemoryCredentialStore()                                  # 进程内,零落地
InsecureLocalCredentialStore(path: str | Path)           # 明文 JSON + 0600(命名即警示,不是加密)
EncryptedFileCredentialStore(path: str | Path, *, key: bytes | str)   # Fernet,需 corespine[crypto];.generate_key() -> bytes
make_credential_store(spec: str, /, **kwargs) -> CredentialStore      # "memory" / "insecure_local" / "encrypted_file";CREDENTIAL_REGISTRY
CredentialError(CorespineError)  code="credential.error"
CredentialNotFound(CredentialError)  code="credential.not_found"
```

文件损坏 / 形状非法抛 `CredentialError(code="credential.corrupt")`,错 key 抛 `credential.bad_key`
(Fernet 无法区分错 key 与密文损坏);异常链已断开、不含明文。进程内锁**不防多进程**同时写同一文件。
秘密值永不出现在 repr / str / 异常 / trace。

## trigger —— `corespine.trigger.source`

```python
class TriggerSource(Protocol):                  # @runtime_checkable;拉模式
    def poll(self) -> list[TriggerEvent]               # 返回尚未观测过的事件,取出即消费;空闲返回 []

TriggerEvent(trigger: str, id: str, payload: dict[str, Any] = {}, at: float = 0.0)   # frozen;repr 不含 payload
ManualTrigger(*, trigger: str = "manual", namespace: str = "")   # fire(payload=None, *, at=0.0) -> TriggerEvent 入队;poll 排空
ScheduleTrigger(*, interval_s: float, now_fn: Callable[[], float], trigger: str = "schedule",
                start_at: float | None = None, max_catchup: int | None = 100)
make_trigger(spec: str, /, **kwargs) -> TriggerSource    # "manual" / "schedule";TRIGGER_REGISTRY
TRIGGER_MANUAL = "manual";  TRIGGER_SCHEDULE = "schedule";  DEFAULT_MAX_CATCHUP = 100   # DEFAULT_MAX_CATCHUP 仅在 corespine.trigger / corespine.trigger.source
```

- `ScheduleTrigger` 时钟经 `now_fn` 注入(绝不直接 `time.time()`)。一次 `poll()` 最多补发 `max_catchup`
  个 tick:积压更多时只保留【最近】的,更早的被合并跳过且之后不再涌出;跳过数见 `.skipped_ticks`,
  本次有跳过时首个事件 payload 带 `skipped_ticks`。传 `max_catchup=None` 恢复无限补发(大跳时线性耗时)。
- `ManualTrigger` 的事件 id 只由 (trigger, namespace, 序号) 决定:同实例内唯一、可重放;跨实例 / 重启
  同序号会重复,需不撞时传不同 `namespace`。
