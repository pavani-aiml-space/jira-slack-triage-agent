# Technical Design: Phase 7 — Agent Memory (Episodic + Semantic + Working)

**Date:** 2026-04-29
**Input:** `docs/plans/2026-04-29-phase7-memory-brainstorm.md` (approved)

---

## Problem (from brainstorm)

Every run starts from zero — the agent re-derives triage decisions from scratch without any recollection of past patterns, making accuracy non-deterministic and improvement manual.

---

## Approach Chosen

**Option A — MemoryContext object passed explicitly to `triage_agent.run()`.**

`memory_runner.pre_run()` loads stores and returns a `MemoryContext` dataclass.
`triage_agent.run(memory_context=None)` uses it to build the effective system prompt and retrieve per-block episode context.
`memory_runner.post_run(run_log)` writes new episodes and extracts patterns after the run.

Satisfies:
- **Testability rule (brainstorm)** — all injection is visible in function signatures; `None` = no memory
- **Rule 5** — every store I/O failure is caught, logged, and silently skipped
- **Rule 10** — LLM summarisation failure falls back to count-based patterns
- **Rule 11** — empty episode retrieval continues without injection
- Mirrors `eval_runner.py` pre/post lifecycle exactly — no new patterns introduced

---

## Components

### Code Diagram
See: [docs/diagrams/2026-04-29-phase7-memory.md](../diagrams/2026-04-29-phase7-memory.md)

### New Files

| File | Purpose |
|---|---|
| `pipeline/episode_store.py` | Episodic memory: `Episode` + `EpisodeStore` dataclasses; load/save/add/retrieve/format |
| `pipeline/semantic_store.py` | Semantic memory: `Pattern` + `SemanticStore` dataclasses; count extraction, LLM summarisation, injection formatting |
| `pipeline/memory_runner.py` | Lifecycle orchestrator: `MemoryContext` dataclass; `pre_run()` + `post_run()` |

### Modified Files

| File | What Changes |
|---|---|
| `agents/triage/triage_agent.py` | `run()` gains `memory_context: MemoryContext \| None = None`; `_run_llm_loop()` gains `episode_context: str = ""`; effective prompt built at run time; block loop reuses `block_emb` for episode retrieval |
| `run_triage.py` | `main()` adds `memory_runner.pre_run()` before eval pre-step and `memory_runner.post_run(run_log)` after eval post-step |
| `config/settings.py` | 7 new Phase 7 settings |

---

## Data Contracts

### `pipeline/episode_store.py`

```python
@dataclass
class Episode:
    run_id: str
    block_index: int
    block_snippet: str        # raw Slack text (up to 200 chars) — shown in injection display
    ticket_key: str
    ticket_type: str          # Bug | Story | Task
    ticket_priority: str      # Critical | High | Medium | Low
    ticket_summary: str       # LLM-generated summary — embedded for retrieval
    embedding: list[float]    # text-embedding-3-small vector of ticket_summary
    run_ts: str               # ISO 8601 timestamp

@dataclass
class EpisodeStore:
    episodes: list[Episode] = field(default_factory=list)

def load_episode_store(path: str) -> EpisodeStore
    # Safe on missing/corrupt file; returns empty EpisodeStore; never raises

def save_episode_store(store: EpisodeStore, path: str) -> None
    # Logs warning on failure; never raises (Rule 5)

def add_episode(store: EpisodeStore, episode: Episode, max_episodes: int) -> None
    # Appends episode; prunes oldest entries if len > max_episodes

def retrieve_similar(
    store: EpisodeStore,
    query_emb: list[float],
    top_k: int,
) -> list[Episode]
    # Cosine similarity of query_emb vs each episode.embedding; returns top_k
    # Returns [] if store is empty (Rule 11)

def format_episode_context(episodes: list[Episode]) -> str
    # Formats episodes as injection text for the LLM user message
    # e.g. "## Similar past decisions\n- [SCRUM-8] 'Login crash' → Bug, High\n..."
    # Returns "" if episodes is empty
```

### `pipeline/semantic_store.py`

```python
@dataclass
class Pattern:
    type_priority_key: str      # e.g. "Bug:High" — grouping key
    count: int                  # how many episodes match this key
    example_summaries: list[str]  # up to 5 representative summaries
    summary_text: str           # human-readable pattern (count-based or LLM-generated)
    created_at: str             # ISO 8601 timestamp
    source: str                 # "count_based" | "llm_summarised"

@dataclass
class SemanticStore:
    patterns: list[Pattern] = field(default_factory=list)
    last_extracted_episode_count: int = 0  # re-extract only when new episodes added

def load_semantic_store(path: str) -> SemanticStore
    # Safe on missing/corrupt file; returns empty SemanticStore; never raises

def save_semantic_store(store: SemanticStore, path: str) -> None
    # Logs warning on failure; never raises (Rule 5)

def extract_count_patterns(
    episodes: list[Episode],
    min_count: int,             # settings.SEMANTIC_EXTRACTION_THRESHOLD (default 5)
) -> list[Pattern]
    # Groups episodes by (ticket_type, ticket_priority); emits Pattern when count >= min_count
    # Pure function — no I/O, no external calls

async def summarise_with_llm(
    patterns: list[Pattern],
    openai_client,
) -> list[Pattern]
    # Calls GPT-4o with episode summaries per pattern; writes richer summary_text
    # On any exception: returns patterns unchanged with source="count_based" (Rule 10)
    # Uses asyncio.to_thread for sync OpenAI SDK call (consistent with triage_agent.py)

def build_semantic_injection(store: SemanticStore, max_chars: int) -> str
    # Formats active patterns as SYSTEM_PROMPT appendix
    # Truncates to max_chars; returns "" if no patterns
```

### `pipeline/memory_runner.py`

```python
@dataclass
class MemoryContext:
    semantic_injection: str       # pre-formatted string for SYSTEM_PROMPT; "" if none
    episode_store: EpisodeStore   # pre-loaded for per-block retrieval

async def pre_run() -> MemoryContext
    # 1. load_episode_store(settings.EPISODE_STORE_PATH)
    # 2. load_semantic_store(settings.SEMANTIC_STORE_PATH)
    # 3. build_semantic_injection(semantic_store, settings.MAX_SEMANTIC_PATTERN_CHARS)
    # 4. return MemoryContext(semantic_injection, episode_store)
    # Never raises (Rule 5)

async def post_run(run_log: RunLog) -> None
    # 1. load_episode_store(settings.EPISODE_STORE_PATH)
    # 2. For each ticket_created BlockResult in run_log.blocks:
    #      [emb] = await embed_texts([block.ticket_summary])  # one embed call per new ticket
    #      add_episode(store, Episode(...), settings.MAX_EPISODES)
    #      on embed failure: skip episode, log warning (Rule 5)
    # 3. load_semantic_store(settings.SEMANTIC_STORE_PATH)
    # 4. If new episodes added AND episode_count delta >= SEMANTIC_EXTRACTION_THRESHOLD:
    #      new_patterns = extract_count_patterns(store.episodes, min_count=SEMANTIC_EXTRACTION_THRESHOLD)
    #      if len(new_patterns) >= SEMANTIC_LLM_MIN_PATTERNS:
    #        new_patterns = await summarise_with_llm(new_patterns, _client)  # Rule 10 fallback
    #      semantic_store.patterns = new_patterns
    #      semantic_store.last_extracted_episode_count = len(store.episodes)
    # 5. save_episode_store() + save_semantic_store()
    # Never raises (Rule 5)
```

### `agents/triage/triage_agent.py` — modified signatures

```python
async def run(memory_context: MemoryContext | None = None) -> RunLog:
    # effective_system_prompt = SYSTEM_PROMPT
    # if memory_context and memory_context.semantic_injection:
    #     effective_system_prompt += "\n\n## Learned Patterns\n" + memory_context.semantic_injection
    #
    # Per-block (inside existing embed try/except block):
    #   [block_emb] = await embed_texts([snippet])           ← 1 call (shared with dup gate)
    #   match = find_duplicate(block_emb, cache, threshold)
    #   if memory_context:                                    ← Rule 11: no match = no injection
    #     episodes = retrieve_similar(memory_context.episode_store, block_emb, MAX_INJECTED_EPISODES)
    #     episode_context = format_episode_context(episodes)
    #   else:
    #     episode_context = ""
    #   result = await _run_llm_loop(block_text, block_index, block_snippet, episode_context)

async def _run_llm_loop(
    block_text: str,
    block_index: int,
    block_snippet: str,
    episode_context: str = "",    # NEW — default "" = no episodic injection
) -> BlockResult:
    # user_content = f"Slack message(s):\n\n{block_text}"
    # if episode_context:
    #     user_content += f"\n\n{episode_context}"
    # messages = [
    #     {"role": "system", "content": effective_system_prompt},  ← from outer scope
    #     {"role": "user",   "content": user_content},
    # ]
```

### `run_triage.py` — modified `main()`

```python
async def main() -> None:
    memory_context = await memory_runner.pre_run()          # NEW Step 1
    await run_eval_step(run_log=None)                       # Phase 5 (unchanged)
    run_log = await triage_run(memory_context=memory_context)  # modified call
    await run_eval_step(run_log=run_log)                    # Phase 5 (unchanged)
    await memory_runner.post_run(run_log)                   # NEW Step 5
```

---

## New Settings (`config/settings.py`)

```python
# ── Phase 7 — Memory ─────────────────────────────────────────────────────────
EPISODE_STORE_PATH:             str   = "memory/episode_store.json"
SEMANTIC_STORE_PATH:            str   = "memory/semantic_store.json"
MAX_EPISODES:                   int   = 200     # rolling window; oldest evicted first
MAX_INJECTED_EPISODES:          int   = 3       # per-block; caps context growth
SEMANTIC_EXTRACTION_THRESHOLD:  int   = 5       # min episodes per type:priority before pattern emitted
SEMANTIC_LLM_MIN_PATTERNS:      int   = 3       # min count-based patterns before LLM summarisation runs
MAX_SEMANTIC_PATTERN_CHARS:     int   = 1000    # caps semantic injection in SYSTEM_PROMPT
```

---

## External Calls

| Service | Called From | What Is Sent | What Is Returned | Auth | Failure Mode |
|---|---|---|---|---|---|
| OpenAI Embeddings (`text-embedding-3-small`) | `memory_runner.post_run()` | `ticket_summary` per new ticket | `list[float]` (1536 dims) | `OPENAI_API_KEY` | Skip episode write, log warning (Rule 5) |
| OpenAI Chat (`gpt-4o`) | `semantic_store.summarise_with_llm()` | Pattern summaries + example episode summaries | Human-readable pattern text | `OPENAI_API_KEY` | Return patterns unchanged (count-based stays), log warning (Rule 10) |
| OpenAI Embeddings (retrieval) | `triage_agent.run()` block loop | `block_snippet` | `list[float]` | `OPENAI_API_KEY` | Already caught by existing `except Exception as emb_err`; `episode_context = ""` (Rule 11) |

---

## Failure Modes

| Failure | Where caught | What happens | Rule |
|---|---|---|---|
| `embed_texts` fails during block retrieval | Existing `except Exception` in block loop | `match = None`, `episode_context = ""` — triage continues without memory injection | Rule 11 |
| `embed_texts` fails during `post_run` episode write | Try/except in `post_run` per-episode loop | Skip that episode, log warning, continue writing others | Rule 5 |
| `summarise_with_llm` raises | Try/except in `summarise_with_llm` | Return `patterns` unchanged; `source` stays `"count_based"` | Rule 10 |
| `save_episode_store` fails | Try/except in save function | Log warning, never raise | Rule 5 |
| `save_semantic_store` fails | Try/except in save function | Log warning, never raise | Rule 5 |
| `load_episode_store` / `load_semantic_store` corrupt | `json.JSONDecodeError` caught | Return empty store; log warning (not FileNotFoundError) | Rule 5 |
| `pre_run` fails entirely | Try/except wraps `pre_run` in `memory_runner` | Return `MemoryContext(semantic_injection="", episode_store=EpisodeStore())` — run proceeds with no memory | Rule 5 |

---

## Injection Format (LLM context)

**SYSTEM_PROMPT appendix (semantic):**
```
## Learned Patterns
- Bug:High (8 decisions) — Login and authentication issues consistently triaged as Bug, High priority
- Bug:Medium (6 decisions) — Dashboard rendering issues consistently triaged as Bug, Medium priority
- Story:Medium (5 decisions) — Feature requests for export/download consistently triaged as Story, Medium
```

**User message appendix (episodic, per block):**
```
## Similar past decisions
- [SCRUM-8] "Login page crashes on empty email field" → Bug, High (2026-04-15)
- [SCRUM-19] "Authentication fails after password reset" → Bug, High (2026-04-22)
- [SCRUM-31] "Session timeout error on login" → Bug, Medium (2026-04-27)
```

---

## Out of Scope

- Phase 6 watermark / `last_processed_ts` — Phase 6 adds `memory/run_state.json` independently; no shared file
- Confidence threshold auto-tuning from memory (Phase 5b)
- Memory pruning UI / `--show-memory` CLI flag (future)
- Vector database (cosine similarity on JSON sufficient at current scale)
- Cross-project memory (single project scope)

---

## Open Questions Resolved

| Question (from brainstorm) | Resolution |
|---|---|
| Episode store growth | `MAX_EPISODES = 200` rolling window; `add_episode()` prunes oldest-first |
| Semantic extraction timing | Synchronous in `post_run()`; wrap in `asyncio.to_thread` if latency becomes an issue |
| LLM extraction failure | Rule 10 — count-based patterns always available as fallback |
| Duplicate overlap with embedding cache | Embedding cache = same ticket (dedup gate). Episode memory = same class of issue (context injection). Embedding gate runs first; no conflict |
| Injection size | `MAX_INJECTED_EPISODES = 3` per block; `MAX_SEMANTIC_PATTERN_CHARS = 1000` for semantic |
| Phase 6 compatibility | Fully decoupled — JSON files only; Phase 6 uses `memory/run_state.json` |
| Retrieval method | Cosine similarity via existing `cosine_similarity()` from `duplicate_detector.py` |
| What to embed per episode | `ticket_summary` (embedded for retrieval); `block_snippet` (stored for display) |
