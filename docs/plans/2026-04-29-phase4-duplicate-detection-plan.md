# Implementation Plan: Phase 4 — Duplicate Detection

> Plan doc. Written 2026-04-29.
> Design: `docs/plans/2026-04-29-phase4-duplicate-detection-design.md`
> Diagram: `docs/diagrams/2026-04-29-phase4-duplicate-detection.md`

---

## Goal

Add a pre-ticket gate that checks whether a Slack message is semantically similar to an existing open Jira ticket (cosine similarity ≥ 0.85) before calling the LLM — preventing duplicate ticket creation.

## Architecture

A new `pipeline/duplicate_detector.py` module owns all embedding + similarity logic. At run start, `triage_agent.run()` fetches Slack messages and open Jira tickets in parallel via `asyncio.gather`, builds an embedding cache (only re-embedding tickets not already cached), then checks each block before deciding whether to call `_run_llm_loop()`. If a match is found, it posts a Slack duplicate notice and logs `BlockResult(action="duplicate_flagged")` without calling the LLM.

## Files Affected

| Action | File |
|--------|------|
| CREATE | `pipeline/duplicate_detector.py` |
| CREATE | `tests/unit/test_duplicate_detector.py` |
| CREATE | `memory/` directory (empty placeholder) |
| MODIFY | `agents/triage/triage_agent.py` |
| MODIFY | `tests/unit/test_triage_agent.py` |
| MODIFY | `pipeline/run_logger.py` |
| MODIFY | `config/settings.py` |
| MODIFY | `.gitignore` |
| MODIFY | `dashboard.py` |

---

## Block 1 — `duplicate_detector.py`: Pure Logic + I/O

### Chunk 1.1 — `cosine_similarity()` + `find_duplicate()`
```
Test layer: UNIT
Files:
  Create: pipeline/duplicate_detector.py
  Create: tests/unit/test_duplicate_detector.py
Test file: tests/unit/test_duplicate_detector.py
```

**RED** — Write these failing tests:
```python
import math, pytest
from pipeline.duplicate_detector import cosine_similarity, find_duplicate

def test_cosine_similarity_identical_vectors_returns_one():
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)

def test_cosine_similarity_orthogonal_vectors_returns_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

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
```
Run: `pytest tests/unit/test_duplicate_detector.py -v`
Expect: `FAILED — ModuleNotFoundError: No module named 'pipeline.duplicate_detector'`

**GREEN** — Implement `cosine_similarity` and `find_duplicate` in `pipeline/duplicate_detector.py`:
```python
import math

def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    mag = math.sqrt(sum(x**2 for x in a)) * math.sqrt(sum(x**2 for x in b))
    return dot / mag if mag else 0.0

def find_duplicate(block_embedding, cache, threshold):
    best = None
    for key, entry in cache.items():
        sim = cosine_similarity(block_embedding, entry["embedding"])
        if sim >= threshold and (best is None or sim > best["similarity"]):
            best = {"key": key, "summary": entry["summary"], "similarity": sim}
    return best
```
Run: `pytest tests/unit/test_duplicate_detector.py -v`
Expect: `5 passed`

**REFACTOR** — Add module docstring. Confirm `find_duplicate` docstring describes the `{key, summary, similarity}` return shape.

**COMMIT:**
```
git add . && git commit -m "[Add] duplicate_detector: cosine_similarity + find_duplicate"
```

---

### Chunk 1.2 — `load_embedding_cache()`
```
Test layer: UNIT
Files:
  Modify: pipeline/duplicate_detector.py
  Modify: tests/unit/test_duplicate_detector.py
Test file: tests/unit/test_duplicate_detector.py
```

**RED**:
```python
import json, os
from pipeline.duplicate_detector import load_embedding_cache

def test_load_embedding_cache_returns_empty_when_file_missing():
    assert load_embedding_cache("/tmp/nonexistent_xyz_abc/cache.json") == {}

def test_load_embedding_cache_returns_dict_when_file_exists(tmp_path):
    data = {"tickets": {"SCRUM-1": {"summary": "test", "status": "Open", "embedding": [0.1]}}}
    f = tmp_path / "cache.json"
    f.write_text(json.dumps(data))
    assert load_embedding_cache(str(f)) == data

def test_load_embedding_cache_returns_empty_when_malformed(tmp_path):
    f = tmp_path / "cache.json"
    f.write_text("{not valid json")
    assert load_embedding_cache(str(f)) == {}
```
Run: `pytest tests/unit/test_duplicate_detector.py -v`
Expect: `FAILED — ImportError: cannot import name 'load_embedding_cache'`

**GREEN** — Add `load_embedding_cache` to `duplicate_detector.py`.

**REFACTOR** — Confirm it never raises, only returns `{}` on any error.

**COMMIT:**
```
git add . && git commit -m "[Add] duplicate_detector: load_embedding_cache"
```

---

### Chunk 1.3 — `embed_texts()`
```
Test layer: UNIT
Files:
  Modify: pipeline/duplicate_detector.py
  Modify: tests/unit/test_duplicate_detector.py
Test file: tests/unit/test_duplicate_detector.py
```

**RED**:
```python
import pytest
from unittest.mock import MagicMock, patch
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
```
Run: `pytest tests/unit/test_duplicate_detector.py -v`
Expect: `FAILED — ImportError: cannot import name 'embed_texts'`

**GREEN** — Add `_embed_client = OpenAI(api_key=settings.OPENAI_API_KEY)` and `async def embed_texts(texts)` to `duplicate_detector.py`. Uses `asyncio.get_event_loop().run_in_executor(None, ...)` or direct sync call (OpenAI SDK is sync).

Note: `openai.embeddings.create` is synchronous. Wrap in `asyncio.to_thread()` for non-blocking:
```python
import asyncio
async def embed_texts(texts):
    resp = await asyncio.to_thread(
        _embed_client.embeddings.create,
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    return [d.embedding for d in resp.data]
```

**REFACTOR** — Confirm `_embed_client` is module-level (follows `_client` pattern in `triage_agent.py`).

**COMMIT:**
```
git add . && git commit -m "[Add] duplicate_detector: embed_texts via text-embedding-3-small"
```

---

### Chunk 1.4 — `build_embedding_cache()`
```
Test layer: UNIT
Files:
  Modify: pipeline/duplicate_detector.py
  Modify: tests/unit/test_duplicate_detector.py
Test file: tests/unit/test_duplicate_detector.py
```

**RED**:
```python
import json, os, pytest
from unittest.mock import AsyncMock, patch
from pipeline.duplicate_detector import build_embedding_cache

@pytest.mark.asyncio
async def test_build_embedding_cache_skips_already_cached_tickets(tmp_path):
    existing = {"SCRUM-1": {"summary": "old", "status": "Open", "embedding": [0.5]}}
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
            {}, cache_path
        )
    assert os.path.exists(cache_path)
    data = json.load(open(cache_path))
    assert "SCRUM-1" in data["tickets"]
    assert "SCRUM-1" in result

@pytest.mark.asyncio
async def test_build_embedding_cache_returns_merged_cache_when_no_new_tickets(tmp_path):
    existing = {"SCRUM-5": {"summary": "x", "status": "Open", "embedding": [0.5]}}
    with patch("pipeline.duplicate_detector.embed_texts",
               new_callable=AsyncMock) as mock_embed:
        result = await build_embedding_cache(
            [{"key": "SCRUM-5", "summary": "x", "status": "Open"}],
            existing, str(tmp_path / "c.json")
        )
    mock_embed.assert_not_called()
    assert "SCRUM-5" in result
```
Run: `pytest tests/unit/test_duplicate_detector.py -v`
Expect: `FAILED — ImportError: cannot import name 'build_embedding_cache'`

**GREEN** — Implement `build_embedding_cache`.

**REFACTOR** — Cache file written with `{"refreshed_at": ..., "project_key": ..., "tickets": {...}}` schema.

**COMMIT:**
```
git add . && git commit -m "[Add] duplicate_detector: build_embedding_cache with diff-only re-embedding"
```

---

### Chunk 1.5 — `fetch_open_tickets()`
```
Test layer: UNIT
Files:
  Modify: pipeline/duplicate_detector.py
  Modify: tests/unit/test_duplicate_detector.py
Test file: tests/unit/test_duplicate_detector.py
```

**RED**:
```python
import json, pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pipeline.duplicate_detector import fetch_open_tickets

@pytest.mark.asyncio
async def test_fetch_open_tickets_calls_jira_search_with_jql():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps({"issues": [
        {"key": "SCRUM-5", "fields": {"summary": "Login crash",
                                      "status": {"name": "In Progress"}}}
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

@pytest.mark.asyncio
async def test_fetch_open_tickets_returns_empty_list_on_error():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(side_effect=Exception("Jira MCP down"))
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("pipeline.duplicate_detector.jira_mcp_session", return_value=ctx):
        result = await fetch_open_tickets("SCRUM")
    assert result == []
```
Run: `pytest tests/unit/test_duplicate_detector.py -v`
Expect: `FAILED — ImportError: cannot import name 'fetch_open_tickets'`

**GREEN** — Import `jira_mcp_session` from `agents.triage.tools.jira_tools`. Implement `fetch_open_tickets`. Parse `result.content[0].text` JSON → extract `issues` list → map to `{key, summary, status}`.

**REFACTOR** — JQL string: `f"project = {project_key} AND status not in (Done, Closed) ORDER BY created DESC"`.

**COMMIT:**
```
git add . && git commit -m "[Add] duplicate_detector: fetch_open_tickets via jira_search JQL"
```

---

### Chunk 1.6 — `add_ticket_to_cache()`
```
Test layer: UNIT
Files:
  Modify: pipeline/duplicate_detector.py
  Modify: tests/unit/test_duplicate_detector.py
Test file: tests/unit/test_duplicate_detector.py
```

**RED**:
```python
import json, os
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
```
Run: `pytest tests/unit/test_duplicate_detector.py -v`
Expect: `FAILED — ImportError: cannot import name 'add_ticket_to_cache'`

**GREEN** — Implement `add_ticket_to_cache`. Mutates and returns cache. Writes `{"tickets": cache}` to disk.

**REFACTOR** — Confirm disk write failure is logged but not raised.

**COMMIT:**
```
git add . && git commit -m "[Add] duplicate_detector: add_ticket_to_cache for intra-run dedup"
```

**After Block 1 — run full unit suite:**
```
pytest tests/unit/ -v
```
Expect: all passing, no regressions.

---

## Block 2 — Config + RunLog

### Chunk 2.1 — New Settings
```
Test layer: UNIT
Files:
  Modify: config/settings.py
Test file: tests/unit/test_duplicate_detector.py (imports settings)
```

**RED** — Add to existing tests:
```python
from config.settings import settings

def test_settings_has_duplicate_threshold():
    assert hasattr(settings, "DUPLICATE_SIMILARITY_THRESHOLD")
    assert settings.DUPLICATE_SIMILARITY_THRESHOLD == pytest.approx(0.85)

def test_settings_has_embedding_model():
    assert settings.EMBEDDING_MODEL == "text-embedding-3-small"

def test_settings_has_embedding_cache_path():
    assert "memory" in settings.EMBEDDING_CACHE_PATH

def test_settings_has_jira_open_tickets_limit():
    assert settings.JIRA_OPEN_TICKETS_LIMIT == 50
```
Run: `pytest tests/unit/test_duplicate_detector.py -k "settings" -v`
Expect: `FAILED — AttributeError: type object 'Settings' has no attribute 'DUPLICATE_SIMILARITY_THRESHOLD'`

**GREEN** — Add four settings to `config/settings.py`.

**REFACTOR** — Place under a `# ── Duplicate Detection ──` header.

**COMMIT:**
```
git add . && git commit -m "[Add] settings: DUPLICATE_SIMILARITY_THRESHOLD, EMBEDDING_MODEL, EMBEDDING_CACHE_PATH, JIRA_OPEN_TICKETS_LIMIT"
```

---

### Chunk 2.2 — `RunLog.duplicates_flagged_count`
```
Test layer: UNIT
Files:
  Modify: pipeline/run_logger.py
  Modify: tests/unit/test_run_logger.py
Test file: tests/unit/test_run_logger.py
```

**RED**:
```python
from pipeline.run_logger import RunLog

def test_run_log_has_duplicates_flagged_count_field():
    log = RunLog(
        run_id="x", started_at="x", completed_at="x", status="success",
        messages_fetched=0, blocks_processed=0, tickets_created_count=0,
        clarifications_asked_count=0, blocks_skipped_count=0, error_count=0,
    )
    assert hasattr(log, "duplicates_flagged_count")
    assert log.duplicates_flagged_count == 0

def test_run_log_serialises_duplicates_flagged_count(tmp_path):
    from pipeline.run_logger import write_run_log
    log = RunLog(
        run_id="2026-04-29T10:00:00", started_at="x", completed_at="x",
        status="success", messages_fetched=5, blocks_processed=2,
        tickets_created_count=1, clarifications_asked_count=0,
        blocks_skipped_count=0, error_count=0, duplicates_flagged_count=1,
    )
    path = write_run_log(log, str(tmp_path))
    import json
    data = json.load(open(path))
    assert data["duplicates_flagged_count"] == 1
```
Run: `pytest tests/unit/test_run_logger.py -v`
Expect: `FAILED — TypeError: RunLog.__init__() got an unexpected keyword argument`

**GREEN** — Add `duplicates_flagged_count: int = 0` to `RunLog` dataclass (with default so existing code doesn't break).

**REFACTOR** — Confirm default=0 means all existing `RunLog(...)` calls compile without change.

**COMMIT:**
```
git add . && git commit -m "[Add] run_logger: RunLog.duplicates_flagged_count field"
```

**After Block 2 — run full unit suite:**
```
pytest tests/unit/ -v
```

---

## Block 3 — `run()` Integration

> **Note on existing test helper:** `patch_run_deps` in `test_triage_agent.py` currently patches
> `fetch_messages`, `build_context_blocks`, and `_run_llm_loop`. Chunks 3.1–3.4 extend it or
> add standalone tests that patch the new Phase 4 functions directly.

### Chunk 3.1 — Parallel Fetch (`asyncio.gather`)
```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
  Modify: tests/unit/test_triage_agent.py
Test file: tests/unit/test_triage_agent.py
```

**RED**:
```python
@pytest.mark.asyncio
async def test_run_fetches_open_tickets_in_parallel():
    """fetch_open_tickets is called once per run."""
    blocks = make_one_block()
    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock, return_value=[]):
        with patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock, return_value=make_block_result()):
                with patch("agents.triage.triage_agent.fetch_open_tickets",
                           new_callable=AsyncMock, return_value=[]) as mock_fetch_tickets:
                    with patch("agents.triage.triage_agent.build_embedding_cache",
                               new_callable=AsyncMock, return_value={}):
                        with patch("agents.triage.triage_agent.embed_texts",
                                   new_callable=AsyncMock, return_value=[[0.5]]):
                            with patch("agents.triage.triage_agent.find_duplicate",
                                       return_value=None):
                                with patch("agents.triage.triage_agent.write_run_log"):
                                    with patch("agents.triage.triage_agent.post_slack_message",
                                               new_callable=AsyncMock):
                                        await run()
    mock_fetch_tickets.assert_called_once_with(settings.JIRA_PROJECT_KEY)
```
Run: `pytest tests/unit/test_triage_agent.py -k "parallel" -v`
Expect: `FAILED — AttributeError: module 'agents.triage.triage_agent' does not have attribute 'fetch_open_tickets'`

**GREEN** — Add to `triage_agent.py`:
```python
import asyncio
from pipeline.duplicate_detector import (
    fetch_open_tickets, embed_texts, build_embedding_cache,
    find_duplicate, add_ticket_to_cache, load_embedding_cache,
)
```
In `run()`: replace sequential fetch with:
```python
messages, open_tickets = await asyncio.gather(
    fetch_messages(settings.SLACK_CHANNEL_ID),
    fetch_open_tickets(settings.JIRA_PROJECT_KEY),
)
```

**REFACTOR** — Confirm `asyncio` import is back at top-of-file.

**COMMIT:**
```
git add . && git commit -m "[Add] triage_agent: asyncio.gather for parallel Slack+Jira fetch"
```

---

### Chunk 3.2 — Cache Bootstrap
```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
  Modify: tests/unit/test_triage_agent.py
Test file: tests/unit/test_triage_agent.py
```

**RED**:
```python
@pytest.mark.asyncio
async def test_run_builds_embedding_cache_after_fetch():
    """build_embedding_cache is called once with the fetched tickets."""
    blocks = make_one_block()
    tickets = [{"key": "SCRUM-5", "summary": "Old bug", "status": "Open"}]
    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock, return_value=[]):
        with patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock, return_value=make_block_result()):
                with patch("agents.triage.triage_agent.fetch_open_tickets",
                           new_callable=AsyncMock, return_value=tickets):
                    with patch("agents.triage.triage_agent.load_embedding_cache",
                               return_value={}) as mock_load:
                        with patch("agents.triage.triage_agent.build_embedding_cache",
                                   new_callable=AsyncMock, return_value={}) as mock_build:
                            with patch("agents.triage.triage_agent.embed_texts",
                                       new_callable=AsyncMock, return_value=[[0.5]]):
                                with patch("agents.triage.triage_agent.find_duplicate",
                                           return_value=None):
                                    with patch("agents.triage.triage_agent.write_run_log"):
                                        with patch("agents.triage.triage_agent.post_slack_message",
                                                   new_callable=AsyncMock):
                                            await run()
    mock_build.assert_called_once()
    args = mock_build.call_args[0]
    assert args[0] == tickets  # tickets passed in
```
Expect: `FAILED — AssertionError: build_embedding_cache not called`

**GREEN** — Add to `run()` after the gather:
```python
existing_cache = load_embedding_cache(settings.EMBEDDING_CACHE_PATH)
cache = await build_embedding_cache(open_tickets, existing_cache, settings.EMBEDDING_CACHE_PATH)
```

**COMMIT:**
```
git add . && git commit -m "[Add] triage_agent: load + build embedding cache at run start"
```

---

### Chunk 3.3 — Duplicate Gate in Block Loop
```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
  Modify: tests/unit/test_triage_agent.py
Test file: tests/unit/test_triage_agent.py
```

**RED**:
```python
@pytest.mark.asyncio
async def test_run_flags_duplicate_when_similarity_above_threshold():
    """When find_duplicate returns a match, LLM loop is skipped."""
    blocks = make_one_block("login is broken")
    match = {"key": "SCRUM-5", "summary": "Login crash", "similarity": 0.92}

    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock, return_value=[]):
        with patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks):
            with patch("agents.triage.triage_agent.fetch_open_tickets",
                       new_callable=AsyncMock, return_value=[]):
                with patch("agents.triage.triage_agent.load_embedding_cache", return_value={}):
                    with patch("agents.triage.triage_agent.build_embedding_cache",
                               new_callable=AsyncMock, return_value={}):
                        with patch("agents.triage.triage_agent.embed_texts",
                                   new_callable=AsyncMock, return_value=[[0.5]]):
                            with patch("agents.triage.triage_agent.find_duplicate",
                                       return_value=match):
                                with patch("agents.triage.triage_agent._run_llm_loop",
                                           new_callable=AsyncMock) as mock_llm:
                                    with patch("agents.triage.triage_agent.post_slack_message",
                                               new_callable=AsyncMock) as mock_post:
                                        with patch("agents.triage.triage_agent.write_run_log"):
                                            await run()

    mock_llm.assert_not_called()
    assert mock_post.called
    first_call = mock_post.call_args_list[0][0][0]
    assert "SCRUM-5" in first_call


@pytest.mark.asyncio
async def test_run_proceeds_to_llm_when_no_duplicate():
    """When find_duplicate returns None, LLM loop runs normally."""
    blocks = make_one_block("login is broken")

    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock, return_value=[]):
        with patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks):
            with patch("agents.triage.triage_agent.fetch_open_tickets",
                       new_callable=AsyncMock, return_value=[]):
                with patch("agents.triage.triage_agent.load_embedding_cache", return_value={}):
                    with patch("agents.triage.triage_agent.build_embedding_cache",
                               new_callable=AsyncMock, return_value={}):
                        with patch("agents.triage.triage_agent.embed_texts",
                                   new_callable=AsyncMock, return_value=[[0.5]]):
                            with patch("agents.triage.triage_agent.find_duplicate",
                                       return_value=None):
                                with patch("agents.triage.triage_agent._run_llm_loop",
                                           new_callable=AsyncMock,
                                           return_value=make_block_result()) as mock_llm:
                                    with patch("agents.triage.triage_agent.post_slack_message",
                                               new_callable=AsyncMock):
                                        with patch("agents.triage.triage_agent.write_run_log"):
                                            await run()

    mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_run_increments_duplicates_flagged_count():
    blocks = make_one_block()
    match = {"key": "SCRUM-5", "summary": "Login crash", "similarity": 0.92}

    with patch("agents.triage.triage_agent.fetch_messages", new_callable=AsyncMock, return_value=[]):
        with patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks):
            with patch("agents.triage.triage_agent.fetch_open_tickets", new_callable=AsyncMock, return_value=[]):
                with patch("agents.triage.triage_agent.load_embedding_cache", return_value={}):
                    with patch("agents.triage.triage_agent.build_embedding_cache", new_callable=AsyncMock, return_value={}):
                        with patch("agents.triage.triage_agent.embed_texts", new_callable=AsyncMock, return_value=[[0.5]]):
                            with patch("agents.triage.triage_agent.find_duplicate", return_value=match):
                                with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                                    with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                                        await run()

    log = mock_write.call_args[0][0]
    assert log.duplicates_flagged_count == 1
```
Expect: `FAILED — AssertionError` (LLM called when it shouldn't be, or count wrong)

**GREEN** — In the block loop, before `_run_llm_loop`:
```python
[block_emb] = await embed_texts([snippet])
match = find_duplicate(block_emb, cache, settings.DUPLICATE_SIMILARITY_THRESHOLD)
if match:
    await post_slack_message(
        f"⚠️ This looks like a duplicate of [{match['key']}]"
        f"({settings.JIRA_URL}/browse/{match['key']}): "
        f"\"{match['summary']}\"\n"
        f"Similarity: {match['similarity']:.0%}. "
        f"Is this the same issue, or something new?"
    )
    run_log.duplicates_flagged_count += 1
    run_log.blocks.append(BlockResult(
        block_index=i, block_snippet=snippet,
        action="duplicate_flagged", ticket_key=match["key"],
        ticket_summary=match["summary"],
    ))
    _print_block_outcome(run_log.blocks[-1], index=i, total=len(blocks))
    continue
```

**REFACTOR** — Add `"duplicate_flagged"` to the `icons` dict in `_print_block_outcome`.

**COMMIT:**
```
git add . && git commit -m "[Add] triage_agent: duplicate gate with similarity check before LLM loop"
```

---

### Chunk 3.4 — `add_ticket_to_cache` After Creation
```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
  Modify: tests/unit/test_triage_agent.py
Test file: tests/unit/test_triage_agent.py
```

**RED**:
```python
@pytest.mark.asyncio
async def test_run_adds_new_ticket_to_cache_after_creation():
    """After a ticket is created, add_ticket_to_cache is called."""
    blocks = make_one_block()

    with patch("agents.triage.triage_agent.fetch_messages", new_callable=AsyncMock, return_value=[]):
        with patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks):
            with patch("agents.triage.triage_agent.fetch_open_tickets", new_callable=AsyncMock, return_value=[]):
                with patch("agents.triage.triage_agent.load_embedding_cache", return_value={}):
                    with patch("agents.triage.triage_agent.build_embedding_cache", new_callable=AsyncMock, return_value={}):
                        with patch("agents.triage.triage_agent.embed_texts",
                                   new_callable=AsyncMock, return_value=[[0.5]]):
                            with patch("agents.triage.triage_agent.find_duplicate", return_value=None):
                                with patch("agents.triage.triage_agent._run_llm_loop",
                                           new_callable=AsyncMock,
                                           return_value=make_block_result(action="ticket_created", key="SCRUM-12")):
                                    with patch("agents.triage.triage_agent.add_ticket_to_cache") as mock_add:
                                        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                                            with patch("agents.triage.triage_agent.write_run_log"):
                                                await run()

    mock_add.assert_called_once()
    args = mock_add.call_args[0]
    assert args[1] == "SCRUM-12"  # ticket_key
```
Expect: `FAILED — AssertionError: add_ticket_to_cache not called`

**GREEN** — After `_run_llm_loop` returns a `ticket_created` result:
```python
if result.action == "ticket_created" and result.ticket_key:
    [ticket_emb] = await embed_texts([result.ticket_summary or ""])
    cache = add_ticket_to_cache(
        cache, result.ticket_key, result.ticket_summary or "",
        ticket_emb, settings.EMBEDDING_CACHE_PATH
    )
```

**REFACTOR** — Confirm the cache variable is always updated (returned value from `add_ticket_to_cache` replaces `cache`).

**COMMIT:**
```
git add . && git commit -m "[Add] triage_agent: add newly created ticket to cache for intra-run dedup"
```

**After Block 3 — run full unit suite:**
```
pytest tests/unit/ -v
```

---

## Block 4 — Infrastructure

### Chunk 4.1 — `.gitignore`, `memory/`, Dashboard Column
```
Test layer: UNIT (settings test)
Files:
  Modify: .gitignore
  Modify: dashboard.py
  Create: memory/.gitkeep
Test file: (no new unit tests needed — dashboard verified E2E)
```

**GREEN** (no RED step — infrastructure changes):

1. Add to `.gitignore`:
```
memory/
```

2. Create `memory/.gitkeep` (empty placeholder so the dir is tracked but contents are not).

3. In `dashboard.py` run history table, add `"Duplicates": l.get("duplicates_flagged_count", 0)` to the rows dict.

Verify: `pytest tests/unit/ -v` — all still passing.

**COMMIT:**
```
git add . && git commit -m "[Add] infrastructure: memory/ dir, .gitignore, dashboard Duplicates column"
```

**After Block 4 — run full unit suite:**
```
pytest tests/unit/ -v
```

---

## E2E Verification Checklist (run in /audit Part 3)

| Scenario | Expected | How to verify |
|----------|----------|---------------|
| Post a message identical to an existing bug | `⚠️ Possible duplicate of SCRUM-X` posted in Slack, no new ticket | Run agent twice with same Slack message |
| Post a clearly different bug | New Jira ticket created, no duplicate notice | Run agent with new message |
| Run when Jira is unreachable | Agent continues, logs error, creates ticket normally | Temporarily break Jira URL in `.env` |
| Cold start (empty cache) | All blocks proceed to LLM loop | Delete `memory/ticket_embeddings.json`, run agent |
| Dashboard shows `Duplicates` column | Column appears in run history table | Open `http://localhost:8501` after a run with a duplicate |
| Run log contains `duplicates_flagged_count` | `> 0` in JSON log | Check `logs/run_*.json` after E2E |

---

## Success Criteria

- [ ] Duplicate ticket rate = 0% for messages with a known match ≥ 0.85 — verified E2E
- [ ] False positive rate < 5% — verified E2E with 3+ distinct messages
- [ ] Cache overhead < 2 seconds — timed before/after
- [ ] All duplicate decisions in `BlockResult` (action = "duplicate_flagged") — verified in log
- [ ] All unit tests pass
- [ ] All integration tests pass

---

## Known Technical Debt

| ID | Description | Acceptable because |
|----|-------------|-------------------|
| DEBT-010 | `jira_search` capped at 50 tickets | Sufficient for current project size; pagination added in Phase 4b if needed |
| DEBT-011 | `embed_texts` uses `asyncio.to_thread` (sync SDK wrapped) | OpenAI SDK is sync-only; wrapping is the idiomatic solution until async support ships |
| DEBT-012 | `memory/ticket_embeddings.json` has no size limit or rotation | Cache grows indefinitely; acceptable until Phase 5 reliability work |
