"""
Unit tests for pipeline/episode_store.py
All I/O is tmp_path-isolated — no real disk interaction.
"""
import pytest


# ── Chunk 1.1 — Settings ─────────────────────────────────────────────────────

def test_phase7_settings_exist():
    from config.settings import settings
    assert hasattr(settings, "EPISODE_STORE_PATH")
    assert hasattr(settings, "SEMANTIC_STORE_PATH")
    assert hasattr(settings, "MAX_EPISODES")
    assert hasattr(settings, "MAX_INJECTED_EPISODES")
    assert hasattr(settings, "SEMANTIC_EXTRACTION_THRESHOLD")
    assert hasattr(settings, "SEMANTIC_LLM_MIN_PATTERNS")
    assert hasattr(settings, "MAX_SEMANTIC_PATTERN_CHARS")
    assert settings.MAX_EPISODES == 200
    assert settings.MAX_INJECTED_EPISODES == 3
    assert settings.SEMANTIC_EXTRACTION_THRESHOLD == 5


# ── Chunk 1.2 — Episode + EpisodeStore dataclasses + load/save ───────────────

def test_episode_store_round_trip(tmp_path):
    from pipeline.episode_store import (
        Episode, EpisodeStore, save_episode_store, load_episode_store,
    )
    ep = Episode(
        run_id="r1", block_index=0, block_snippet="Login crash",
        ticket_key="SCRUM-8", ticket_type="Bug", ticket_priority="High",
        ticket_summary="Login crashes on empty email", embedding=[0.1, 0.2],
        run_ts="2026-04-29T10:00:00",
    )
    store = EpisodeStore(episodes=[ep])
    path = str(tmp_path / "ep.json")
    save_episode_store(store, path)
    loaded = load_episode_store(path)
    assert len(loaded.episodes) == 1
    assert loaded.episodes[0].ticket_key == "SCRUM-8"


def test_load_episode_store_missing_file_returns_empty():
    from pipeline.episode_store import load_episode_store, EpisodeStore
    store = load_episode_store("memory/nonexistent_ep_test.json")
    assert isinstance(store, EpisodeStore)
    assert store.episodes == []


def test_load_episode_store_corrupt_returns_empty(tmp_path):
    from pipeline.episode_store import load_episode_store
    p = tmp_path / "ep.json"
    p.write_text("not json")
    store = load_episode_store(str(p))
    assert store.episodes == []


# ── Chunk 1.3 — add_episode with MAX_EPISODES pruning ────────────────────────

def _make_ep(key, ts="2026-01-01"):
    from pipeline.episode_store import Episode
    return Episode("r", 0, "snip", key, "Bug", "High", "summary", [0.1], ts)


def test_add_episode_appends_to_store():
    from pipeline.episode_store import add_episode, EpisodeStore
    store = EpisodeStore()
    add_episode(store, _make_ep("SCRUM-1"), max_episodes=10)
    assert len(store.episodes) == 1
    assert store.episodes[0].ticket_key == "SCRUM-1"


def test_add_episode_prunes_oldest_when_over_max():
    from pipeline.episode_store import add_episode, EpisodeStore
    store = EpisodeStore()
    for i in range(5):
        add_episode(store, _make_ep(f"SCRUM-{i}"), max_episodes=3)
    assert len(store.episodes) == 3
    # oldest evicted — only the last 3 remain
    keys = [ep.ticket_key for ep in store.episodes]
    assert "SCRUM-0" not in keys
    assert "SCRUM-4" in keys


# ── Chunk 1.4 — retrieve_similar ─────────────────────────────────────────────

def test_retrieve_similar_returns_top_k_by_cosine():
    from pipeline.episode_store import retrieve_similar, EpisodeStore
    store = EpisodeStore(episodes=[
        _make_ep_with_emb("SCRUM-1", [1.0, 0.0]),   # identical to query
        _make_ep_with_emb("SCRUM-2", [0.0, 1.0]),   # orthogonal
        _make_ep_with_emb("SCRUM-3", [0.9, 0.1]),   # close
    ])
    results = retrieve_similar(store, query_emb=[1.0, 0.0], top_k=2)
    assert len(results) == 2
    assert results[0].ticket_key == "SCRUM-1"
    assert results[1].ticket_key == "SCRUM-3"


def test_retrieve_similar_returns_empty_when_store_empty():
    from pipeline.episode_store import retrieve_similar, EpisodeStore
    results = retrieve_similar(EpisodeStore(), [1.0, 0.0], top_k=3)
    assert results == []


def test_retrieve_similar_returns_all_when_top_k_exceeds_store():
    from pipeline.episode_store import retrieve_similar, EpisodeStore
    store = EpisodeStore(episodes=[
        _make_ep_with_emb("SCRUM-1", [1.0, 0.0]),
        _make_ep_with_emb("SCRUM-2", [0.5, 0.5]),
    ])
    results = retrieve_similar(store, [1.0, 0.0], top_k=10)
    assert len(results) == 2


def _make_ep_with_emb(key, emb):
    from pipeline.episode_store import Episode
    return Episode("r", 0, "snip", key, "Bug", "High", "summary", emb, "2026-01-01")


# ── Chunk 1.5 — format_episode_context ───────────────────────────────────────

def test_format_episode_context_returns_empty_for_no_episodes():
    from pipeline.episode_store import format_episode_context
    assert format_episode_context([]) == ""


def test_format_episode_context_contains_ticket_info():
    from pipeline.episode_store import format_episode_context, Episode
    ep = Episode("r", 0, "Login crash", "SCRUM-8", "Bug", "High",
                 "Login crashes", [], "2026-04-15T10:00:00")
    result = format_episode_context([ep])
    assert "SCRUM-8" in result
    assert "Bug" in result
    assert "High" in result
    assert "## Similar past decisions" in result


def test_format_episode_context_truncates_snippet():
    from pipeline.episode_store import format_episode_context, Episode
    long_snippet = "x" * 200
    ep = Episode("r", 0, long_snippet, "SCRUM-9", "Story", "Medium",
                 "summary", [], "2026-04-15T10:00:00")
    result = format_episode_context([ep])
    # snippet truncated to 80 chars in the output
    assert len(result) < 300
