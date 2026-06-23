# ADR 0002:ProviderError 提上 corespine(rule of three)

- 状态:已接受
- 日期:2026-06-23
- 上游:家族 ADR 0001(D3 薄核 / D5 零依赖 / D6 机制非保证)

## 背景

ragspine(`agent/llm_provider.py`)与 spineagent(`llm/errors.py`)各自【本地】定义了
**同形** `ProviderError`:都继承 corespine `CorespineError`、都 `code="provider.error"`、
都用于把 vendor SDK 的网络/超时/API 异常归一到一个稳定可 grep 的边界异常,而程序错
(KeyError/TypeError)照常上抛。两个真实消费者重复同一块稳定边界面。

## 决策

把 `ProviderError` 提上 corespine 的 errors 缝(作 `CorespineError` 子类),与既有
`ConfigError` / `SeamError` 同模式。ragspine / spineagent 改 `from corespine import ProviderError`;
各自本地保留一行 re-export 以向后兼容下游(`ragspine.agent.ProviderError` 等仍可用)。

## 理由(rule of three 五问)

1. 证据:2 个真实消费者各自实现过,且形状完全收敛(同基类、同 code、同语义)。
2. 恰好那块:只提 ProviderError 这个边界异常类型,不带任何 RAG/agent 语义。
3. 零领域泄漏:纯异常类,domain-neutral,不 import 任何 SDK——完全符合薄核。
4. 零依赖:corespine `dependencies` 仍为空(异常是纯标准库)。
5. 机制非保证:core 只给稳定的边界异常【类型】;具体哪些 vendor 异常归一到它、是否重试,
   仍由各 app 在自己的适配器里绑(ADR 0001 D6)。

## 后果

- corespine 0.1.0 → **0.1.1**(新增 ProviderError export)。
- ragspine / spineagent 依赖 `corespine>=0.1.1`。
- errors 缝现有三个示范子类:ConfigError / SeamError / ProviderError。
- 未来若第 4 类边界异常出现 2+ 消费者,同样走此流程提上来。
