# corespine API 参考(给 AI 消费者)

> `corespine.__all__` 每个导出的【真实签名】+ 契约 + 一句话,按缝分节。所有签名经
> `inspect.signature` 核对、所有行为经实跑核对(见 [recipes.md](recipes.md))。心智模型见
> [overview.md](overview.md),易错点见 [gotchas.md](gotchas.md)。
>
> 全部从顶层包导入:`from corespine import Registry, MockProvider, ...`。
> `corespine.__version__ == "0.0.1"`。`__all__` 共 28 项(含 `__version__`)。

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

make(spec: str, **kwargs: Any) -> T
    # 把 spec 解析到工厂并构造实例。解析顺序:内置注册 -> entry-point 发现 -> 抛 ValueError。
    # spec 归一:去首尾留白 + 转小写 + 连字符/空格统一为下划线("In-Process"=="in_process")。
    # kwargs 原样透传给工厂。未知 spec 抛 ValueError 并列清【当前全部可用名】。

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

### `class InProcessPrivacyTraceSink`

进程内默认 `TraceSink`:把事件存进内存列表,且**拒绝**任何携带受限内容的载荷。

```python
InProcessPrivacyTraceSink() -> None                 # 无参构造

emit(self, code: str, **fields: object) -> None
    # 先扫 fields 键,命中 FORBIDDEN_KEYS(归一为小写后比对)立即抛 TraceError、不记录;
    # 通过校验的事件以 TraceEvent 追加到内部列表。隐私 by construction。

codes(self) -> list[str]                            # 已记录事件的 code 序列(按记录顺序)

# 属性(只读 property)
events -> list[TraceEvent]                          # 已记录事件的只读副本(外部改动不污染内部)
```

### `class TraceEvent`(frozen dataclass)

```python
TraceEvent(code: str, fields: dict[str, object] = {})   # 一条被记录的 trace(只读)
```

### `class TraceError(CorespineError)`

trace 载荷违反隐私约定时抛出。`code = "trace.forbidden"`,`retryable = False`。继承 `CorespineError`。

### `FORBIDDEN_KEYS: frozenset[str]`

禁止出现在 trace 载荷里的键(归一为小写后比对,命中任一即拒整条):
`{answer, body, chunk, chunk_text, completion, content, prompt, text, value}`。

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

## config —— `corespine.config.env`

### `load_from_env(cls: type[T], *, prefix: str, env: Mapping[str, str] | None = None) -> T`

按 dataclass 字段从 `PREFIX_*` 读取并构造实例。

- `cls` 必须是 dataclass(否则 `TypeError`)。
- 每个字段从 `env_key(prefix, field)` 读取;命中则按字段注解类型转换,缺失则用 dataclass 字段默认值。
- 字段**无默认值且 env 缺失** → 抛 `ValueError`(把缺失的 env 名报清楚)。
- 支持类型:`str` / `int` / `float` / `bool`(及其 `X | None` 可选形式)。bool 解析:
  `{1,true,yes,on}` 为真、`{0,false,no,off,""}` 为假(均大小写不敏感),其余 raise `ValueError`。
- `env` 可注入(默认读 `os.environ`)——测试传入自己的 mapping,不碰进程环境。

### `env_key(prefix: str, field_name: str) -> str`

字段名 → 环境变量名:`f"{prefix.rstrip('_')}_{field_name.upper()}"`。例:`env_key("APP", "port") == "APP_PORT"`。

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

add(self, name: str, check: Invariant[T]) -> InvariantPack[T]    # 登记一个不变量;返回自身(可链式)
names(self) -> list[str]                                         # 全部不变量名
```

`Invariant[T] = Callable[[T], None]`——拿到一个【新构造的实现实例】,通过则正常返回、违反则抛异常。
检查应只读、只验外部可观测行为。

### `class ConformanceSuite(Generic[T])`

把 实现注册表 × 不变量包 绑成可执行 / 可参数化的套件。

```python
ConformanceSuite(implementations: dict[str, Factory[T]], pack: InvariantPack[T]) -> None
    # implementations: 名字 -> 无参工厂 Callable[[], T](每格各新建实例,杜绝状态串味)。
    # implementations 为空 -> 抛 ValueError。

cases(self) -> list[tuple[str, str]]                # 全部 (实现名, 不变量名) 组合
ids(self) -> list[str]                              # 与 cases() 对齐的可读 id,形如 "impl/invariant"
check(self, impl: str, invariant: str) -> None      # 跑单格:新建该实现实例,执行该不变量(失败原样抛)
run(self) -> list[CaseResult]                       # 跑全部格子并收集结果(不抛;用于离线自检/报告)
passed(self) -> bool                                # 便捷:全部格子是否通过

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
CaseResult(impl: str, invariant: str, passed: bool, error: str | None = None)
    # 单个 (实现 × 不变量) 格子的执行结果;error 形如 "AssertionError: <msg>"(passed=False 时)。
```

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
```

### `error_to_dict(exc: BaseException) -> dict[str, object]`

把【任意】异常归一为可序列化 dict。`CorespineError` 走其 `to_dict()`;其余异常给同形状的保守默认
(`code="error"`、`retryable=False`、`context={}`)。

### 内置子类

```python
class ConfigError(CorespineError): code = "config.invalid"    # 配置非法(缺必填、类型不符等)
class SeamError(CorespineError):   code = "seam.unknown"      # 缝相关错误(未知实现、工厂构造失败等)
```

> 注:`Registry.make` 的未知 spec 抛的是标准库 `ValueError`(非 `SeamError`);`load_from_env` 的缺失/
> 非法抛标准库 `ValueError` / `TypeError`。`SeamError` / `ConfigError` 是供各 app 自用的语义子类示范。
