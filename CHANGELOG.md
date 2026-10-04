# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。本节为尚未发版的改动,
**未改版本号**。

## [Unreleased]

### 行为变化(升级前请读)

- **`ScheduleTrigger` 单次 `poll()` 补发上限**:新增 `max_catchup` 参数,缺省 `DEFAULT_MAX_CATCHUP`
  (100)。原先时钟大跳(如 `start_at=0` 配 `time.time`、进程休眠后恢复)会一次生成无上界的事件
  (200 万 tick 约 73 秒);现在只保留最近 `max_catchup` 个 tick,更早的被合并跳过且不会在
  之后的 poll 再涌出。被跳过数见 `ScheduleTrigger.skipped_ticks`,本次有跳过时首个事件的 payload
  带 `skipped_ticks`。**依赖「无限补发」的调用方**传 `max_catchup=None` 恢复旧行为。
- `ScheduleTrigger` 的 tick 计算改为真除法加 epsilon,修复 `interval_s=0.1` 在 t=0.3 少发一个
  tick(晚一拍补发)的浮点问题。

- **`load_from_env` 类型行为(收紧)**:
  - 转型失败抛 `ConfigError`(仍是 `ValueError`),消息与 context 只含环境变量名、不含取值;
  - 明确支持 `str` / `int` / `float` / `bool` / `Path` / `list[str]`(逗号分隔、去空白、丢空项)
    及 `X | None`;`int | None` 等遇空串得 `None`(`str | None` / `bool | None` 保持旧行为);
  - **不支持的注解(如 `int | str`、`list[int]`、`object`)在对应 env 存在时抛 `ConfigError`**,
    不再静默塞入原始字符串(未设置 env 时不受影响);
  - `init=False` 字段不再读 env(原先有对应 env 时 `TypeError`);
  - `env_key("", "x")` 现为 `X`(原为 `_X`);非空前缀的行为不变。

- **conformance 基座收紧**:
  - `ConformanceSuite` 拿到**空 `InvariantPack`** 现抛 `ValueError`(原先 0 个用例、`passed()`
    静默为 True);`InvariantPack.add` **同名重复登记**现抛 `ValueError`(原先静默覆盖)。
  - `ConformanceSuite.ids()` 由 `impl/invariant` 改为 `impl-invariant`,与 `parametrize_kwargs()`
    的 ids 一致(下游参数化测试用的就是后者,其 id 不变)。
  - `run()` 现把 `pytest.fail` 记为失败、`pytest.skip` / `unittest.SkipTest` 记为 skipped
    (`CaseResult.skipped=True`、`passed=False`,故 `passed()` 不会把跳过当通过),不再中断整轮;
    `KeyboardInterrupt` / `SystemExit` / `pytest.exit` 仍原样外抛。`CaseResult` 新增有缺省值的
    `skipped` 字段。
  - 新增 `corespine.conformance.packs`:`QUEUE_PACK` / `LLM_PROVIDER_PACK` /
    `STREAMING_PROVIDER_PACK`(与既有 `TRACE_PRIVACY_PACK` 并列)。

### 新增 / 修复(按提交顺序追加)

- 递归 trace 隐私门 `enforce_trace_privacy`(ADR 0006),`MAX_TRACE_DEPTH` / `MAX_TRACE_NODES`,
  `corespine.conformance.packs.TRACE_PRIVACY_PACK`。默认 sink 现在对【嵌套】禁词键同样抛
  `TraceError`;`FORBIDDEN_KEYS` 并入 `raw_text` / `user_prompt` / `contents` / `question` /
  `output` / `messages`(以这些名字作 trace 字段的调用方会被拒绝,请改用计数类字段名)。
- `Registry.make` 及 `make_blob_store` / `make_credential_store` / `make_trigger` 的 `spec` 改为
  仅位置参数,工厂自己的 `spec=` 关键字不再抛 `TypeError: multiple values`。已核对全家族无人以
  `make(spec=...)` 关键字形式调用,向后兼容。
- `ConfigError` / `SeamError` 现同时继承 `ValueError`,核心在未知 spec(`Registry.make` 及
  `make_*`,`SeamError`,context 含 `seam` / `spec`)与缺必填 env(`load_from_env`,
  `ConfigError`,context 含 `key`)处改抛它们;既有 `except ValueError` 不受影响。
- `RateLimitedProvider` 包装支持流式的 inner 时,结果现满足 `StreamingLLMProvider`(带限流计量的
  `stream_chat`;chunk 无 usage 时按约 4 字符 / token 估算);inner 不支持流式时 isinstance 仍为
  False。构造方式不变,两种结果都是 `RateLimitedProvider` 的实例。
- credential 文件 store(`InsecureLocalCredentialStore` / `EncryptedFileCredentialStore`)读到
  损坏文件、错 key、形状非法的内容时,统一抛 `CredentialError`(code `credential.corrupt` /
  `credential.bad_key`),不再外泄裸 `InvalidToken` / `JSONDecodeError` / `UnicodeDecodeError` /
  `TypeError`;异常链已断开,消息 / 属性不含明文。**注意**:此前依赖捕获裸 `InvalidToken` 的
  调用方需改捕获 `CredentialError`(盘点三个下游均无此用法)。进程内锁不防多进程写入,已写入
  docstring。
- 小口子:
  - `TraceEvent.fields` 构造时拷贝为只读 dict(仍是 `dict` 子类,`==` / `json.dumps` 照常;就地
    修改抛 `TypeError`);sink 扇出时每个 exporter 拿独立深拷贝。
  - `InProcessPrivacyTraceSink`:单个 exporter 抛错不再中断其余 exporter、也不冒泡给业务调用方,
    累计到 `exporter_failures`(仅计数)。**依赖「exporter 异常向上冒泡」的调用方需改看该计数。**
  - `TriggerEvent` 的 repr 不再包含 payload(`payload` 数据本身不变)。
  - `ManualTrigger` 新增 `namespace` 参数;缺省 id 与此前一致(同序号跨实例 / 重启会重复,
    docstring 已改为准确表述,不再声称「唯一」);需要跨实例不撞时传不同 `namespace`。
  - `CorespineError.to_dict()` 的 context 里 `Path` / `set` / 未知对象会被转成可 `json.dumps`
    的形式(Path -> str,set -> 排序 list,其余 -> str);`error.context` 本身不变。
  - `MemoryBlobStore.put` / `FileSystemBlobStore.put` 对非 bytes-like 输入(如 `5`、`[1, 2]`)抛
    `TypeError`(原先 `bytes(5)` 静默存 5 个零字节);bytes / bytearray / memoryview 照常。
- 工程:CI 新增 `ruff format --check` 门并让 lint 覆盖 `examples`(Makefile 同步,`make ci` 含
  `format-check`);修掉 8 个既有不合规文件(含 4 份 markdown 与 README 的代码块格式);
  `docs/llms/api.md` / `llms.txt` / `overview.md` / `gotchas.md` / `CLAUDE.md` / `README.md` 补齐
  blob / credential / trigger / `RateLimitedProvider` / `StreamingLLMProvider` / `TraceExporter` /
  `ProviderError` 与本批新增公开面。
