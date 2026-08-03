"""
Unit tests for confirmation_tools.py

All MCP calls are mocked. Pending-store I/O is tmp_path-isolated via
monkeypatching settings.PENDING_CONFIRMATION_STORE_PATH.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.triage.tools.confirmation_tools import escalate_for_confirmation
from pipeline.pending_confirmation_store import load_pending_store


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mcp_response(ts: str) -> AsyncMock:
    block = MagicMock()
    block.text = json.dumps({"ts": ts})
    result = MagicMock()
    result.content = [block]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result)
    return session


def patch_slack_session(session: AsyncMock):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("agents.triage.tools.confirmation_tools.slack_mcp_session", return_value=mock_ctx)


def block_context(**overrides):
    ctx = {"run_id": "r1", "block_index": 0, "block_snippet": "search feels broken today"}
    ctx.update(overrides)
    return ctx


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    path = str(tmp_path / "pending.json")
    monkeypatch.setattr(
        "agents.triage.tools.confirmation_tools.settings.PENDING_CONFIRMATION_STORE_PATH",
        path,
    )
    return path


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_posts_message_with_proposed_classification(isolated_store):
    session = make_mcp_response("1714045800.0001")
    with patch_slack_session(session):
        await escalate_for_confirmation(
            summary="Investigate search regression", issue_type="Bug", priority="Medium",
            description="desc", labels=["search"], confidence=0.42,
            reasoning="Vague report, no repro steps", block_context=block_context(),
        )
    args = session.call_tool.call_args[1]["arguments"]
    assert "Investigate search regression" in args["text"]
    assert "Bug" in args["text"]
    assert "0.42" in args["text"]
    assert "Vague report" in args["text"]


@pytest.mark.asyncio
async def test_returns_escalated_prefix(isolated_store):
    session = make_mcp_response("1714045800.0001")
    with patch_slack_session(session):
        result = await escalate_for_confirmation(
            summary="s", issue_type="Bug", priority="Medium", description="d",
            labels=[], confidence=0.42, reasoning="", block_context=block_context(),
        )
    assert result.startswith("[ESCALATED]")
    assert "0.42" in result


@pytest.mark.asyncio
async def test_persists_pending_confirmation_with_correct_fields(isolated_store):
    session = make_mcp_response("1714045800.0001")
    with patch_slack_session(session):
        await escalate_for_confirmation(
            summary="Investigate search regression", issue_type="Bug", priority="Medium",
            description="## What\nsearch broken\n", labels=["search"], confidence=0.42,
            reasoning="Vague report", block_context=block_context(),
        )
    store = load_pending_store(isolated_store)
    assert len(store.items) == 1
    item = store.items[0]
    assert item.proposed_summary == "Investigate search regression"
    assert item.proposed_issue_type == "Bug"
    assert item.confidence == 0.42
    assert item.proposal_ts == "1714045800.0001"
    assert item.run_id == "r1"
    assert item.block_snippet == "search feels broken today"
    assert item.status == "pending"


@pytest.mark.asyncio
async def test_missing_block_context_uses_defaults(isolated_store):
    session = make_mcp_response("1714045800.0001")
    with patch_slack_session(session):
        await escalate_for_confirmation(
            summary="s", issue_type="Bug", priority="Medium", description="d",
            labels=[], confidence=0.42, reasoning="", block_context={},
        )
    store = load_pending_store(isolated_store)
    assert store.items[0].run_id == "unknown"
    assert store.items[0].block_index == -1


# ── Failure handling (Rule 5 — never raise) ───────────────────────────────────

@pytest.mark.asyncio
async def test_slack_post_failure_returns_error_string_not_raise(isolated_store):
    with patch(
        "agents.triage.tools.confirmation_tools.slack_mcp_session",
        side_effect=Exception("Slack down"),
    ):
        result = await escalate_for_confirmation(
            summary="s", issue_type="Bug", priority="Medium", description="d",
            labels=[], confidence=0.42, reasoning="", block_context=block_context(),
        )
    assert result.startswith("[ESCALATION_ERROR]")


@pytest.mark.asyncio
async def test_slack_post_failure_does_not_persist_pending_item(isolated_store):
    with patch(
        "agents.triage.tools.confirmation_tools.slack_mcp_session",
        side_effect=Exception("Slack down"),
    ):
        await escalate_for_confirmation(
            summary="s", issue_type="Bug", priority="Medium", description="d",
            labels=[], confidence=0.42, reasoning="", block_context=block_context(),
        )
    store = load_pending_store(isolated_store)
    assert store.items == []


@pytest.mark.asyncio
async def test_missing_ts_in_response_returns_error_and_does_not_persist(isolated_store):
    block = MagicMock()
    block.text = json.dumps({})  # no "ts"
    result_obj = MagicMock()
    result_obj.content = [block]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result_obj)
    with patch_slack_session(session):
        result = await escalate_for_confirmation(
            summary="s", issue_type="Bug", priority="Medium", description="d",
            labels=[], confidence=0.42, reasoning="", block_context=block_context(),
        )
    assert result.startswith("[ESCALATION_ERROR]")
    store = load_pending_store(isolated_store)
    assert store.items == []
