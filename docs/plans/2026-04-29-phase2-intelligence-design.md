# Technical Design: Phase 2 — Intelligence (Duplicate Detection + Failure Transparency)

> Status: Draft — pending approval
> Date: 2026-04-29
> Brainstorm: `docs/plans/2026-04-29-phase2-intelligence-brainstorm.md`

---

## Problem (from brainstorm)

The agent creates duplicate Jira tickets on every run because it has no awareness of existing tickets, and it fails silently when Jira, OpenAI, or Slack MCP is unavailable.

---

## Approach Chosen

**E1 — Duplicate Detection:** Option A — in-memory cosine similarity with JSON embedding cache.
Rationale: 50 tickets × 1536 floats = ~300KB. Python cosine similarity on 50 vectors is microseconds. No new service dependency. Satisfies the latency target (< 3s first run, < 500ms subsequent). Rules 4, 7 satisfied — human always confirms.

**E2 — Failure Transparency:** Option A — individual try/except per call site.
Rationale: Rules 1 (Jira), 5 (Slack mid-run), and 6 (OpenAI) each require different behaviour. A single handler or decorator cannot distinguish which rule to apply. Individual handlers are explicit, independently testable, and directly map to named Priority Rules.

---

## Components

### New Files

| File | Purpose |
|---|---|
| `pipeline/duplicate_detector.py` | Embedding cache management + cosine similarity + `find_duplicate()` |
| `memory/ticket_embeddings.json` | Runtime-generated embedding cache — never commit |

### Modified Files

| File | What changes |
|---|---|
| `agents/triage/tools/jira_tools.py` | Add `fetch_open_jira_tickets()` + Jira-down error handler inside `create_jira_ticket()` |
| `agents/triage/triage_agent.py` | Parallel fetch, duplicate gate before LLM loop, OpenAI error handler, Slack error accumulator |
| `config/settings.py` | Add `MAX_OPEN_TICKETS_TO_FETCH`, `DUPLICATE_SIMILARITY_THRESHOLD`, `EMBEDDING_MODEL` |

---

## Data Contracts

### `pipeline/duplicate_detector.py`

```python
async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Batch embed a list of strings using OpenAI text-embedding-3-small.
    Returns: list of 1536-dim vectors, one per input string.
    """

def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """
    Pure function. Returns similarity in [0, 1]. No async needed.
    """

async def refresh_embedding_cache(
    open_tickets: list[dict]
) -> dict[str, dict]:
    """
    Load cache from memory/ticket_embeddings.json, then:
      - Add entries for new ticket keys
      - Re-embed entries whose 'updated' timestamp changed
      - Remove entries for keys no longer in open_tickets
    Save updated cache to disk.
    Returns: {ticket_key: {"summary": str, "embedding": list[float], "updated": str}}
    """

async def find_duplicate(
    block_text: str,
    cache: dict[str, dict],
    threshold: float,
) -> dict | None:
    """
    Embed block_text, compute cosine similarity against all cached tickets.
    Returns: {"key": "SCRUM-7", "summary": "...", "similarity": 0.91}
    or None if no match above threshold.
    """
```

### `agents/triage/tools/jira_tools.py` — new function

```python
async def fetch_open_jira_tickets(limit: int = 50) -> list[dict]:
    """
    Fetch open Jira tickets via Jira MCP.
    JQL: project = {JIRA_PROJECT_KEY} AND status != Done ORDER BY updated DESC
    Returns: [{"key": "SCRUM-7", "summary": "...", "updated": "2026-04-29T10:03:00Z"}]
    On any failure: returns [] and logs a warning (never raises — stale cache is better than blocked run)
    """
```

### `config/settings.py` — new settings

```python
MAX_OPEN_TICKETS_TO_FETCH: int   = int(os.getenv("MAX_OPEN_TICKETS_TO_FETCH", "50"))
DUPLICATE_SIMILARITY_THRESHOLD: float = float(os.getenv("DUPLICATE_SIMILARITY_THRESHOLD", "0.85"))
EMBEDDING_MODEL: str             = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
```

### `triage_agent.py run()` — modified structure

```python
async def run() -> None:
    slack_errors: list[str] = []  # accumulated Slack MCP failures (Rule 5)

    # Step 1 — parallel fetch (Jira fetch failure handled inside fetch_open_jira_tickets)
    messages, open_tickets = await asyncio.gather(
        fetch_messages(settings.SLACK_CHANNEL_ID),
        fetch_open_jira_tickets(limit=settings.MAX_OPEN_TICKETS_TO_FETCH),
    )

    # Step 2 — refresh embedding cache
    cache = await refresh_embedding_cache(open_tickets)

    # Step 3 — group into blocks
    blocks = build_context_blocks(messages)

    # Step 4 — process each block
    for block in blocks:
        # E1: pre-LLM duplicate gate
        duplicate = await find_duplicate(
            block["combined_text"], cache, settings.DUPLICATE_SIMILARITY_THRESHOLD
        )
        if duplicate:
            msg = (
                f"This looks like a duplicate of [{duplicate['key']}]: "
                f"{duplicate['summary']} ({duplicate['similarity']:.0%} similarity). "
                f"Is this the same issue?"
            )
            try:
                await post_slack_message(msg)
            except Exception as e:
                slack_errors.append(f"Block '{block['combined_text'][:40]}...': {e}")
            continue

        # E2: OpenAI error handling (Rule 6 — post + exit)
        try:
            await _run_llm_loop(block["combined_text"])
        except OpenAIError as e:
            error_msg = f"⚠️ OpenAI is unavailable: {e}. Please triage manually or retry."
            try:
                await post_slack_message(error_msg)
            except Exception:
                print(f"[FATAL] OpenAI down AND Slack down: {e}", flush=True)
            raise SystemExit(1)

    # Step 5 — consolidated Slack error report (Rule 5)
    if slack_errors:
        summary = "⚠️ Slack MCP failed for these blocks during this run:\n" + "\n".join(slack_errors)
        try:
            await post_slack_message(summary)
        except Exception as e:
            print(f"[ERROR] Could not post consolidated error to Slack: {e}\n{summary}", flush=True)
            raise SystemExit(1)
```

---

## External API Calls

### 1. Jira MCP — fetch open tickets

| Property | Value |
|---|---|
| Transport | stdio (`uvx mcp-atlassian`) — same as existing |
| Tool name | `jira_search_issues` *(spike needed to confirm exact name)* |
| JQL | `project = {JIRA_PROJECT_KEY} AND status != Done ORDER BY updated DESC LIMIT {limit}` |
| Response shape | `[{"key": str, "summary": str, "updated": str}]` |
| Auth | `--jira-url`, `--jira-username`, `--jira-token` CLI args — same as existing |
| On failure | Return `[]` — log warning — never raise |

### 2. OpenAI Embeddings API

| Property | Value |
|---|---|
| Client | Same `_client = OpenAI(api_key=settings.OPENAI_API_KEY)` |
| Call | `_client.embeddings.create(model="text-embedding-3-small", input=[...])` |
| Input | `list[str]` — batch of ticket summaries OR single block text |
| Response | `response.data[i].embedding` → `list[float]` (1536 dimensions) |
| Auth | `OPENAI_API_KEY` — already in settings |
| On failure | Skip duplicate check, log warning, proceed to create ticket |

---

## Failure Modes

| External call | Failure | Handler | Priority Rule |
|---|---|---|---|
| `fetch_open_jira_tickets()` at run start | Any exception | Return `[]`, log warning, continue with empty cache — never block the run | New rule: stale cache > blocked run |
| `embed_texts()` — ticket batch embed | Any exception | Skip cache refresh, log warning, proceed without duplicate check | Fail open — missing a duplicate is recoverable |
| `embed_texts()` — block embed | Any exception | Skip duplicate check for this block, proceed to LLM loop | Fail open |
| `create_jira_ticket()` — Jira MCP | Any exception | Post Slack alert "Jira unavailable — please create manually: {summary}", return error string, continue | **Rule 1** |
| `_client.chat.completions.create()` | `OpenAIError` | Post Slack alert with specific error + manual triage instruction, exit with code 1 | **Rule 6** |
| `post_slack_message()` / `ask_for_clarification()` | Any exception | Accumulate error in `slack_errors` list, continue processing remaining blocks | **Rule 5** |
| End-of-run consolidated error post | Any exception | Print to stdout, exit with code 1 | New rule: stdout is last resort |

---

## Cache File

**Path:** `memory/ticket_embeddings.json`
**Never committed** — add to `.gitignore`

```json
{
  "SCRUM-7": {
    "summary": "Fix login crash on empty password",
    "embedding": [0.023, -0.041, "... 1536 floats ..."],
    "updated": "2026-04-29T10:03:00Z"
  }
}
```

**Freshness invariants maintained at every run start:**
- Keys added for new open tickets not in cache
- Keys re-embedded if `updated` timestamp changed
- Keys removed if no longer in the open tickets list
- Agent-created tickets written to cache immediately after `create_jira_ticket()` succeeds

**Migration:** Phase 6 will move this into `agent_memory.db` (SQLite `episodic` table). JSON file is sufficient for ≤ 200 tickets.

---

## Out of Scope

- Vector database — in-memory comparison is sufficient for ≤ 200 tickets
- Retry logic with backoff — post error and continue/stop is sufficient for Phase 2
- Auto-resolving duplicates — human always confirms (Rule 4)
- Multiple Jira project support — single project only
- Embedding models other than `text-embedding-3-small`

---

## Open Questions Resolved

| Question | Resolution |
|---|---|
| Keyword vs embedding matching? | Embeddings — paraphrase resilience justifies the API call |
| How to avoid Jira fetch latency? | `asyncio.gather()` — runs in parallel with Slack fetch, hidden in existing wait |
| How to keep cache fresh? | Diff open ticket keys + timestamps at run start; write immediately on create |
| Use `classifier.py` `is_duplicate`? | No — duplicate detection is a pre-LLM gate; LLM is not asked questions we already have deterministic answers to |
| Exact Jira MCP tool name for search? | **Spike needed** — confirm `jira_search_issues` exists in `uvx mcp-atlassian` before /build starts |
| Threshold value? | 0.85 default, configurable via `DUPLICATE_SIMILARITY_THRESHOLD` env var |
| Decorator for error handling? | No — Rules 1, 5, 6 each require different behaviour; individual try/except is more explicit and testable |

---

## Spike Required Before /build

Before writing any code, confirm the exact Jira MCP tool name for searching issues:

```bash
uvx mcp-atlassian --jira-url $JIRA_URL --jira-username $JIRA_EMAIL --jira-token $JIRA_API_TOKEN
# Then call list_tools and look for the search/query tool name
```

Expected: `jira_search_issues` or `jira_get_issues`. Actual name must be confirmed and hardcoded (same pattern as `JIRA_CREATE_TOOL = "jira_create_issue"` in `jira_tools.py`).
