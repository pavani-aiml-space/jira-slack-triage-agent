"""Unit tests for pipeline/judge_store.py"""
import json
from pathlib import Path

from pipeline.judge_store import JudgeScoreEntry, append_judge_entries, load_judge_store, save_judge_store


def test_load_judge_store_missing_returns_empty(tmp_path):
    assert load_judge_store(str(tmp_path / "none.json")) == []


def test_load_judge_store_corrupt_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert load_judge_store(str(p)) == []


def test_save_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "judge.json")
    save_judge_store(p, [{"run_id": "r1", "block_index": 0}])
    data = load_judge_store(p)
    assert data == [{"run_id": "r1", "block_index": 0}]


def test_append_judge_entries_extends_file(tmp_path):
    p = str(tmp_path / "judge.json")
    save_judge_store(p, [{"a": 1}])
    append_judge_entries(
        p,
        [
            JudgeScoreEntry(
                run_id="r2",
                block_index=1,
                ticket_key="SCRUM-9",
                judged_at="2026-04-30T12:00:00+00:00",
                type_score=4,
                priority_score=5,
                title_score=4,
                description_score=4,
                reason="Solid",
            )
        ],
    )
    raw = json.loads(Path(p).read_text())
    assert len(raw["scores"]) == 2
    assert raw["scores"][1]["ticket_key"] == "SCRUM-9"
    assert raw["scores"][1]["type_score"] == 4


def test_append_judge_entries_noop_when_empty(tmp_path):
    p = str(tmp_path / "judge.json")
    save_judge_store(p, [{"only": True}])
    append_judge_entries(p, [])
    assert load_judge_store(p) == [{"only": True}]
