"""
Unit tests for pipeline/reaction_collector.py
All MCP calls mocked — no real Slack connection.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pipeline.quality_metrics import PendingReaction
from pipeline.reaction_collector import fetch_reactions_for_pending


def _pending(ts, run_id="r1"):
    # posted_at must be within window_hours of "now" or the collector skips the row
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return PendingReaction(
        run_id=run_id, block_index=0, ticket_key="SCRUM-1",
        confirmation_ts=ts, posted_at_iso=now,
    )


def _mock_history_session(messages):
    payload = {"messages": messages}
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps(payload))]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest.mark.asyncio
async def test_fetch_reactions_returns_empty_when_no_pending():
    result = await fetch_reactions_for_pending(
        pending=[], channel_id="C123", history_limit=50, window_hours=48
    )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_reactions_matches_by_ts_and_counts():
    messages = [{
        "ts": "111.000",
        "text": "✅ Created SCRUM-1",
        "reactions": [
            {"name": "+1", "count": 2, "users": ["U1", "U2"]},
            {"name": "-1", "count": 1, "users": ["U3"]},
            {"name": "tada", "count": 3, "users": ["U4"]},  # ignored — not +1/-1
        ],
    }]
    mock_ctx = _mock_history_session(messages)
    with patch("pipeline.reaction_collector.slack_mcp_session", return_value=mock_ctx):
        result = await fetch_reactions_for_pending(
            pending=[_pending("111.000")], channel_id="C123",
            history_limit=50, window_hours=48
        )
    assert len(result) == 1
    assert result[0].thumbs_up == 2
    assert result[0].thumbs_down == 1
    assert result[0].run_id == "r1"
    assert result[0].ticket_key == "SCRUM-1"


@pytest.mark.asyncio
async def test_fetch_reactions_unmatched_ts_returns_empty():
    messages = [{"ts": "999.000", "text": "other message", "reactions": []}]
    mock_ctx = _mock_history_session(messages)
    with patch("pipeline.reaction_collector.slack_mcp_session", return_value=mock_ctx):
        result = await fetch_reactions_for_pending(
            pending=[_pending("111.000")], channel_id="C123",
            history_limit=50, window_hours=48
        )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_reactions_returns_empty_on_mcp_error():
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("MCP down"))
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("pipeline.reaction_collector.slack_mcp_session", return_value=mock_ctx):
        result = await fetch_reactions_for_pending(
            pending=[_pending("111.000")], channel_id="C123",
            history_limit=50, window_hours=48
        )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_reactions_message_with_no_reactions_field():
    messages = [{"ts": "111.000", "text": "✅ Created SCRUM-1"}]  # no reactions key
    mock_ctx = _mock_history_session(messages)
    with patch("pipeline.reaction_collector.slack_mcp_session", return_value=mock_ctx):
        result = await fetch_reactions_for_pending(
            pending=[_pending("111.000")], channel_id="C123",
            history_limit=50, window_hours=48
        )
    assert len(result) == 1
    assert result[0].thumbs_up == 0
    assert result[0].thumbs_down == 0
