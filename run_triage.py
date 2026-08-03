"""
Entry point for the Triage Agent.

Usage:
    python run_triage.py                  # run once and exit
    python run_triage.py --schedule 5     # run every 5 minutes (overrides SCHEDULE_INTERVAL_MINUTES)

Five-step run orchestration per iteration:
    1. memory_runner.pre_run  — load memory stores, build MemoryContext
    2. run_eval_step(None)    — collect Slack reactions from previous run
    3. triage_run             — read Slack, classify, create tickets / ask clarification
    4. run_eval_step(run_log) — register confirmation posts for next run's reaction tracking
    5. memory_runner.post_run — write new episodes, extract semantic patterns

Watermark:
    After each successful run, the ts of the last processed message is saved to
    WATERMARK_PATH (default: memory/watermark.json). On the next run, only messages
    newer than that ts are fetched — so the agent never re-processes old messages and
    never misses new ones between scheduled runs.

Scheduling:
    --schedule N (or SCHEDULE_INTERVAL_MINUTES env var) runs in a continuous loop,
    sleeping N minutes between iterations. Each iteration processes only the messages
    that arrived since the previous run's watermark.
"""
import argparse
import asyncio
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path

# load environment variables before anything else
load_dotenv(dotenv_path=Path(__file__).parent / "config" / ".env")

from agents.triage.triage_agent import run as triage_run
from config.settings import settings
from pipeline import memory_runner
from pipeline.eval_runner import run_eval_step
from pipeline.run_logger import SENTINEL_FILE
from pipeline.watermark import load_watermark, save_watermark


async def main(oldest: str | None = None) -> str | None:
    """
    One full triage iteration.

    Returns the last_message_ts from the completed run (to use as the next
    watermark), or None if no messages were processed.
    """
    memory_context = await memory_runner.pre_run()
    await run_eval_step(run_log=None)
    run_log = await triage_run(memory_context=memory_context, oldest=oldest)
    await run_eval_step(run_log=run_log)
    await memory_runner.post_run(run_log)
    return run_log.last_message_ts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="JiraSlack triage agent — reads Slack, creates Jira tickets.",
    )
    parser.add_argument(
        "--schedule",
        metavar="MINUTES",
        type=int,
        default=None,
        help=(
            "Run on a repeating schedule every MINUTES minutes. "
            "Overrides SCHEDULE_INTERVAL_MINUTES env var. "
            "Omit (or set to 0) to run once and exit."
        ),
    )
    return parser.parse_args()


async def run_loop(interval_minutes: int) -> None:
    """Run the triage agent in a continuous loop with watermark tracking."""
    watermark_path = settings.WATERMARK_PATH
    run_count = 0

    while True:
        run_count += 1
        oldest = load_watermark(watermark_path)

        mode = f"every {interval_minutes}m" if interval_minutes else "once"
        since = f"since {oldest}" if oldest else "bootstrap (no watermark)"
        print(f"\n{'='*60}")
        print(f"  Run #{run_count}  |  {mode}  |  {since}")
        print(f"{'='*60}\n")

        new_ts = await main(oldest=oldest)

        if new_ts:
            save_watermark(watermark_path, new_ts)
            print(f"\n[watermark] Advanced to {new_ts}")
        else:
            print("\n[watermark] No new messages — watermark unchanged.")

        if interval_minutes == 0:
            break

        next_run = datetime.now(tz=timezone.utc)
        sleep_secs = interval_minutes * 60
        print(f"[schedule] Next run in {interval_minutes}m  "
              f"(~{next_run.strftime('%H:%M:%S')} UTC + {interval_minutes}m)")
        await asyncio.sleep(sleep_secs)


if __name__ == "__main__":
    args = _parse_args()

    # CLI flag takes precedence; fall back to env var; default = 0 (run once)
    interval = args.schedule if args.schedule is not None else settings.SCHEDULE_INTERVAL_MINUTES

    os.makedirs("logs", exist_ok=True)
    open(SENTINEL_FILE, "w").close()   # signal: agent is running
    try:
        asyncio.run(run_loop(interval_minutes=interval))
    finally:
        if os.path.exists(SENTINEL_FILE):
            os.remove(SENTINEL_FILE)   # always clean up, even on sys.exit(1)
