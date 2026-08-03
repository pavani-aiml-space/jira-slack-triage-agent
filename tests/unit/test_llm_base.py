# tests/unit/test_llm_base.py
from agents.llm.base import ToolCall, LLMTurn, LLMProviderError


def test_tool_call_stores_parsed_args():
    tc = ToolCall(id="tc1", name="create_jira_ticket", args={"summary": "bug"})
    assert tc.id == "tc1"
    assert tc.name == "create_jira_ticket"
    assert isinstance(tc.args, dict)


def test_llm_turn_has_empty_defaults():
    turn = LLMTurn(finish_reason="stop", content="done")
    assert turn.tool_calls == []
    assert turn.prompt_tokens == 0
    assert turn.completion_tokens == 0
    assert turn.raw_message is None


def test_llm_turn_tool_calls_list():
    tc = ToolCall(id="tc1", name="create_jira_ticket", args={})
    turn = LLMTurn(finish_reason="tool_calls", content=None, tool_calls=[tc])
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "create_jira_ticket"


def test_llm_provider_error_is_exception():
    err = LLMProviderError("API unavailable")
    assert isinstance(err, Exception)
    assert str(err) == "API unavailable"
