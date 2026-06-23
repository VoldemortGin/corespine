"""缝开销基线:量 corespine 几条核心缝的【每次操作纳秒级开销】(纯标准库 timeit)。

跑法:`python benchmarks/bench_seams.py`(或 `make bench`)。全程零网络 / 零 key /
零真实 API,确定性可复现。这不是"系统性能"测试,而是给薄核每条缝的【机制开销】钉一个
基线数字——缝本就该是近乎零成本的间接层,基线让"某次改动悄悄把缝变贵"无所遁形。

量四条路径(都走真实导出的公开 API,与消费者用法一致):
  1. Registry.make(spec)        —— 缝分派:归一 spec → 命中内置工厂 → 构造实例;
  2. trace.emit 拒正文路径       —— 隐私闸门命中 FORBIDDEN_KEYS → 抛 TraceError(常走的失败面);
  3. RateLimitedProvider.chat   —— 限流包装器在【预算充足】下的转发开销(相对裸 MockProvider);
  4. error_to_dict(exc)         —— 把异常归一成可序列化 dict(CorespineError 与普通异常两支)。

末行打印 "bench seams OK"。基线数字见同目录 BENCH.md。
"""

from __future__ import annotations

import timeit
from collections.abc import Callable

from corespine import (
    InProcessPrivacyTraceSink,
    MockProvider,
    RateLimitedProvider,
    Registry,
    TraceError,
    error_to_dict,
)


def _fmt_ns(seconds_per_op: float) -> str:
    """把"每次操作秒数"格式化成人类可读(ns / µs)。"""
    ns = seconds_per_op * 1e9
    if ns < 1000:
        return f"{ns:8.1f} ns"
    return f"{ns / 1000:8.2f} µs"


def _measure(label: str, fn: Callable[[], object], *, number: int, repeat: int = 5) -> None:
    """对 fn 跑 repeat 轮、每轮 number 次,取【最优轮】的单次开销并打印(timeit 惯例:取 min)。"""
    # timeit 的标准用法是取多轮 min:它最接近"无外部噪声干扰下的真实开销"。
    best = min(timeit.repeat(fn, number=number, repeat=repeat)) / number
    print(f"  {label:<46} {_fmt_ns(best)}   (n={number:,}×{repeat})")


# ── 路径 1:Registry.make ──────────────────────────────────────────────────
class _Impl:
    """玩具缝实现:构造近乎零成本,使测量聚焦在 make 的分派开销而非实现自身。"""

    def __init__(self, *, tag: str = "x") -> None:
        self.tag = tag


def _bench_registry_make() -> None:
    reg: Registry[_Impl] = Registry("bench")
    reg.register("in-process", lambda **kw: _Impl(**kw))
    # 大小写/连字符不敏感的真实 spec(归一路径会做 strip+lower+replace)。
    _measure("Registry.make('In-Process')", lambda: reg.make("In-Process", tag="x"), number=200_000)


# ── 路径 2:trace.emit 拒正文路径 ────────────────────────────────────────────
def _emit_rejects() -> None:
    sink = InProcessPrivacyTraceSink()
    # content 命中 FORBIDDEN_KEYS:emit 必抛 TraceError——量的就是这条隐私闸门失败面。
    try:
        sink.emit("answer", content="x")
    except TraceError:
        return


def _bench_trace_reject() -> None:
    _measure("trace.emit 拒正文(抛 TraceError)", _emit_rejects, number=100_000)
    # 对照:正常元数据 emit(通过校验、落内存)的开销。
    sink = InProcessPrivacyTraceSink()
    _measure("trace.emit 正常元数据(对照)", lambda: sink.emit("step", n=1), number=200_000)


# ── 路径 3:RateLimitedProvider 限流开销 ──────────────────────────────────────
def _bench_rate_limit() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    inner = MockProvider()
    # 预算给足(tpm 极大,永不触发阻塞 sleep)+【短窗口】:窗口很快滑过,滑动窗口 deque 始终
    # 只留极少记录,量的是限流包装器在快路径下的纯转发开销(锁 + 过期清理 + append),而非
    # deque 无限堆积的 O(n) 退化。注:窗口设大且预算永不耗尽时,_used_tokens 的 sum 会随调用
    # 数线性变贵——那是"长时间不滑动"的退化形态,不代表单次包装开销,故此处用短窗口隔离。
    limited = RateLimitedProvider(inner, tokens_per_minute=10**12, window_seconds=0.001)
    _measure("MockProvider.chat(裸,对照)", lambda: inner.chat(msgs), number=100_000)
    _measure("RateLimitedProvider.chat(预算充足)", lambda: limited.chat(msgs), number=100_000)


# ── 路径 4:error_to_dict ────────────────────────────────────────────────────
def _bench_error_to_dict() -> None:
    from corespine import ConfigError

    core_exc = ConfigError("bad", field="x")
    plain_exc = ValueError("bad")
    _measure("error_to_dict(CorespineError)", lambda: error_to_dict(core_exc), number=200_000)
    _measure("error_to_dict(普通异常)", lambda: error_to_dict(plain_exc), number=200_000)


def main() -> None:
    print("corespine 缝开销基线(单次操作;越小越好):")
    print("[1] Registry.make —— 缝分派")
    _bench_registry_make()
    print("[2] trace.emit —— 隐私闸门")
    _bench_trace_reject()
    print("[3] RateLimitedProvider —— 限流包装开销")
    _bench_rate_limit()
    print("[4] error_to_dict —— 错误归一")
    _bench_error_to_dict()
    print("bench seams OK")


if __name__ == "__main__":
    main()
