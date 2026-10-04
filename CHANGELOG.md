# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。本节为尚未发版的改动,
**未改版本号**。

## [Unreleased]

### 下游升级须知(升级前请读)

**「下游无需改代码」不成立,请按下面执行。** 经独立审查在 ragspine `main` 上实测:升级后其全套离线
测试 7199 passed -> **1 failed**——失败的是 `tests/conformance/test_trace_sink.py`(约 318 行,
`test_package_exports_the_recursive_in_process_sink`)里的一条**反证断言**:「corespine 原实现只查
顶层,同一载荷照样收下」(`corespine.InProcessPrivacyTraceSink().emit("trace", llm_calls=[...])`
不抛)。本版起核心 sink 递归拒绝嵌套禁词键,这条断言过时了。**这是 ragspine 的测试过时,不是运行时
回归**(其余 7198 条不变)。

- **ragspine 须做**:删除该反证断言(其余断言——`Exported is InProcessPrivacyTraceSink`、子类关系、
  `Exported().emit(...)` 抛 `TraceError`——仍成立);并可改用核心的 `enforce_trace_privacy` /
  `MAX_TRACE_DEPTH`、删掉自己的 `_children` / `_scan` / `enforce_trace_privacy` 副本(见 ADR 0006)。
  本仓**不**改 ragspine。
- **发版需要协调**:**先发 corespine,ragspine 再升级依赖并删除该断言**——不要反过来,也不要在
  ragspine 的断言删除前让它的 CI 解析到新版 corespine。
- spineagent / spinestudio:以各自 `main` 的归档副本实测,corespine main -> 本分支的通过 / 失败数一致
  (spineagent 757 passed -> 757 passed;spinestudio 后端 230 passed + 1 skipped -> 同),无需改代码;spinestudio
  的全局 sink 与 exporter 各存一份深拷贝且无上限增长是它自己的问题(可用新增的 `max_events` 兜底)。

### 行为变化(相对 main;逐条列明)

**trace 隐私门**(ADR 0006)
- 默认 sink 现在对【嵌套】禁词键同样抛 `TraceError`(原先只查顶层);`FORBIDDEN_KEYS` 并入
  `raw_text` / `user_prompt` / `contents` / `question` / `output` / `messages`(以这些名字作 trace
  字段的调用方会被拒绝,请改用计数类字段名)。
- **键匹配**:键必须是 `str`(子类取其真实字符串值,不信任 `__str__`);`bytes` / tuple / int /
  Enum 等非 str 键 fail-closed。比对前做 NFKC(全角 -> 半角)、去 Unicode 格式 / 控制字符(Cf / Cc,
  含零宽字符)、去首尾空白、casefold。(原先只做 `str(key).strip().lower()`,全角 / 零宽 / bytes 键可绕过。)
- **放行的值**:不可变标量叶子——`None` / `bool` / `int` / `float` / `complex` / `str` / `bytes` /
  `Enum` 成员 / `Path`·`PurePath` / `datetime`·`date`·`time`·`timedelta` / `Decimal` / `Fraction` /
  `UUID` / `range`(子类按基类)。值的内容不检查,隐私门只按键名把关。
  **fail-closed 抛 `TraceError`**:白名单外的对象(自定义类实例、生成器、`bytearray`、`memoryview`、
  类本身 ...)、**自定义 Mapping**(只把内置 `dict` 及其子类、`types.MappingProxyType` 当映射并转成
  普通 `dict`;其 `items()` 可被覆写欺骗,故其它 Mapping 一律拒绝,**顶层也一样**)、遍历时抛错的
  容器、层数超 `MAX_TRACE_DEPTH`(8)、节点数超 `MAX_TRACE_NODES`(10000)。原先 sink 对非键的取值
  一概放行,现在放行范围收窄到上述白名单。
- **报错消息**只含被命中的禁用键名(来自固定集合)与路径的形状:其余键名以 `<key>` 占位、序列下标
  保留数字,如 `<key>[0].prompt`(原先是完整路径 `calls[0].prompt`;以用户数据作 dict 键时正文会进
  日志)。异常 args / `context` / `to_dict()` 同样不含原始键名与取值。
- `TraceEvent.fields` 构造时拷贝为只读 dict(仍是 `dict` 子类,`==` / `json.dumps` 照常;就地修改、
  重复 `__init__` 抛 `TypeError`)。**防误用,不防恶意**:`dict.__setitem__(f, ...)` 这类直接调基类
  方法的绕过在 Python 里挡不住;exporter 拿到的始终是独立拷贝。
- sink 扇出:单次扫描同时校验并取独立拷贝;全标量的扁平事件不递归、不深拷贝(标量不可变),含
  容器才深拷贝给每个 exporter。
- `InProcessPrivacyTraceSink`:单个 exporter 抛错不再中断其余 exporter、也不冒泡给业务调用方,累计到
  `exporter_failures`(仅计数)。**依赖「exporter 异常向上冒泡」的调用方需改看该计数。**

**ScheduleTrigger**
- tick 序号改为以锚点为原点、按真实边界时刻(`anchor + k*interval`)取「不晚于 now」的最大 k,
  边界比较用 4 个 ulp 的相对容差(按时间戳量级),不用绝对 epsilon。效果:`interval_s=0.1` 在 t=0.3
  处发满 3 个 tick(main 少发一个、晚一拍补);量级 1.7e9 的时间戳配 3600s 间隔不会提前约 3 微秒发火
  (至多提前数个 ulp,即该量级下不到 1 微秒,比 now 自身的分辨率还小)。
- 新增 `max_catchup`(缺省 `None` = 不限,与 main 一致,**不静默丢 tick**)与 `catchup` 策略:
  `"drop_oldest"`(缺省策略)只保留最近 N 个、更早的永久丢弃(`skipped_ticks` 可观察);`"batch"`
  每次 `poll()` 最多返回 N 个最旧的未发 tick、状态只前移到已返回位置,连续 poll 恰好补完、一个不丢。
  注意与 spinestudio 的 `MAX_SCHEDULE_CATCH_UP`(从最旧开始分批、不丢)对应的是 `batch`,不是
  `drop_oldest`。`make_trigger("schedule", ...)` 透传两个参数。(上一个未发布提交曾把缺省设为 100
  并导出 `DEFAULT_MAX_CATCHUP`——已撤销,该常量不存在于任何已发布版本。)

**load_from_env**
- 转型失败抛 `ConfigError`(仍是 `ValueError`),消息与 context 只含环境变量名、不含取值,且异常在
  `except` 块**外**抛出——`__context__` / `__cause__` 都为 `None`(原先 `from None` 只是不显示,
  `__context__` 里仍留着 `int()` 的原始报错与密钥取值)。
- 类型支持:`str` / `int` / `float` / `bool` / `Path` / `list[str]`(逗号分隔、去空白、丢空项)/
  `Literal["a", "b"]`(全为字符串字面量,取值须在其中,否则 `ConfigError`,消息只列合法取值、不含
  实际取值)及 `X | None`;`int | None` 等遇空串得 `None`(`str | None` / `bool | None` 保持旧行为)。
  **与 main 的差异**:`Path` 字段原来得到 `str`、现在得到 `Path`;`list[str]` 字段原来得到原串、现在
  得到 list;**非可选 `Path` 遇空串抛 `ConfigError`**(原先静默成 `Path('.')` 当前目录);
  `tuple[str, ...]` / `Literal[非字符串]` / `int | str` / `list[int]` / `object` 等不支持的注解
  **在对应 env 存在时抛 `ConfigError`**(原先静默塞入原始字符串;未设置 env 时不受影响)。
- `init=False` 字段不再读 env(原先有对应 env 时 `TypeError`);`env_key("", "x")` 现为 `X`
  (原为 `_X`);非空前缀不变。缺必填 env 现抛 `ConfigError`(`ValueError` 子类)。

**conformance**
- `ConformanceSuite` 拿到空 `InvariantPack` 现抛 `ValueError`(原先 0 个用例、`passed()` 静默为
  True);`InvariantPack.add` 同名重复登记抛 `ValueError`(原先静默覆盖)。
- `ConformanceSuite.ids()` 由 `impl/invariant` 改为 `impl-invariant`,与 `parametrize_kwargs()` 的
  ids 一致(下游参数化用的是后者,其 id 不变)。
- `run()` 把 `pytest.fail` / `pytest.xfail` 记为失败、`pytest.skip` / `unittest.SkipTest` 记为 skipped
  (`CaseResult.skipped=True`、`passed=False`),不再中断整轮;`KeyboardInterrupt` / `SystemExit` /
  `pytest.exit` 仍原样外抛。识别时核对异常类的基类 / `__module__`(`pytest` / `_pytest.` /
  `unittest.` 前缀),**同名的假类(自定义 `SkipTest` / `Skipped` ...)不会被当成 skip / fail**。
  `CaseResult` 新增有缺省值的 `skipped` 字段。
- **`passed()` 语义**:任一格子失败或被 skip 都返回 `False`(跳过不算通过;全是 skip 的套件什么也没
  验证,与「空 pack 不得静默全绿」同一原则)。main 上不变量里的 `pytest.skip` 会从 `passed()` 透传出
  去、让调用方的测试变成 skipped,现在改为记录并返回 `False`;想容忍 skip 的调用方自己遍历 `run()`。
- 新增 `corespine.conformance.packs`:`QUEUE_PACK` / `LLM_PROVIDER_PACK` / `STREAMING_PROVIDER_PACK`
  (与既有 `TRACE_PRIVACY_PACK` 并列)。**`QUEUE_PACK` 只适用同步内联执行的 `TaskQueue`**(如
  `FakeQueue`):它要求 enqueue 后 `get` 立即终态、闭包 job 在当前进程运行;异步后端(如 RQ)不适用。

**错误形态**
- 未知 spec(`Registry.make` 及 `make_blob_store` / `make_credential_store` / `make_trigger`)的
  `error_to_dict` 由 `type="ValueError", code="error"` 变为 `type="SeamError", code="seam.unknown"`
  (context 含 `seam` / `spec`)。`SeamError` / `ConfigError` 同时继承 `ValueError`,既有
  `except ValueError` 不受影响;但按 `type` / `code` 字符串分支的调用方需留意。
- `Registry.make` 及 `make_*` 的 `spec` 改为仅位置参数(已核对全家族无人以 `make(spec=...)` 调用)。

**RateLimitedProvider**
- 包装支持流式的 inner 时,**实例上**才带 `stream_chat`(带限流计量),故 `isinstance(x,
  StreamingLLMProvider)` 为真;inner 不支持流式时为假、`hasattr(x, "stream_chat")` 为假。始终是
  同一个类:`type(x) is RateLimitedProvider`,`copy.copy`(与 main 一致,共享窗口)/ `deepcopy` /
  pickle(得到独立窗口,锁重建)/ 带额外构造参数的子类化都正常。
- 流式 token 估算(chunk 无 `usage` 时)只统计文本部分:`str` 内容与文本片段按字符数(约 4 字符 /
  token),图片 / 音频等非文本片段每个固定计 32 字符,**与载荷大小无关**(原先 `str(content)` 会把
  400KB base64 图片估成约 20 万 token)。

**其它**
- `ManualTrigger` 新增 `namespace` 参数:缺省 id 与 main **逐字节一致**(同序号跨实例 / 重启会重复,
  docstring 已如实表述);带 namespace 的 id 对各部件做无歧义编码,`trigger="manual:x"` 与
  `namespace="x"` 不再撞 id。`TriggerEvent` 的 repr 不再包含 payload。
- `InProcessPrivacyTraceSink` / `InProcessTraceExporter` 新增可选 `max_events`(缺省不限,行为同前)
  与 `dropped_events`;**长驻服务应设置 `max_events`**,否则内存随事件数无限增长。
- credential 文件 store(`InsecureLocalCredentialStore` / `EncryptedFileCredentialStore`)读到损坏文件、
  错 key、形状非法的内容时,统一抛 `CredentialError`(code `credential.corrupt` / `credential.bad_key`),
  不再外泄裸 `InvalidToken` / `JSONDecodeError` 等;异常链已断开,消息 / 属性不含明文。**依赖捕获裸
  `InvalidToken` 的调用方需改捕获 `CredentialError`**(盘点三个下游均无此用法)。进程内锁不防多进程写入。
- `CorespineError.to_dict()` 的 context 里 `Path` / `set` / 未知对象会被转成可 `json.dumps` 的形式;
  `error.context` 本身不变。
- `MemoryBlobStore.put` / `FileSystemBlobStore.put` 对非 bytes-like 输入(如 `5`、`[1, 2]`)抛
  `TypeError`(原先 `bytes(5)` 静默存 5 个零字节)。

### 新增 / 工程

- 公开的递归隐私门 `enforce_trace_privacy`、`MAX_TRACE_DEPTH` / `MAX_TRACE_NODES`、
  `corespine.conformance.packs.TRACE_PRIVACY_PACK`(ADR 0006)。
- 工程:CI 新增 `ruff format --check` 门并让 lint 覆盖 `examples`(Makefile 同步,`make ci` 含
  `format-check`);`docs/llms/*` / `llms.txt` / `CLAUDE.md` / `README.md` 补齐 blob / credential /
  trigger / `RateLimitedProvider` / `StreamingLLMProvider` / `TraceExporter` / `ProviderError` 等公开面。
