"""
Unit tests for pipeline/pending_confirmation_store.py
All I/O is tmp_path-isolated — no real disk interaction.
"""
from pipeline.pending_confirmation_store import (
    PendingConfirmation,
    PendingConfirmationStore,
    load_pending_store,
    save_pending_store,
    add_pending,
    mark_resolved,
    pending_only,
)


def _make_item(proposal_ts="1714045800.0001", status="pending"):
    return PendingConfirmation(
        run_id="r1",
        block_index=0,
        block_snippet="search feels broken today",
        proposed_summary="Investigate search regression",
        proposed_issue_type="Bug",
        proposed_priority="Medium",
        proposed_description="## What\nSearch reported as broken\n",
        proposed_labels=["search"],
        confidence=0.42,
        reasoning="Vague report, no repro steps",
        channel_id="C123ABC",
        proposal_ts=proposal_ts,
        created_at="2026-08-03T10:00:00",
        status=status,
    )


def test_round_trip(tmp_path):
    store = PendingConfirmationStore(items=[_make_item()])
    path = str(tmp_path / "pending.json")
    save_pending_store(store, path)
    loaded = load_pending_store(path)
    assert len(loaded.items) == 1
    assert loaded.items[0].proposed_summary == "Investigate search regression"
    assert loaded.items[0].confidence == 0.42


def test_load_missing_file_returns_empty():
    store = load_pending_store("memory/nonexistent_pending_test.json")
    assert isinstance(store, PendingConfirmationStore)
    assert store.items == []


def test_load_corrupt_returns_empty(tmp_path):
    p = tmp_path / "pending.json"
    p.write_text("not json")
    store = load_pending_store(str(p))
    assert store.items == []


def test_add_pending_appends():
    store = PendingConfirmationStore()
    add_pending(store, _make_item())
    assert len(store.items) == 1


def test_mark_resolved_removes_only_matching_item():
    store = PendingConfirmationStore(items=[
        _make_item(proposal_ts="ts-1"),
        _make_item(proposal_ts="ts-2"),
    ])
    mark_resolved(store, "ts-1")
    assert len(store.items) == 1
    assert store.items[0].proposal_ts == "ts-2"


def test_pending_only_filters_by_status():
    store = PendingConfirmationStore(items=[
        _make_item(proposal_ts="ts-1", status="pending"),
        _make_item(proposal_ts="ts-2", status="resolved"),
    ])
    result = pending_only(store)
    assert len(result) == 1
    assert result[0].proposal_ts == "ts-1"
