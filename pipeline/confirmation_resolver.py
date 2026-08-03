"""
Confirmation Resolver — Phase 10.

Runs once per triage cycle, before new Slack messages are processed. For each
PendingConfirmation left over from a previous run's low-confidence escalation:

  1. Fetch replies in that Slack thread.
  2. No reply, still within the age window → leave pending, do nothing.
  3. No reply, past PENDING_CONFIRMATION_MAX_AGE_HOURS → file as originally
     proposed (safety net — never silently drop it).
  4. Affirmative reply → file exactly as proposed.
  5. Any other reply → one LLM call to re-classify using the human's
     feedback, then file the corrected fields.

Never raises — a failure resolving one pending item (Jira down, LLM error,
Slack fetch error) is logged and that item is left pending for the next
cycle; it does not stop the rest of the batch.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from agents.llm.factory import get_llm_provider
from agents.llm.base import LLMProviderError
from agents.triage.tools.jira_tools import _create_ticket_in_jira
from agents.triage.tools.slack_tools import post_slack_message
from pipeline.slack_reader import fetch_thread_replies
from pipeline.pending_confirmation_store import (
    PendingConfirmation,
    PendingConfirmationStore,
    mark_resolved,
)
from config.settings import settings

AFFIRMATIVE_MARKERS = {
    "yes", "y", "yep", "yeah", "yup", "confirm", "confirmed", "correct",
    "go ahead", "approve", "approved", "lgtm", "+1", "do it", "sounds good",
    "ok", "okay",
}

_RECLASSIFY_PROMPT = """A teammate is reviewing a low-confidence Jira ticket proposal.

Original Slack message:
{block_snippet}

Originally proposed:
  Type: {proposed_issue_type}
  Priority: {proposed_priority}
  Summary: {proposed_summary}
  Description: {proposed_description}

The teammate replied with this correction:
"{correction_text}"

Update the ticket fields based on their feedback. Return JSON only, no other text:
{{"issue_type": "Bug|Story|Task", "priority": "Critical|High|Medium|Low", "summary": "...", "description": "...", "labels": ["..."]}}"""


def is_affirmative(text: str) -> bool:
    """True if the reply text is a simple confirmation, not a correction."""
    normalized = text.strip().lower().strip(".!")
    return normalized in AFFIRMATIVE_MARKERS


def _age_hours(created_at_iso: str) -> float:
    created = datetime.fromisoformat(created_at_iso)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created
    return delta.total_seconds() / 3600


async def _file_pending_item(
    item: PendingConfirmation,
    issue_type: str,
    priority: str,
    summary: str,
    description: str,
    labels: list[str],
    note: str,
) -> None:
    result = await _create_ticket_in_jira(summary, issue_type, priority, description, labels)
    await post_slack_message(f"{result}\n_{note}_")


async def _resolve_with_correction(item: PendingConfirmation, correction_text: str) -> None:
    """One LLM call to re-classify using the human's feedback, then file it."""
    prompt = _RECLASSIFY_PROMPT.format(
        block_snippet=item.block_snippet,
        proposed_issue_type=item.proposed_issue_type,
        proposed_priority=item.proposed_priority,
        proposed_summary=item.proposed_summary,
        proposed_description=item.proposed_description,
        correction_text=correction_text,
    )
    try:
        provider = get_llm_provider(settings)
        turn = await provider.chat(messages=[{"role": "user", "content": prompt}], tools=[])
        parsed = json.loads((turn.content or "").strip())
        await _file_pending_item(
            item,
            issue_type=parsed.get("issue_type", item.proposed_issue_type),
            priority=parsed.get("priority", item.proposed_priority),
            summary=parsed.get("summary", item.proposed_summary),
            description=parsed.get("description", item.proposed_description),
            labels=parsed.get("labels", item.proposed_labels),
            note="Filed with corrections from the team's reply.",
        )
    except (LLMProviderError, json.JSONDecodeError, ValueError, KeyError) as e:
        # Never crash on a malformed correction — file the original proposal
        # rather than silently dropping it (Rule 5 spirit).
        print(f"[confirmation_resolver] re-classify failed, filing original proposal: {e}")
        await _file_pending_item(
            item,
            issue_type=item.proposed_issue_type,
            priority=item.proposed_priority,
            summary=item.proposed_summary,
            description=item.proposed_description,
            labels=item.proposed_labels,
            note="Could not parse the correction — filed the original proposal instead.",
        )


async def resolve_pending_confirmations(
    store: PendingConfirmationStore,
    max_age_hours: int | None = None,
) -> PendingConfirmationStore:
    """
    Check every pending confirmation for a reply and act on it.
    Returns the same store, mutated in place (resolved items removed).
    """
    max_age = max_age_hours if max_age_hours is not None else settings.PENDING_CONFIRMATION_MAX_AGE_HOURS

    for item in list(store.items):  # copy — mark_resolved mutates store.items
        try:
            replies = await fetch_thread_replies(item.channel_id, item.proposal_ts)
        except Exception as e:
            print(f"[confirmation_resolver] failed to fetch replies for {item.proposal_ts}: {e}")
            continue  # leave pending, try again next cycle

        try:
            if replies:
                latest = replies[-1]["text"]
                if is_affirmative(latest):
                    await _file_pending_item(
                        item,
                        issue_type=item.proposed_issue_type,
                        priority=item.proposed_priority,
                        summary=item.proposed_summary,
                        description=item.proposed_description,
                        labels=item.proposed_labels,
                        note="Confirmed by the team.",
                    )
                else:
                    await _resolve_with_correction(item, latest)
                mark_resolved(store, item.proposal_ts)

            elif _age_hours(item.created_at) >= max_age:
                await _file_pending_item(
                    item,
                    issue_type=item.proposed_issue_type,
                    priority=item.proposed_priority,
                    summary=item.proposed_summary,
                    description=item.proposed_description,
                    labels=item.proposed_labels,
                    note=f"No response after {max_age}h — auto-filed as originally proposed.",
                )
                mark_resolved(store, item.proposal_ts)

            # else: still within the window, no reply — leave pending

        except Exception as e:
            print(f"[confirmation_resolver] failed to resolve {item.proposal_ts}: {e}")
            continue  # leave pending, try again next cycle

    return store
