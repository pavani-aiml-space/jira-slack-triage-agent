"""
LLM-as-Judge — scores each ticket_created block after a triage run.

Uses a separate model (default: gpt-4o-mini) via the same LLMProvider interface as triage.
Scores are 1–5 on type fit, priority fit, title quality, and description completeness.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from agents.llm.base import LLMProvider, LLMProviderError
from agents.llm.factory import get_judge_llm_provider
from config.settings import settings
from pipeline.judge_store import JudgeScoreEntry, append_judge_entries
from pipeline.run_logger import BlockResult, RunLog

JUDGE_SYSTEM = """You are an expert engineering triage judge. Your job is to rate how well a Jira ticket matches the Slack conversation it came from.

Score each dimension from 1 (poor) to 5 (excellent):
- type: Is issue_type (Bug / Story / Task) appropriate for what was reported?
- priority: Is priority (Critical / High / Medium / Low) appropriate for impact and urgency described?
- title: Is the summary clear, specific, and actionable?
- description: Does the description capture what/why/steps/context reasonably given the Slack text?

Return ONLY a single JSON object with keys: type, priority, title, description (each integer 1-5), and reason (one short sentence explaining the main strength or gap). No markdown fences, no other text."""


def _extract_json_object(text: str) -> dict | None:
    """Parse first JSON object from model output; tolerate ```json fences."""
    if not text:
        return None
    s = text.strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _normalise_scores(raw: dict) -> tuple[dict[str, int], str] | tuple[None, str]:
    """Validate keys and integer ranges; return (scores_dict, reason) or (None, error)."""
    out: dict[str, int] = {}
    for key in ("type", "priority", "title", "description"):
        v = raw.get(key)
        if not isinstance(v, int) or v < 1 or v > 5:
            return None, f"invalid or missing score for '{key}': {v!r}"
        out[key] = v
    reason = raw.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason)
    return out, reason


async def judge_one_block(
    provider: LLMProvider,
    run_id: str,
    block: BlockResult,
    *,
    slack_context: str | None = None,
) -> JudgeScoreEntry:
    """
    One LLM call; returns JudgeScoreEntry (with error set on failure).

    slack_context: when set (e.g. full Slack text from label_fixtures), used as the
    conversation shown to the judge instead of block_snippet (triage only stores ~60 chars).
    """
    judged_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    raw_slack = slack_context if slack_context is not None else (block.block_snippet or "")
    slack_excerpt = raw_slack[:8000]
    agent_payload = {
        "ticket_key": block.ticket_key,
        "summary": block.ticket_summary,
        "issue_type": block.ticket_type,
        "priority": block.ticket_priority,
        "description": (block.ticket_description or "")[:8000],
    }
    user_msg = (
        "Slack conversation excerpt (may be truncated):\n"
        f"{slack_excerpt}\n\n"
        "Agent-created Jira ticket fields:\n"
        f"{json.dumps(agent_payload, indent=2)}"
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        turn = await provider.chat(messages, [], system=JUDGE_SYSTEM)
    except LLMProviderError as e:
        return JudgeScoreEntry(
            run_id=run_id,
            block_index=block.block_index,
            ticket_key=block.ticket_key,
            judged_at=judged_at,
            error=str(e),
        )
    except Exception as e:
        return JudgeScoreEntry(
            run_id=run_id,
            block_index=block.block_index,
            ticket_key=block.ticket_key,
            judged_at=judged_at,
            error=f"{type(e).__name__}: {e}",
        )

    parsed = _extract_json_object(turn.content or "")
    if not parsed:
        return JudgeScoreEntry(
            run_id=run_id,
            block_index=block.block_index,
            ticket_key=block.ticket_key,
            judged_at=judged_at,
            error="judge returned no parseable JSON",
        )

    scores, reason_or_err = _normalise_scores(parsed)
    if scores is None:
        return JudgeScoreEntry(
            run_id=run_id,
            block_index=block.block_index,
            ticket_key=block.ticket_key,
            judged_at=judged_at,
            error=reason_or_err,
        )

    return JudgeScoreEntry(
        run_id=run_id,
        block_index=block.block_index,
        ticket_key=block.ticket_key,
        judged_at=judged_at,
        type_score=scores["type"],
        priority_score=scores["priority"],
        title_score=scores["title"],
        description_score=scores["description"],
        reason=reason_or_err,
    )


async def run_judge_for_run(run_log: RunLog) -> None:
    """
    After triage: score every ticket_created block in parallel, append to judge_store.

    Skips entirely when ENABLE_LLM_JUDGE is false. Never raises (Rule 5).
    """
    if not settings.ENABLE_LLM_JUDGE:
        return
    try:
        blocks = [
            b for b in run_log.blocks
            if b.action == "ticket_created" and b.ticket_key
        ]
        if not blocks:
            return
        try:
            provider = get_judge_llm_provider(settings)
        except Exception as e:
            print(f"[llm_judge] could not build judge provider: {e}")
            return

        tasks = [judge_one_block(provider, run_log.run_id, b) for b in blocks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        entries: list[JudgeScoreEntry] = []
        for block, res in zip(blocks, results):
            if isinstance(res, Exception):
                entries.append(
                    JudgeScoreEntry(
                        run_id=run_log.run_id,
                        block_index=block.block_index,
                        ticket_key=block.ticket_key,
                        judged_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                        error=f"{type(res).__name__}: {res}",
                    )
                )
            else:
                entries.append(res)
        append_judge_entries(settings.JUDGE_STORE_PATH, entries)
    except Exception as e:
        print(f"[llm_judge] run_judge_for_run failed: {e}")
