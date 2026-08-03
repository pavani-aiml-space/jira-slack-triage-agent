"""
Unit tests for jira_tools.py

All MCP calls are mocked — no real Jira connection, no real subprocess.

_create_ticket_in_jira() is the raw Jira MCP call (unchanged since Phase 1).
create_jira_ticket() is the Phase 10 routing wrapper around it — its own
behavior (auto_act/flag/escalate) is tested separately below.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.triage.tools.jira_tools import (
    _create_ticket_in_jira,
    create_jira_ticket,
    JIRA_CREATE_TOOL,
)
from config.settings import settings


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mcp_response(payload: dict) -> AsyncMock:
    """Build a mock MCP call_tool response with one content block."""
    block = MagicMock()
    block.text = json.dumps(payload)
    result = MagicMock()
    result.content = [block]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result)
    return session


def patch_jira_session(session: AsyncMock):
    """Context manager that replaces jira_mcp_session with a mock."""
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("agents.triage.tools.jira_tools.jira_mcp_session", return_value=mock_ctx)


# ══════════════════════════════════════════════════════════════════════════
# _create_ticket_in_jira — the raw MCP call (Phase 1 behavior, unchanged)
# ══════════════════════════════════════════════════════════════════════════

# ── Return value ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_key_and_summary_in_result():
    session = make_mcp_response({"issue": {"key": "SCRUM-42"}})
    with patch_jira_session(session):
        result = await _create_ticket_in_jira("Fix crash", "Bug", "High", "desc")
    assert "SCRUM-42" in result
    assert "Fix crash" in result


@pytest.mark.asyncio
async def test_returns_url_in_result():
    session = make_mcp_response({"issue": {"key": "SCRUM-42"}})
    with patch_jira_session(session):
        result = await _create_ticket_in_jira("Fix crash", "Bug", "High", "desc")
    assert "SCRUM-42" in result
    assert "→" in result


@pytest.mark.asyncio
async def test_unknown_key_falls_back_to_unknown():
    session = make_mcp_response({})          # no "issue" or "key"
    with patch_jira_session(session):
        result = await _create_ticket_in_jira("s", "Bug", "High", "d")
    assert "unknown" in result


@pytest.mark.asyncio
async def test_malformed_json_response_falls_back_to_unknown():
    block = MagicMock()
    block.text = "not json {"
    mock_result = MagicMock()
    mock_result.content = [block]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=mock_result)
    with patch_jira_session(session):
        result = await _create_ticket_in_jira("s", "Bug", "High", "d")
    assert "unknown" in result


# ── Correct tool called ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calls_jira_create_issue_tool():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await _create_ticket_in_jira("s", "Bug", "High", "d")
    assert session.call_tool.call_args[0][0] == JIRA_CREATE_TOOL


@pytest.mark.asyncio
async def test_sends_correct_project_key():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await _create_ticket_in_jira("s", "Bug", "High", "d")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["project_key"] == settings.JIRA_PROJECT_KEY


@pytest.mark.asyncio
async def test_sends_summary_and_issue_type():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await _create_ticket_in_jira("Fix login crash", "Story", "High", "d")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["summary"] == "Fix login crash"
    assert args["issue_type"] == "Story"


# ── additional_fields (priority + labels) ────────────────────────────────────

@pytest.mark.asyncio
async def test_priority_sent_via_additional_fields():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await _create_ticket_in_jira("s", "Bug", "Critical", "d")
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert additional["priority"]["name"] == "Critical"


@pytest.mark.asyncio
async def test_labels_sent_via_additional_fields_when_provided():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await _create_ticket_in_jira("s", "Bug", "High", "d", labels=["login", "regression"])
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert additional["labels"] == ["login", "regression"]


@pytest.mark.asyncio
async def test_labels_omitted_from_additional_fields_when_none():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await _create_ticket_in_jira("s", "Bug", "High", "d")
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert "labels" not in additional


@pytest.mark.asyncio
async def test_empty_labels_list_omitted_from_additional_fields():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await _create_ticket_in_jira("s", "Bug", "High", "d", labels=[])
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert "labels" not in additional


# ── Rule 1 — Jira error handler ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_jira_mcp_unavailable_posts_slack_alert():
    """When Jira MCP raises, post_slack_message is called with an alert."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("uvx subprocess failed")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock) as mock_post:
            await _create_ticket_in_jira(
                summary="Fix login crash",
                issue_type="Bug",
                priority="High",
                description="Crashes on empty password",
            )
    mock_post.assert_called_once()
    alert_text = mock_post.call_args[0][0]
    assert "Jira unavailable" in alert_text
    assert "Fix login crash" in alert_text


@pytest.mark.asyncio
async def test_jira_mcp_unavailable_returns_jira_error_string():
    """When Jira MCP raises, the return value starts with [JIRA_ERROR]."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("uvx subprocess failed")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock):
            result = await _create_ticket_in_jira(
                summary="Fix login crash",
                issue_type="Bug",
                priority="High",
                description="Crashes on empty password",
            )
    assert result.startswith("[JIRA_ERROR]")
    assert "Fix login crash" in result


@pytest.mark.asyncio
async def test_jira_and_slack_both_down_propagates():
    """When both Jira and Slack MCP fail, the Slack exception propagates."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("Jira down")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock,
                   side_effect=Exception("Slack also down")):
            with pytest.raises(Exception, match="Slack also down"):
                await _create_ticket_in_jira(
                    summary="Fix login crash",
                    issue_type="Bug",
                    priority="High",
                    description="desc",
                )


# ══════════════════════════════════════════════════════════════════════════
# create_jira_ticket — Phase 10 confidence routing wrapper
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_auto_act_tier_files_ticket_unchanged():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        result = await create_jira_ticket("s", "Bug", "High", "d", confidence=0.95)
    assert "SCRUM-1" in result
    assert "flagged" not in result


@pytest.mark.asyncio
async def test_auto_act_tier_does_not_add_needs_review_label():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "High", "d", confidence=0.95)
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert "labels" not in additional


@pytest.mark.asyncio
async def test_flag_tier_adds_needs_review_label():
    session = make_mcp_response({"issue": {"key": "SCRUM-2"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "Medium", "d", confidence=0.75)
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert "needs-review" in additional["labels"]


@pytest.mark.asyncio
async def test_flag_tier_preserves_existing_labels_and_adds_needs_review():
    session = make_mcp_response({"issue": {"key": "SCRUM-2"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "Medium", "d", confidence=0.75, labels=["login"])
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert set(additional["labels"]) == {"login", "needs-review"}


@pytest.mark.asyncio
async def test_flag_tier_appends_confidence_note_to_result():
    session = make_mcp_response({"issue": {"key": "SCRUM-2"}})
    with patch_jira_session(session):
        result = await create_jira_ticket("s", "Bug", "Medium", "d", confidence=0.75)
    assert "flagged for review" in result
    assert "0.75" in result


@pytest.mark.asyncio
async def test_escalate_tier_never_calls_jira_mcp():
    with patch("agents.triage.tools.jira_tools.jira_mcp_session") as mock_session:
        with patch(
            "agents.triage.tools.confirmation_tools.escalate_for_confirmation",
            new_callable=AsyncMock,
            return_value="[ESCALATED] posted for confirmation",
        ):
            await create_jira_ticket("s", "Bug", "Medium", "d", confidence=0.4)
    mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_escalate_tier_delegates_to_confirmation_tools_with_correct_args():
    with patch(
        "agents.triage.tools.confirmation_tools.escalate_for_confirmation",
        new_callable=AsyncMock,
        return_value="[ESCALATED]",
    ) as mock_escalate:
        await create_jira_ticket(
            "Fix login", "Bug", "Medium", "d",
            confidence=0.3, labels=["login"], reasoning="unclear repro steps",
            block_context={"run_id": "r1", "block_index": 0, "block_snippet": "login broke"},
        )
    mock_escalate.assert_called_once()
    kwargs = mock_escalate.call_args.kwargs
    assert kwargs["summary"] == "Fix login"
    assert kwargs["confidence"] == 0.3
    assert kwargs["reasoning"] == "unclear repro steps"
    assert kwargs["block_context"]["run_id"] == "r1"


@pytest.mark.asyncio
async def test_escalate_tier_returns_escalation_result_directly():
    with patch(
        "agents.triage.tools.confirmation_tools.escalate_for_confirmation",
        new_callable=AsyncMock,
        return_value="[ESCALATED] Low confidence (0.30) — posted for human confirmation.",
    ):
        result = await create_jira_ticket("s", "Bug", "Medium", "d", confidence=0.3)
    assert result.startswith("[ESCALATED]")


@pytest.mark.asyncio
async def test_boundary_confidence_at_auto_act_threshold_is_auto_act():
    session = make_mcp_response({"issue": {"key": "SCRUM-3"}})
    with patch_jira_session(session):
        result = await create_jira_ticket("s", "Bug", "High", "d", confidence=settings.CONFIDENCE_AUTO_ACT)
    assert "flagged" not in result
