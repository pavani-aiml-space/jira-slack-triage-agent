"""Unit tests for pipeline/judge_calibration.py"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.judge_calibration import (
    CalibrationReport,
    CalibrationRowResult,
    MismatchReport,
    MismatchRowResult,
    default_fixtures_path,
    format_mismatch_report_text,
    format_report_text,
    label_to_synthetic_block,
    load_label_fixtures,
    passes_type_priority_threshold,
    report_to_json_dict,
    run_fixture_calibration,
    run_mismatch_calibration,
    wrong_issue_type_for_mismatch,
)
from pipeline.judge_store import JudgeScoreEntry


def test_default_fixtures_path_points_at_eval_file():
    p = Path(default_fixtures_path())
    assert p.name == "label_fixtures.json"
    assert p.parent.name == "eval"


def test_load_label_fixtures_roundtrip(tmp_path):
    data = {
        "labels": [
            {"id": "a", "slack_text": "x", "correct_type": "Bug", "correct_priority": "High",
             "correct_action": "create_jira_ticket"},
        ]
    }
    fp = tmp_path / "f.json"
    fp.write_text(json.dumps(data), encoding="utf-8")
    rows = load_label_fixtures(str(fp))
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_load_label_fixtures_rejects_non_list_labels(tmp_path):
    fp = tmp_path / "f.json"
    fp.write_text(json.dumps({"labels": "bad"}), encoding="utf-8")
    with pytest.raises(ValueError, match="labels"):
        load_label_fixtures(str(fp))


def test_wrong_issue_type_for_mismatch_rotates():
    assert wrong_issue_type_for_mismatch("Bug") == "Story"
    assert wrong_issue_type_for_mismatch("Story") == "Task"
    assert wrong_issue_type_for_mismatch("Task") == "Bug"


def test_label_to_synthetic_mismatch_uses_wrong_type():
    lab = {
        "id": "x",
        "slack_text": "broken",
        "correct_type": "Bug",
        "correct_priority": "High",
        "correct_action": "create_jira_ticket",
    }
    b = label_to_synthetic_block(lab, 0, mismatch=True)
    assert b.ticket_type == "Story"
    assert b.ticket_priority == "High"
    assert "mismatch" in (b.ticket_key or "")


def test_label_to_synthetic_block_uses_ground_truth():
    lab = {
        "id": "bug-high-001",
        "slack_text": "Something broke badly on prod.",
        "correct_type": "Bug",
        "correct_priority": "High",
        "notes": "impact",
        "correct_action": "create_jira_ticket",
    }
    b = label_to_synthetic_block(lab, 3)
    assert b.block_index == 3
    assert b.ticket_type == "Bug"
    assert b.ticket_priority == "High"
    assert b.ticket_key == "FIXTURE-bug-high-001"
    assert "Labeler notes" in (b.ticket_description or "")
    assert b.action == "ticket_created"


def test_passes_type_priority_threshold():
    ok = CalibrationRowResult("x", False, 5, 5, 5, 5)
    bad = CalibrationRowResult("y", False, 3, 5, 5, 5)
    err = CalibrationRowResult("z", False, error="nope")
    assert passes_type_priority_threshold(ok, 4) is True
    assert passes_type_priority_threshold(bad, 4) is False
    assert passes_type_priority_threshold(err, 4) is False


def test_calibration_report_agreement_rate():
    report = CalibrationReport("/tmp/x.json", 4)
    report.rows = [
        CalibrationRowResult("a", False, 5, 5, 4, 4),
        CalibrationRowResult("b", False, 2, 5, 4, 4),
        CalibrationRowResult("c", False, error="fail"),
    ]
    assert report.eligible_count == 3
    assert report.error_count == 1
    assert report.agreement_count == 1
    assert report.agreement_rate == 0.5


def test_format_report_text_contains_fixture_ids():
    report = CalibrationReport("f.json", 4)
    report.rows = [
        CalibrationRowResult("fix-1", False, 5, 4, 4, 4, reason="ok"),
    ]
    text = format_report_text(report)
    assert "fix-1" in text
    assert "PASS" in text


def test_report_to_json_dict():
    report = CalibrationReport("p.json", 4)
    report.rows = [CalibrationRowResult("a", True, 4, 4, 3, 3)]
    d = report_to_json_dict(report)
    assert d["agreement_count"] == 1
    assert d["rows"][0]["fixture_id"] == "a"


@pytest.mark.asyncio
async def test_run_fixture_calibration_filters_actions_and_mocks_judge(monkeypatch, tmp_path):
    data = {
        "labels": [
            {
                "id": "t1",
                "slack_text": "Bug text",
                "correct_type": "Bug",
                "correct_priority": "Medium",
                "correct_action": "create_jira_ticket",
            },
            {
                "id": "skip-clarify",
                "slack_text": "vague",
                "correct_type": "Bug",
                "correct_priority": "Low",
                "correct_action": "ask_for_clarification",
            },
        ]
    }
    fp = tmp_path / "lab.json"
    fp.write_text(json.dumps(data), encoding="utf-8")

    async def fake_judge(provider, run_id, block, slack_context=None):
        return JudgeScoreEntry(
            run_id=run_id,
            block_index=block.block_index,
            ticket_key=block.ticket_key,
            judged_at="t",
            type_score=5,
            priority_score=5,
            title_score=4,
            description_score=4,
            reason="mock",
        )

    monkeypatch.setattr(
        "pipeline.judge_calibration.judge_one_block",
        fake_judge,
    )
    report = await run_fixture_calibration(str(fp), MagicMock(), min_type_priority=4)
    assert len(report.rows) == 1
    assert report.rows[0].fixture_id == "t1"
    assert report.agreement_count == 1


def test_mismatch_row_judge_caught_wrong_type():
    r = MismatchRowResult("id", False, "Bug", "Story", type_score=2, priority_score=4)
    assert r.judge_caught_wrong_type(3) is True
    assert r.judge_caught_wrong_type(1) is False


def test_format_mismatch_report_text():
    rep = MismatchReport("f.json", 3)
    rep.rows = [
        MismatchRowResult("a", False, "Bug", "Story", type_score=2, priority_score=5),
    ]
    text = format_mismatch_report_text(rep)
    assert "CATCH" in text
    assert "human=Bug" in text


@pytest.mark.asyncio
async def test_run_mismatch_calibration_with_mock(tmp_path, monkeypatch):
    data = {
        "labels": [
            {
                "id": "m1",
                "slack_text": "crash",
                "correct_type": "Bug",
                "correct_priority": "High",
                "correct_action": "create_jira_ticket",
            },
        ]
    }
    fp = tmp_path / "lab.json"
    fp.write_text(json.dumps(data), encoding="utf-8")

    async def fake_judge(provider, run_id, block, slack_context=None):
        # Wrong type shown — judge "notices" with low type score
        return JudgeScoreEntry(
            run_id=run_id,
            block_index=block.block_index,
            ticket_key=block.ticket_key,
            judged_at="t",
            type_score=2,
            priority_score=5,
            title_score=4,
            description_score=4,
            reason="mock",
        )

    monkeypatch.setattr("pipeline.judge_calibration.judge_one_block", fake_judge)
    rep = await run_mismatch_calibration(str(fp), MagicMock(), mismatch_max_type=3)
    assert rep.caught_count == 1
    assert rep.catch_rate == 1.0


@pytest.mark.asyncio
async def test_run_fixture_calibration_only_tricky(tmp_path, monkeypatch):
    data = {
        "labels": [
            {
                "id": "easy",
                "slack_text": "x",
                "correct_type": "Bug",
                "correct_priority": "Low",
                "correct_action": "create_jira_ticket",
                "tricky": False,
            },
            {
                "id": "hard",
                "slack_text": "y",
                "correct_type": "Story",
                "correct_priority": "High",
                "correct_action": "create_jira_ticket",
                "tricky": True,
            },
        ]
    }
    fp = tmp_path / "lab.json"
    fp.write_text(json.dumps(data), encoding="utf-8")

    async def fake_judge(provider, run_id, block, slack_context=None):
        return JudgeScoreEntry(
            run_id=run_id,
            block_index=block.block_index,
            ticket_key=block.ticket_key,
            judged_at="t",
            type_score=5,
            priority_score=5,
            title_score=5,
            description_score=5,
            reason="m",
        )

    monkeypatch.setattr("pipeline.judge_calibration.judge_one_block", fake_judge)
    report = await run_fixture_calibration(
        str(fp), MagicMock(), only_tricky=True,
    )
    assert len(report.rows) == 1
    assert report.rows[0].fixture_id == "hard"
