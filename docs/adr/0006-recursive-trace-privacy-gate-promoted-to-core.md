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
`emit` 在记录 / 扇出前调用它:

- 递归进 Mapping 的键、dataclass / NamedTuple 的字段名、list / tuple / set 的元素;
- 无法检查的非标量对象(含遍历时抛错的容器)**fail-closed**,抛 `TraceError`;
- 对抗输入上限:容器层数(含顶层)`MAX_TRACE_DEPTH = 8`(与 ragspine 同值同语义),访问节点数
  `MAX_TRACE_NODES = 10_000`;自引用结构由深度上限挡下;用显式栈遍历,不受 Python 递归深度影响;
- 报错只给路径(`calls[0].prompt`),不含取值;
- `FORBIDDEN_KEYS` 并入 `raw_text` / `user_prompt` / `contents` / `question` / `output` /
  `messages`(此前仅 9 个键)。盘点三个下游的 `emit` 调用,没有任何一处用这些名字作字段
  (spineagent 一贯用 `answer_chars` / `output_chars` 之类的计数名)。
- fail-closed 的方式沿用顶层检查:**抛 `TraceError`**,不记录、不扇出(而非静默丢弃)。
- 新增 `corespine.conformance.packs.TRACE_PRIVACY_PACK`:任何 `TraceSink` 实现一行绑定即可验证
  顶层 / 嵌套 / 不可检查对象 / 合法元数据四条契约。

## 兼容性

- `TraceError`、`FORBIDDEN_KEYS`(仍是 frozenset,是旧集合的超集)、`emit` 签名均不变。
- 行为只会更严:嵌套禁词键与新增的 6 个键名现在抛 `TraceError`(原先放行)。合法的
  code / 计数 / 耗时 / 标志事件不受影响。
- 与 ragspine 的 `enforce_trace_privacy` 语义兼容(深度 / 路径格式 / fail-closed 一致),
  核心版额外多一个节点数上限。

## 迁移(下游各自动作;本仓不改兄弟仓)

- **ragspine**:删除 `common/observability/sink.py` 里的 `_children` / `_scan` /
  `enforce_trace_privacy` / `MAX_TRACE_DEPTH` 副本,改为
  `from corespine.observability.trace import enforce_trace_privacy, MAX_TRACE_DEPTH`
  并 re-export 保持包门面不变;`InProcessPrivacyTraceSink` 子类可直接退化为核心类的别名
  (isinstance 对核心类成立)。ragspine 的 otel 适配器继续在扇出前调用同名函数即可。
- **spineagent**:`conformance.py` 的「按键名」检查可换成对核心 `TRACE_PRIVACY_PACK` 的绑定;
  「按值」防线是核心刻意不做的(core 不猜哪些值是正文),保留。
- **spinestudio**:升级 corespine 后自动获得嵌套防线,无需改代码;可选地在 conformance 里
  绑定 `TRACE_PRIVACY_PACK`。

## 理由(rule of three)

1. 证据:ragspine 与 spineagent 两个消费者各自重复;spinestudio 因缺这层而暴露。
2. 恰好那块:只提「递归键名校验」,不含任何业务键名语义之外的东西。
3. 零领域泄漏、零依赖:纯标准库。
4. 机制非保证:按值检测、业务键名扩展仍由各 app 自绑。
