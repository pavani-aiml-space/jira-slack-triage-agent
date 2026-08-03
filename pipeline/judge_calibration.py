"""
Judge calibration against tests/eval/label_fixtures.json.

Two modes (see run_judge_calibration.py --mode):
  gold     — synthetic ticket matches human labels; expect high type/priority scores.
  mismatch — same Slack, wrong issue type (Bug→Story→Task rotation); expect low type scores.

Run from repo root:
    python run_judge_calibration.py
    python run_judge_calibration.py --mode mismatch
    python run_judge_calibration.py --mode both --json-out logs/judge_calibration.json
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agents.llm.base import LLMProvider
from pipeline.judge_store import JudgeScoreEntry
from pipeline.llm_judge import judge_one_block
from pipeline.run_logger import BlockResult


def default_fixtures_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "tests" / "eval" / "label_fixtures.json")


def load_label_fixtures(path: str) -> list[dict[str, Any]]:
    """Load labels array from label_fixtures.json. Raises on missing file or bad JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    labels = data.get("labels", [])
    if not isinstance(labels, list):
        raise ValueError("label_fixtures.json: expected top-level 'labels' array")
    return labels


def _summary_from_slack(slack_text: str) -> str:
    first = slack_text.split("\n")[0].strip()
    if len(first) > 78:
        return first[:75] + "..."
    return first or "Ticket from Slack"


_WRONG_TYPE_ROTATION = {"Bug": "Story", "Story": "Task", "Task": "Bug"}


def wrong_issue_type_for_mismatch(correct_type: str | None) -> str:
    """
    Return a deliberately wrong Jira issue type for the same Slack text.
    Rotates Bug→Story→Task→Bug so the ticket still looks plausible but disagrees with the label.
    """
    if not correct_type:
        return "Story"
    return _WRONG_TYPE_ROTATION.get(correct_type, "Story")


def label_to_synthetic_block(
    label: dict[str, Any],
    block_index: int,
    *,
    mismatch: bool = False,
) -> BlockResult:
    """
    Build a synthetic ticket from a fixture row.

    mismatch=False: gold ticket — type and priority match human labels.
    mismatch=True: same Slack and priority, but issue_type is intentionally wrong
    (the judge should usually score *type* low).
    """
    slack = label.get("slack_text") or ""
    fid = label.get("id", f"row-{block_index}")
    desc = slack
    notes = label.get("notes")
    if notes:
        desc = f"{slack}\n\n--- Labeler notes ---\n{notes}"
    correct_type = label.get("correct_type")
    issue_type = (
        wrong_issue_type_for_mismatch(correct_type)
        if mismatch
        else correct_type
    )
    suffix = "-mismatch" if mismatch else ""
    return BlockResult(
        block_index=block_index,
        block_snippet=slack[:60] if slack else "",
        action="ticket_created",
        ticket_key=f"FIXTURE-{fid}{suffix}",
        ticket_summary=_summary_from_slack(slack),
        ticket_type=issue_type,
        ticket_priority=label.get("correct_priority"),
        ticket_description=desc[:8000],
    )


@dataclass
class CalibrationRowResult:
    fixture_id: str
    tricky: bool
    type_score: int | None = None
    priority_score: int | None = None
    title_score: int | None = None
    description_score: int | None = None
    reason: str = ""
    error: str | None = None


@dataclass
class CalibrationReport:
    """Aggregated outcome of running the judge on all eligible fixtures."""

    fixture_path: str
    min_type_priority: int
    rows: list[CalibrationRowResult] = field(default_factory=list)

    @property
    def eligible_count(self) -> int:
        return len(self.rows)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.rows if r.error)

    @property
    def agreement_count(self) -> int:
        """Count where type and priority scores meet threshold (human-aligned ticket)."""
        t = self.min_type_priority
        n = 0
        for r in self.rows:
            if r.error or r.type_score is None or r.priority_score is None:
                continue
            if r.type_score >= t and r.priority_score >= t:
                n += 1
        return n

    @property
    def agreement_rate(self) -> float | None:
        ok = self.eligible_count - self.error_count
        if ok <= 0:
            return None
        return self.agreement_count / ok

    def mean_dimension(self, attr: str) -> float | None:
        vals = [getattr(r, attr) for r in self.rows if getattr(r, attr) is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)


def _row_from_entry(label: dict[str, Any], entry: JudgeScoreEntry) -> CalibrationRowResult:
    fid = str(label.get("id", ""))
    tricky = bool(label.get("tricky", False))
    if entry.error:
        return CalibrationRowResult(fixture_id=fid, tricky=tricky, error=entry.error)
    return CalibrationRowResult(
        fixture_id=fid,
        tricky=tricky,
        type_score=entry.type_score,
        priority_score=entry.priority_score,
        title_score=entry.title_score,
        description_score=entry.description_score,
        reason=entry.reason or "",
        error=entry.error,
    )


def passes_type_priority_threshold(row: CalibrationRowResult, min_tp: int) -> bool:
    if row.error or row.type_score is None or row.priority_score is None:
        return False
    return row.type_score >= min_tp and row.priority_score >= min_tp


async def run_fixture_calibration(
    fixtures_path: str,
    provider: LLMProvider,
    *,
    min_type_priority: int = 4,
    concurrency: int = 5,
    only_tricky: bool = False,
) -> CalibrationReport:
    """
    Run the judge on every fixture with correct_action == create_jira_ticket.

    Each call uses full slack_text as context so scores are comparable to careful
    human labels (not the 60-char triage snippet alone).
    """
    labels_all = load_label_fixtures(fixtures_path)
    labels = [
        lab
        for lab in labels_all
        if lab.get("correct_action") == "create_jira_ticket"
        and lab.get("correct_type")
        and lab.get("correct_priority")
    ]
    if only_tricky:
        labels = [lab for lab in labels if lab.get("tricky")]

    run_id = "calibration-fixtures"
    sem = asyncio.Semaphore(concurrency)

    async def one(idx: int, label: dict[str, Any]):
        block = label_to_synthetic_block(label, idx)
        slack_full = (label.get("slack_text") or "")[:8000]
        async with sem:
            return await judge_one_block(
                provider, run_id, block, slack_context=slack_full,
            )

    tasks = [one(i, lab) for i, lab in enumerate(labels)]
    entries = await asyncio.gather(*tasks, return_exceptions=False)

    report = CalibrationReport(
        fixture_path=fixtures_path,
        min_type_priority=min_type_priority,
        rows=[_row_from_entry(lab, ent) for lab, ent in zip(labels, entries)],
    )
    return report


def report_to_json_dict(report: CalibrationReport) -> dict[str, Any]:
    return {
        "fixture_path": report.fixture_path,
        "min_type_priority": report.min_type_priority,
        "eligible_count": report.eligible_count,
        "error_count": report.error_count,
        "agreement_count": report.agreement_count,
        "agreement_rate": report.agreement_rate,
        "mean_type": report.mean_dimension("type_score"),
        "mean_priority": report.mean_dimension("priority_score"),
        "mean_title": report.mean_dimension("title_score"),
        "mean_description": report.mean_dimension("description_score"),
        "rows": [asdict(r) for r in report.rows],
    }


def format_report_text(report: CalibrationReport) -> str:
    lines: list[str] = []
    lines.append(f"Judge calibration — {report.fixture_path}")
    lines.append(
        f"Threshold: type ≥ {report.min_type_priority} AND priority ≥ {report.min_type_priority} "
        f"(on gold-aligned synthetic tickets)\n"
    )
    for r in report.rows:
        if r.error:
            lines.append(f"  {r.fixture_id:26}  FAIL  err={r.error[:80]}")
        else:
            ok = passes_type_priority_threshold(r, report.min_type_priority)
            tag = "PASS" if ok else "LOW "
            tri = " (tricky)" if r.tricky else ""
            lines.append(
                f"  {r.fixture_id:26}  {tag}{tri}  "
                f"type={r.type_score} pri={r.priority_score} title={r.title_score} desc={r.description_score}"
            )
    lines.append("")
    rate = report.agreement_rate
    rate_s = f"{rate:.1%}" if rate is not None else "n/a"
    lines.append(
        f"Agreement (type+priority ≥ threshold, excluding errors): "
        f"{report.agreement_count} / {report.eligible_count - report.error_count} = {rate_s}"
    )
    lines.append(
        f"Means — type: {report.mean_dimension('type_score') or 0:.2f}, "
        f"priority: {report.mean_dimension('priority_score') or 0:.2f}, "
        f"title: {report.mean_dimension('title_score') or 0:.2f}, "
        f"description: {report.mean_dimension('description_score') or 0:.2f}"
    )
    lines.append(f"Errors: {report.error_count}")
    return "\n".join(lines)


# ── Mismatch calibration (wrong type on purpose) ─────────────────────────────


@dataclass
class MismatchRowResult:
    fixture_id: str
    tricky: bool
    human_type: str
    wrong_type_used: str
    type_score: int | None = None
    priority_score: int | None = None
    title_score: int | None = None
    description_score: int | None = None
    reason: str = ""
    error: str | None = None

    def judge_caught_wrong_type(self, max_type_score: int) -> bool:
        """True if type score is at or below threshold (judge penalised the bad type)."""
        if self.error or self.type_score is None:
            return False
        return self.type_score <= max_type_score


@dataclass
class MismatchReport:
    fixture_path: str
    max_type_score_for_catch: int  # e.g. 3 — type_score <= 3 counts as "caught"
    rows: list[MismatchRowResult] = field(default_factory=list)

    @property
    def eligible_count(self) -> int:
        return len(self.rows)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.rows if r.error)

    @property
    def caught_count(self) -> int:
        t = self.max_type_score_for_catch
        return sum(1 for r in self.rows if r.judge_caught_wrong_type(t))

    @property
    def catch_rate(self) -> float | None:
        ok = self.eligible_count - self.error_count
        if ok <= 0:
            return None
        return self.caught_count / ok


def _mismatch_row_from_entry(
    label: dict[str, Any],
    wrong_type: str,
    entry: JudgeScoreEntry,
) -> MismatchRowResult:
    fid = str(label.get("id", ""))
    tricky = bool(label.get("tricky", False))
    human = str(label.get("correct_type") or "")
    if entry.error:
        return MismatchRowResult(
            fixture_id=fid,
            tricky=tricky,
            human_type=human,
            wrong_type_used=wrong_type,
            error=entry.error,
        )
    return MismatchRowResult(
        fixture_id=fid,
        tricky=tricky,
        human_type=human,
        wrong_type_used=wrong_type,
        type_score=entry.type_score,
        priority_score=entry.priority_score,
        title_score=entry.title_score,
        description_score=entry.description_score,
        reason=entry.reason or "",
        error=entry.error,
    )


async def run_mismatch_calibration(
    fixtures_path: str,
    provider: LLMProvider,
    *,
    mismatch_max_type: int = 3,
    concurrency: int = 5,
    only_tricky: bool = False,
) -> MismatchReport:
    """
    Same fixtures as gold mode, but each synthetic ticket uses a *wrong* issue_type
    (Bug→Story→Task rotation). Priority stays correct. We expect the judge to score
    *type* low — if it still scores high, it may not be discriminating bad type choices.
    """
    labels_all = load_label_fixtures(fixtures_path)
    labels = [
        lab
        for lab in labels_all
        if lab.get("correct_action") == "create_jira_ticket"
        and lab.get("correct_type")
        and lab.get("correct_priority")
    ]
    if only_tricky:
        labels = [lab for lab in labels if lab.get("tricky")]

    run_id = "calibration-fixtures-mismatch"
    sem = asyncio.Semaphore(concurrency)

    async def one(idx: int, label: dict[str, Any]):
        block = label_to_synthetic_block(label, idx, mismatch=True)
        wrong_t = wrong_issue_type_for_mismatch(label.get("correct_type"))
        slack_full = (label.get("slack_text") or "")[:8000]
        async with sem:
            ent = await judge_one_block(
                provider, run_id, block, slack_context=slack_full,
            )
        return label, wrong_t, ent

    results = await asyncio.gather(*[one(i, lab) for i, lab in enumerate(labels)])

    rows = [
        _mismatch_row_from_entry(lab, wrong_t, ent)
        for lab, wrong_t, ent in results
    ]
    return MismatchReport(
        fixture_path=fixtures_path,
        max_type_score_for_catch=mismatch_max_type,
        rows=rows,
    )


def mismatch_report_to_json_dict(report: MismatchReport) -> dict[str, Any]:
    return {
        "fixture_path": report.fixture_path,
        "mismatch_max_type_score_for_catch": report.max_type_score_for_catch,
        "eligible_count": report.eligible_count,
        "error_count": report.error_count,
        "caught_count": report.caught_count,
        "catch_rate": report.catch_rate,
        "rows": [asdict(r) for r in report.rows],
    }


def format_mismatch_report_text(report: MismatchReport) -> str:
    mx = report.max_type_score_for_catch
    lines: list[str] = []
    lines.append(f"Judge mismatch calibration — {report.fixture_path}")
    lines.append(
        f"Each row uses a deliberately *wrong* issue type (human type → rotated wrong type). "
        f"CATCH = type score ≤ {mx} (judge noticed the type does not fit Slack).\n"
    )
    for r in report.rows:
        if r.error:
            lines.append(f"  {r.fixture_id:26}  FAIL  err={r.error[:80]}")
        else:
            caught = r.judge_caught_wrong_type(mx)
            tag = "CATCH" if caught else "MISS"
            tri = " (tricky)" if r.tricky else ""
            lines.append(
                f"  {r.fixture_id:26}  {tag}{tri}  "
                f"human={r.human_type} shown={r.wrong_type_used}  "
                f"type={r.type_score} pri={r.priority_score}"
            )
    lines.append("")
    rate = report.catch_rate
    rate_s = f"{rate:.1%}" if rate is not None else "n/a"
    lines.append(
        f"Catch rate (type ≤ {mx}, excluding errors): "
        f"{report.caught_count} / {report.eligible_count - report.error_count} = {rate_s}"
    )
    lines.append(f"Errors: {report.error_count}")
    return "\n".join(lines)
