# corespine 性能基线(缝开销)

薄核每条缝本就该是近乎零成本的间接层。这里给核心几条缝的【单次操作开销】钉一个可复现基线,
让"某次改动悄悄把缝变贵"在数字上无所遁形。**全程零网络 / 零 key / 零真实 API,纯标准库 `timeit`。**

## 跑

始终从包根运行:

```bash
make bench
# 等价于:
.venv/bin/python benchmarks/bench_seams.py
```

末行打印 `bench seams OK`。基线工具只用标准库(`timeit`),走 dev 装法即可,
**不进 corespine 运行时 `dependencies`(其恒为空,见宪章 ADR 0001 D5)。**

## 量什么(都走真实导出的公开 API,与消费者用法一致)

| # | 路径 | 测的开销 |
|---|------|----------|
| 1 | `Registry.make(spec)` | 缝分派:归一 spec → 命中内置工厂 → 构造实例 |
| 2 | `trace.emit` 拒正文 | 隐私闸门命中 `FORBIDDEN_KEYS` → 抛 `TraceError`(常走的失败面)|
| 3 | `RateLimitedProvider.chat` | 限流包装器在【预算充足】下相对裸 provider 的转发开销 |
| 4 | `error_to_dict(exc)` | 把异常归一成可序列化 dict(`CorespineError` 与普通异常两支)|

## 基线数字

> 环境:Apple M1 Pro · macOS arm64 · CPython 3.13.2。timeit 取多轮 min(单次操作开销,越小越好)。
> 绝对值随机器/解释器浮动;**关注的是量级与相对关系**,不是精确毫秒数。

| 路径 | 单次开销 | 备注 |
|------|---------:|------|
| `Registry.make('In-Process')` | ~415 ns | 含 spec 归一(strip+lower+replace)+ 字典命中 + 工厂构造 |
| `trace.emit` 拒正文(抛 `TraceError`) | ~966 ns | 隐私闸门失败面;比正常 emit 多 ~280 ns(异常构造+抛/捕获)|
| `trace.emit` 正常元数据(对照) | ~686 ns | 扫禁词键 + 追加 `TraceEvent` |
| `MockProvider.chat`(裸,对照) | ~2.6 µs | 基线 provider(sha256 指纹 + 回显)|
| `RateLimitedProvider.chat`(预算充足) | ~6.3 µs | 包装开销 ~3.7 µs:取锁 + 滑窗过期清理 + append |
| `error_to_dict(CorespineError)` | ~237 ns | 走 `to_dict()` |
| `error_to_dict(普通异常)` | ~167 ns | 保守默认形状 |

## 解读 / 已知特征

- **缝间接层近乎免费。** `Registry.make`、`error_to_dict` 都在百纳秒量级,相对真实 LLM/IO
  (毫秒~秒)完全可忽略——薄核的间接层不构成性能负担。
- **隐私闸门拒正文走异常路径**,比正常 emit 略贵(~280 ns)。这是隐私 by construction 的代价,
  且拒正文本应是异常而非热路径,可接受。
- **`RateLimitedProvider` 的退化形态(非默认):** 当 `window_seconds` 很大且预算长期耗不尽时,
  滑动窗口 deque 不滑动、`_used_tokens` 的 `sum(...)` 会随调用数线性变贵(实测在百万次量级下
  可退化到亚毫秒/次)。这是"长时间不滑动"的退化,不代表单次包装开销;基线用短窗口隔离出真实
  快路径开销(~3.7 µs)。若有 app 在超大窗口下高频调用,可考虑把窗口内 token 累计改为增量维护
  ——目前两个消费者都按【分钟级窗口 + 真实 LLM 调用(秒级间隔)】使用,窗口正常滑动,不触发退化,
  故不预先优化(rule-of-three:痛了再抽)。
