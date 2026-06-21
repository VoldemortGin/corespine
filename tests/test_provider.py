"""llm.provider 合约:MockProvider 确定性 + 输出 OpenAI chat-completions 形状 + Protocol 匹配。"""

from corespine.llm.provider import ChatCompletion, LLMProvider, MockProvider


def _u(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_mock_is_deterministic_for_same_input():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "中国内地FY2024的REVENUE是多少"}]
    a = MockProvider().chat(msgs)
    b = MockProvider().chat(msgs)
    assert a == b  # 跨独立实例、同对话 -> 逐字一致
    assert isinstance(a, ChatCompletion)


def test_output_is_openai_shaped():
    out = MockProvider().chat(_u("hello"))
    # choices[0].message.content / finish_reason / usage.* —— 与 OpenAI 字段完全一致
    assert out.choices[0].message.role == "assistant"
    assert "hello" in out.choices[0].message.content
    assert out.choices[0].finish_reason == "stop"
    assert out.usage.prompt_tokens > 0
    assert out.usage.total_tokens == out.usage.prompt_tokens + out.usage.completion_tokens
    assert out.id.startswith("chatcmpl-")


def test_different_input_yields_different_text():
    p = MockProvider()
    assert p.chat(_u("Q1")).choices[0].message.content != p.chat(_u("Q2")).choices[0].message.content
    # system 消息也参与指纹:同 user 不同 system 也应不同。
    a = p.chat([{"role": "system", "content": "a"}, {"role": "user", "content": "Q"}])
    b = p.chat([{"role": "system", "content": "b"}, {"role": "user", "content": "Q"}])
    assert a.choices[0].message.content != b.choices[0].message.content


def test_mock_never_fabricates_tool_calls():
    # 离线默认不假装会 function-calling:即便给了 tools 也不回 tool_calls。
    out = MockProvider().chat(_u("用工具算 1+1"), tools=[{"type": "function", "function": {"name": "calc"}}])
    assert out.choices[0].message.tool_calls is None
    assert out.choices[0].finish_reason == "stop"


def test_prefix_is_configurable():
    assert MockProvider(prefix="offline").chat(_u("hi")).choices[0].message.content.startswith("[offline:")


def test_mock_satisfies_protocol():
    assert isinstance(MockProvider(), LLMProvider)
