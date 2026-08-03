"""
Unit tests for pipeline/semantic_store.py
OpenAI client is mocked — no real API calls.
"""
import pytest
from unittest.mock import MagicMock, patch


# ── Chunk 2.1 — Pattern + SemanticStore + load/save ──────────────────────────

def test_semantic_store_round_trip(tmp_path):
    from pipeline.semantic_store import Pattern, SemanticStore, save_semantic_store, load_semantic_store
    p = Pattern(
        type_priority_key="Bug:High",
        count=8,
        example_summaries=["ex1", "ex2"],
        summary_text="Login and auth issues → Bug High",
        created_at="2026-04-29",
        source="count_based",
    )
    store = SemanticStore(patterns=[p], last_extracted_episode_count=10)
    path = str(tmp_path / "sem.json")
    save_semantic_store(store, path)
    loaded = load_semantic_store(path)
    assert len(loaded.patterns) == 1
    assert loaded.patterns[0].type_priority_key == "Bug:High"
    assert loaded.last_extracted_episode_count == 10


def test_load_semantic_store_missing_file_returns_empty():
    from pipeline.semantic_store import load_semantic_store, SemanticStore
    store = load_semantic_store("memory/nonexistent_sem_test.json")
    assert isinstance(store, SemanticStore)
    assert store.patterns == []
    assert store.last_extracted_episode_count == 0


def test_load_semantic_store_corrupt_returns_empty(tmp_path):
    from pipeline.semantic_store import load_semantic_store
    p = tmp_path / "sem.json"
    p.write_text("not json")
    store = load_semantic_store(str(p))
    assert store.patterns == []


# ── Chunk 2.2 — extract_count_patterns ───────────────────────────────────────

def _make_ep(ticket_type, ticket_priority):
    from pipeline.episode_store import Episode
    return Episode("r", 0, "s", "K", ticket_type, ticket_priority, "s", [0.1], "2026-01-01")


def test_extract_count_patterns_emits_when_count_meets_threshold():
    from pipeline.semantic_store import extract_count_patterns
    episodes = [_make_ep("Bug", "High")] * 6 + [_make_ep("Story", "Medium")] * 3
    patterns = extract_count_patterns(episodes, min_count=5)
    assert len(patterns) == 1
    assert patterns[0].type_priority_key == "Bug:High"
    assert patterns[0].count == 6


def test_extract_count_patterns_returns_empty_below_threshold():
    from pipeline.semantic_store import extract_count_patterns
    episodes = [_make_ep("Bug", "High")] * 3
    patterns = extract_count_patterns(episodes, min_count=5)
    assert patterns == []


def test_extract_count_patterns_multiple_types():
    from pipeline.semantic_store import extract_count_patterns
    episodes = (
        [_make_ep("Bug", "High")] * 5 +
        [_make_ep("Story", "Medium")] * 5 +
        [_make_ep("Task", "Low")] * 2
    )
    patterns = extract_count_patterns(episodes, min_count=5)
    keys = {p.type_priority_key for p in patterns}
    assert "Bug:High" in keys
    assert "Story:Medium" in keys
    assert "Task:Low" not in keys


def test_extract_count_patterns_sets_source_to_count_based():
    from pipeline.semantic_store import extract_count_patterns
    episodes = [_make_ep("Bug", "High")] * 5
    patterns = extract_count_patterns(episodes, min_count=5)
    assert patterns[0].source == "count_based"


# ── Chunk 2.3 — build_semantic_injection ─────────────────────────────────────

def test_build_semantic_injection_returns_empty_when_no_patterns():
    from pipeline.semantic_store import build_semantic_injection, SemanticStore
    assert build_semantic_injection(SemanticStore(), max_chars=1000) == ""


def test_build_semantic_injection_contains_pattern_text():
    from pipeline.semantic_store import build_semantic_injection, SemanticStore, Pattern
    p = Pattern("Bug:High", 8, [], "Login issues → Bug High", "2026-04-29", "count_based")
    store = SemanticStore(patterns=[p])
    result = build_semantic_injection(store, max_chars=1000)
    assert "Bug:High" in result or "Login issues" in result
    assert "## Learned Patterns" in result


def test_build_semantic_injection_respects_max_chars():
    from pipeline.semantic_store import build_semantic_injection, SemanticStore, Pattern
    patterns = [
        Pattern(f"Bug:High{i}", 5, [], "x" * 200, "2026-04-29", "count_based")
        for i in range(20)
    ]
    store = SemanticStore(patterns=patterns)
    result = build_semantic_injection(store, max_chars=100)
    assert len(result) <= 100


# ── Chunk 2.4 — summarise_with_llm ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_summarise_with_llm_updates_summary_text():
    from pipeline.semantic_store import summarise_with_llm, Pattern
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Login and auth issues → Bug, High priority"
    patterns = [
        Pattern("Bug:High", 6, ["login crash", "auth fail"], "Bug:High (6)", "2026-04-29", "count_based")
    ]
    with patch("pipeline.semantic_store._client") as mock_client:
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)
        result = await summarise_with_llm(patterns)
    assert result[0].source == "llm_summarised"
    assert "Login" in result[0].summary_text


@pytest.mark.asyncio
async def test_summarise_with_llm_returns_unchanged_on_exception():
    from pipeline.semantic_store import summarise_with_llm, Pattern
    patterns = [
        Pattern("Bug:High", 6, [], "Bug:High (6)", "2026-04-29", "count_based")
    ]
    with patch("pipeline.semantic_store._client") as mock_client:
        mock_client.chat.completions.create = MagicMock(side_effect=Exception("API down"))
        result = await summarise_with_llm(patterns)
    # Rule 10 — unchanged on failure
    assert result[0].source == "count_based"
    assert result[0].summary_text == "Bug:High (6)"
