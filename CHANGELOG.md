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
