"""Unit tests for agents/llm/anthropic_provider.py"""
import pytest
from unittest.mock import MagicMock, patch

from agents.llm.anthropic_provider import (
    AnthropicProvider,
    messages_to_anthropic,
    openai_tools_to_anthropic,
)
from agents.llm.base import LLMProviderError, LLMTurn


def test_openai_tools_to_anthropic_converts_function_schema():
    tools = [{
        "type": "function",
        "function": {
            "name": "create_jira_ticket",
            "description": "Create a ticket",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    }]
    out = openai_tools_to_anthropic(tools)
    assert out == [{
        "name": "create_jira_ticket",
        "description": "Create a ticket",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    }]


def test_messages_to_anthropic_extracts_system_and_user():
    messages = [
        {"role": "system", "content": "You are a judge."},
        {"role": "user", "content": "Hello"},
    ]
    system, anth = messages_to_anthropic(messages)
    assert system == "You are a judge."
    assert anth == [{"role": "user", "content": "Hello"}]


def test_messages_to_anthropic_prefers_system_kwarg():
    messages = [
        {"role": "system", "content": "from messages"},
        {"role": "user", "content": "Hi"},
    ]
    system, anth = messages_to_anthropic(messages, system_kwarg="from kwarg")
    assert system == "from kwarg"
    assert len(anth) == 1


def test_messages_to_anthropic_merges_consecutive_tool_results():
    messages = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "create_jira_ticket", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "post_slack_message", "input": {}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "Created SCRUM-1"},
        {"role": "tool", "tool_call_id": "t2", "content": "Posted"},
    ]
    system, anth = messages_to_anthropic(messages)
    assert system == ""
    assert anth[0]["role"] == "user"
    assert anth[1]["role"] == "assistant"
    assert anth[2]["role"] == "user"
    assert len(anth[2]["content"]) == 2
    assert anth[2]["content"][0]["type"] == "tool_result"
    assert anth[2]["content"][0]["tool_use_id"] == "t1"
    assert anth[2]["content"][1]["tool_use_id"] == "t2"


def _make_tool_use_response():
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Creating ticket"

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "toolu_abc"
    tool_block.name = "create_jira_ticket"
    tool_block.input = {"summary": "Fix login", "issue_type": "Bug"}

    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [text_block, tool_block]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 40
    return resp


def _make_end_turn_response(content="Done"):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = content
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [text_block]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    return resp


@pytest.mark.asyncio
async def test_chat_tool_use_normalises_to_tool_calls():
    with patch("agents.llm.anthropic_provider.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_tool_use_response()

        provider = AnthropicProvider(api_key="sk-ant-test", model="claude-test")
        turn = await provider.chat(
            messages=[{"role": "user", "content": "login broken"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "create_jira_ticket",
                    "description": "x",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            system="You are a triage agent.",
        )

        assert isinstance(turn, LLMTurn)
        assert turn.finish_reason == "tool_calls"
        assert turn.content == "Creating ticket"
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "create_jira_ticket"
        assert turn.tool_calls[0].args["summary"] == "Fix login"
        assert turn.raw_message["role"] == "assistant"
        assert turn.prompt_tokens == 100
        assert turn.completion_tokens == 40

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "You are a triage agent."
        assert call_kwargs["tools"][0]["name"] == "create_jira_ticket"
        assert "input_schema" in call_kwargs["tools"][0]


@pytest.mark.asyncio
async def test_chat_end_turn_is_stop():
    with patch("agents.llm.anthropic_provider.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_end_turn_response()

        provider = AnthropicProvider(api_key="sk-ant-test", model="claude-test")
        turn = await provider.chat(messages=[{"role": "user", "content": "hi"}], tools=[])
        assert turn.finish_reason == "stop"
        assert turn.content == "Done"
        assert turn.tool_calls == []
        # empty tools → Anthropic call should omit tools kwarg
        assert "tools" not in mock_client.messages.create.call_args.kwargs


@pytest.mark.asyncio
async def test_chat_wraps_api_error():
    import anthropic as anth_mod

    with patch("agents.llm.anthropic_provider.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.side_effect = anth_mod.APIError(
            message="boom",
            request=MagicMock(),
            body=None,
        )

        provider = AnthropicProvider(api_key="sk-ant-test", model="claude-test")
        with pytest.raises(LLMProviderError, match="boom"):
            await provider.chat(messages=[{"role": "user", "content": "x"}], tools=[])
