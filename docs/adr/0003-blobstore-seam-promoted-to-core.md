# ADR 0003:BlobStore 缝提上 corespine(需求先行的上提)

- 状态:已接受
- 日期:2026-07-13
- 上游:家族 ADR 0001(D3 薄核 / D5 零依赖 / D6 机制非保证)

## 背景

Spine 家族本轮(用户 2026-07-13 指示:对标 MIT 框架 xerrors/Yuxi「语析」盘点家族缺口、
功能全量补齐)立项两个真实消费者,都需要一块「key -> bytes」的制品存储:

- **spineagent** 的 skills / artifacts:agent 产出的制品(截图、导出文件、中间产物)需要
  存下来按 key 取回。
- **ragspine** 的 ingestion:附件字节(原始 PDF / 图片 / 上传原文)在切分前需要落地暂存。

两者都在各自领域外重复同一块 domain-neutral 的稳定面:一个「按 key 存取字节 blob」的抽象,
默认要能离线确定性地跑(进程内 / 本地盘),真实后端(S3 / MinIO)按需接。

## 决策

在 corespine 新增 `blob` 缝(`src/corespine/blob/`),照家族缝的元模式落地:

- `BlobStore` **Protocol**:`put / get / delete / exists`,key(str)-> bytes,元数据最小化
  (核心 Protocol 只承诺字节 round-trip,不背 content-type / 大小 / 时间戳等元数据;
  需要元数据的 app 自行在 key 命名空间或外层包装里绑)。
- 两个**离线确定性默认**、零依赖实现:`MemoryBlobStore`(进程内 dict)、
  `FileSystemBlobStore`(本地文件系统,key 经 sha256 摊平成扁平文件名,杜绝路径穿越、
  跨平台文件名安全、同 key 跨进程确定映射)。
- `Registry` 工厂:`BLOB_REGISTRY`(注册 `memory` / `filesystem`)+ 便捷 `make_blob_store(spec)`;
  真实后端(s3 / minio)走 entry-point 自动发现 + `lazy_extra_import` 延迟接入,
  corespine 的 `dependencies` 仍为空。
- 参数化 **conformance**(round-trip、删除后不存在、key 隔离)在测试侧绑定(机制非保证)。

## 理由(rule of three 五问)

1. 证据:2 个真实消费者(spineagent skills/artifacts、ragspine ingestion 附件)本轮同时立项,
   都需要同一块「key -> bytes」稳定面。**如实记:这是一次「需求先行的上提」**——不同于 ADR 0002
   是从两处**既有**实现收敛,本条是两个消费者**本轮同时立项、需求先行**;缝的形状取家族已验证的
   元模式(Protocol + 离线默认 + Registry + conformance),风险受控。痛点已明确、消费者已锁定,
   故不等到重复实现落地再抽。
2. 恰好那块:只提「按 key 存取字节」这一最小面,不含任何 RAG(chunking / provenance)或
   agent(工具循环 / 制品语义)概念。
3. 零领域泄漏:纯字节存储,换到任何后端引擎都成立;S3 / MinIO 的 SDK 一律不进核心。
4. 零依赖:两个默认实现纯标准库(dict / pathlib / hashlib);真实后端经可选 extra 延迟 import。
5. 机制非保证:core 只给 Protocol + 离线默认 + 工厂;具体后端一致性由各 app 用 conformance
   套件钉死(ADR 0001 D6)。

## 后果

- corespine 0.1.1 → **0.2.0**(新增 blob 缝,导入面扩大)。
- 家族缝再添一员:blob(与 seam / observability / llm / config / queue / conformance / errors 并列)。
- 未来接真实后端时,新增可选 extra(如 `corespine[s3]`)并经 entry-point 注册,核心默认路径不变。
