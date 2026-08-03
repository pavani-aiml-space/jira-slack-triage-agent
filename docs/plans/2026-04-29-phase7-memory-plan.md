# Implementation Plan: Phase 7 — Agent Memory

**Date:** 2026-04-29
**Goal:** Add three-layer persistent memory (episodic log, semantic patterns, working injection) that makes the agent measurably more consistent across runs without manual prompt editing.

**Architecture:**
`memory_runner.pre_run()` loads stores and returns a `MemoryContext` object. `triage_agent.run(memory_context)` builds an effective system prompt (semantic) and per-block episode context (episodic). `memory_runner.post_run(run_log)` writes new episodes and extracts patterns. All failure modes are silent (Rule 5 / Rule 10 / Rule 11).

**Files affected:**
- CREATE: `pipeline/episode_store.py`
- CREATE: `pipeline/semantic_store.py`
- CREATE: `pipeline/memory_runner.py`
- CREATE: `tests/unit/test_episode_store.py`
- CREATE: `tests/unit/test_semantic_store.py`
- CREATE: `tests/unit/test_memory_runner.py`
- MODIFY: `config/settings.py` — 7 new settings
- MODIFY: `agents/triage/triage_agent.py` — 2 signatures + effective_prompt + block retrieval
- MODIFY: `run_triage.py` — main() gains 2 memory hooks
- MODIFY: `tests/unit/test_triage_agent.py` — add memory_context tests
- MODIFY: `tests/unit/test_eval_runner.py` — add run_triage.main() memory hook test

---

## Block 1 — Settings + Episode Store (Data Layer)

### Chunk 1.1 — Phase 7 Settings
Test layer: UNIT
Files:
  Modify: `config/settings.py`
Test file: `tests/unit/test_episode_store.py`

**Step 1 (RED)** — Write this failing test:
```python
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
```
Run: `pytest tests/unit/test_episode_store.py::test_phase7_settings_exist -v`
Expect: FAILED — AttributeError: type object 'Settings' has no attribute 'EPISODE_STORE_PATH'

**Step 2 (GREEN)** — Add to `config/settings.py` under `# ── Phase 7 — Memory`:
```python
EPISODE_STORE_PATH:             str = os.getenv("EPISODE_STORE_PATH",  "memory/episode_store.json")
SEMANTIC_STORE_PATH:            str = os.getenv("SEMANTIC_STORE_PATH", "memory/semantic_store.json")
MAX_EPISODES:                   int = int(os.getenv("MAX_EPISODES",                   "200"))
MAX_INJECTED_EPISODES:          int = int(os.getenv("MAX_INJECTED_EPISODES",          "3"))
SEMANTIC_EXTRACTION_THRESHOLD:  int = int(os.getenv("SEMANTIC_EXTRACTION_THRESHOLD",  "5"))
SEMANTIC_LLM_MIN_PATTERNS:      int = int(os.getenv("SEMANTIC_LLM_MIN_PATTERNS",      "3"))
MAX_SEMANTIC_PATTERN_CHARS:     int = int(os.getenv("MAX_SEMANTIC_PATTERN_CHARS",     "1000"))
```
Run: `pytest tests/unit/test_episode_store.py::test_phase7_settings_exist -v`
Expect: PASSED

**Step 3 (REFACTOR)** — No cleanup needed; settings block is already well-structured.

**Step 4 (COMMIT):**
```
git commit -m "[Add] Phase 7 settings: EPISODE_STORE_PATH, MAX_EPISODES, SEMANTIC_EXTRACTION_THRESHOLD, and 4 others"
```

---

### Chunk 1.2 — Episode + EpisodeStore Dataclasses + load/save
Test layer: UNIT
Files:
  Create: `pipeline/episode_store.py`
Test file: `tests/unit/test_episode_store.py`

**Step 1 (RED)** — Add these tests:
```python
def test_episode_store_round_trip(tmp_path):
    from pipeline.episode_store import (
        Episode, EpisodeStore, save_episode_store, load_episode_store
    )
    ep = Episode(
        run_id="r1", block_index=0, block_snippet="Login crash",
        ticket_key="SCRUM-8", ticket_type="Bug", ticket_priority="High",
        ticket_summary="Login crashes on empty email", embedding=[0.1, 0.2],
        run_ts="2026-04-29T10:00:00"
    )
    store = EpisodeStore(episodes=[ep])
    path = str(tmp_path / "ep.json")
    save_episode_store(store, path)
    loaded = load_episode_store(path)
    assert len(loaded.episodes) == 1
    assert loaded.episodes[0].ticket_key == "SCRUM-8"

def test_load_episode_store_missing_file_returns_empty():
    from pipeline.episode_store import load_episode_store, EpisodeStore
    store = load_episode_store("memory/nonexistent_ep.json")
    assert isinstance(store, EpisodeStore)
    assert store.episodes == []

def test_load_episode_store_corrupt_returns_empty(tmp_path):
    from pipeline.episode_store import load_episode_store
    p = tmp_path / "ep.json"
    p.write_text("not json")
    store = load_episode_store(str(p))
    assert store.episodes == []
```
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: FAILED — ModuleNotFoundError: No module named 'pipeline.episode_store'

**Step 2 (GREEN)** — Create `pipeline/episode_store.py` with:
- `Episode` dataclass (all 9 fields)
- `EpisodeStore` dataclass (episodes: list[Episode] = field(default_factory=list))
- `load_episode_store(path)` — safe on missing/corrupt, returns empty EpisodeStore
- `save_episode_store(store, path)` — makedirs, json.dump, never raises (Rule 5)
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — Add module docstring describing the module's purpose.

**Step 4 (COMMIT):**
```
git commit -m "[Add] pipeline/episode_store.py: Episode + EpisodeStore dataclasses with safe load/save"
```

---

### Chunk 1.3 — add_episode with MAX_EPISODES pruning
Test layer: UNIT
Files:
  Modify: `pipeline/episode_store.py`
Test file: `tests/unit/test_episode_store.py`

**Step 1 (RED)** — Add:
```python
def test_add_episode_appends_to_store():
    from pipeline.episode_store import add_episode, Episode, EpisodeStore
    store = EpisodeStore()
    ep = Episode("r1",0,"snip","SCRUM-1","Bug","High","summary",[0.1],"2026-01-01")
    add_episode(store, ep, max_episodes=10)
    assert len(store.episodes) == 1
    assert store.episodes[0].ticket_key == "SCRUM-1"

def test_add_episode_prunes_oldest_when_over_max():
    from pipeline.episode_store import add_episode, Episode, EpisodeStore
    store = EpisodeStore()
    for i in range(5):
        add_episode(store, Episode(f"r{i}",0,"s",f"SCRUM-{i}","Bug","High","s",[0.1],"2026-01-01"), max_episodes=3)
    assert len(store.episodes) == 3
    assert store.episodes[0].ticket_key == "SCRUM-2"  # oldest evicted
```
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: FAILED — AttributeError: module has no attribute 'add_episode'

**Step 2 (GREEN)** — Add `add_episode(store, episode, max_episodes)`:
```python
def add_episode(store: EpisodeStore, episode: Episode, max_episodes: int) -> None:
    store.episodes.append(episode)
    if len(store.episodes) > max_episodes:
        store.episodes = store.episodes[-max_episodes:]
```
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — Add docstring to `add_episode`.

**Step 4 (COMMIT):**
```
git commit -m "[Add] episode_store.add_episode with MAX_EPISODES oldest-first pruning"
```

---

### Chunk 1.4 — retrieve_similar (cosine similarity top-K)
Test layer: UNIT
Files:
  Modify: `pipeline/episode_store.py`
Test file: `tests/unit/test_episode_store.py`

**Step 1 (RED)** — Add:
```python
def test_retrieve_similar_returns_top_k_by_cosine():
    from pipeline.episode_store import retrieve_similar, Episode, EpisodeStore
    def ep(key, emb): return Episode("r",0,"s",key,"Bug","High","s",emb,"2026-01-01")
    store = EpisodeStore(episodes=[
        ep("SCRUM-1", [1.0, 0.0]),  # identical to query
        ep("SCRUM-2", [0.0, 1.0]),  # orthogonal
        ep("SCRUM-3", [0.9, 0.1]),  # close
    ])
    results = retrieve_similar(store, query_emb=[1.0, 0.0], top_k=2)
    assert len(results) == 2
    assert results[0].ticket_key == "SCRUM-1"  # highest similarity first
    assert results[1].ticket_key == "SCRUM-3"

def test_retrieve_similar_returns_empty_when_store_empty():
    from pipeline.episode_store import retrieve_similar, EpisodeStore
    results = retrieve_similar(EpisodeStore(), [1.0, 0.0], top_k=3)
    assert results == []
```
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: FAILED — AttributeError: module has no attribute 'retrieve_similar'

**Step 2 (GREEN)** — Add `retrieve_similar`:
```python
from pipeline.duplicate_detector import cosine_similarity

def retrieve_similar(store: EpisodeStore, query_emb: list[float], top_k: int) -> list[Episode]:
    if not store.episodes:
        return []
    scored = [(cosine_similarity(query_emb, ep.embedding), ep) for ep in store.episodes]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ep for _, ep in scored[:top_k]]
```
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — Add docstring noting Rule 11 (empty list is valid, not an error).

**Step 4 (COMMIT):**
```
git commit -m "[Add] episode_store.retrieve_similar: top-K cosine similarity retrieval"
```

---

### Chunk 1.5 — format_episode_context
Test layer: UNIT
Files:
  Modify: `pipeline/episode_store.py`
Test file: `tests/unit/test_episode_store.py`

**Step 1 (RED)** — Add:
```python
def test_format_episode_context_returns_empty_for_no_episodes():
    from pipeline.episode_store import format_episode_context
    assert format_episode_context([]) == ""

def test_format_episode_context_contains_ticket_info():
    from pipeline.episode_store import format_episode_context, Episode
    ep = Episode("r",0,"Login crash","SCRUM-8","Bug","High","Login crashes",[],"2026-04-15T10:00:00")
    result = format_episode_context([ep])
    assert "SCRUM-8" in result
    assert "Bug" in result
    assert "High" in result
    assert "## Similar past decisions" in result
```
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: FAILED — AttributeError: module has no attribute 'format_episode_context'

**Step 2 (GREEN)** — Add:
```python
def format_episode_context(episodes: list[Episode]) -> str:
    if not episodes:
        return ""
    lines = ["## Similar past decisions"]
    for ep in episodes:
        date = ep.run_ts[:10]
        lines.append(f"- [{ep.ticket_key}] \"{ep.block_snippet[:80]}\" → {ep.ticket_type}, {ep.ticket_priority} ({date})")
    return "\n".join(lines)
```
Run: `pytest tests/unit/test_episode_store.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — No changes needed.

**Step 4 (COMMIT):**
```
git commit -m "[Add] episode_store.format_episode_context: format episodes for LLM user message"
```

---

## Block 2 — Semantic Store

### Chunk 2.1 — Pattern + SemanticStore + load/save
Test layer: UNIT
Files:
  Create: `pipeline/semantic_store.py`
Test file: `tests/unit/test_semantic_store.py`

**Step 1 (RED)** — Create test file with:
```python
def test_semantic_store_round_trip(tmp_path):
    from pipeline.semantic_store import Pattern, SemanticStore, save_semantic_store, load_semantic_store
    p = Pattern("Bug:High", 8, ["ex1"], "Login issues → Bug High", "2026-04-29", "count_based")
    store = SemanticStore(patterns=[p], last_extracted_episode_count=10)
    path = str(tmp_path / "sem.json")
    save_semantic_store(store, path)
    loaded = load_semantic_store(path)
    assert len(loaded.patterns) == 1
    assert loaded.patterns[0].type_priority_key == "Bug:High"
    assert loaded.last_extracted_episode_count == 10

def test_load_semantic_store_missing_file_returns_empty():
    from pipeline.semantic_store import load_semantic_store, SemanticStore
    store = load_semantic_store("memory/nonexistent_sem.json")
    assert isinstance(store, SemanticStore)
    assert store.patterns == []
    assert store.last_extracted_episode_count == 0
```
Run: `pytest tests/unit/test_semantic_store.py -v`
Expect: FAILED — ModuleNotFoundError: No module named 'pipeline.semantic_store'

**Step 2 (GREEN)** — Create `pipeline/semantic_store.py` with `Pattern`, `SemanticStore`, `load_semantic_store`, `save_semantic_store`.

**Step 3 (REFACTOR)** — Add module docstring.

**Step 4 (COMMIT):**
```
git commit -m "[Add] pipeline/semantic_store.py: Pattern + SemanticStore dataclasses with safe load/save"
```

---

### Chunk 2.2 — extract_count_patterns (pure function)
Test layer: UNIT
Files:
  Modify: `pipeline/semantic_store.py`
Test file: `tests/unit/test_semantic_store.py`

**Step 1 (RED)** — Add:
```python
def test_extract_count_patterns_emits_when_count_meets_threshold():
    from pipeline.semantic_store import extract_count_patterns
    from pipeline.episode_store import Episode
    def ep(t, p): return Episode("r",0,"s",f"K","Bug" if t=="Bug" else t, p,"s",[0.1],"2026-01-01")
    # 6 Bug:High, 3 Story:Medium — threshold=5 → only Bug:High emitted
    episodes = [Episode("r",0,"s","K","Bug","High","s",[0.1],"2026-01-01")] * 6 + \
               [Episode("r",0,"s","K","Story","Medium","s",[0.1],"2026-01-01")] * 3
    patterns = extract_count_patterns(episodes, min_count=5)
    assert len(patterns) == 1
    assert patterns[0].type_priority_key == "Bug:High"
    assert patterns[0].count == 6

def test_extract_count_patterns_returns_empty_below_threshold():
    from pipeline.semantic_store import extract_count_patterns
    from pipeline.episode_store import Episode
    episodes = [Episode("r",0,"s","K","Bug","High","s",[0.1],"2026-01-01")] * 3
    patterns = extract_count_patterns(episodes, min_count=5)
    assert patterns == []
```
Run: `pytest tests/unit/test_semantic_store.py -v`
Expect: FAILED — AttributeError: module has no attribute 'extract_count_patterns'

**Step 2 (GREEN)** — Add `extract_count_patterns`:
```python
def extract_count_patterns(episodes: list, min_count: int) -> list[Pattern]:
    from collections import defaultdict
    counts: dict[str, list] = defaultdict(list)
    for ep in episodes:
        key = f"{ep.ticket_type}:{ep.ticket_priority}"
        counts[key].append(ep.ticket_summary)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [
        Pattern(
            type_priority_key=key,
            count=len(summaries),
            example_summaries=summaries[:5],
            summary_text=f"{key} ({len(summaries)} decisions)",
            created_at=now,
            source="count_based",
        )
        for key, summaries in counts.items()
        if len(summaries) >= min_count
    ]
```
Run: `pytest tests/unit/test_semantic_store.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — Add docstring noting this is a pure function (no I/O, no API).

**Step 4 (COMMIT):**
```
git commit -m "[Add] semantic_store.extract_count_patterns: emit Pattern when type:priority >= min_count"
```

---

### Chunk 2.3 — build_semantic_injection
Test layer: UNIT
Files:
  Modify: `pipeline/semantic_store.py`
Test file: `tests/unit/test_semantic_store.py`

**Step 1 (RED)** — Add:
```python
def test_build_semantic_injection_returns_empty_when_no_patterns():
    from pipeline.semantic_store import build_semantic_injection, SemanticStore
    assert build_semantic_injection(SemanticStore(), max_chars=1000) == ""

def test_build_semantic_injection_contains_pattern_text():
    from pipeline.semantic_store import build_semantic_injection, SemanticStore, Pattern
    p = Pattern("Bug:High", 8, [], "Login issues → Bug High", "2026-04-29", "count_based")
    store = SemanticStore(patterns=[p])
    result = build_semantic_injection(store, max_chars=1000)
    assert "Bug:High" in result
    assert "Login issues" in result

def test_build_semantic_injection_respects_max_chars():
    from pipeline.semantic_store import build_semantic_injection, SemanticStore, Pattern
    patterns = [Pattern(f"Bug:High{i}", 5, [], "x" * 200, "2026-04-29", "count_based") for i in range(20)]
    store = SemanticStore(patterns=patterns)
    result = build_semantic_injection(store, max_chars=100)
    assert len(result) <= 100
```
Run: `pytest tests/unit/test_semantic_store.py -v`
Expect: FAILED — AttributeError: module has no attribute 'build_semantic_injection'

**Step 2 (GREEN)** — Add `build_semantic_injection`:
```python
def build_semantic_injection(store: SemanticStore, max_chars: int) -> str:
    if not store.patterns:
        return ""
    lines = ["## Learned Patterns"]
    for p in store.patterns:
        lines.append(f"- {p.summary_text}")
    text = "\n".join(lines)
    return text[:max_chars]
```
Run: `pytest tests/unit/test_semantic_store.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — No changes needed.

**Step 4 (COMMIT):**
```
git commit -m "[Add] semantic_store.build_semantic_injection: format patterns for SYSTEM_PROMPT injection"
```

---

### Chunk 2.4 — summarise_with_llm (async, Rule 10)
Test layer: UNIT
Files:
  Modify: `pipeline/semantic_store.py`
Test file: `tests/unit/test_semantic_store.py`

**Step 1 (RED)** — Add:
```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
async def test_summarise_with_llm_updates_summary_text():
    from pipeline.semantic_store import summarise_with_llm, Pattern
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Login and auth issues → Bug, High priority"
    patterns = [Pattern("Bug:High", 6, ["login crash", "auth fail"], "Bug:High (6)", "2026-04-29", "count_based")]
    with patch("pipeline.semantic_store._client") as mock_client:
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)
        result = await summarise_with_llm(patterns)
    assert result[0].source == "llm_summarised"
    assert "Login" in result[0].summary_text

@pytest.mark.asyncio
async def test_summarise_with_llm_returns_unchanged_on_exception():
    from pipeline.semantic_store import summarise_with_llm, Pattern
    patterns = [Pattern("Bug:High", 6, [], "Bug:High (6)", "2026-04-29", "count_based")]
    with patch("pipeline.semantic_store._client") as mock_client:
        mock_client.chat.completions.create = MagicMock(side_effect=Exception("API down"))
        result = await summarise_with_llm(patterns)
    assert result[0].source == "count_based"  # Rule 10 — unchanged
```
Run: `pytest tests/unit/test_semantic_store.py -v`
Expect: FAILED — AttributeError: module has no attribute 'summarise_with_llm'

**Step 2 (GREEN)** — Add to `semantic_store.py`:
```python
import asyncio
from openai import OpenAI
_client = OpenAI(api_key=settings.OPENAI_API_KEY)

async def summarise_with_llm(patterns: list[Pattern]) -> list[Pattern]:
    result = list(patterns)
    for i, p in enumerate(result):
        examples = "\n".join(f"- {s}" for s in p.example_summaries[:5])
        prompt = (
            f"Summarise this triage pattern in one plain-English sentence "
            f"(type: {p.type_priority_key}, {p.count} decisions):\n{examples}"
        )
        try:
            response = await asyncio.to_thread(
                _client.chat.completions.create,
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            result[i] = Pattern(
                type_priority_key=p.type_priority_key,
                count=p.count,
                example_summaries=p.example_summaries,
                summary_text=response.choices[0].message.content.strip(),
                created_at=p.created_at,
                source="llm_summarised",
            )
        except Exception as e:
            print(f"[semantic_store] summarise_with_llm failed (Rule 10): {e}")
            # Rule 10 — return pattern unchanged
    return result
```
Run: `pytest tests/unit/test_semantic_store.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — Add docstring noting Rule 10 fallback.

**Step 4 (COMMIT):**
```
git commit -m "[Add] semantic_store.summarise_with_llm: LLM pattern enrichment with Rule 10 fallback"
```

---

## Block 3 — Memory Runner

### Chunk 3.1 — MemoryContext + pre_run()
Test layer: UNIT
Files:
  Create: `pipeline/memory_runner.py`
Test file: `tests/unit/test_memory_runner.py`

**Step 1 (RED)** — Create test file with:
```python
import pytest
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_pre_run_returns_memory_context():
    from pipeline.memory_runner import pre_run, MemoryContext
    from pipeline.episode_store import EpisodeStore
    with patch("pipeline.memory_runner.load_episode_store", return_value=EpisodeStore()), \
         patch("pipeline.memory_runner.load_semantic_store") as mock_ss_load, \
         patch("pipeline.memory_runner.build_semantic_injection", return_value="## Patterns\n- Bug:High"):
        from pipeline.semantic_store import SemanticStore
        mock_ss_load.return_value = SemanticStore()
        ctx = await pre_run()
    assert isinstance(ctx, MemoryContext)
    assert ctx.semantic_injection == "## Patterns\n- Bug:High"
    assert isinstance(ctx.episode_store, EpisodeStore)

@pytest.mark.asyncio
async def test_pre_run_returns_empty_context_on_load_error():
    from pipeline.memory_runner import pre_run, MemoryContext
    with patch("pipeline.memory_runner.load_episode_store", side_effect=Exception("disk error")):
        ctx = await pre_run()
    assert isinstance(ctx, MemoryContext)
    assert ctx.semantic_injection == ""
```
Run: `pytest tests/unit/test_memory_runner.py -v`
Expect: FAILED — ModuleNotFoundError: No module named 'pipeline.memory_runner'

**Step 2 (GREEN)** — Create `pipeline/memory_runner.py` with `MemoryContext` dataclass and `pre_run()`.

**Step 3 (REFACTOR)** — Add module docstring describing the two-call lifecycle.

**Step 4 (COMMIT):**
```
git commit -m "[Add] pipeline/memory_runner.py: MemoryContext dataclass and pre_run() lifecycle step"
```

---

### Chunk 3.2 — post_run() episode write
Test layer: UNIT
Files:
  Modify: `pipeline/memory_runner.py`
Test file: `tests/unit/test_memory_runner.py`

**Step 1 (RED)** — Add:
```python
@pytest.mark.asyncio
async def test_post_run_writes_episode_for_ticket_created_block():
    from pipeline.memory_runner import post_run
    from pipeline.run_logger import RunLog, BlockResult, LlmStats
    run_log = RunLog(
        run_id="r1", started_at="2026-01-01", completed_at=None, status="success",
        messages_fetched=1, blocks_processed=1, tickets_created_count=1,
        clarifications_asked_count=0, blocks_skipped_count=0, error_count=0,
        duplicates_flagged_count=0,
    )
    run_log.blocks = [BlockResult(
        block_index=0, block_snippet="Login crash", action="ticket_created",
        ticket_key="SCRUM-8", ticket_summary="Login crashes", ticket_type="Bug",
        ticket_priority="High", llm=LlmStats(1,[],  "stop", 10, 5),
    )]
    with patch("pipeline.memory_runner.embed_texts", return_value=[[0.1, 0.2]]) as mock_embed, \
         patch("pipeline.memory_runner.add_episode") as mock_add, \
         patch("pipeline.memory_runner.load_episode_store") as mock_load, \
         patch("pipeline.memory_runner.save_episode_store") as mock_save, \
         patch("pipeline.memory_runner.load_semantic_store"), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]):
        from pipeline.episode_store import EpisodeStore
        mock_load.return_value = EpisodeStore()
        await post_run(run_log)
    mock_embed.assert_called_once()
    mock_add.assert_called_once()
    mock_save.assert_called_once()

@pytest.mark.asyncio
async def test_post_run_skips_non_ticket_blocks():
    from pipeline.memory_runner import post_run
    from pipeline.run_logger import RunLog, BlockResult
    run_log = RunLog("r1","2026-01-01",None,"success",1,1,0,1,0,0,0)
    run_log.blocks = [BlockResult(0,"snip","clarification_asked")]
    with patch("pipeline.memory_runner.embed_texts") as mock_embed, \
         patch("pipeline.memory_runner.load_episode_store") as mock_load, \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store"), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]):
        from pipeline.episode_store import EpisodeStore
        mock_load.return_value = EpisodeStore()
        await post_run(run_log)
    mock_embed.assert_not_called()
```
Run: `pytest tests/unit/test_memory_runner.py -v`
Expect: FAILED — AttributeError: module has no attribute 'post_run'

**Step 2 (GREEN)** — Implement `post_run(run_log)` in `memory_runner.py` with episode write loop.

**Step 3 (REFACTOR)** — Inline comments for Rule 5 handlers.

**Step 4 (COMMIT):**
```
git commit -m "[Add] memory_runner.post_run: write new episodes with Rule 5 embed failure handling"
```

---

### Chunk 3.3 — post_run() semantic extraction trigger
Test layer: UNIT
Files:
  Modify: `pipeline/memory_runner.py`
Test file: `tests/unit/test_memory_runner.py`

**Step 1 (RED)** — Add:
```python
@pytest.mark.asyncio
async def test_post_run_triggers_extraction_when_threshold_met():
    from pipeline.memory_runner import post_run
    from pipeline.run_logger import RunLog, BlockResult, LlmStats
    from pipeline.episode_store import EpisodeStore, Episode
    run_log = RunLog("r1","2026-01-01",None,"success",1,1,1,0,0,0,0)
    run_log.blocks = [BlockResult(0,"s","ticket_created","SCRUM-1","Bug","High","summary",LlmStats(1,[],"stop",5,3))]

    existing_episodes = [Episode("r",0,"s","K","Bug","High","s",[0.1],"2026-01-01")] * 4

    with patch("pipeline.memory_runner.embed_texts", return_value=[[0.1]]), \
         patch("pipeline.memory_runner.load_episode_store") as mock_load_ep, \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store") as mock_load_sem, \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]) as mock_extract, \
         patch("pipeline.memory_runner.add_episode"):
        from pipeline.semantic_store import SemanticStore
        mock_load_ep.return_value = EpisodeStore(episodes=existing_episodes)
        mock_load_sem.return_value = SemanticStore(last_extracted_episode_count=0)
        await post_run(run_log)
    # 4 existing + 1 new = 5 total delta from 0 → threshold=5 → should trigger
    mock_extract.assert_called_once()

@pytest.mark.asyncio
async def test_post_run_calls_summarise_when_enough_patterns():
    from pipeline.memory_runner import post_run
    from pipeline.run_logger import RunLog, BlockResult, LlmStats
    from pipeline.episode_store import EpisodeStore
    from pipeline.semantic_store import SemanticStore, Pattern
    run_log = RunLog("r1","2026-01-01",None,"success",1,1,1,0,0,0,0)
    run_log.blocks = [BlockResult(0,"s","ticket_created","K","Bug","High","s",LlmStats(1,[],"stop",5,3))]
    patterns = [Pattern(f"Bug:High{i}",5,[],"t","2026-01-01","count_based") for i in range(3)]
    with patch("pipeline.memory_runner.embed_texts", return_value=[[0.1]]), \
         patch("pipeline.memory_runner.load_episode_store") as mock_load_ep, \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store") as mock_load_sem, \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=patterns), \
         patch("pipeline.memory_runner.summarise_with_llm", return_value=patterns) as mock_summ, \
         patch("pipeline.memory_runner.add_episode"):
        mock_load_ep.return_value = EpisodeStore()
        mock_load_sem.return_value = SemanticStore(last_extracted_episode_count=0)
        await post_run(run_log)
    mock_summ.assert_called_once()
```
Run: `pytest tests/unit/test_memory_runner.py -v`
Expect: FAILED — extraction logic not yet implemented

**Step 2 (GREEN)** — Complete `post_run()` with semantic extraction threshold check and `summarise_with_llm` call.

**Step 3 (REFACTOR)** — Add inline comments for threshold logic.

**Step 4 (COMMIT):**
```
git commit -m "[Add] memory_runner.post_run: semantic extraction trigger with LLM summarisation (Rule 10)"
```

---

## Block 4 — Triage Agent Integration

### Chunk 4.1 — _run_llm_loop gains episode_context param
Test layer: UNIT
Files:
  Modify: `agents/triage/triage_agent.py`
Test file: `tests/unit/test_triage_agent.py`

**Step 1 (RED)** — Add to existing test file:
```python
@pytest.mark.asyncio
async def test_run_llm_loop_appends_episode_context_to_user_message(patch_run_deps):
    # verify user message content includes episode_context when provided
    captured_messages = []
    def capture_create(**kwargs):
        captured_messages.extend(kwargs["messages"])
        return _make_stop_response()
    with patch("agents.triage.triage_agent.asyncio.to_thread",
               side_effect=lambda fn, **kw: asyncio.coroutine(lambda: capture_create(**kw))()):
        ...  # simplified — assert episode_context appears in user message
    # Use the existing test infrastructure; add episode_context="## Similar\n- [X]"
    # and assert it appears in the captured user content
```

*Note: The exact test shape follows the existing `_run_llm_loop` test pattern in `test_triage_agent.py` — pass `episode_context="## Similar past decisions\n- [SCRUM-8]..."` and verify it appears in the user content of the first message.*

Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: FAILED — `_run_llm_loop() got unexpected keyword argument 'episode_context'`

**Step 2 (GREEN)** — Change `_run_llm_loop` signature:
```python
async def _run_llm_loop(
    block_text: str,
    block_index: int,
    block_snippet: str,
    episode_context: str = "",
) -> BlockResult:
```
And modify user message construction:
```python
user_content = f"Slack message(s):\n\n{block_text}"
if episode_context:
    user_content += f"\n\n{episode_context}"
messages = [
    {"role": "system", "content": effective_system_prompt},  # set from outer scope
    {"role": "user",   "content": user_content},
]
```
*`effective_system_prompt` is a parameter passed in from `run()` — handled in next chunk.*

Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: PASSED — all existing tests still pass (episode_context="" default is backward-compatible)

**Step 3 (REFACTOR)** — Add docstring note for `episode_context` parameter.

**Step 4 (COMMIT):**
```
git commit -m "[Add] triage_agent._run_llm_loop: episode_context param for episodic memory injection"
```

---

### Chunk 4.2 — run() gains memory_context + effective_prompt
Test layer: UNIT
Files:
  Modify: `agents/triage/triage_agent.py`
Test file: `tests/unit/test_triage_agent.py`

**Step 1 (RED)** — Add:
```python
@pytest.mark.asyncio
async def test_run_with_memory_context_injects_semantic_into_prompt(patch_run_deps):
    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import EpisodeStore
    ctx = MemoryContext(
        semantic_injection="## Learned Patterns\n- Bug:High (8 decisions)",
        episode_store=EpisodeStore()
    )
    captured = []
    original_loop = triage_agent_module._run_llm_loop
    async def capturing_loop(block_text, block_index, block_snippet, episode_context="", effective_system_prompt=SYSTEM_PROMPT):
        captured.append(effective_system_prompt)
        return BlockResult(block_index=block_index, block_snippet=block_snippet, action="no_action")
    with patch.object(triage_agent_module, "_run_llm_loop", side_effect=capturing_loop):
        await triage_agent_module.run(memory_context=ctx)
    assert any("Learned Patterns" in p for p in captured)

@pytest.mark.asyncio
async def test_run_with_no_memory_context_uses_original_prompt(patch_run_deps):
    # existing run() call with no memory_context → no change to SYSTEM_PROMPT
    run_log = await triage_agent_module.run(memory_context=None)
    assert isinstance(run_log, RunLog)
```
Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: FAILED — `run() got unexpected keyword argument 'memory_context'`

**Step 2 (GREEN)** — Add `memory_context: MemoryContext | None = None` to `run()` signature. Build `effective_system_prompt` before block loop:
```python
from pipeline.memory_runner import MemoryContext

async def run(memory_context: MemoryContext | None = None) -> RunLog:
    ...
    effective_system_prompt = SYSTEM_PROMPT
    if memory_context and memory_context.semantic_injection:
        effective_system_prompt = SYSTEM_PROMPT + "\n\n" + memory_context.semantic_injection
```
Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: PASSED

**Step 3 (REFACTOR)** — Add comment above `effective_system_prompt` assignment.

**Step 4 (COMMIT):**
```
git commit -m "[Add] triage_agent.run: memory_context param and effective_system_prompt build for semantic injection"
```

---

### Chunk 4.3 — run() per-block episode retrieval
Test layer: UNIT
Files:
  Modify: `agents/triage/triage_agent.py`
Test file: `tests/unit/test_triage_agent.py`

**Step 1 (RED)** — Add:
```python
@pytest.mark.asyncio
async def test_run_calls_retrieve_similar_when_memory_context_given(patch_run_deps):
    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import EpisodeStore
    ctx = MemoryContext(semantic_injection="", episode_store=EpisodeStore())
    with patch("agents.triage.triage_agent.retrieve_similar", return_value=[]) as mock_ret, \
         patch("agents.triage.triage_agent.format_episode_context", return_value=""):
        await triage_agent_module.run(memory_context=ctx)
    mock_ret.assert_called()

@pytest.mark.asyncio
async def test_run_skips_retrieve_when_no_memory_context(patch_run_deps):
    with patch("agents.triage.triage_agent.retrieve_similar") as mock_ret:
        await triage_agent_module.run(memory_context=None)
    mock_ret.assert_not_called()
```
Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: FAILED — retrieve_similar not called

**Step 2 (GREEN)** — In `run()`, inside the block loop, after `embed_texts` and `find_duplicate`, add:
```python
if memory_context:
    similar = retrieve_similar(
        memory_context.episode_store, block_emb, settings.MAX_INJECTED_EPISODES
    )
    episode_context = format_episode_context(similar)
else:
    episode_context = ""
```
And pass `episode_context` + `effective_system_prompt` to `_run_llm_loop`.

Also extend the existing `except Exception as emb_err` block to set `episode_context = ""` (Rule 11).

Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: PASSED — all existing + new tests pass

**Step 3 (REFACTOR)** — Add inline comment `# Rule 11 — no episodes = no injection, continue` on the fallback.

**Step 4 (COMMIT):**
```
git commit -m "[Add] triage_agent.run: per-block episode retrieval and injection via MemoryContext"
```

---

## Block 5 — Entry Point Integration

### Chunk 5.1 — run_triage.main() memory hooks
Test layer: UNIT
Files:
  Modify: `run_triage.py`
Test file: `tests/unit/test_eval_runner.py`

**Step 1 (RED)** — Add to existing test file:
```python
@pytest.mark.asyncio
async def test_run_triage_main_calls_memory_runner_wrapping_eval():
    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import EpisodeStore
    mock_ctx = MemoryContext(semantic_injection="", episode_store=EpisodeStore())
    call_order = []
    async def mock_pre():
        call_order.append("memory_pre")
        return mock_ctx
    async def mock_eval_pre(run_log):
        call_order.append("eval_pre")
    async def mock_triage(memory_context=None):
        call_order.append("triage")
        from pipeline.run_logger import RunLog
        return RunLog("r","r",None,"success",0,0,0,0,0,0,0)
    async def mock_eval_post(run_log):
        call_order.append("eval_post")
    async def mock_post(run_log):
        call_order.append("memory_post")
    with patch("run_triage.memory_runner.pre_run", side_effect=mock_pre), \
         patch("run_triage.run_eval_step", side_effect=mock_eval_pre), \
         patch("run_triage.triage_run", side_effect=mock_triage), \
         patch("run_triage.memory_runner.post_run", side_effect=mock_post):
        import run_triage
        await run_triage.main()
    assert call_order == ["memory_pre", "eval_pre", "triage", "eval_post", "memory_post"]
```
Run: `pytest tests/unit/test_eval_runner.py::test_run_triage_main_calls_memory_runner_wrapping_eval -v`
Expect: FAILED — memory_runner not imported in run_triage

**Step 2 (GREEN)** — Modify `run_triage.py`:
```python
from pipeline import memory_runner

async def main() -> None:
    memory_context = await memory_runner.pre_run()
    await run_eval_step(run_log=None)
    run_log = await triage_run(memory_context=memory_context)
    await run_eval_step(run_log=run_log)
    await memory_runner.post_run(run_log)
```
Run: `pytest tests/unit/test_eval_runner.py -v`
Expect: PASSED — including pre-existing run_triage test

**Step 3 (REFACTOR)** — Update docstring in `run_triage.py` to document the 5-step sequence.

**Step 4 (COMMIT):**
```
git commit -m "[Add] run_triage.main: memory pre_run/post_run hooks wrapping eval and triage steps"
```

---

## Success Criteria

Map to brainstorm success metrics:

- [ ] **Episodic log completeness** — verified by `test_post_run_writes_episode_for_ticket_created_block`; skips non-ticket blocks (`test_post_run_skips_non_ticket_blocks`)
- [ ] **Pattern emergence after 5 decisions** — verified by `test_extract_count_patterns_emits_when_count_meets_threshold`
- [ ] **LLM summarisation updates patterns** — verified by `test_summarise_with_llm_updates_summary_text`; Rule 10 fallback by `test_summarise_with_llm_returns_unchanged_on_exception`
- [ ] **Semantic injection appears in prompt** — verified by `test_run_with_memory_context_injects_semantic_into_prompt`
- [ ] **Episode context appears in user message** — verified by `test_run_llm_loop_appends_episode_context_to_user_message`
- [ ] **No regressions** — all 175 existing tests must pass after every chunk
- [ ] **Lifecycle order correct** — verified by `test_run_triage_main_calls_memory_runner_wrapping_eval`
- [ ] **Rule 11 (embed fails)** — verified by existing `test_run_proceeds_with_empty_episode_context_on_embed_failure`
- [ ] **Rule 10 (LLM summarisation fails)** — verified by `test_summarise_with_llm_returns_unchanged_on_exception`
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] E2E: manual spot check — run 6 times with same class of message; verify patterns in `memory/semantic_store.json` after 5 runs; verify episode injection in LLM context (stdout)

---

## Known Technical Debt

| Item | Acceptable because |
|---|---|
| `effective_system_prompt` passed via closure to `_run_llm_loop` rather than as an explicit parameter | Avoids a 4th parameter on an already-complex function; `_run_llm_loop` is a private function in the same file |
| `summarise_with_llm` loops over patterns sequentially | Acceptable at low pattern counts (≤20); parallelise with `asyncio.gather` if pattern volume grows |
| No dashboard panel for memory stats (episode count, pattern count) | Out of scope per brainstorm; add at Phase 7 closeout if time permits |
| Pattern re-extraction runs on every `post_run` that meets the threshold | Could cache last-extracted count more precisely; current implementation is safe and simple |
