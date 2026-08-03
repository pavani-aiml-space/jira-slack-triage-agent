"""
Integration tests for Slack MCP (@modelcontextprotocol/server-slack via npx).

These tests make a REAL connection to the Slack MCP subprocess.
They do NOT post any messages or modify any channel state.

Run with:
    pytest tests/integration/ -v
"""
import pytest
from pipeline.slack_reader import slack_mcp_session, fetch_messages
from config.settings import settings


@pytest.mark.asyncio
async def test_slack_mcp_session_connects_successfully():
    """The MCP subprocess starts and the session initialises without error."""
    async with slack_mcp_session() as session:
        assert session is not None


@pytest.mark.asyncio
async def test_slack_mcp_exposes_channel_history_tool():
    """slack_get_channel_history is present — required by fetch_messages()."""
    async with slack_mcp_session() as session:
        result = await session.list_tools()
        tool_names = [t.name for t in result.tools]
        assert "slack_get_channel_history" in tool_names, (
            f"Expected 'slack_get_channel_history', got: {tool_names}"
        )


@pytest.mark.asyncio
async def test_slack_mcp_exposes_post_message_tool():
    """slack_post_message is present — required by post_slack_message()."""
    async with slack_mcp_session() as session:
        result = await session.list_tools()
        tool_names = [t.name for t in result.tools]
        assert "slack_post_message" in tool_names, (
            f"Expected 'slack_post_message', got: {tool_names}"
        )


@pytest.mark.asyncio
async def test_fetch_messages_returns_list():
    """fetch_messages() returns a list (may be empty if channel has no messages)."""
    messages = await fetch_messages(settings.SLACK_CHANNEL_ID)
    assert isinstance(messages, list)


@pytest.mark.asyncio
async def test_fetch_messages_each_item_has_required_fields():
    """Every returned message has user, text, and ts fields."""
    messages = await fetch_messages(settings.SLACK_CHANNEL_ID)
    for msg in messages:
        assert "user" in msg, f"Missing 'user' in: {msg}"
        assert "text" in msg, f"Missing 'text' in: {msg}"
        assert "ts"   in msg, f"Missing 'ts' in: {msg}"


@pytest.mark.asyncio
async def test_fetch_messages_no_empty_text():
    """fetch_messages() never returns a message with empty or whitespace-only text."""
    messages = await fetch_messages(settings.SLACK_CHANNEL_ID)
    for msg in messages:
        assert msg["text"].strip(), f"Got empty text in message: {msg}"


@pytest.mark.asyncio
async def test_fetch_messages_no_system_events():
    """fetch_messages() filters out system events (subtype field absent)."""
    messages = await fetch_messages(settings.SLACK_CHANNEL_ID)
    for msg in messages:
        assert "subtype" not in msg, f"System event leaked through: {msg}"
