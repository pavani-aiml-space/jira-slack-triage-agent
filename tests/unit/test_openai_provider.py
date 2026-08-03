import pytest
from unittest.mock import MagicMock, patch

from agents.llm.openai_provider import OpenAIProvider
from agents.llm.base import LLMTurn


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_stop_response(content="Done"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


def _make_tool_calls_response(tool_calls_list, content=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "tool_calls"
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls_list
    resp.usage.prompt_tokens = 20
    resp.usage.completion_tokens = 8
    return resp


# ── stop path ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_stop_returns_llm_turn():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_stop_response()

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert isinstance(turn, LLMTurn)
        assert turn.finish_reason == "stop"
        assert turn.content == "Done"
        assert turn.tool_calls == []
        assert turn.prompt_tokens == 10
        assert turn.completion_tokens == 5


@pytest.mark.asyncio
async def test_chat_passes_messages_and_tools_to_sdk():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_stop_response()
        msgs = [{"role": "user", "content": "hello"}]
        tools = [{"type": "function", "function": {"name": "test"}}]

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        await provider.chat(messages=msgs, tools=tools, system="")

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["messages"] == msgs
        assert call_kwargs.kwargs["tools"] == tools
        assert call_kwargs.kwargs["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_chat_omits_tools_kwarg_when_tools_empty():
    """Judge and other text-only paths pass tools=[] — SDK must not receive empty tools."""
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_stop_response()
        msgs = [{"role": "user", "content": "hello"}]

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        await provider.chat(messages=msgs, tools=[], system="")

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "tools" not in kwargs
        assert kwargs["messages"] == msgs
        assert kwargs["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_chat_stop_stores_raw_message():
    mock_msg = MagicMock()
    mock_msg.content = "Done"
    mock_msg.tool_calls = None

    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].finish_reason = "stop"
        resp.choices[0].message = mock_msg
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 5
        mock_client.chat.completions.create.return_value = resp

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert turn.raw_message is mock_msg  # exact SDK object — required for multi-turn


# ── tool_calls path ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_tool_calls_returns_parsed_tool_call():
    mock_tc = MagicMock()
    mock_tc.id = "call_xyz"
    mock_tc.function.name = "create_jira_ticket"
    mock_tc.function.arguments = '{"summary": "Login crash", "issue_type": "Bug"}'

    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_tool_calls_response([mock_tc])

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert turn.finish_reason == "tool_calls"
        assert len(turn.tool_calls) == 1
        tc = turn.tool_calls[0]
        assert tc.id == "call_xyz"
        assert tc.name == "create_jira_ticket"
        assert tc.args == {"summary": "Login crash", "issue_type": "Bug"}
        assert isinstance(tc.args, dict)  # never a JSON string


@pytest.mark.asyncio
async def test_chat_tool_calls_stores_raw_message():
    mock_tc = MagicMock()
    mock_tc.id = "call_abc"
    mock_tc.function.name = "post_slack_message"
    mock_tc.function.arguments = '{"message": "Done"}'
    mock_msg = MagicMock()
    mock_msg.tool_calls = [mock_tc]
    mock_msg.content = None

    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].finish_reason = "tool_calls"
        resp.choices[0].message = mock_msg
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 5
        mock_client.chat.completions.create.return_value = resp

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert turn.raw_message is mock_msg  # exact SDK object — required for multi-turn


@pytest.mark.asyncio
async def test_chat_multiple_tool_calls_all_parsed():
    tc1 = MagicMock()
    tc1.id = "call_1"
    tc1.function.name = "create_jira_ticket"
    tc1.function.arguments = '{"summary": "Bug A"}'

    tc2 = MagicMock()
    tc2.id = "call_2"
    tc2.function.name = "post_slack_message"
    tc2.function.arguments = '{"message": "created"}'

    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_tool_calls_response([tc1, tc2])

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert len(turn.tool_calls) == 2
        assert turn.tool_calls[0].name == "create_jira_ticket"
        assert turn.tool_calls[1].name == "post_slack_message"


# ── error wrapping ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_wraps_api_error_as_llm_provider_error():
    import openai
    from agents.llm.base import LLMProviderError

    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError.__new__(openai.APIConnectionError)
        )

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        with pytest.raises(LLMProviderError):
            await provider.chat(messages=[], tools=[], system="")


@pytest.mark.asyncio
async def test_chat_does_not_swallow_non_api_errors():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ValueError("unexpected")

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        with pytest.raises(ValueError):
            await provider.chat(messages=[], tools=[], system="")


@pytest.mark.asyncio
async def test_chat_token_counts_accumulated():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        resp = _make_stop_response()
        resp.usage.prompt_tokens = 42
        resp.usage.completion_tokens = 17
        mock_client.chat.completions.create.return_value = resp

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert turn.prompt_tokens == 42
        assert turn.completion_tokens == 17
