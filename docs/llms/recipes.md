# corespine 配方(可运行最小示例)

> 每个示例都【离线 / 零网络 / 零依赖】,逐个实跑过并附**真实输出**。复制即可运行(`from corespine
> import ...`)。API 速查见 [api.md](api.md),易错点见 [gotchas.md](gotchas.md)。

## R1 · Registry:用一个 spec 字符串选实现

```python
from corespine import Registry, MockProvider

reg: Registry = Registry("llm")                       # 缝名 "llm" -> entry-point group "corespine.llm"
reg.register("mock", lambda **kw: MockProvider(**kw)) # 工厂签名:(**kwargs) -> 实例

provider = reg.make("  MOCK ")                         # 大小写/留白/连字符不敏感 -> 解析到 "mock"
print(reg.names(), reg.group, reg.seam)               # ['mock'] corespine.llm llm
print(type(provider).__name__)                        # MockProvider

# 未知 spec:抛 ValueError 并列清当前全部可用名(绝不让人猜)
try:
    reg.make("nope")
except ValueError as e:
    print(e)
# 未知的 llm spec:'nope'。当前可用:mock(内置注册或经 entry-point group 'corespine.llm' 发现;第三方实现可装包扩展)。
```

`make(spec, **kwargs)` 的 kwargs 原样透传给工厂:`reg.make("mock", prefix="m2")` → `MockProvider(prefix="m2")`。

## R2 · 隐私 trace:只记元数据,正文被拒

```python
from corespine import InProcessPrivacyTraceSink, TraceError

sink = InProcessPrivacyTraceSink()
sink.emit("retrieve", count=3, took_ms=12)            # 只记 code/计数/耗时 —— OK
sink.emit("rank", n=5)
print(sink.codes())                                   # ['retrieve', 'rank']
print(sink.events[0])                                 # TraceEvent(code='retrieve', fields={'count': 3, 'took_ms': 12})

# 任何携带正文字段(content/answer/value/text/...)的载荷被【直接拒绝】(raise TraceError、不记录)
try:
    sink.emit("answer.made", content="secret body")
except TraceError as e:
    print(e.code)                                     # trace.forbidden
```

被禁的键见 `FORBIDDEN_KEYS`:`answer / body / chunk / chunk_text / completion / content / prompt / text / value`。

## R3 · MockProvider.chat:OpenAI 形状、确定性

```python
from corespine import MockProvider

prov = MockProvider()
out = prov.chat([
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hello"},
])

print(out.choices[0].message.content)                 # [mock:e761052af1bb] hello
print(out.choices[0].finish_reason)                   # stop
print(out.usage)                                      # Usage(prompt_tokens=26, completion_tokens=25, total_tokens=51)
print(out.model, out.id)                              # mock chatcmpl-e761052af1bb

# 确定性:同一对话恒定产出同一文本
out2 = prov.chat([{"role": "system", "content": "be brief"}, {"role": "user", "content": "hello"}])
print(out.choices[0].message.content == out2.choices[0].message.content)   # True

# Mock 绝不伪造 tool_calls(离线默认不假装会 function-calling)
print(out.choices[0].message.tool_calls)              # None
```

注意是 `chat(messages)`,**不是** `complete(prompt)`(见 [gotchas.md](gotchas.md))。`tools` 是关键字参数:
`prov.chat(messages, tools=[...])`(Mock 收下但仍不产 tool_calls)。

## R4 · load_from_env:env → frozen dataclass

```python
from dataclasses import dataclass
from corespine import load_from_env, env_key

@dataclass(frozen=True)
class Cfg:
    host: str = "localhost"
    port: int = 5432
    debug: bool = False
    token: str | None = None

# env 可注入(默认读 os.environ);缺失字段用 dataclass 默认值
cfg = load_from_env(Cfg, prefix="APP", env={"APP_PORT": "9000", "APP_DEBUG": "yes", "APP_TOKEN": "t-123"})
print(cfg)                                            # Cfg(host='localhost', port=9000, debug=True, token='t-123')
print(env_key("APP", "port"))                         # APP_PORT

# 无默认值的字段缺对应 env -> ValueError(把缺失 env 名报清楚)
@dataclass(frozen=True)
class Req:
    api_key: str
try:
    load_from_env(Req, prefix="SVC", env={})
except ValueError as e:
    print(e)                                          # 缺少必填配置 SVC_API_KEY(字段 'api_key' 无默认值)
```

bool 解析:`{1,true,yes,on}` 真、`{0,false,no,off,""}` 假(大小写不敏感)。支持 `str/int/float/bool` 及 `X|None`。

## R5 · FakeQueue:同步内联执行

```python
from corespine import FakeQueue

q = FakeQueue()

# job 签名约定:fn(payload: dict) -> dict
jid = q.enqueue(lambda payload: {"doubled": payload["n"] * 2}, {"n": 21})
st = q.get(jid)
print(st.status, st.result)                           # finished {'doubled': 42}

# 失败被【捕获】记进状态,不外抛
def boom(payload):
    raise RuntimeError("kaboom")
fid = q.enqueue(boom, {})
print(q.get(fid).status, q.get(fid).error)            # failed {'type': 'RuntimeError', 'message': 'kaboom'}

# 幂等:显式 job_id 且已知 -> 直接返回,不重跑
print(q.enqueue(lambda p: {"x": 1}, {}, job_id=jid) == jid)   # True
print(q.get("unknown-id"))                            # None
```

`func` 也可传点路径字符串(如 `"mypkg.jobs.process"`,末段为可调用对象)。

## R6 · ConformanceSuite:实现 × 不变量 笛卡尔积

```python
from corespine import ConformanceSuite, InvariantPack

class Counter:                                        # 守约实现
    def __init__(self): self._t = 0
    def add(self, n): self._t += n; return self._t

class Broken:                                         # 故意违约:add 永远返回 0
    def add(self, n): return 0

def _require(cond, msg):
    if not cond: raise AssertionError(msg)            # 不变量:通过则返回,违反则抛

# 不变量包:add() 可链式;corespine 核心不含任何具体不变量,这些由 app 绑
pack = (
    InvariantPack("counter-contract")
    .add("first", lambda c: _require(c.add(3) == 3, "首次 add(3) 应返回 3"))
    .add("accum", lambda c: _require((c.add(2), c.add(5)) == (2, 7), "应连续累加"))
)

# 实现注册表:名字 -> 无参工厂(每格各新建实例,杜绝状态串味)
suite = ConformanceSuite({"counter": Counter, "broken": Broken}, pack)

print(suite.cases())   # [('counter','first'), ('counter','accum'), ('broken','first'), ('broken','accum')]
print(suite.ids())     # ['counter/first', 'counter/accum', 'broken/first', 'broken/accum']

for r in suite.run():                                 # run() 收集全部结果,不抛
    print(f"{r.impl}/{r.invariant}: {'PASS' if r.passed else 'FAIL ' + r.error}")
# counter/first: PASS
# counter/accum: PASS
# broken/first: FAIL AssertionError: 首次 add(3) 应返回 3
# broken/accum: FAIL AssertionError: 应连续累加

print(suite.passed())                                 # False(broken 挂了)
# 也可逐格断言:suite.check("counter", "first")  # 通过则静默返回,违反则原样抛
```

### R6b · 接入 pytest(`parametrize_kwargs`,core 不 import pytest)

```python
# 消费者的测试文件里(corespine 只返回纯数据,pytest 依赖留在你这边):
import pytest
from corespine import ConformanceSuite, InvariantPack

suite = ConformanceSuite({"counter": Counter}, pack)

@pytest.mark.parametrize(**suite.parametrize_kwargs())
def test_conformance(case):
    case()                                            # 每个 case 是已绑定该格的零参 thunk;违反则抛

# parametrize_kwargs() 返回:
# {'argnames': 'case',
#  'argvalues': [<每格一个 thunk>...],
#  'ids': ['counter-first', 'counter-accum']}
```

## R7 · errors:统一异常基类 + 归一 dict

```python
from corespine import CorespineError, ConfigError, error_to_dict

# 子类:覆盖 code/retryable 类属性;任意 kwargs 进 context
class JobError(CorespineError):
    code = "job.failed"
    retryable = True

e = JobError("disk full", path="/tmp/x")
print(e.to_dict())
# {'type': 'JobError', 'code': 'job.failed', 'message': 'disk full', 'retryable': True, 'context': {'path': '/tmp/x'}}

# error_to_dict 吃任意异常,给统一形状(非 CorespineError 走保守默认)
print(error_to_dict(ValueError("bad")))
# {'type': 'ValueError', 'code': 'error', 'message': 'bad', 'retryable': False, 'context': {}}

# 内置语义子类(供 app 自用的示范)
print(ConfigError("missing key", key="API_KEY").to_dict())
# {'type': 'ConfigError', 'code': 'config.invalid', 'message': 'missing key', 'retryable': False, 'context': {'key': 'API_KEY'}}
```

## 仓库内的完整可跑范例

- [`examples/quickstart.py`](../../examples/quickstart.py) —— Registry + ConformanceSuite + 隐私 trace,末行打印 `corespine OK`(或 `make demo`)。
- [`examples/conformance_usage.py`](../../examples/conformance_usage.py) —— 守约 vs 违约实现,证明 conformance 能逮住坏实现。
