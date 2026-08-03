"""
Eval Runner — Phase 5 Eval & Feedback Loop

Owns the eval lifecycle that wraps every triage run.
Called twice by run_triage.py:

  Pre-triage  (run_log=None):
    - Load quality store
    - If pending reactions exist: poll Slack, compute metrics, alert if needed
    - Save store

  Post-triage (run_log given):
    - Load quality store
    - Register new confirmation posts from the completed run
    - Save store
    - If ENABLE_LLM_JUDGE: run LLM-as-Judge on each ticket_created block, append to judge_store.json
"""
from __future__ import annotations

from typing import Optional

from pipeline.run_logger import RunLog
from agents.triage.tools.slack_tools import post_slack_message
from config.settings import settings
from pipeline.quality_metrics import (
    QualityStore,
    RunQuality,
    add_pending_from_run,
    apply_collected,
    load_quality_store,
    save_quality_store,
    should_alert,
)
from pipeline.reaction_collector import fetch_reactions_for_pending
from pipeline.llm_judge import run_judge_for_run


async def run_eval_step(run_log: RunLog | None = None) -> None:
    """
    Pre-triage (run_log=None):  collect reactions → compute metrics → alert if needed.
    Post-triage (run_log given): register new pending confirmation posts.

    Never raises — all failures are logged and the run continues (Rule 5).
    """
    store = load_quality_store(settings.QUALITY_STORE_PATH)

    if run_log is None:
        await _pre_triage_step(store)
    else:
        _post_triage_step(store, run_log)
        save_quality_store(store, settings.QUALITY_STORE_PATH)
        await run_judge_for_run(run_log)


async def _pre_triage_step(store: QualityStore) -> None:
    """Collect reactions from prior runs, compute quality, alert if below threshold."""
    if not store.pending:
        return  # nothing to collect — skip MCP call entirely

    collected = await fetch_reactions_for_pending(
        pending=store.pending,
        channel_id=settings.SLACK_CHANNEL_ID,
        history_limit=settings.REACTION_HISTORY_LIMIT,
        window_hours=settings.REACTION_WINDOW_HOURS,
    )
    apply_collected(store, collected)

    alert, rq = should_alert(
        store,
        threshold=settings.QUALITY_ALERT_THRESHOLD,
        min_reactions=settings.MIN_REACTIONS_FOR_QUALITY,
    )
    if alert and rq is not None:
        try:
            await post_slack_message(_quality_alert_message(rq))
        except Exception as e:
            print(f"[eval_runner] quality alert post failed (Rule 5): {e}")

    save_quality_store(store, settings.QUALITY_STORE_PATH)


def _post_triage_step(store: QualityStore, run_log: RunLog) -> None:
    """Register new confirmation posts from the just-completed run."""
    add_pending_from_run(store, run_log)


def _quality_alert_message(rq: RunQuality) -> str:
    rate_pct = f"{rq.thumbs_up_rate * 100:.0f}%" if rq.thumbs_up_rate is not None else "N/A"
    threshold_pct = f"{settings.QUALITY_ALERT_THRESHOLD * 100:.0f}%"
    return (
        f"⚠️ Quality alert — run {rq.run_id[:16]}\n"
        f"Thumbs-up rate: {rate_pct} (threshold: {threshold_pct})\n"
        f"Reactions this run: 👍 {rq.thumbs_up}  👎 {rq.thumbs_down}\n"
        f"Please review recent ticket classifications."
    )
