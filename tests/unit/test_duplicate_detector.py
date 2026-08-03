"""
Unit tests for pipeline.duplicate_detector

Tests are grouped by function:
  - cosine_similarity
  - find_duplicate
  - load_embedding_cache
  - embed_texts
  - build_embedding_cache
  - fetch_open_tickets
  - add_ticket_to_cache
"""
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import settings


# ── cosine_similarity ─────────────────────────────────────────────────────────

from pipeline.duplicate_detector import cosine_similarity, find_duplicate


def test_cosine_similarity_identical_vectors_returns_one():
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_returns_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector_returns_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)


# ── find_duplicate ────────────────────────────────────────────────────────────

def test_find_duplicate_returns_none_when_cache_empty():
    assert find_duplicate([1.0, 0.0], {}, threshold=0.85) is None


def test_find_duplicate_returns_best_match_above_threshold():
    cache = {"SCRUM-5": {"summary": "Login crash", "status": "Open", "embedding": [1.0, 0.0]}}
    result = find_duplicate([1.0, 0.0], cache, threshold=0.85)
    assert result is not None
    assert result["key"] == "SCRUM-5"
    assert result["similarity"] >= 0.85


def test_find_duplicate_returns_none_below_threshold():
    cache = {"SCRUM-5": {"summary": "Login crash", "status": "Open", "embedding": [0.0, 1.0]}}
    assert find_duplicate([1.0, 0.0], cache, threshold=0.85) is None


def test_find_duplicate_returns_best_of_multiple_matches():
    cache = {
        "SCRUM-3": {"summary": "Old bug", "status": "Open", "embedding": [0.9, 0.1]},
        "SCRUM-5": {"summary": "Login crash", "status": "Open", "embedding": [1.0, 0.0]},
    }
    result = find_duplicate([1.0, 0.0], cache, threshold=0.85)
    assert result is not None
    assert result["key"] == "SCRUM-5"


def test_find_duplicate_result_contains_expected_keys():
    cache = {"SCRUM-5": {"summary": "Login crash", "status": "Open", "embedding": [1.0, 0.0]}}
    result = find_duplicate([1.0, 0.0], cache, threshold=0.85)
    assert "key" in result
    assert "summary" in result
    assert "similarity" in result


# ── load_embedding_cache ──────────────────────────────────────────────────────

from pipeline.duplicate_detector import load_embedding_cache


def test_load_embedding_cache_returns_empty_when_file_missing():
    assert load_embedding_cache("/tmp/nonexistent_xyz_abc_123/cache.json") == {}


def test_load_embedding_cache_returns_dict_when_file_exists(tmp_path):
    data = {"tickets": {"SCRUM-1": {"summary": "test", "status": "Open", "embedding": [0.1]}}}
    f = tmp_path / "cache.json"
    f.write_text(json.dumps(data))
    assert load_embedding_cache(str(f)) == data


def test_load_embedding_cache_returns_empty_when_malformed(tmp_path):
    f = tmp_path / "cache.json"
    f.write_text("{not valid json")
    assert load_embedding_cache(str(f)) == {}


# ── embed_texts ───────────────────────────────────────────────────────────────

from pipeline.duplicate_detector import embed_texts


@pytest.mark.asyncio
async def test_embed_texts_calls_openai_with_correct_model():
    mock_resp = MagicMock()
    mock_resp.data = [MagicMock(embedding=[0.1, 0.2])]
    with patch("pipeline.duplicate_detector._embed_client") as mock_client:
        mock_client.embeddings.create.return_value = mock_resp
        result = await embed_texts(["hello"])
    kwargs = mock_client.embeddings.create.call_args[1]
    assert kwargs["model"] == "text-embedding-3-small"
    assert result == [[0.1, 0.2]]


@pytest.mark.asyncio
async def test_embed_texts_returns_one_vector_per_input():
    mock_resp = MagicMock()
    mock_resp.data = [MagicMock(embedding=[0.1]), MagicMock(embedding=[0.2])]
    with patch("pipeline.duplicate_detector._embed_client") as mock_client:
        mock_client.embeddings.create.return_value = mock_resp
        result = await embed_texts(["a", "b"])
    assert len(result) == 2


# ── build_embedding_cache ─────────────────────────────────────────────────────

from pipeline.duplicate_detector import build_embedding_cache


@pytest.mark.asyncio
async def test_build_embedding_cache_skips_already_cached_tickets(tmp_path):
    existing = {"tickets": {"SCRUM-1": {"summary": "old", "status": "Open", "embedding": [0.5]}}}
    tickets = [
        {"key": "SCRUM-1", "summary": "old", "status": "Open"},
        {"key": "SCRUM-2", "summary": "new one", "status": "Open"},
    ]
    with patch("pipeline.duplicate_detector.embed_texts",
               new_callable=AsyncMock, return_value=[[0.9, 0.1]]) as mock_embed:
        await build_embedding_cache(tickets, existing, str(tmp_path / "cache.json"))
    mock_embed.assert_called_once_with(["new one"])


@pytest.mark.asyncio
async def test_build_embedding_cache_writes_to_disk(tmp_path):
    cache_path = str(tmp_path / "cache.json")
    with patch("pipeline.duplicate_detector.embed_texts",
               new_callable=AsyncMock, return_value=[[0.1, 0.2]]):
        result = await build_embedding_cache(
            [{"key": "SCRUM-1", "summary": "test", "status": "Open"}],
            {}, cache_path,
        )
    assert os.path.exists(cache_path)
    data = json.load(open(cache_path))
    assert "SCRUM-1" in data["tickets"]
    assert "SCRUM-1" in result


@pytest.mark.asyncio
async def test_build_embedding_cache_does_not_call_embed_when_all_cached(tmp_path):
    existing = {"tickets": {"SCRUM-5": {"summary": "x", "status": "Open", "embedding": [0.5]}}}
    with patch("pipeline.duplicate_detector.embed_texts",
               new_callable=AsyncMock) as mock_embed:
        result = await build_embedding_cache(
            [{"key": "SCRUM-5", "summary": "x", "status": "Open"}],
            existing, str(tmp_path / "c.json"),
        )
    mock_embed.assert_not_called()
    assert "SCRUM-5" in result


@pytest.mark.asyncio
async def test_build_embedding_cache_prunes_tickets_not_in_open_list(tmp_path):
    """Closed tickets in the cache that are not in the open list should be removed."""
    existing = {
        "tickets": {
            "SCRUM-1": {"summary": "open bug", "status": "Open", "embedding": [0.1]},
            "SCRUM-99": {"summary": "closed bug", "status": "Done", "embedding": [0.9]},
        }
    }
    # Only SCRUM-1 is still open
    tickets = [{"key": "SCRUM-1", "summary": "open bug", "status": "Open"}]
    with patch("pipeline.duplicate_detector.embed_texts", new_callable=AsyncMock):
        result = await build_embedding_cache(tickets, existing, str(tmp_path / "c.json"))
    assert "SCRUM-1" in result
    assert "SCRUM-99" not in result


@pytest.mark.asyncio
async def test_build_embedding_cache_does_not_mutate_input_cache(tmp_path):
    """The caller's existing_cache dict must not be modified in-place."""
    existing = {
        "tickets": {"SCRUM-1": {"summary": "old", "status": "Open", "embedding": [0.5]}}
    }
    original_keys = set(existing["tickets"].keys())
    tickets = [
        {"key": "SCRUM-1", "summary": "old", "status": "Open"},
        {"key": "SCRUM-2", "summary": "new", "status": "Open"},
    ]
    with patch("pipeline.duplicate_detector.embed_texts",
               new_callable=AsyncMock, return_value=[[0.9]]):
        await build_embedding_cache(tickets, existing, str(tmp_path / "c.json"))
    # Caller's dict should be unchanged
    assert set(existing["tickets"].keys()) == original_keys


# ── fetch_open_tickets ────────────────────────────────────────────────────────

from pipeline.duplicate_detector import fetch_open_tickets


@pytest.mark.asyncio
async def test_fetch_open_tickets_returns_parsed_issues():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps({"issues": [
        {"key": "SCRUM-5", "summary": "Login crash",
         "status": {"name": "In Progress"}}
    ]}))]
    mock_session.call_tool = AsyncMock(return_value=mock_result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("pipeline.duplicate_detector.jira_mcp_session", return_value=ctx):
        result = await fetch_open_tickets("SCRUM")

    assert len(result) == 1
    assert result[0]["key"] == "SCRUM-5"
    assert result[0]["summary"] == "Login crash"
    assert result[0]["status"] == "In Progress"


@pytest.mark.asyncio
async def test_fetch_open_tickets_paginates_until_last_page():
    """Two pages: first full (2 issues = limit), second partial (1 issue) → 3 total."""
    mock_session = AsyncMock()

    page1 = MagicMock()
    page1.content = [MagicMock(text=json.dumps({"issues": [
        {"key": "SCRUM-1", "summary": "Bug A", "status": {"name": "Open"}},
        {"key": "SCRUM-2", "summary": "Bug B", "status": {"name": "Open"}},
    ]}))]
    page2 = MagicMock()
    page2.content = [MagicMock(text=json.dumps({"issues": [
        {"key": "SCRUM-3", "summary": "Bug C", "status": {"name": "Open"}},
    ]}))]
    mock_session.call_tool = AsyncMock(side_effect=[page1, page2])

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("pipeline.duplicate_detector.jira_mcp_session", return_value=ctx):
        with patch.object(type(settings), "JIRA_OPEN_TICKETS_LIMIT",
                          new_callable=lambda: property(lambda self: 2)):
            with patch.object(type(settings), "JIRA_MAX_PAGES",
                              new_callable=lambda: property(lambda self: 5)):
                result = await fetch_open_tickets("SCRUM")

    assert len(result) == 3
    assert {r["key"] for r in result} == {"SCRUM-1", "SCRUM-2", "SCRUM-3"}
    assert mock_session.call_tool.call_count == 2


@pytest.mark.asyncio
async def test_fetch_open_tickets_returns_partial_on_mid_pagination_error():
    """First page succeeds, second raises → returns first-page results, not []."""
    mock_session = AsyncMock()

    page1 = MagicMock()
    page1.content = [MagicMock(text=json.dumps({"issues": [
        {"key": "SCRUM-10", "summary": "Bug X", "status": {"name": "Open"}},
        {"key": "SCRUM-11", "summary": "Bug Y", "status": {"name": "Open"}},
    ]}))]
    mock_session.call_tool = AsyncMock(side_effect=[page1, Exception("MCP timeout")])

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("pipeline.duplicate_detector.jira_mcp_session", return_value=ctx):
        with patch.object(type(settings), "JIRA_OPEN_TICKETS_LIMIT",
                          new_callable=lambda: property(lambda self: 2)):
            with patch.object(type(settings), "JIRA_MAX_PAGES",
                              new_callable=lambda: property(lambda self: 5)):
                result = await fetch_open_tickets("SCRUM")

    assert len(result) == 2
    assert result[0]["key"] == "SCRUM-10"


@pytest.mark.asyncio
async def test_fetch_open_tickets_returns_empty_list_on_error():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=Exception("Jira MCP down"))
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("pipeline.duplicate_detector.jira_mcp_session", return_value=ctx):
        result = await fetch_open_tickets("SCRUM")
    assert result == []


# ── add_ticket_to_cache ───────────────────────────────────────────────────────

from pipeline.duplicate_detector import add_ticket_to_cache


def test_add_ticket_to_cache_adds_new_entry_to_in_memory_cache(tmp_path):
    cache = {}
    result = add_ticket_to_cache(cache, "SCRUM-12", "New bug", [0.1, 0.2],
                                  str(tmp_path / "c.json"))
    assert "SCRUM-12" in result
    assert result["SCRUM-12"]["summary"] == "New bug"
    assert result["SCRUM-12"]["embedding"] == [0.1, 0.2]


def test_add_ticket_to_cache_writes_to_disk(tmp_path):
    cache_path = str(tmp_path / "c.json")
    add_ticket_to_cache({}, "SCRUM-12", "Bug", [0.5], cache_path)
    data = json.load(open(cache_path))
    assert "SCRUM-12" in data["tickets"]


def test_add_ticket_to_cache_preserves_existing_entries(tmp_path):
    cache = {"SCRUM-5": {"summary": "Old", "status": "Open", "embedding": [0.9]}}
    result = add_ticket_to_cache(cache, "SCRUM-6", "New", [0.1],
                                  str(tmp_path / "c.json"))
    assert len(result) == 2
