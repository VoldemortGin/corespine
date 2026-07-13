# ADR 0004:CredentialStore 缝提上 corespine(多租户凭据加密落库)

- 状态:已接受
- 日期:2026-07-14
- 上游:家族 ADR 0001(D3 薄核 / D5 零依赖 / D6 机制非保证)

## 背景

家族本轮对标 n8n 凭据管理与 Dify 加密凭据(n8n 是 fair-code 许可,只学概念、绝不抄代码),
盘点到多租户产品的一处真实缺口:**每租户「自带 API key」需要加密落库**,而此前家族只有
「环境变量名」这种间接引用(`config/env` 读 `PREFIX_*`),没有「按租户存取一个受保护秘密值」
的原语。这是 domain-neutral 的一小块:按 (namespace, name) 存取一个 str 秘密——namespace 是
纯字符串隔离维度(调用方拿它拼租户 / 环境 / 作用域,core 不认识任何产品概念)。

## 决策

在 corespine 新增 `credential` 缝(`src/corespine/credential/`),照家族缝的元模式落地:

- `CredentialStore` **Protocol**:`set / get / delete / list`,(namespace, name)-> str 秘密值。
  namespace 隔离、`get` 缺失抛 `CredentialNotFound`、`delete` 幂等、`list(namespace)` 字典序。
- **离线确定性默认**、零依赖实现:
  - `MemoryCredentialStore`:进程内 dict,零落地;
  - `InsecureLocalCredentialStore`:明文本地 JSON + 文件权限 0600 + 名字直书 "Insecure" 作命名警示。
- **真实加密**走可选 extra:`EncryptedFileCredentialStore` 经 `corespine[crypto]` 用
  cryptography 的 Fernet(AES-128-CBC + HMAC-SHA256),cryptography 在 `__init__` 里
  `lazy_extra_import` **延迟 import**,corespine 的 `dependencies` 仍为空。
- `Registry` 工厂:`CREDENTIAL_REGISTRY`(注册 `memory` / `insecure_local` / `encrypted_file`)
  + 便捷 `make_credential_store(spec)`;真实外部 vault(1Password / HashiCorp 等)只留
  entry-point 扩展点(group `corespine.credential`),**核心不实现任何 vault 客户端**。
- 参数化 **conformance**(round-trip、覆盖、删后不存在、幂等、namespace 隔离、list 作用域、
  以及**隐私负向不变量**「秘密值永不出现在 repr / str / 异常消息里」)在测试侧绑定(机制非保证)。

## 加密路线裁决(诚实优于自造密码学)

薄核宪章要求默认路径**零三方依赖、import-clean**。Python 标准库**没有 AEAD**
(hashlib / hmac / secrets 只够做完整性 / 密钥派生,拼不出安全的认证加密),而家族铁律是
**不自造加密**。故采「诚实路线」而非「假装加密」:

- 两个零依赖默认实现**不谎称加密**——`InsecureLocal` 明文 + 0600 + 名字里直书 "Insecure",
  杜绝把「藏起来 / 混淆」误当「安全」;
- 真加密交给经审计的 `cryptography`(Fernet),经 `[crypto]` 可选 extra 延迟 import。

许可相容:cryptography 采 Apache-2.0 / BSD-3 双许可,与家族纯 Apache 立场相容。extra 命名
用 `crypto`(命名的是**能力**而非某个后端,与 `[redis]` / `[openai]` 的「以后端命名」略有出入,
但更贴近其语义:装它即「获得本地加密能力」)。dev extra 内含 cryptography,使 conformance
在 CI 下覆盖全部实现(含加密实现);未装 crypto 时仅跳过加密实现,import-clean 门不受影响。

## 隐私不变量

秘密值**永不**出现在 `repr` / `str` / 异常消息里:各实现自定义 `__repr__` 只暴露计数 / 路径;
`CredentialNotFound` 消息只带 namespace / name 定位符,绝无 value;Fernet key 只进 Fernet
实例、不作实例属性存明。本缝实现**自身不发射 trace**,故秘密值也到不了任何 trace 事件
(且 `observability/trace` 的 `FORBIDDEN_KEYS` 已含 `value`,任何想把秘密塞进 trace 的尝试
都会被 `TraceError` 挡下——双重保障)。以上由参数化 conformance 的负向不变量 + 专测钉死。

## 理由(rule of three 五问)

1. 证据:多租户产品(spinestudio 及其上层)需要「每租户自带 key 加密落库」,对标 n8n / Dify
   均有此形状。**如实记:这是一次「需求先行的上提」**(同 ADR 0003)——缝的形状取家族已验证的
   元模式(Protocol + 离线默认 + Registry + conformance),风险受控。
2. 恰好那块:只提「按 (namespace, name) 存取一个受保护 str」这一最小面,不含任何 RAG /
   agent / 产品(workspace / user / tenant 语义)概念,namespace 是纯字符串。
3. 零领域泄漏:纯键值秘密存储,换到任何后端引擎都成立;真实 vault 的 SDK 一律不进核心。
4. 零依赖:两个默认实现纯标准库(dict / pathlib / json / os);真加密经可选 extra 延迟 import,
   `dependencies` 仍为空。
5. 机制非保证:core 只给 Protocol + 离线默认 + 工厂;具体加密强度 / 密钥管理 / vault 后端
   由各 app 自绑(ADR 0001 D6),core 不落地 key、不管密钥轮换。

## 后果

- corespine 0.2.0 → **0.3.0**(新增 credential 缝,导入面扩大)。
- 首次引入可选 extra `[crypto]`(cryptography):兑现「离线精简默认 + 可选重依赖」范式的
  第一个真实 adapter——核心默认路径依旧零依赖、import-clean。
- 家族缝再添一员:credential(与 seam / observability / llm / config / queue / conformance /
  errors / blob 并列)。
- 未来接真实 vault 时,新增 entry-point 注册的第三方包即可,核心默认路径不变。
