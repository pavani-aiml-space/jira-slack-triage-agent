"""
Unit tests for pipeline.watermark — load/save watermark.
"""
import json
import pytest
from pathlib import Path

from pipeline.watermark import load_watermark, save_watermark


# ── load_watermark ─────────────────────────────────────────────────────────────

def test_load_watermark_returns_none_when_file_missing(tmp_path):
    path = str(tmp_path / "watermark.json")
    assert load_watermark(path) is None


def test_load_watermark_returns_ts_from_existing_file(tmp_path):
    path = tmp_path / "watermark.json"
    path.write_text(json.dumps({"last_ts": "1714045800.123"}))
    assert load_watermark(str(path)) == "1714045800.123"


def test_load_watermark_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "watermark.json"
    path.write_text("not-json{{{")
    assert load_watermark(str(path)) is None


def test_load_watermark_returns_none_when_key_missing(tmp_path):
    path = tmp_path / "watermark.json"
    path.write_text(json.dumps({"other_key": "value"}))
    assert load_watermark(str(path)) is None


# ── save_watermark ─────────────────────────────────────────────────────────────

def test_save_watermark_creates_file_with_correct_ts(tmp_path):
    path = str(tmp_path / "memory" / "watermark.json")
    save_watermark(path, "1714045800.123")
    data = json.loads(Path(path).read_text())
    assert data == {"last_ts": "1714045800.123"}


def test_save_watermark_creates_parent_directories(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "dir" / "watermark.json")
    save_watermark(path, "1714045800.000")
    assert Path(path).exists()


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "watermark.json")
    save_watermark(path, "1714045800.456")
    assert load_watermark(path) == "1714045800.456"


def test_save_watermark_overwrites_existing(tmp_path):
    path = str(tmp_path / "watermark.json")
    save_watermark(path, "1714045800.100")
    save_watermark(path, "1714045900.200")
    assert load_watermark(path) == "1714045900.200"
