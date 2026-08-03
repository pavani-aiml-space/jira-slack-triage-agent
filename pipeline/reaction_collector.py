"""
Reaction Collector — Phase 5 Eval & Feedback Loop

Fetches 👍/👎 reactions from Slack confirmation posts by polling channel history
once per run and matching messages by their Slack message_ts.

fetch_reactions_for_pending:
  - Returns [] immediately if pending list is empty (no MCP call needed)
  - Makes exactly one slack_get_channel_history call per run
  - Matches messages by ts; counts name="+1" as thumbs_up, name="-1" as thumbs_down
  - Returns [] on any MCP error (Rule 5 — never crashes the run)
  - Unmatched pending entries are silently skipped (Rule 9 — handled by apply_collected)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from pipeline.quality_metrics import CollectedReaction, PendingReaction
from pipeline.slack_reader import slack_mcp_session
from config.settings import settings


async def fetch_reactions_for_pending(
    pending: list[PendingReaction],
    channel_id: str,
    history_limit: int,
    window_hours: int,
) -> list[CollectedReaction]:
    """
    Fetch Slack channel history and extract reactions for each pending confirmation.

    Makes one slack_get_channel_history call.  Filters pending entries to those
    within window_hours of now before matching.  Only "+1" and "-1" reaction names
    count; all others are ignored.

    Returns [] if pending is empty, or on any MCP error (Rule 5).
    """
    if not pending:
        return []

    # Filter to entries within the reaction window
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    active_pending = [
        p for p in pending
        if _parse_iso(p.posted_at_iso) >= cutoff
    ]
    if not active_pending:
        return []

    # Build ts → pending lookup for fast matching
    ts_to_pending: dict[str, PendingReaction] = {
        p.confirmation_ts: p for p in active_pending
    }

    try:
        async with slack_mcp_session() as session:
            result = await session.call_tool(
                "slack_get_channel_history",
                arguments={
                    "channel_id": channel_id,
                    "limit":      history_limit,
                },
            )
        data = json.loads(result.content[0].text)
        messages = data.get("messages", [])
    except Exception as e:
        print(f"[reaction_collector] fetch failed (Rule 5): {e}")
        return []

    collected: list[CollectedReaction] = []
    collected_at = now.isoformat(timespec="seconds")

    for msg in messages:
        ts = msg.get("ts")
        if ts not in ts_to_pending:
            continue

        pending_entry = ts_to_pending[ts]
        reactions     = msg.get("reactions", [])
        thumbs_up   = sum(r["count"] for r in reactions if r.get("name") == "+1")
        thumbs_down = sum(r["count"] for r in reactions if r.get("name") == "-1")

        collected.append(CollectedReaction(
            run_id=pending_entry.run_id,
            block_index=pending_entry.block_index,
            ticket_key=pending_entry.ticket_key,
            thumbs_up=thumbs_up,
            thumbs_down=thumbs_down,
            collected_at=collected_at,
        ))

    return collected


def _parse_iso(iso_str: str) -> datetime:
    """Parse ISO timestamp string to timezone-aware datetime. Falls back to epoch on error."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
