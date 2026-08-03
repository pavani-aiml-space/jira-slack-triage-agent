"""
Confirmation Tools — Phase 10 low-confidence escalation.

Not an LLM-callable tool. create_jira_ticket() calls escalate_for_confirmation()
directly when confidence routes to "escalate" — the LLM never chooses this path
itself; the routing gate is code-enforced (see docs/plans/2026-08-03-confidence-routing-design.md).

Posts the proposed classification to Slack asking for confirmation, and
persists it as a PendingConfirmation for a later run's
confirmation_resolver.resolve_pending_confirmations() to check.
"""
import json
from datetime import datetime, timezone

from pipeline.slack_reader import slack_mcp_session
from pipeline.pending_confirmation_store import (
    PendingConfirmation,
    load_pending_store,
    save_pending_store,
    add_pending,
)
from config.settings import settings


async def escalate_for_confirmation(
    summary: str,
    issue_type: str,
    priority: str,
    description: str,
    labels: list[str],
    confidence: float,
    reasoning: str,
    block_context: dict,
) -> str:
    """
    Post the proposed ticket to Slack asking for confirmation, persist it as
    pending, and return a result string for the LLM.

    Never raises (Rule 5) — if the Slack post itself fails, returns an error
    string instead of a crash; nothing is persisted in that case since there's
    no proposal_ts to resolve against later.
    """
    message = (
        f"🤔 Low confidence ({confidence:.2f}) on this one — I'm not filing it automatically.\n\n"
        f"*Proposed:* {issue_type} / {priority} — \"{summary}\"\n"
        f"*Reasoning:* {reasoning or 'not provided'}\n\n"
        f"Reply *yes* to file it as proposed, or reply with corrections "
        f"(e.g. \"it's actually a Task, low priority\") and I'll use that instead."
    )

    try:
        async with slack_mcp_session() as session:
            result = await session.call_tool(
                "slack_post_message",
                arguments={
                    "channel_id": settings.SLACK_CHANNEL_ID,
                    "text":       message,
                },
            )
        data = json.loads(result.content[0].text)
        proposal_ts = str(data.get("ts", ""))
    except Exception as e:
        return (
            f"[ESCALATION_ERROR] Could not post confirmation request to Slack ({e}). "
            f"Ticket was NOT filed. Summary was: {summary}"
        )

    if not proposal_ts:
        return (
            f"[ESCALATION_ERROR] Confirmation posted to Slack but no message ts was "
            f"returned — cannot track a reply. Ticket was NOT filed. Summary was: {summary}"
        )

    item = PendingConfirmation(
        run_id=block_context.get("run_id", "unknown"),
        block_index=block_context.get("block_index", -1),
        block_snippet=block_context.get("block_snippet", ""),
        proposed_summary=summary,
        proposed_issue_type=issue_type,
        proposed_priority=priority,
        proposed_description=description,
        proposed_labels=list(labels or []),
        confidence=confidence,
        reasoning=reasoning,
        channel_id=settings.SLACK_CHANNEL_ID,
        proposal_ts=proposal_ts,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    store = load_pending_store(settings.PENDING_CONFIRMATION_STORE_PATH)
    add_pending(store, item)
    save_pending_store(store, settings.PENDING_CONFIRMATION_STORE_PATH)

    return (
        f"[ESCALATED] Low confidence ({confidence:.2f}) — posted for human confirmation. "
        f"Do not post an additional confirmation message."
    )
