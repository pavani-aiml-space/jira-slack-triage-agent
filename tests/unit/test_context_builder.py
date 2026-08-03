"""
Unit tests for context_builder.py

Pure Python — no mocks needed. Tests the grouping logic directly.
"""
import pytest
from pipeline.context_builder import build_context_blocks, _make_block


# ── _make_block ───────────────────────────────────────────────────────────────

def test_make_block_single_message():
    msgs = [{"user": "U1", "text": "hello", "ts": "1000.0"}]
    block = _make_block(msgs)
    assert block["messages"] == msgs
    assert block["combined_text"] == "hello"
    assert block["start_ts"] == "1000.0"
    assert block["end_ts"] == "1000.0"


def test_make_block_multiple_messages_joins_text():
    msgs = [
        {"user": "U1", "text": "line one", "ts": "1000.0"},
        {"user": "U2", "text": "line two", "ts": "1010.0"},
    ]
    block = _make_block(msgs)
    assert block["combined_text"] == "line one\nline two"
    assert block["start_ts"] == "1000.0"
    assert block["end_ts"] == "1010.0"


# ── build_context_blocks ──────────────────────────────────────────────────────

def test_empty_messages_returns_empty():
    assert build_context_blocks([]) == []


def test_single_message_becomes_one_block():
    msgs = [{"user": "U1", "text": "bug report", "ts": "1000.0"}]
    blocks = build_context_blocks(msgs)
    assert len(blocks) == 1
    assert blocks[0]["combined_text"] == "bug report"


def test_messages_within_window_grouped_into_one_block():
    # 60s apart, window default = 5 min (300s) → same block
    msgs = [
        {"user": "U1", "text": "login is broken", "ts": "1000.0"},
        {"user": "U1", "text": "started after deploy", "ts": "1060.0"},
    ]
    blocks = build_context_blocks(msgs)
    assert len(blocks) == 1
    assert "login is broken" in blocks[0]["combined_text"]
    assert "started after deploy" in blocks[0]["combined_text"]


def test_messages_outside_window_split_into_separate_blocks():
    # 400s apart, window = 5 min (300s) → separate blocks
    msgs = [
        {"user": "U1", "text": "first report", "ts": "1000.0"},
        {"user": "U2", "text": "second report", "ts": "1400.0"},
    ]
    blocks = build_context_blocks(msgs)
    assert len(blocks) == 2
    assert blocks[0]["combined_text"] == "first report"
    assert blocks[1]["combined_text"] == "second report"


def test_three_messages_two_windows():
    msgs = [
        {"user": "U1", "text": "a", "ts": "1000.0"},
        {"user": "U1", "text": "b", "ts": "1060.0"},   # same window as a
        {"user": "U2", "text": "c", "ts": "2000.0"},   # new window
    ]
    blocks = build_context_blocks(msgs)
    assert len(blocks) == 2
    assert "a" in blocks[0]["combined_text"]
    assert "b" in blocks[0]["combined_text"]
    assert blocks[1]["combined_text"] == "c"


def test_messages_exactly_at_window_boundary_stay_together():
    # Gap == window_seconds exactly → same block (gap <= window)
    msgs = [
        {"user": "U1", "text": "msg1", "ts": "1000.0"},
        {"user": "U1", "text": "msg2", "ts": "1300.0"},  # exactly 300s = 5 min
    ]
    blocks = build_context_blocks(msgs)
    assert len(blocks) == 1


def test_block_preserves_original_messages():
    msgs = [
        {"user": "U1", "text": "x", "ts": "1000.0"},
        {"user": "U2", "text": "y", "ts": "1001.0"},
    ]
    blocks = build_context_blocks(msgs)
    assert blocks[0]["messages"] == msgs


def test_block_start_and_end_timestamps():
    msgs = [
        {"user": "U1", "text": "first", "ts": "1000.0"},
        {"user": "U1", "text": "last",  "ts": "1200.0"},
    ]
    block = build_context_blocks(msgs)[0]
    assert block["start_ts"] == "1000.0"
    assert block["end_ts"] == "1200.0"
