"""
Unit tests for slack_reader.py

All MCP calls are mocked — no real Slack connection, no real subprocess.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pipeline.slack_reader import fetch_messages


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mock_session(messages: list[dict]) -> AsyncMock:
    block = MagicMock()
    block.text = json.dumps({"messages": messages})
    mock_result = MagicMock()
    mock_result.content = [block]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=mock_result)
    return session


def patch_slack_session(session: AsyncMock):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("pipeline.slack_reader.slack_mcp_session", return_value=mock_ctx)


# ── Ordering ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_messages_oldest_first():
    raw = [
        {"user": "U1", "text": "newer", "ts": "2000.0"},
        {"user": "U2", "text": "older", "ts": "1000.0"},
    ]
    session = make_mock_session(raw)
    with patch_slack_session(session):
        result = await fetch_messages("C123")
    assert result[0]["text"] == "older"
    assert result[1]["text"] == "newer"


# ── Filtering ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filters_out_empty_text():
    raw = [
        {"user": "U1", "text": "", "ts": "1000.0"},
        {"user": "U1", "text": "real message", "ts": "1001.0"},
    ]
    session = make_mock_session(raw)
    with patch_slack_session(session):
        result = await fetch_messages("C123")
    assert len(result) == 1
    assert result[0]["text"] == "real message"


@pytest.mark.asyncio
async def test_filters_out_whitespace_only_text():
    raw = [
        {"user": "U1", "text": "   ", "ts": "1000.0"},
        {"user": "U1", "text": "\t\n", "ts": "1001.0"},
        {"user": "U1", "text": "actual content", "ts": "1002.0"},
    ]
    session = make_mock_session(raw)
    with patch_slack_session(session):
        result = await fetch_messages("C123")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_filters_out_system_events_with_subtype():
    raw = [
        {"user": "U1", "text": "joined", "ts": "1000.0", "subtype": "channel_join"},
        {"user": "U1", "text": "left",   "ts": "1001.0", "subtype": "channel_leave"},
        {"user": "U2", "text": "bug report", "ts": "1002.0"},
    ]
    session = make_mock_session(raw)
    with patch_slack_session(session):
        result = await fetch_messages("C123")
    assert len(result) == 1
    assert result[0]["text"] == "bug report"


@pytest.mark.asyncio
async def test_empty_channel_returns_empty_list():
    session = make_mock_session([])
    with patch_slack_session(session):
        result = await fetch_messages("C123")
    assert result == []


# ── Field selection ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_only_user_text_ts_fields():
    raw = [{"user": "U1", "text": "hello", "ts": "1000.0", "extra": "ignored", "client_msg_id": "abc"}]
    session = make_mock_session(raw)
    with patch_slack_session(session):
        result = await fetch_messages("C123")
    assert set(result[0].keys()) == {"user", "text", "ts"}


@pytest.mark.asyncio
async def test_missing_user_field_defaults_to_unknown():
    raw = [{"text": "anonymous message", "ts": "1000.0"}]
    session = make_mock_session(raw)
    with patch_slack_session(session):
        result = await fetch_messages("C123")
    assert result[0]["user"] == "unknown"


# ── MCP call arguments ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calls_slack_get_channel_history_tool():
    session = make_mock_session([])
    with patch_slack_session(session):
        await fetch_messages("C_TEST_123")
    assert session.call_tool.call_args[0][0] == "slack_get_channel_history"


@pytest.mark.asyncio
async def test_passes_channel_id_to_mcp():
    session = make_mock_session([])
    with patch_slack_session(session):
        await fetch_messages("C_SPECIFIC_CHANNEL")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["channel_id"] == "C_SPECIFIC_CHANNEL"
