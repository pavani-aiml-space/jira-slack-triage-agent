"""
Unit tests for slack_tools.py

All MCP calls are mocked — no real Slack connection, no real subprocess.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.triage.tools.slack_tools import post_slack_message, ask_for_clarification
from config.settings import settings


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mock_session() -> AsyncMock:
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=MagicMock())
    return session


def patch_slack_session(session: AsyncMock):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("agents.triage.tools.slack_tools.slack_mcp_session", return_value=mock_ctx)


# ── post_slack_message ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_slack_message_calls_slack_post_message_tool():
    session = make_mock_session()
    with patch_slack_session(session):
        await post_slack_message("Ticket created")
    assert session.call_tool.call_args[0][0] == "slack_post_message"


@pytest.mark.asyncio
async def test_post_slack_message_sends_to_configured_channel():
    session = make_mock_session()
    with patch_slack_session(session):
        await post_slack_message("Hello")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["channel_id"] == settings.SLACK_CHANNEL_ID


@pytest.mark.asyncio
async def test_post_slack_message_sends_exact_text():
    session = make_mock_session()
    with patch_slack_session(session):
        await post_slack_message("Created SCRUM-5 ✓")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["text"] == "Created SCRUM-5 ✓"


@pytest.mark.asyncio
async def test_post_slack_message_returns_confirmation():
    session = make_mock_session()
    with patch_slack_session(session):
        result = await post_slack_message("hello")
    assert "hello" in result
    assert "posted" in result.lower()


# ── ask_for_clarification ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ask_for_clarification_calls_slack_post_message_tool():
    session = make_mock_session()
    with patch_slack_session(session):
        await ask_for_clarification("What is the priority?")
    assert session.call_tool.call_args[0][0] == "slack_post_message"


@pytest.mark.asyncio
async def test_ask_for_clarification_prepends_thinking_emoji():
    session = make_mock_session()
    with patch_slack_session(session):
        await ask_for_clarification("Can you be more specific?")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["text"] == "🤔 Can you be more specific?"


@pytest.mark.asyncio
async def test_ask_for_clarification_sends_to_configured_channel():
    session = make_mock_session()
    with patch_slack_session(session):
        await ask_for_clarification("Q?")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["channel_id"] == settings.SLACK_CHANNEL_ID


@pytest.mark.asyncio
async def test_ask_for_clarification_returns_confirmation():
    session = make_mock_session()
    with patch_slack_session(session):
        result = await ask_for_clarification("What broke?")
    assert "What broke?" in result


# ── Chunk 2.1 — ts capture buffer ────────────────────────────────────────────

import json
import agents.triage.tools.slack_tools as slack_tools_module


def _make_ts_session(ts_value=None):
    """Build a mock MCP session whose call_tool returns a response with optional ts."""
    payload = {"ok": True}
    if ts_value:
        payload["ts"] = ts_value
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps(payload))]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=mock_result)
    return session


def _patch_slack_ts(session):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("agents.triage.tools.slack_tools.slack_mcp_session", return_value=mock_ctx)


@pytest.mark.asyncio
async def test_post_slack_message_captures_ts_when_present():
    slack_tools_module._confirmation_ts_buffer.clear()
    session = _make_ts_session(ts_value="1714406400.123")
    with _patch_slack_ts(session):
        await slack_tools_module.post_slack_message("hello")
    assert slack_tools_module._confirmation_ts_buffer == ["1714406400.123"]


@pytest.mark.asyncio
async def test_post_slack_message_silent_when_ts_absent():
    slack_tools_module._confirmation_ts_buffer.clear()
    session = _make_ts_session(ts_value=None)
    with _patch_slack_ts(session):
        await slack_tools_module.post_slack_message("hello")
    assert slack_tools_module._confirmation_ts_buffer == []


@pytest.mark.asyncio
async def test_post_slack_message_silent_on_malformed_response():
    slack_tools_module._confirmation_ts_buffer.clear()
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text="not json at all {{")]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=mock_result)
    with _patch_slack_ts(session):
        await slack_tools_module.post_slack_message("hello")
    assert slack_tools_module._confirmation_ts_buffer == []


def test_drain_confirmation_ts_pops_and_clears():
    slack_tools_module._confirmation_ts_buffer.clear()
    slack_tools_module._confirmation_ts_buffer.append("111.222")
    ts = slack_tools_module.drain_confirmation_ts()
    assert ts == "111.222"
    assert slack_tools_module._confirmation_ts_buffer == []


def test_drain_confirmation_ts_returns_none_when_empty():
    slack_tools_module._confirmation_ts_buffer.clear()
    assert slack_tools_module.drain_confirmation_ts() is None
