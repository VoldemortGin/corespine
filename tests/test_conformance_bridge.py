"""conformance pytest 集成桥合约:parametrize_kwargs() 收敛消费者 glue。

证据驱动:ragspine 在 conftest 手写 @pytest.fixture(params=...) 参数化、spineagent
手写遍历 cases() 的 glue——两处在做同一件事。把它提成 ConformanceSuite 上一个返回
纯数据的方法,消费者就只需:

    @pytest.mark.parametrize(**suite.parametrize_kwargs())
    def test_conformance(case): case()

【pytest-free 铁律】harness 只吐 str/list,本测试文件自己才 import pytest。
"""

import pytest

from corespine.conformance.harness import ConformanceSuite, InvariantPack

# --- TOY 缝:两个正确实现(domain-neutral 计数器)----------------------------


class IntCounter:
    def __init__(self) -> None:
        self._n = 0

    def inc(self):
        self._n += 1

    def value(self):
        return self._n


class ListCounter:
    def __init__(self) -> None:
        self._marks: list = []

    def inc(self):
        self._marks.append(1)

    def value(self):
        return len(self._marks)


def _starts_at_zero(counter):
    assert counter.value() == 0


def _inc_increments(counter):
    counter.inc()
    counter.inc()
    assert counter.value() == 2


PACK = (
    InvariantPack("counter")
    .add("starts_at_zero", _starts_at_zero)
    .add("inc_increments", _inc_increments)
)

# 2 实现 × 2 不变量 = 4 格。
SUITE = ConformanceSuite({"int": IntCounter, "list": ListCounter}, PACK)


def test_argnames_is_case():
    """argnames 固定为单形参 "case"。"""
    assert SUITE.parametrize_kwargs()["argnames"] == "case"


def test_argvalues_and_ids_count_matches_grid():
    """argvalues / ids 长度都等于 实现数 × 不变量数。"""
    kwargs = SUITE.parametrize_kwargs()
    expected = 2 * 2  # 2 实现 × 2 不变量
    assert len(kwargs["argvalues"]) == expected
    assert len(kwargs["ids"]) == expected
    assert len(kwargs["argvalues"]) == len(kwargs["ids"])


def test_ids_format_impl_dash_inv():
    """ids 形如 "impl-inv",且覆盖全部格子(与 cases() 顺序对齐)。"""
    ids = SUITE.parametrize_kwargs()["ids"]
    assert ids == [f"{impl}-{inv}" for impl, inv in SUITE.cases()]
    # 每个 id 至少含一个连字符分隔,且左右两段非空。
    for id_ in ids:
        impl, _, inv = id_.partition("-")
        assert impl and inv
    assert "int-starts_at_zero" in ids
    assert "list-inc_increments" in ids


def test_thunk_runs_passing_case_silently():
    """取一个满足的格子,调用 thunk 不抛。"""
    kwargs = SUITE.parametrize_kwargs()
    idx = kwargs["ids"].index("int-inc_increments")
    kwargs["argvalues"][idx]()  # 满足:静默返回


def test_thunk_raises_on_violation():
    """坏实现的 thunk 调用应抛 AssertionError(原样冒泡,便于 pytest 定位)。"""

    class BrokenCounter:
        def __init__(self) -> None:
            self._n = 0

        def inc(self):
            pass  # 故意不自增

        def value(self):
            return self._n

    broken = ConformanceSuite({"broken": BrokenCounter}, PACK)
    kwargs = broken.parametrize_kwargs()
    idx = kwargs["ids"].index("broken-inc_increments")
    with pytest.raises(AssertionError):
        kwargs["argvalues"][idx]()


def test_kwargs_are_pure_data():
    """返回值是纯数据:argnames 是 str、ids 全 str、argvalues 全 callable。"""
    kwargs = SUITE.parametrize_kwargs()
    assert isinstance(kwargs["argnames"], str)
    assert all(isinstance(i, str) for i in kwargs["ids"])
    assert all(callable(v) for v in kwargs["argvalues"])


# 端到端:消费者实际用法只有这两行——本桥的核心价值。
@pytest.mark.parametrize(**SUITE.parametrize_kwargs())
def test_conformance(case):
    case()
