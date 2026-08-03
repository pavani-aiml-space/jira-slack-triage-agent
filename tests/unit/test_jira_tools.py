"""
Unit tests for jira_tools.py

All MCP calls are mocked — no real Jira connection, no real subprocess.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.triage.tools.jira_tools import create_jira_ticket, JIRA_CREATE_TOOL


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


# ── Return value ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_returns_key_and_summary_in_result():
    session = make_mcp_response({"issue": {"key": "SCRUM-42"}})
    with patch_jira_session(session):
        result = await create_jira_ticket("Fix crash", "Bug", "High", "desc")
    assert "SCRUM-42" in result
    assert "Fix crash" in result


@pytest.mark.asyncio
async def test_returns_url_in_result():
    session = make_mcp_response({"issue": {"key": "SCRUM-42"}})
    with patch_jira_session(session):
        result = await create_jira_ticket("Fix crash", "Bug", "High", "desc")
    assert "SCRUM-42" in result
    assert "→" in result


@pytest.mark.asyncio
async def test_unknown_key_falls_back_to_unknown():
    session = make_mcp_response({})          # no "issue" or "key"
    with patch_jira_session(session):
        result = await create_jira_ticket("s", "Bug", "High", "d")
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
        result = await create_jira_ticket("s", "Bug", "High", "d")
    assert "unknown" in result


# ── Correct tool called ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calls_jira_create_issue_tool():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "High", "d")
    assert session.call_tool.call_args[0][0] == JIRA_CREATE_TOOL


@pytest.mark.asyncio
async def test_sends_correct_project_key():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "High", "d")
    args = session.call_tool.call_args[1]["arguments"]
    from config.settings import settings
    assert args["project_key"] == settings.JIRA_PROJECT_KEY


@pytest.mark.asyncio
async def test_sends_summary_and_issue_type():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("Fix login crash", "Story", "High", "d")
    args = session.call_tool.call_args[1]["arguments"]
    assert args["summary"] == "Fix login crash"
    assert args["issue_type"] == "Story"


# ── additional_fields (priority + labels) ────────────────────────────────────

@pytest.mark.asyncio
async def test_priority_sent_via_additional_fields():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "Critical", "d")
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert additional["priority"]["name"] == "Critical"


@pytest.mark.asyncio
async def test_labels_sent_via_additional_fields_when_provided():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "High", "d", labels=["login", "regression"])
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert additional["labels"] == ["login", "regression"]


@pytest.mark.asyncio
async def test_labels_omitted_from_additional_fields_when_none():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "High", "d")
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert "labels" not in additional


@pytest.mark.asyncio
async def test_empty_labels_list_omitted_from_additional_fields():
    session = make_mcp_response({"issue": {"key": "SCRUM-1"}})
    with patch_jira_session(session):
        await create_jira_ticket("s", "Bug", "High", "d", labels=[])
    args = session.call_tool.call_args[1]["arguments"]
    additional = json.loads(args["additional_fields"])
    assert "labels" not in additional


# ── Rule 1 — Jira error handler ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_jira_ticket_jira_mcp_unavailable_posts_slack_alert():
    """When Jira MCP raises, post_slack_message is called with an alert."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("uvx subprocess failed")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock) as mock_post:
            await create_jira_ticket(
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
async def test_create_jira_ticket_jira_mcp_unavailable_returns_jira_error_string():
    """When Jira MCP raises, the return value starts with [JIRA_ERROR]."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("uvx subprocess failed")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock):
            result = await create_jira_ticket(
                summary="Fix login crash",
                issue_type="Bug",
                priority="High",
                description="Crashes on empty password",
            )
    assert result.startswith("[JIRA_ERROR]")
    assert "Fix login crash" in result


@pytest.mark.asyncio
async def test_create_jira_ticket_jira_and_slack_both_down_propagates():
    """When both Jira and Slack MCP fail, the Slack exception propagates."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("Jira down")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock,
                   side_effect=Exception("Slack also down")):
            with pytest.raises(Exception, match="Slack also down"):
                await create_jira_ticket(
                    summary="Fix login crash",
                    issue_type="Bug",
                    priority="High",
                    description="desc",
                )
