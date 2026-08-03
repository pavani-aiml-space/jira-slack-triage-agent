"""
Unit tests for pipeline/run_logger.py

All I/O uses tmp_path — no real logs/ directory touched.
"""
import json
import os
import pytest
from pipeline.run_logger import (
    RunLog,
    BlockResult,
    LlmStats,
    ErrorEntry,
    write_run_log,
    load_run_logs,
    SENTINEL_FILE,
)


def make_minimal_log(run_id="2026-04-29T13:00:00", status="success") -> RunLog:
    return RunLog(
        run_id=run_id,
        started_at=run_id,
        completed_at=run_id,
        status=status,
        messages_fetched=5,
        blocks_processed=2,
        tickets_created_count=1,
        clarifications_asked_count=1,
        blocks_skipped_count=0,
        error_count=0,
    )


# ── write_run_log ─────────────────────────────────────────────────────────────

def test_write_run_log_creates_file(tmp_path):
    log = make_minimal_log()
    path = write_run_log(log, log_dir=str(tmp_path))
    assert os.path.exists(path)


def test_write_run_log_is_valid_json(tmp_path):
    log = make_minimal_log()
    path = write_run_log(log, log_dir=str(tmp_path))
    with open(path) as f:
        data = json.load(f)
    assert data["run_id"] == "2026-04-29T13:00:00"
    assert data["status"] == "success"


def test_write_run_log_filename_contains_run_id(tmp_path):
    log = make_minimal_log(run_id="2026-04-29T13:00:00", status="fatal")
    path = write_run_log(log, log_dir=str(tmp_path))
    assert "2026-04-29T13" in path


def test_write_run_log_creates_log_dir_if_missing(tmp_path):
    new_dir = str(tmp_path / "nested" / "logs")
    log = make_minimal_log()
    path = write_run_log(log, log_dir=new_dir)
    assert os.path.exists(path)


def test_write_run_log_includes_blocks(tmp_path):
    log = make_minimal_log()
    log.blocks.append(
        BlockResult(
            block_index=0,
            block_snippet="Login is broken",
            action="ticket_created",
            ticket_key="SCRUM-11",
            ticket_summary="Login crash",
            ticket_type="Bug",
            ticket_priority="High",
            llm=LlmStats(
                iterations=2,
                tools_called=["create_jira_ticket"],
                finish_reason="stop",
                prompt_tokens=412,
                completion_tokens=89,
            ),
        )
    )
    path = write_run_log(log, log_dir=str(tmp_path))
    with open(path) as f:
        data = json.load(f)
    assert len(data["blocks"]) == 1
    assert data["blocks"][0]["ticket_key"] == "SCRUM-11"
    assert data["blocks"][0]["llm"]["prompt_tokens"] == 412


def test_write_run_log_includes_errors(tmp_path):
    log = make_minimal_log(status="partial")
    log.errors.append(
        ErrorEntry(
            block_index=1,
            block_snippet="Export crashes when",
            error_type="SlackMCPError",
            error_message="Connection closed",
            phase2_rule="Rule 5",
        )
    )
    path = write_run_log(log, log_dir=str(tmp_path))
    with open(path) as f:
        data = json.load(f)
    assert data["errors"][0]["phase2_rule"] == "Rule 5"


# ── load_run_logs ─────────────────────────────────────────────────────────────

def test_load_run_logs_returns_empty_list_when_dir_missing():
    result = load_run_logs(log_dir="/tmp/nonexistent_logs_xyz_abc")
    assert result == []


def test_load_run_logs_returns_sorted_newest_first(tmp_path):
    for ts in ["2026-04-29T10:00:00", "2026-04-29T12:00:00", "2026-04-29T11:00:00"]:
        write_run_log(make_minimal_log(run_id=ts), log_dir=str(tmp_path))
    logs = load_run_logs(log_dir=str(tmp_path))
    assert logs[0]["run_id"] == "2026-04-29T12:00:00"
    assert logs[-1]["run_id"] == "2026-04-29T10:00:00"


def test_load_run_logs_skips_non_run_files(tmp_path):
    (tmp_path / ".running").write_text("")
    (tmp_path / "notes.txt").write_text("ignore me")
    write_run_log(make_minimal_log(), log_dir=str(tmp_path))
    logs = load_run_logs(log_dir=str(tmp_path))
    assert len(logs) == 1


def test_load_run_logs_skips_malformed_json(tmp_path):
    (tmp_path / "run_bad.json").write_text("{not valid json")
    write_run_log(make_minimal_log(), log_dir=str(tmp_path))
    logs = load_run_logs(log_dir=str(tmp_path))
    assert len(logs) == 1


def test_sentinel_file_constant_is_in_logs_dir():
    assert SENTINEL_FILE.startswith("logs/") or SENTINEL_FILE.startswith("logs\\")


# ── RunLog.duplicates_flagged_count ───────────────────────────────────────────

def test_run_log_has_duplicates_flagged_count_field():
    log = make_minimal_log()
    assert hasattr(log, "duplicates_flagged_count")
    assert log.duplicates_flagged_count == 0


def test_run_log_accepts_nonzero_duplicates_flagged_count():
    log = make_minimal_log()
    log.duplicates_flagged_count = 2
    assert log.duplicates_flagged_count == 2


def test_run_log_serialises_duplicates_flagged_count(tmp_path):
    log = make_minimal_log()
    log.duplicates_flagged_count = 3
    path = write_run_log(log, log_dir=str(tmp_path))
    data = json.load(open(path))
    assert data["duplicates_flagged_count"] == 3


# ── Chunk 1.2 — BlockResult.confirmation_ts ───────────────────────────────────

def test_block_result_confirmation_ts_defaults_to_none():
    result = BlockResult(block_index=0, block_snippet="text", action="ticket_created")
    assert result.confirmation_ts is None


def test_block_result_confirmation_ts_can_be_set():
    result = BlockResult(block_index=0, block_snippet="text",
                         action="ticket_created", confirmation_ts="1714406400.123")
    assert result.confirmation_ts == "1714406400.123"


def test_block_result_confirmation_ts_serialises(tmp_path):
    log = make_minimal_log()
    log.blocks.append(BlockResult(
        block_index=0, block_snippet="bug", action="ticket_created",
        ticket_key="SCRUM-1", confirmation_ts="1714406400.123",
    ))
    path = write_run_log(log, log_dir=str(tmp_path))
    data = json.load(open(path))
    assert data["blocks"][0]["confirmation_ts"] == "1714406400.123"
