# 扩展 corespine(给消费者 / 第三方)

> 本文是**操作指南**:第三方包如何在不改 corespine 一行代码的前提下,给某条缝补一个实现、
> 接一个真实后端、绑自己的不变量。机制源码见 [`seam/registry.py`](../src/corespine/seam/registry.py)
> 与 [`conformance/harness.py`](../src/corespine/conformance/harness.py);宪章见
> [`../CLAUDE.md`](../CLAUDE.md),增长判据见 [`roadmap.md`](roadmap.md),现状见 [`prd.md`](prd.md)。

corespine 只提供**机制**,扩展点全在缝上,三种方式互不耦合:

| 扩展方式 | 解决什么 | 关键 API | 章节 |
|---|---|---|---|
| entry-point 扩展一条缝 | 第三方装包即被 `make` 发现,无需改核心 | `Registry.make` / group `corespine.<seam>` | [一](#一用-entry-point-扩展一条缝) |
| 可选 extra + 延迟 import | 真实后端 SDK 只在选用时才 import,缺依赖给友好提示 | `lazy_extra_import` / `[project.optional-dependencies]` | [二](#二可选-extra-命名约定) |
| 给一条缝绑 conformance 不变量 | app 用自己的不变量逮住坏实现 | `InvariantPack` / `ConformanceSuite` | [三](#三给一条缝绑-conformance-不变量) |

## 一、用 entry-point 扩展一条缝

每条缝是一个 `Registry("<seam>")` 实例;`make(spec)` 找不到内置名时,**回落到
`importlib.metadata` 的 entry-point 自动发现**,group 命名固定为 **`corespine.<seam>`**。
于是第三方只需在自己的 `pyproject.toml` 声明一个 entry point,**装包即扩展**,corespine
核心一行不改。

### 第三方包的 pyproject 片段

假设某条缝叫 `vector_store`,你的包 `myadapter` 想注册一个名为 `pgvector` 的实现:

```toml
# myadapter/pyproject.toml
[project.entry-points."corespine.vector_store"]
# 名字(左) -> "模块:工厂可调用对象"(右);工厂签名为 (**kwargs) -> 实现实例
pgvector = "myadapter.pg:make_pgvector"
```

```python
# myadapter/pg.py
def make_pgvector(**kwargs):
    """工厂:返回一个实现该缝 Protocol 的实例(此处省略真实细节)。"""
    return PgVectorStore(**kwargs)
```

### 装包后的调用示例

```python
from corespine import Registry

# 同一条缝名 "vector_store" 必须与 entry-point group 后缀一致。
registry: Registry = Registry("vector_store")

# 内置名找不到 → 自动扫 group "corespine.vector_store" 发现 myadapter 的 pgvector。
store = registry.make("pgvector", dsn="postgresql://...")

# 解析大小写 / 连字符 / 留白不敏感:"PG-Vector" / " pgvector " 都解析到同一项。
registry.names()  # 列出【内置 + entry-point 发现】的全部可用名(字典序、去重)
```

要点:

- group 名**必须**是 `corespine.<seam>`,`<seam>` 与 `Registry("<seam>")` 的入参完全一致;
- **内置注册优先于 entry-point**:同名时内置胜出(便于在测试里覆盖);
- 发现是**延迟**的:只在 `make` / `names` 解析时才扫,不在 import 期付出代价;
- 未知 spec 抛 `ValueError`,并**列清当前全部可用名**(绝不让人猜)。

## 二、可选 extra 命名约定

薄核宪章(ADR 0001 D5):核心 `dependencies` **永远为空**,默认路径零重依赖。真实后端的
SDK(Redis / OpenAI / …)由各 app 在自己的缝里经**可选 extra** 声明,并用
[`lazy_extra_import`](../src/corespine/seam/registry.py) **延迟 import** —— 选用该 adapter
时才 import,没装就给出可直接照做的安装指引。

### extra 命名约定

| 约定 | 示例 | 说明 |
|---|---|---|
| 一个真实后端 → 一个 extra,**以后端命名** | `[redis]` / `[openai]` | 名字即"装哪个后端",直观可猜 |
| extra 内只放该后端的 SDK | `redis = ["redis>=5"]` | 不夹带无关依赖,装得最小 |
| `lazy_extra_import` 的 `extra=` 与之同名 | `extra="redis"` | 缺依赖时提示 `pip install <pkg>[redis]` 即可修 |

```toml
# 你的包 pyproject.toml:真实后端依赖声明为可选 extra(以后端命名)
[project.optional-dependencies]
redis = ["redis>=5"]
openai = ["openai>=1.0"]
```

### lazy_extra_import 用法

`lazy_extra_import(module, *, pkg, extra)` 把裸 `ImportError` 翻译成
**"pip install <pkg>[<extra>]"** 的友好提示:

```python
from corespine import lazy_extra_import


def make_redis_queue(**kwargs):
    # 只在真正构造该 adapter 时才 import;没装 redis 不影响核心离线默认路径。
    redis = lazy_extra_import("redis", pkg="myadapter", extra="redis")
    client = redis.Redis(**kwargs)
    return RedisQueue(client)
```

未装 `redis` 时调用 `make_redis_queue(...)` 会抛:

```
ImportError: 缺少可选依赖 'redis':请先 `pip install myadapter[redis]` 再重试。
```

—— 而不是让调用方对着 `ModuleNotFoundError: No module named 'redis'` 自己猜该装哪个 extra。

## 三、给一条缝绑 conformance 不变量

corespine 的 conformance 是**机制,非保证**:harness 只负责"跑 实现 × 不变量 的笛卡尔积 +
报告哪个格子坏了",**具体不变量由各 app 自己绑**(ADR 0001 D6)。app 把自己的
[`InvariantPack`](../src/corespine/conformance/harness.py) 喂进
[`ConformanceSuite`](../src/corespine/conformance/harness.py) 即可。

最小骨架(完整可跑范例见 [`../examples/conformance_usage.py`](../examples/conformance_usage.py)):

```python
from corespine import ConformanceSuite, InvariantPack

# 1) 实现注册表:名字 -> 无参工厂(每格各新建实例,杜绝实现间状态串味)
impls = {"counter": Counter, "broken": BrokenCounter}

# 2) app 自己的不变量包(corespine 核心不含任何具体不变量)
pack = (
    InvariantPack("counter-contract")
    .add("first-add-returns-n", lambda c: assert_eq(c.add(3), 3))
    .add("accumulates", lambda c: assert_eq((c.add(2), c.add(5)), (2, 7)))
)

# 3) 绑成笛卡尔积,逐格跑
suite = ConformanceSuite(impls, pack)
for impl, invariant in suite.cases():
    suite.check(impl, invariant)   # 失败即抛,定位到具体格子
# 或:suite.run() 收集全部结果(不抛);suite.passed() 便捷判全过
```

要点:

- 不变量 = `(实现实例) -> None`,**通过则正常返回、违反则抛异常**;只验外部可观测行为;
- 每个格子都**新建实例**(工厂无参),杜绝实现间状态串味;
- `cases()` 返回 `(实现名, 不变量名)` 列表,可直接喂 `pytest.mark.parametrize`;
  `ids()` 给对齐的可读 id(形如 `impl/invariant`);
- 这正是"敢放手让第三方填广度、却让脊柱不变量烂不掉"的落点:没过 conformance 的实现直接
  CI 红,而非生产事故。
