"""
Unit tests for confirmation_resolver.py

All MCP calls, the LLM provider, and Jira/Slack tools are mocked.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, patch

from pipeline.confirmation_resolver import is_affirmative, resolve_pending_confirmations
from pipeline.pending_confirmation_store import PendingConfirmation, PendingConfirmationStore
from agents.llm.base import LLMTurn


# ── is_affirmative ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["yes", "Yes!", "YEP", "lgtm", "+1", "confirmed", "go ahead", " ok "])
def test_is_affirmative_true_cases(text):
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", [
    "it's actually a Task, low priority",
    "no",  # deliberately not in the affirmative set — treated as a correction/free text
    "not sure, can you clarify?",
    "",
])
def test_is_affirmative_false_cases(text):
    assert is_affirmative(text) is False


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_item(created_at=None, proposal_ts="1000.0", channel_id="C123"):
    return PendingConfirmation(
        run_id="r1", block_index=0, block_snippet="search feels broken today",
        proposed_summary="Investigate search regression", proposed_issue_type="Bug",
        proposed_priority="Medium", proposed_description="desc", proposed_labels=["search"],
        confidence=0.42, reasoning="Vague report", channel_id=channel_id,
        proposal_ts=proposal_ts,
        created_at=(created_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    )


# ── No reply, within window ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_reply_within_window_leaves_item_pending():
    store = PendingConfirmationStore(items=[make_item()])
    with patch("pipeline.confirmation_resolver.fetch_thread_replies", new_callable=AsyncMock, return_value=[]):
        result = await resolve_pending_confirmations(store, max_age_hours=72)
    assert len(result.items) == 1


# ── No reply, past max age ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_reply_past_max_age_auto_files_and_resolves():
    old = datetime.now(timezone.utc) - timedelta(hours=100)
    store = PendingConfirmationStore(items=[make_item(created_at=old)])
    with patch("pipeline.confirmation_resolver.fetch_thread_replies", new_callable=AsyncMock, return_value=[]):
        with patch(
            "pipeline.confirmation_resolver._create_ticket_in_jira",
            new_callable=AsyncMock, return_value="Created SCRUM-9: Investigate search regression",
        ) as mock_create:
            with patch("pipeline.confirmation_resolver.post_slack_message", new_callable=AsyncMock) as mock_post:
                result = await resolve_pending_confirmations(store, max_age_hours=72)
    assert result.items == []
    mock_create.assert_called_once()
    assert mock_create.call_args.args[0] == "Investigate search regression"
    assert "No response" in mock_post.call_args.args[0] or "auto-filed" in mock_post.call_args.args[0]


# ── Affirmative reply ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_affirmative_reply_files_original_proposal():
    store = PendingConfirmationStore(items=[make_item()])
    with patch(
        "pipeline.confirmation_resolver.fetch_thread_replies",
        new_callable=AsyncMock, return_value=[{"user": "U1", "text": "yes", "ts": "1005.0"}],
    ):
        with patch(
            "pipeline.confirmation_resolver._create_ticket_in_jira",
            new_callable=AsyncMock, return_value="Created SCRUM-9: Investigate search regression",
        ) as mock_create:
            with patch("pipeline.confirmation_resolver.post_slack_message", new_callable=AsyncMock):
                result = await resolve_pending_confirmations(store, max_age_hours=72)
    assert result.items == []
    mock_create.assert_called_once_with(
        "Investigate search regression", "Bug", "Medium", "desc", ["search"],
    )


# ── Correction reply ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_correction_reply_calls_llm_and_files_corrected_fields():
    store = PendingConfirmationStore(items=[make_item()])
    corrected = {
        "issue_type": "Task", "priority": "Low",
        "summary": "Follow up on search indexing job", "description": "updated desc",
        "labels": ["search", "backend"],
    }
    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=LLMTurn(
        finish_reason="stop", content=json.dumps(corrected),
    ))
    with patch(
        "pipeline.confirmation_resolver.fetch_thread_replies",
        new_callable=AsyncMock,
        return_value=[{"user": "U1", "text": "it's actually a low-priority Task", "ts": "1005.0"}],
    ):
        with patch("pipeline.confirmation_resolver.get_llm_provider", return_value=mock_provider):
            with patch(
                "pipeline.confirmation_resolver._create_ticket_in_jira",
                new_callable=AsyncMock, return_value="Created SCRUM-9",
            ) as mock_create:
                with patch("pipeline.confirmation_resolver.post_slack_message", new_callable=AsyncMock):
                    result = await resolve_pending_confirmations(store, max_age_hours=72)
    assert result.items == []
    mock_create.assert_called_once_with(
        "Follow up on search indexing job", "Task", "Low", "updated desc", ["search", "backend"],
    )


@pytest.mark.asyncio
async def test_correction_reply_malformed_llm_response_falls_back_to_original():
    store = PendingConfirmationStore(items=[make_item()])
    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=LLMTurn(finish_reason="stop", content="not json"))
    with patch(
        "pipeline.confirmation_resolver.fetch_thread_replies",
        new_callable=AsyncMock,
        return_value=[{"user": "U1", "text": "hmm not quite right", "ts": "1005.0"}],
    ):
        with patch("pipeline.confirmation_resolver.get_llm_provider", return_value=mock_provider):
            with patch(
                "pipeline.confirmation_resolver._create_ticket_in_jira",
                new_callable=AsyncMock, return_value="Created SCRUM-9",
            ) as mock_create:
                with patch("pipeline.confirmation_resolver.post_slack_message", new_callable=AsyncMock):
                    result = await resolve_pending_confirmations(store, max_age_hours=72)
    assert result.items == []  # still resolved — never gets stuck
    mock_create.assert_called_once_with(
        "Investigate search regression", "Bug", "Medium", "desc", ["search"],
    )


# ── Failure isolation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_replies_failure_leaves_item_pending_not_crash():
    store = PendingConfirmationStore(items=[make_item()])
    with patch(
        "pipeline.confirmation_resolver.fetch_thread_replies",
        new_callable=AsyncMock, side_effect=Exception("Slack MCP down"),
    ):
        result = await resolve_pending_confirmations(store, max_age_hours=72)
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_one_item_failure_does_not_block_others():
    good = make_item(proposal_ts="ts-good")
    bad = make_item(proposal_ts="ts-bad")
    store = PendingConfirmationStore(items=[bad, good])

    async def fetch_side_effect(channel_id, thread_ts):
        if thread_ts == "ts-bad":
            raise Exception("boom")
        return [{"user": "U1", "text": "yes", "ts": "1005.0"}]

    with patch("pipeline.confirmation_resolver.fetch_thread_replies", side_effect=fetch_side_effect):
        with patch(
            "pipeline.confirmation_resolver._create_ticket_in_jira",
            new_callable=AsyncMock, return_value="Created SCRUM-9",
        ):
            with patch("pipeline.confirmation_resolver.post_slack_message", new_callable=AsyncMock):
                result = await resolve_pending_confirmations(store, max_age_hours=72)
    remaining_ts = [item.proposal_ts for item in result.items]
    assert remaining_ts == ["ts-bad"]  # bad one stays pending, good one resolved
