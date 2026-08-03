"""
Unit tests for pipeline/quality_metrics.py and related settings.
"""
import json
import os
import pytest
from config.settings import settings


# ── Chunk 1.1 — Settings ──────────────────────────────────────────────────────

def test_quality_settings_exist():
    assert hasattr(settings, "QUALITY_ALERT_THRESHOLD")
    assert hasattr(settings, "MIN_REACTIONS_FOR_QUALITY")
    assert hasattr(settings, "REACTION_WINDOW_HOURS")
    assert hasattr(settings, "REACTION_HISTORY_LIMIT")
    assert hasattr(settings, "QUALITY_STORE_PATH")
    assert 0.0 < settings.QUALITY_ALERT_THRESHOLD < 1.0
    assert settings.MIN_REACTIONS_FOR_QUALITY > 0


# ── Chunk 1.3 — quality_metrics dataclasses + I/O ────────────────────────────

from pipeline.quality_metrics import (
    PendingReaction, CollectedReaction, RunQuality, QualityStore,
    load_quality_store, save_quality_store,
)


def test_load_quality_store_missing_file_returns_empty():
    store = load_quality_store("/nonexistent/path.json")
    assert store.pending == []
    assert store.runs == []


def test_quality_store_round_trip(tmp_path):
    path = str(tmp_path / "quality_store.json")
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0,
                                 ticket_key="SCRUM-1", confirmation_ts="123.456",
                                 posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    save_quality_store(store, path)
    loaded = load_quality_store(path)
    assert len(loaded.pending) == 1
    assert loaded.pending[0].confirmation_ts == "123.456"
    assert loaded.pending[0].ticket_key == "SCRUM-1"


def test_load_quality_store_corrupt_file_returns_empty(tmp_path):
    path = str(tmp_path / "bad.json")
    with open(path, "w") as f:
        f.write("{not valid json")
    store = load_quality_store(path)
    assert store.pending == []


def test_save_quality_store_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "quality_store.json")
    save_quality_store(QualityStore(pending=[], runs=[]), path)
    assert os.path.exists(path)


# ── Chunk 3.1 — add_pending_from_run ─────────────────────────────────────────

from pipeline.quality_metrics import add_pending_from_run
from pipeline.run_logger import BlockResult, RunLog


def _make_run_log(blocks, run_id="2026-04-29T10:00:00"):
    return RunLog(
        run_id=run_id, started_at=run_id, completed_at=None, status="success",
        messages_fetched=1, blocks_processed=len(blocks),
        tickets_created_count=0, clarifications_asked_count=0,
        blocks_skipped_count=0, error_count=0, blocks=blocks,
    )


def test_add_pending_adds_ticket_blocks_with_ts():
    store = QualityStore(pending=[], runs=[])
    run_log = _make_run_log([
        BlockResult(block_index=0, block_snippet="bug", action="ticket_created",
                    ticket_key="SCRUM-1", confirmation_ts="111.000"),
        BlockResult(block_index=1, block_snippet="story", action="ticket_created",
                    ticket_key="SCRUM-2", confirmation_ts=None),  # no ts — skip
    ])
    add_pending_from_run(store, run_log)
    assert len(store.pending) == 1
    assert store.pending[0].confirmation_ts == "111.000"
    assert store.pending[0].ticket_key == "SCRUM-1"


def test_add_pending_skips_non_ticket_actions():
    store = QualityStore(pending=[], runs=[])
    run_log = _make_run_log([
        BlockResult(block_index=0, block_snippet="q", action="clarification_asked"),
    ])
    add_pending_from_run(store, run_log)
    assert store.pending == []


def test_add_pending_skips_duplicate_flagged():
    store = QualityStore(pending=[], runs=[])
    run_log = _make_run_log([
        BlockResult(block_index=0, block_snippet="dup", action="duplicate_flagged",
                    ticket_key="SCRUM-1"),
    ])
    add_pending_from_run(store, run_log)
    assert store.pending == []


# ── Chunk 3.2 — apply_collected ───────────────────────────────────────────────

from pipeline.quality_metrics import apply_collected


def test_apply_collected_moves_matching_pending_to_runs():
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                 confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    collected = [CollectedReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                   thumbs_up=1, thumbs_down=0, collected_at="2026-04-29T11:00:00")]
    apply_collected(store, collected)
    assert len(store.runs) == 1
    assert store.runs[0].thumbs_up == 1
    assert store.runs[0].thumbs_up_rate == 1.0
    assert store.pending == []


def test_apply_collected_unmatched_pending_remains():
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                 confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    apply_collected(store, [])
    assert len(store.pending) == 1


def test_apply_collected_zero_reactions_sets_rate_none():
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                 confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    collected = [CollectedReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                   thumbs_up=0, thumbs_down=0, collected_at="2026-04-29T11:00:00")]
    apply_collected(store, collected)
    assert store.runs[0].thumbs_up_rate is None  # Rule 9


def test_apply_collected_aggregates_multiple_blocks_per_run():
    store = QualityStore(
        pending=[
            PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                            confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00"),
            PendingReaction(run_id="r1", block_index=1, ticket_key="SCRUM-2",
                            confirmation_ts="222.000", posted_at_iso="2026-04-29T10:00:00"),
        ],
        runs=[],
    )
    collected = [
        CollectedReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                          thumbs_up=2, thumbs_down=0, collected_at="2026-04-29T11:00:00"),
        CollectedReaction(run_id="r1", block_index=1, ticket_key="SCRUM-2",
                          thumbs_up=0, thumbs_down=1, collected_at="2026-04-29T11:00:00"),
    ]
    apply_collected(store, collected)
    assert len(store.runs) == 1
    assert store.runs[0].thumbs_up == 2
    assert store.runs[0].thumbs_down == 1
    assert abs(store.runs[0].thumbs_up_rate - 2/3) < 0.001


# ── Chunk 3.3 — should_alert + rolling_thumbs_up_rate ────────────────────────

from pipeline.quality_metrics import should_alert, rolling_thumbs_up_rate


def _run_quality(run_id, thumbs_up, thumbs_down, rate):
    return RunQuality(run_id=run_id, collected_at="2026-04-29T11:00:00",
                      thumbs_up=thumbs_up, thumbs_down=thumbs_down,
                      reactions_found=thumbs_up + thumbs_down, thumbs_up_rate=rate)


def test_should_alert_fires_when_rate_below_threshold():
    # 5 runs × 10 reactions = 50 total > MIN=5; latest rate 0.20 < threshold 0.70
    store = QualityStore(pending=[], runs=[_run_quality("r1", 2, 8, 0.20)] * 5)
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is True
    assert rq is not None


def test_should_alert_no_fire_when_rate_above_threshold():
    store = QualityStore(pending=[], runs=[_run_quality("r1", 8, 2, 0.80)] * 5)
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is False


def test_should_alert_warmup_gate_blocks_alert():
    # Only 1 reaction total — below MIN=5 (Rule 8)
    store = QualityStore(pending=[], runs=[_run_quality("r1", 0, 1, 0.0)])
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is False


def test_should_alert_no_fire_on_empty_store():
    store = QualityStore(pending=[], runs=[])
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is False
    assert rq is None


def test_should_alert_no_fire_when_rate_is_none():
    store = QualityStore(pending=[], runs=[_run_quality("r1", 0, 0, None)] * 5)
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is False


def test_rolling_thumbs_up_rate_aggregates_runs():
    store = QualityStore(pending=[], runs=[
        _run_quality("r1", 3, 1, 0.75),
        _run_quality("r2", 1, 3, 0.25),
    ])
    rate = rolling_thumbs_up_rate(store, min_reactions=5)
    assert rate == 0.50


def test_rolling_thumbs_up_rate_returns_none_below_minimum():
    store = QualityStore(pending=[], runs=[_run_quality("r1", 1, 0, 1.0)])
    rate = rolling_thumbs_up_rate(store, min_reactions=5)
    assert rate is None
