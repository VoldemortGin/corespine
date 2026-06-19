"""llm.provider 合约:MockProvider 确定性 + Protocol 结构匹配。"""

from corespine.llm.provider import Completion, LLMProvider, MockProvider


def test_mock_is_deterministic_for_same_input():
    a = MockProvider().complete("中国内地FY2024的REVENUE是多少", system="sys")
    b = MockProvider().complete("中国内地FY2024的REVENUE是多少", system="sys")
    # 跨独立实例、同输入 -> 逐字一致。
    assert a == b
    assert isinstance(a, Completion)


def test_different_input_yields_different_text():
    p = MockProvider()
    assert p.complete("Q1").text != p.complete("Q2").text
    # system 也参与指纹:同 prompt 不同 system 也应不同。
    assert p.complete("Q", system="a").text != p.complete("Q", system="b").text


def test_completion_carries_usage():
    out = MockProvider().complete("hello")
    assert out.usage is not None
    assert out.usage["input_tokens"] == len("hello")
    assert out.usage["output_tokens"] == len(out.text)


def test_prefix_is_configurable():
    out = MockProvider(prefix="offline").complete("hi")
    assert out.text.startswith("[offline:")


def test_mock_satisfies_protocol():
    assert isinstance(MockProvider(), LLMProvider)
