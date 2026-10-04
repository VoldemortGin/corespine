"""conformance.harness 合约:把一个 TOY 缝绑成参数化 conformance + 检出违反。

演示机制:定义一个 domain-neutral 的 "KV store" 玩具缝(put/get/count),用两个正确
实现(dict 版 / list 版)+ 一组不变量,绑成 ConformanceSuite。每个实现【自动继承】
全套不变量;一个故意写坏的实现则被 run() 如实标红——这正是 harness 的价值。
"""

import pytest

from corespine.conformance.harness import ConformanceSuite, InvariantPack

# --- TOY 缝:两个正确实现 -------------------------------------------------


class DictStore:
    def __init__(self) -> None:
        self._d: dict = {}

    def put(self, key, value):
        self._d[key] = value

    def get(self, key):
        return self._d.get(key)

    def count(self):
        return len(self._d)


class ListStore:
    def __init__(self) -> None:
        self._items: list = []

    def put(self, key, value):
        for i, (k, _) in enumerate(self._items):
            if k == key:
                self._items[i] = (key, value)
                return
        self._items.append((key, value))

    def get(self, key):
        for k, v in self._items:
            if k == key:
                return v
        return None

    def count(self):
        return len(self._items)


# --- 不变量包(机制由 harness 提供,这些"保证"由"app"自己绑)----------------


def _put_then_get(store):
    store.put("a", 1)
    assert store.get("a") == 1


def _count_reflects_puts(store):
    store.put("a", 1)
    store.put("b", 2)
    assert store.count() == 2


def _same_key_replaces(store):
    store.put("a", 1)
    store.put("a", 2)
    assert store.count() == 1
    assert store.get("a") == 2


PACK = (
    InvariantPack("kv_store")
    .add("put_then_get", _put_then_get)
    .add("count_reflects_puts", _count_reflects_puts)
    .add("same_key_replaces", _same_key_replaces)
)

SUITE = ConformanceSuite({"dict": DictStore, "list": ListStore}, PACK)


@pytest.mark.parametrize(("impl", "invariant"), SUITE.cases(), ids=SUITE.ids())
def test_toy_seam_conformance(impl, invariant):
    """每个实现 × 每个不变量 各跑一格(2 实现 × 3 不变量 = 6 格全绿)。"""
    SUITE.check(impl, invariant)


def test_suite_detects_a_violation():
    """故意写坏的实现:run() 应如实标红,定位到具体不变量格子。"""

    class BrokenStore:
        def __init__(self) -> None:
            self._d: dict = {}

        def put(self, key, value):
            pass  # 故意不存

        def get(self, key):
            return self._d.get(key)

        def count(self):
            return len(self._d)

    broken = ConformanceSuite({"broken": BrokenStore}, PACK)
    results = broken.run()
    assert not broken.passed()
    failed = {r.invariant for r in results if not r.passed}
    # put 是 no-op,三条不变量全踩。
    assert failed == {"put_then_get", "count_reflects_puts", "same_key_replaces"}


def test_fresh_instance_per_case_no_state_bleed():
    """每个格子新建实例:某不变量写入后,另一不变量从空库起步,不串味。"""
    suite = ConformanceSuite({"dict": DictStore}, PACK)
    suite.check("dict", "count_reflects_puts")  # 写了 2 条
    # 若复用实例,put_then_get 的 count 语义会被污染;新建则不会。
    suite.check("dict", "put_then_get")


def test_empty_suite_rejected():
    with pytest.raises(ValueError):
        ConformanceSuite({}, PACK)


# --- 空转 / 重名 / id 一致 / pytest 结果处理 --------------------------------------------


def test_empty_pack_rejected_instead_of_silent_green():
    with pytest.raises(ValueError):
        ConformanceSuite({"dict": DictStore}, InvariantPack("empty"))


def test_duplicate_invariant_name_rejected():
    pack = InvariantPack("p").add("same", lambda x: None)
    with pytest.raises(ValueError):
        pack.add("same", lambda x: None)
    assert pack.names() == ["same"]


def test_ids_and_parametrize_kwargs_ids_are_identical():
    kw = SUITE.parametrize_kwargs()
    assert SUITE.ids() == kw["ids"]
    assert all("-" in i and "/" not in i for i in SUITE.ids())


def _pack_of(**checks):
    pack = InvariantPack("t")
    for name, fn in checks.items():
        pack.add(name, fn)
    return pack


def test_run_records_pytest_fail_and_continues():
    def uses_pytest_fail(_):
        pytest.fail("故意失败")

    def fine(_):
        return None

    suite = ConformanceSuite({"d": DictStore}, _pack_of(a=uses_pytest_fail, b=fine))
    results = {r.invariant: r for r in suite.run()}
    assert results["a"].passed is False and "故意失败" in (results["a"].error or "")
    assert results["b"].passed is True  # 没被中断
    assert suite.passed() is False


def test_run_records_pytest_skip_as_skipped_not_green():
    def uses_skip(_):
        pytest.skip("不适用")

    suite = ConformanceSuite({"d": DictStore}, _pack_of(a=uses_skip))
    (result,) = suite.run()
    assert result.skipped is True and result.passed is False
    assert suite.passed() is False  # 跳过不算通过:不静默全绿


def test_run_does_not_swallow_keyboard_interrupt_or_pytest_exit():
    from _pytest.outcomes import Exit

    def interrupt(_):
        raise KeyboardInterrupt

    def hard_exit(_):
        pytest.exit("stop", returncode=3)

    for fn, exc_type in ((interrupt, KeyboardInterrupt), (hard_exit, Exit)):
        suite = ConformanceSuite({"d": DictStore}, _pack_of(a=fn))
        with pytest.raises(exc_type):
            suite.run()
