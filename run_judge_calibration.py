"""
Run LLM-as-Judge against tests/eval/label_fixtures.json.

  --mode gold (default) — ticket matches human labels; expect high scores.
  --mode mismatch       — wrong issue type on purpose; expect low type scores.
  --mode both           — gold then mismatch; JSON has keys gold + mismatch.

Usage:
    cd /Users/pavanibayappu/JiraSlack
    python run_judge_calibration.py
    python run_judge_calibration.py --mode mismatch
    python run_judge_calibration.py --mode both --json-out logs/judge_calibration.json

See tests/eval/FIXTURES_GUIDE.md Part B–C for step-by-step instructions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / "config" / ".env")

from agents.llm.factory import get_judge_llm_provider
from config.settings import settings
from pipeline.judge_calibration import (
    default_fixtures_path,
    format_mismatch_report_text,
    format_report_text,
    mismatch_report_to_json_dict,
    report_to_json_dict,
    run_fixture_calibration,
    run_mismatch_calibration,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Calibrate LLM judge vs label_fixtures.json")
    p.add_argument(
        "--fixtures",
        default=default_fixtures_path(),
        help="Path to label_fixtures.json",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=4,
        metavar="N",
        help="Min score (1-5) on both type and priority to count as agreement (default: 4)",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max parallel judge API calls (default: 5)",
    )
    p.add_argument(
        "--only-tricky",
        action="store_true",
        help="Only fixtures with tricky: true",
    )
    p.add_argument(
        "--json-out",
        metavar="FILE",
        help="Write full report JSON to this path",
    )
    p.add_argument(
        "--mode",
        choices=("gold", "mismatch", "both"),
        default="gold",
        help=(
            "gold = human-correct ticket (expect high type/priority scores); "
            "mismatch = wrong issue type on purpose (expect low type scores); "
            "both = run gold then mismatch"
        ),
    )
    p.add_argument(
        "--mismatch-max-type",
        type=int,
        default=3,
        metavar="N",
        help="Mismatch mode: type score ≤ N counts as CATCH (judge noticed bad type). Default: 3",
    )
    return p.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    if args.threshold < 1 or args.threshold > 5:
        print("--threshold must be between 1 and 5", file=sys.stderr)
        return 2
    if args.mismatch_max_type < 1 or args.mismatch_max_type > 5:
        print("--mismatch-max-type must be between 1 and 5", file=sys.stderr)
        return 2
    try:
        provider = get_judge_llm_provider(settings)
    except Exception as e:
        print(f"Failed to create judge provider: {e}", file=sys.stderr)
        return 1

    gold_report = None
    mismatch_report = None

    if args.mode in ("gold", "both"):
        gold_report = await run_fixture_calibration(
            args.fixtures,
            provider,
            min_type_priority=args.threshold,
            concurrency=args.concurrency,
            only_tricky=args.only_tricky,
        )
        print(format_report_text(gold_report))

    if args.mode in ("mismatch", "both"):
        if args.mode == "both":
            print("\n" + "=" * 60 + "\n")
        mismatch_report = await run_mismatch_calibration(
            args.fixtures,
            provider,
            mismatch_max_type=args.mismatch_max_type,
            concurrency=args.concurrency,
            only_tricky=args.only_tricky,
        )
        print(format_mismatch_report_text(mismatch_report))

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if args.mode == "both":
            payload = {
                "gold": report_to_json_dict(gold_report),
                "mismatch": mismatch_report_to_json_dict(mismatch_report),
            }
        elif args.mode == "gold":
            payload = report_to_json_dict(gold_report)
        else:
            payload = mismatch_report_to_json_dict(mismatch_report)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote JSON report to {out_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
