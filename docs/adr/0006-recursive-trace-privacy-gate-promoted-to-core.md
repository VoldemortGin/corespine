# ADR 0006:递归 trace 隐私门(`enforce_trace_privacy`)上提进 corespine

- 状态:已接受
- 日期:2026-10-04
- 上游:家族 ADR 0001(D3 薄核 / D5 零依赖 / D6 机制非保证);ragspine ADR 0028(递归门的原始形态)

## 背景

`InProcessPrivacyTraceSink.emit` 原先只检查【顶层】键:`emit("c", meta={"text": "正文"})`
被记录并扇出给 exporter,`raw_text` / `user_prompt` / `contents` / `question` / `output` /
`messages` 这类正文承载键名也不在禁词表里。「构造即保证」的隐私承诺因此只在顶层成立。

两个真实消费者已各自补了防线,且是同一块稳定面的重复实现(rule of three 的证据):

1. **ragspine**:子类化 `InProcessPrivacyTraceSink`,`emit` 前先过本地的
   `enforce_trace_privacy`(递归进 Mapping / dataclass / NamedTuple / list / tuple / set,
   无法检查的对象 fail-closed,深度上限 `MAX_TRACE_DEPTH = 8`;ragspine ADR 0028)。
2. **spineagent**:在自己的 conformance 里另补「按值」防线(`conformance.py` 的
   `_step_traces_are_privacy_safe`),因为核心 sink 只查键名。

第三个消费者 **spinestudio** 直接用核心 sink(`observability/trace.py`),**没有这层保护**。

## 决策

在 `corespine.observability.trace` 新增公开函数 `enforce_trace_privacy(fields)`,默认 sink 的
`emit` 在记录 / 扇出前调用它(同一次扫描产出独立拷贝,校验的就是将被扇出的):

**键如何匹配**
- 递归进 dict(含子类,按其真实内容读取)与 `MappingProxyType` 的键、dataclass / NamedTuple 的
  字段名、list / tuple / set 的元素;
- 键必须是 `str`(子类取其真实字符串值,不信任 `__str__`),其它类型(bytes / tuple / int / Enum ...)
  的键 fail-closed;
- 比对前归一:NFKC(全角 -> 半角)、去 Unicode 格式 / 控制字符(Cf / Cc,含零宽字符)、去首尾空白、
  casefold;再与 `FORBIDDEN_KEYS` 精确匹配。

**哪些值放行 / 拒绝**
- 放行【不可变标量叶子】:`None` / `bool` / `int` / `float` / `complex` / `str` / `bytes` / `Enum`
  成员 / `Path`·`PurePath` / `datetime`·`date`·`time`·`timedelta` / `Decimal` / `Fraction` / `UUID` /
  `range`(子类按基类)。放行标量不等于放行正文:隐私门只按【键名】把关,值的内容不检查。
- fail-closed(抛 `TraceError`):白名单外的对象(自定义类实例、生成器、`bytearray` ...);
  **自定义 Mapping**(其 `items()` 可被覆写欺骗而把正文带过门,故只认内置 `dict` 及其子类与
  `MappingProxyType`,扇出时转成普通 `dict`);遍历时抛错的容器。
- 对抗输入上限:容器层数(含顶层)`MAX_TRACE_DEPTH = 8`(与 ragspine 同值同语义),访问节点数
  `MAX_TRACE_NODES = 10_000`;自引用结构由深度上限挡下。

**报错不带内容**
- 消息只含被命中的【禁用键名】(来自固定集合,不是用户数据)与路径的【形状】:其余键名以 `<key>`
  占位、序列下标保留数字,如 `<key>[0].prompt`;不含取值。异常的 args / context / `to_dict()` 同样不含
  原始键名(dict 以用户问题、文档名作键时,正文不会进日志)。

**其它**
- `FORBIDDEN_KEYS` 并入 `raw_text` / `user_prompt` / `contents` / `question` / `output` /
  `messages`(此前仅 9 个键)。盘点三个下游的 `emit` 调用,没有任何一处用这些名字作字段
  (spineagent 一贯用 `answer_chars` / `output_chars` 之类的计数名)。
- fail-closed 的方式沿用顶层检查:**抛 `TraceError`**,不记录、不扇出(而非静默丢弃)。
- 性能:全标量的扁平事件走快路径(不递归、不深拷贝);只有含容器值时才递归与深拷贝。
- `TraceEvent.fields` 是只读 dict:**防误用,不防恶意**(`dict.__setitem__(f, ...)` 这类直接调基类
  方法的绕过在 Python 里挡不住);exporter 拿到的始终是独立拷贝。
- 新增 `corespine.conformance.packs.TRACE_PRIVACY_PACK`:任何 `TraceSink` 实现一行绑定即可验证
  顶层 / 嵌套 / 不可检查对象 / 合法元数据四条契约。

## 兼容性

- `TraceError`、`FORBIDDEN_KEYS`(仍是 frozenset,是旧集合的超集)、`emit` 签名均不变。
- 行为只会更严,而且比「只加嵌套检查」多几处:嵌套禁词键与新增的 6 个键名现在抛 `TraceError`;
  白名单外的对象值、自定义 Mapping、非 str 键也抛(原先 sink 对取值一概放行)。合法的
  code / 计数 / 耗时 / 标志 / 时间戳 / 枚举 / 路径 / Decimal / UUID 事件不受影响。
- 报错路径里非禁用键名变为 `<key>` 占位(原为完整路径),依赖解析消息文本的调用方需留意。
- 与 ragspine 的 `enforce_trace_privacy` 语义兼容(深度 / fail-closed 一致),核心版额外多节点数
  上限、键归一与非 str 键拒绝、白名单标量与自定义 Mapping 拒绝。

## 下游升级须知(**不是**「无需改代码」)

- **ragspine 的一条测试会红**:`tests/conformance/test_trace_sink.py`(约 318 行,
  `test_package_exports_the_recursive_in_process_sink`)末尾有反证断言「corespine 原实现只查顶层,
  同一载荷照样收下」(`corespine.InProcessPrivacyTraceSink().emit("trace", llm_calls=[...])` 不抛)。
  升级到本版 corespine 后该断言不再成立(审查实测:ragspine 离线套件 7199 passed -> 1 failed)。
  这是 **ragspine 的测试过时,不是运行时回归**。ragspine 须**删除该反证断言**,并可借此改用核心的
  `enforce_trace_privacy`、删掉自己的副本(见下节迁移)。
- **发版要协调**:先发 corespine,ragspine 再升级依赖并删除该断言;不要让 ragspine 的 CI 在断言
  删除前解析到新版 corespine。本仓不改 ragspine。
- spineagent / spinestudio:以各自 `main` 归档副本实测,corespine main -> 本分支结果一致
  (spineagent 757 passed -> 757;spinestudio 后端 230 passed + 1 skipped -> 同),无需改代码;可选地在 conformance 里绑定 `TRACE_PRIVACY_PACK`。

## 迁移(下游各自动作;本仓不改兄弟仓)

- **ragspine**:删除上述反证断言;删除 `common/observability/sink.py` 里的 `_children` / `_scan` /
  `enforce_trace_privacy` / `MAX_TRACE_DEPTH` 副本,改为
  `from corespine.observability.trace import enforce_trace_privacy, MAX_TRACE_DEPTH`
  并 re-export 保持包门面不变;`InProcessPrivacyTraceSink` 子类可直接退化为核心类的别名
  (isinstance 对核心类成立)。ragspine 的 otel 适配器继续在扇出前调用同名函数即可。
- **spineagent**:`conformance.py` 的「按键名」检查可换成对核心 `TRACE_PRIVACY_PACK` 的绑定;
  「按值」防线是核心刻意不做的(core 不猜哪些值是正文),保留。
- **spinestudio**:升级 corespine 后自动获得嵌套防线;可选地绑定 `TRACE_PRIVACY_PACK`;长驻服务的
  内存 sink / exporter 请设 `max_events`。

## 理由(rule of three)

1. 证据:ragspine 与 spineagent 两个消费者各自重复;spinestudio 因缺这层而暴露。
2. 恰好那块:只提「递归键名校验」,不含任何业务键名语义之外的东西。
3. 零领域泄漏、零依赖:纯标准库。
4. 机制非保证:按值检测、业务键名扩展仍由各 app 自绑。
