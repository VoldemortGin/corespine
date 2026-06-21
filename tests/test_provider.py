"""llm.provider 合约:MockProvider 确定性(chat/messages 形状)+ Protocol 结构匹配。"""

from corespine.llm.provider import ChatResult, LLMProvider, Message, MockProvider


def _u(text: str) -> list[Message]:
    return [Message(role="user", content=text)]


def test_mock_is_deterministic_for_same_input():
    msgs = [Message("system", "sys"), Message("user", "中国内地FY2024的REVENUE是多少")]
    a = MockProvider().chat(msgs)
    b = MockProvider().chat(msgs)
    # 跨独立实例、同对话 -> 逐字一致。
    assert a == b
    assert isinstance(a, ChatResult)


def test_different_input_yields_different_text():
    p = MockProvider()
    assert p.chat(_u("Q1")).text != p.chat(_u("Q2")).text
    # system 消息也参与指纹:同 user 不同 system 也应不同。
    assert (
        p.chat([Message("system", "a"), Message("user", "Q")]).text
        != p.chat([Message("system", "b"), Message("user", "Q")]).text
    )


def test_result_echoes_last_user_and_carries_usage():
    out = MockProvider().chat([Message("user", "hi"), Message("assistant", "ok"), Message("user", "hello")])
    assert "hello" in out.text  # 回显最后一条 user
    assert out.usage is not None and out.usage["output_tokens"] == len(out.text)


def test_mock_never_fabricates_tool_calls():
    # 离线默认不假装会 function-calling:即便给了 tools 也不回 tool_calls。
    out = MockProvider().chat(_u("用工具算 1+1"), tools=[{"type": "function", "function": {"name": "calc"}}])
    assert out.tool_calls == ()


def test_prefix_is_configurable():
    assert MockProvider(prefix="offline").chat(_u("hi")).text.startswith("[offline:")


def test_mock_satisfies_protocol():
    assert isinstance(MockProvider(), LLMProvider)
