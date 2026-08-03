# Technical Design: Phase 4 — Duplicate Detection

> Design doc. Written 2026-04-29. Approved before /diagram starts.
> Brainstorm: `docs/plans/2026-04-29-phase4-duplicate-detection-brainstorm.md`

---

## Problem (from brainstorm)

Every run re-reads the last 20 Slack messages and creates a ticket without checking whether one already exists — causing duplicate Jira tickets that the team must manually merge or close.

---

## Approach Chosen

**Option A — Embedding pre-gate.**

Before calling the LLM loop for a block, embed the raw Slack block text and compare against a cache of open Jira ticket embeddings using cosine similarity. If similarity ≥ `DUPLICATE_SIMILARITY_THRESHOLD` (default 0.85), post the existing ticket link to Slack and ask the human to confirm (Priority Rule 4). Otherwise, proceed to `_run_llm_loop()` as normal.

**Why Option A:**
- Aligns with roadmap spec (US4.1–US4.5)
- Fast and deterministic — no LLM call for clear duplicates
- `text-embedding-3-small` handles semantic similarity well for short ticket summaries
- Option C (hybrid) is the natural upgrade path if false positives become a real problem

**Failure mode decision (approved):** If Jira search or embeddings API fails → skip the duplicate check for this run and continue with the LLM loop (Rule 5 spirit). A possible duplicate is less harmful than blocking the entire run.

---

## Code Diagram
See: [docs/diagrams/2026-04-29-phase4-duplicate-detection.md](../diagrams/2026-04-29-phase4-duplicate-detection.md)

---

## Components

### New Files

**`pipeline/duplicate_detector.py`**
All duplicate detection logic in one place — same pattern as `run_logger.py`.

Functions:
```python
async def fetch_open_tickets(project_key: str) -> list[dict]
# Calls jira_search via MCP
# JQL: "project = {key} AND status not in (Done, Closed) ORDER BY created DESC"
# Returns list of {key: str, summary: str, status: str}
# Returns [] on any error (caller skips duplicate check)

async def embed_texts(texts: list[str]) -> list[list[float]]
# Calls openai.embeddings.create(model="text-embedding-3-small", input=texts)
# Returns list of float vectors — one per input text

def cosine_similarity(a: list[float], b: list[float]) -> float
# Pure function, no external calls
# Returns float in [0.0, 1.0]

def load_embedding_cache(cache_path: str) -> dict
# Reads memory/ticket_embeddings.json
# Returns {} if missing or malformed — never raises

async def build_embedding_cache(
    tickets: list[dict],
    existing_cache: dict,
    cache_path: str,
) -> dict
# Embeds only tickets NOT already in existing_cache (diff by key)
# Writes updated cache to disk
# Returns full cache dict {ticket_key: {summary, status, embedding}}

def find_duplicate(
    block_embedding: list[float],
    cache: dict,
    threshold: float,
) -> dict | None
# Returns {key, summary, similarity} for the best match above threshold
# Returns None if no match or cache is empty

def add_ticket_to_cache(
    cache: dict,
    ticket_key: str,
    ticket_summary: str,
    ticket_embedding: list[float],
    cache_path: str,
) -> dict
# Adds a newly created ticket to in-memory cache and writes to disk
# Prevents intra-run duplicates (same block reported twice in one run)
# Returns updated cache
```

**`memory/` directory**
Stores `ticket_embeddings.json`. Not committed to git.

Cache schema:
```json
{
  "refreshed_at": "2026-04-29T13:20:01",
  "project_key": "SCRUM",
  "tickets": {
    "SCRUM-5": {
      "summary": "Fix login crash on empty password",
      "status": "In Progress",
      "embedding": [0.123, 0.456, ...]
    }
  }
}
```

---

### Modified Files

**`agents/triage/triage_agent.py`**

`run()` changes:
1. Use `asyncio.gather(fetch_messages(...), fetch_open_tickets(...))` — parallel fetch
2. Call `build_embedding_cache(tickets, existing_cache, cache_path)` once after fetch
3. Block loop — before `_run_llm_loop()`:
   - Embed block text
   - Call `find_duplicate(block_embedding, cache, threshold)`
   - If match: call `post_slack_message(duplicate notice)` + append `BlockResult(action="duplicate_flagged", ticket_key=match["key"])` + `continue`
   - Else: `_run_llm_loop()` as before
4. After `_run_llm_loop()` creates a ticket: call `add_ticket_to_cache()` with the new ticket's embedding

`run_log` changes:
- `BlockResult` already supports `action="duplicate_flagged"` — no schema change needed
- Increment new `run_log.duplicates_flagged_count` counter (add field to `RunLog`)

**`pipeline/run_logger.py`**

Add `duplicates_flagged_count: int = 0` to `RunLog` dataclass.

**`config/settings.py`**

Add:
```python
DUPLICATE_SIMILARITY_THRESHOLD: float = float(
    os.getenv("DUPLICATE_SIMILARITY_THRESHOLD", "0.85")
)
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_CACHE_PATH: str = os.getenv("EMBEDDING_CACHE_PATH", "memory/ticket_embeddings.json")
JIRA_OPEN_TICKETS_LIMIT: int = int(os.getenv("JIRA_OPEN_TICKETS_LIMIT", "50"))
```

**`.gitignore`**

Add `memory/`.

**`dashboard.py`**

Add `Duplicates flagged` column to run history table (reads `duplicates_flagged_count` from log).

---

## External Calls

| Service | Tool / Endpoint | Payload | Response | Auth |
|---------|----------------|---------|----------|------|
| Jira MCP | `jira_search` | `jql: "project={key} AND status not in (Done, Closed) ORDER BY created DESC"`, `fields: "key,summary,status"`, `limit: 50` | JSON array of issues | `jira_mcp_session()` — same pattern as `jira_tools.py` |
| OpenAI Embeddings | `embeddings.create` | `model: "text-embedding-3-small"`, `input: [list of strings]` | List of float vectors | `OPENAI_API_KEY` |

---

## Failure Modes

| Failure | Rule | Response |
|---------|------|----------|
| `jira_search` MCP fails | Rule 5 (skip + continue) | Log to `run_log.errors` with `phase2_rule="Rule 5"`, set `cache = {}`, continue — LLM loop runs for all blocks without duplicate check |
| OpenAI embeddings API fails | Rule 5 (skip + continue) | Same: log error, skip duplicate check for this run, continue with LLM loop |
| Cache file corrupted / missing | None (silent recovery) | `load_embedding_cache` returns `{}`, `build_embedding_cache` rebuilds from scratch |
| Jira returns 0 tickets (cold start) | None | `cache = {}`, `find_duplicate` returns `None` for all blocks → all proceed to LLM loop |
| Similarity exactly = threshold | Rule 4 | Flag as duplicate (≥ threshold triggers human confirmation) |
| `add_ticket_to_cache` fails (disk write) | None | Log warning, continue — next run rebuilds the cache entry |

---

## Build vs Borrow

| Need | Decision |
|------|----------|
| Cosine similarity | Write — 3 lines of math, no library needed (`numpy` not in requirements; avoid adding it for this) |
| Embedding API | Borrow — `openai` SDK already in requirements (`openai.embeddings.create`) |
| JSON cache | Write — plain `json.dump/load`, matches existing pattern in `run_logger.py` |
| Jira search | Borrow — `jira_mcp_session()` from `jira_tools.py`, call `jira_search` tool |

---

## Out of Scope

- Cross-project duplicate detection (only checks `JIRA_PROJECT_KEY`)
- Duplicate detection between blocks in the same run (only checks against Jira tickets)
- Auto-merging or closing duplicate tickets
- Cache size > 50 tickets (DEBT-010: use pagination if project grows)
- UI for inspecting or clearing the cache in the dashboard

---

## Open Questions Resolved

| OQ | Resolution |
|----|-----------|
| OQ1 — Jira search tool name | `jira_search` with `jql` arg — confirmed by live spike |
| OQ2 — Cache invalidation | Refresh at every run start; only embed open tickets; closed tickets excluded by JQL |
| OQ3 — Cold start | Empty cache → `find_duplicate` returns `None` → all blocks proceed to LLM loop |
| OQ4 — Threshold | 0.85 default, configurable via `DUPLICATE_SIMILARITY_THRESHOLD` in `.env` |
| OQ5 — Embedding cost | Negligible at this scale (~$0.000001 per run) |
| Jira down during check | Skip check + continue (Rule 5 spirit) — approved |
| 50-ticket limit | Accepted for now; logged as DEBT |
