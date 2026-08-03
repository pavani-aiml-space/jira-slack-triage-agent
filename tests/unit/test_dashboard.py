"""
Unit tests for dashboard.py quality data helper.
Streamlit rendering is verified in /audit E2E.
"""
import os
import pytest
from pipeline.quality_metrics import QualityStore, RunQuality, save_quality_store, load_quality_store


def _run_quality(run_id, thumbs_up, thumbs_down, rate):
    return RunQuality(
        run_id=run_id, collected_at="2026-04-29T11:00:00",
        thumbs_up=thumbs_up, thumbs_down=thumbs_down,
        reactions_found=thumbs_up + thumbs_down, thumbs_up_rate=rate,
    )


def test_quality_chart_cold_start_returns_empty(tmp_path):
    """load_quality_store with no file returns empty store — dashboard shows warming up."""
    store = load_quality_store(str(tmp_path / "nonexistent.json"))
    assert store.runs == []


def test_quality_chart_data_returns_rates_per_run(tmp_path):
    path = str(tmp_path / "qs.json")
    store = QualityStore(pending=[], runs=[
        _run_quality("2026-04-29T10:00:00", 3, 1, 0.75),
        _run_quality("2026-04-29T11:00:00", 1, 3, 0.25),
    ])
    save_quality_store(store, path)
    loaded = load_quality_store(path)
    assert len(loaded.runs) == 2
    assert loaded.runs[0].thumbs_up_rate == 0.75
    assert loaded.runs[1].thumbs_up_rate == 0.25


def test_quality_chart_run_ids_are_readable(tmp_path):
    path = str(tmp_path / "qs.json")
    store = QualityStore(pending=[], runs=[
        _run_quality("2026-04-29T10:00:00", 2, 0, 1.0),
    ])
    save_quality_store(store, path)
    loaded = load_quality_store(path)
    assert loaded.runs[0].run_id == "2026-04-29T10:00:00"
