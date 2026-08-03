# Feature: Phase 2 — Intelligence (Duplicate Detection + Failure Transparency)

> Status: Draft — pending approval
> Date: 2026-04-29
> Phase: 2

---

## Actors

| Actor | Role | What they need | Priority |
|---|---|---|---|
| Team member | Posts in Slack, receives agent confirmations | Never see two tickets for the same bug; know immediately when the agent fails to act | Primary |
| Operator | Runs and maintains the agent | Reliable, transparent behavior — zero silent failures, zero surprise duplicates | Secondary |
| Agent | Executes triage decisions | Unambiguous rules for every failure mode so it never silently does nothing | Secondary |

---

## Priority Rule

When operator reliability needs conflict with team member notification needs: **team member wins** — they are directly affected by duplicate tickets and missing error alerts. The operator can read logs; the team member cannot.

All actor conflicts in this feature are already resolved by the project-wide Priority Rules in `CLAUDE.md`. No new rules needed.

---

## Customer Problem

**Problem 1 — Duplicates:**
The agent has no awareness of existing Jira tickets. Every run, every Slack message triggers a fresh evaluation. If the same bug is reported twice by two people, two tickets are created. If the agent runs without a watermark and processes the same messages again, it creates duplicates. Jira gets cluttered, triage time is wasted, and the team loses trust in the agent.

**Problem 2 — Silent failures:**
Phase 1 has no error handling for external service failures. If Jira is down mid-run, the agent crashes without posting anything to Slack. If OpenAI is unavailable, the team doesn't know triage has stopped. If the Slack MCP subprocess fails mid-run, remaining conversation blocks are silently dropped. An agent that fails without telling you is worse than no agent — you think it worked when it didn't.

---

## What We're Building

Two epics:

**Epic E1 — Duplicate Detection:**
Before creating a Jira ticket for a conversation block, the agent fetches open Jira tickets, compares using embedding similarity, and flags matches above a configurable threshold. If a match is found, the agent posts the existing ticket link to Slack and asks the human to confirm — never auto-skipping, never auto-creating a second ticket.

**Epic E2 — Failure Transparency:**
Implement error handling for the three known gaps against Priority Rules 1, 5, and 6:
- Jira unavailable → post Slack alert, continue remaining blocks (Rule 1)
- OpenAI unavailable → post Slack alert with specific error, exit cleanly (Rule 6)
- Slack MCP fails mid-run → continue all remaining blocks, post one consolidated error summary at end (Rule 5)

---

## Duplicate Detection — Technical Approach

### Why Embeddings (Not Keyword Matching)
Keyword matching fails on paraphrase: "Login crash" and "login button not working" share minimal word overlap but are the same bug. Embeddings capture semantic meaning — both sentences produce similar vectors regardless of wording.

**Model:** `text-embedding-3-small` (OpenAI, same vendor as GPT-4o, ~$0.02 per million tokens)
**Comparison:** Cosine similarity between Slack block embedding and each open Jira ticket embedding
**Threshold:** Configurable via `DUPLICATE_SIMILARITY_THRESHOLD` (default `0.85`) — above this, flag as duplicate

### Latency Solution — Parallel Fetch + Embedding Cache

**The latency problem:** Fetching Jira tickets + embedding them adds API calls to every run.

**Solution 1 — Parallel fetch (hides Jira call):**
Slack messages and Jira tickets are fetched simultaneously using `asyncio.gather()`. The Jira fetch runs inside the Slack fetch window that was already being paid. Net added latency: ~0s.

```python
slack_messages, open_tickets = await asyncio.gather(
    fetch_messages(channel_id),
    fetch_open_jira_tickets(limit=50)
)
```

**Solution 2 — Embedding cache (amortizes embedding cost):**
Each Jira ticket embedding is computed once and cached locally in `memory/ticket_embeddings.json`. Subsequent runs only embed new or changed tickets.

```
Run 1:  embed 50 tickets (1 batch call)  + embed 1 block = 2 API calls
Run 2:  embed 0 new tickets              + embed 1 block = 1 API call
Run 3:  embed 2 new tickets              + embed 1 block = 2 API calls
```

**Solution 3 — Scope limit:**
`MAX_OPEN_TICKETS_TO_FETCH = 50` caps the Jira response to the 50 most recently updated open tickets — where duplicates realistically live.

### Keeping the Cache Fresh

The cache goes stale in three ways. Each has an explicit resolution:

| Staleness cause | Problem | Resolution |
|---|---|---|
| New ticket created by agent this run | Cache doesn't have it yet | Write to cache immediately after `create_jira_ticket()` succeeds |
| New ticket created by human in Jira | Cache doesn't know | At run start, fetch all open ticket keys + timestamps; embed and add any new keys not in cache |
| Ticket closed in Jira | Cache still has it | At run start, remove cache entries whose keys no longer appear in the open tickets list |

**Cache refresh algorithm (run start):**
```
1. Fetch open ticket keys + updated_at timestamps from Jira (lightweight — keys only)
2. For each key in open list:
   - Not in cache → fetch summary, embed, add to cache
   - In cache, timestamp changed → re-fetch summary, re-embed, update cache
   - In cache, timestamp unchanged → skip (no re-embed needed)
3. Remove cache entries for keys no longer in open list (ticket was closed)
4. Cache is now fresh and complete
```

**Cache file structure (`memory/ticket_embeddings.json`):**
```json
{
  "SCRUM-7": {
    "summary": "Fix login crash on empty password",
    "embedding": [0.023, -0.041, "...1536 values..."],
    "last_updated": "2026-04-29T10:03:00Z"
  }
}
```

Phase 6 will migrate this into `agent_memory.db` (SQLite) when the memory layer is built. For Phase 2, JSON file is sufficient for 50 tickets.

---

## Out of Scope

- Vector database (cosine similarity computed in-memory for 50 tickets — no vector DB needed)
- Auto-resolving duplicates — human always confirms (Rule 4, non-negotiable)
- Retry logic with exponential backoff (post error and stop is sufficient for Phase 2)
- Duplicate detection across multiple Jira projects (single project only)
- Embedding models other than `text-embedding-3-small`

---

## Must-Haves vs Nice-to-Haves

| Category | Item |
|---|---|
| Must-have | Parallel fetch: `asyncio.gather(fetch_messages, fetch_open_jira_tickets)` |
| Must-have | Embedding cache: `memory/ticket_embeddings.json` with freshness logic |
| Must-have | Cosine similarity comparison with configurable `DUPLICATE_SIMILARITY_THRESHOLD` |
| Must-have | If match ≥ threshold: post to Slack "This looks like a duplicate of [TICKET-N]: [title]. Is this the same issue?" and skip creation |
| Must-have | Cache written immediately when agent creates a new ticket |
| Must-have | Jira down: catch error, post Slack alert, continue remaining blocks (Rule 1) |
| Must-have | OpenAI down: catch error, post Slack alert with specific error, exit with non-zero code (Rule 6) |
| Must-have | Slack MCP fails mid-run: catch error per block, continue remaining blocks, post consolidated summary at end (Rule 5) |
| Nice-to-have | `MAX_OPEN_TICKETS_TO_FETCH` setting in `settings.py` (default 50) |
| Nice-to-have | `DUPLICATE_SIMILARITY_THRESHOLD` setting in `settings.py` (default 0.85) |
| Nice-to-have | Consolidated end-of-run summary: X tickets created, Y duplicates flagged, Z errors |

---

## Success Metrics

| Metric | Target | How Measured | Whose need |
|---|---|---|---|
| Duplicate tickets created | 0 when open ticket similarity ≥ threshold | Repeat run test — same messages, count new tickets | Team member |
| False positive duplicate rate | < 3% (embeddings vs < 10% for keyword) | Manual review of flagged matches | Team member |
| Failure notification rate | 100% of Jira/OpenAI/Slack failures post to Slack | Unit tests with mocked failures for each service | Operator |
| Silent exit rate | 0 | E2E with each service mocked to fail in sequence | Team member |
| Latency added by duplicate check | < 3s first run, < 500ms subsequent runs (cached) | Timing | Operator |

---

## Risks & Open Questions

- **Risk:** Jira fetch for cache refresh itself fails. Resolution: treat as "no new tickets to add" — use existing cache as-is, log warning, continue. Never block the run on a cache refresh failure.
- **Risk:** `text-embedding-3-small` vectors are 1536 floats × 50 tickets = ~300KB JSON. Acceptable for Phase 2; migrate to SQLite in Phase 6.
- **Risk:** Slack MCP failure during the end-of-run error post. Resolution: if Slack is unavailable at end of run, write consolidated error to stdout and exit with non-zero code (only acceptable silent case).
- **Risk:** Threshold tuning — 0.85 may produce too many false positives or miss real duplicates on first deployment. Resolution: operator can tune via `DUPLICATE_SIMILARITY_THRESHOLD` env var without code changes.
- **Decided:** `classifier.py`'s `is_duplicate` field is NOT used. Duplicate detection is a pre-LLM gate using Jira data — you don't ask the LLM a question you already have a deterministic answer to.

---

## New Priority Rules (feature-specific only)

- **Jira cache refresh fails at run start:** Use existing cache as-is and continue. A stale cache is better than a blocked run — missing a duplicate is recoverable, dropping all messages is not.
- **Slack unavailable at end-of-run error summary:** Write consolidated error to stdout and exit with non-zero code. This is the only acceptable case where Slack is not notified.

---

## Decisions Made This Session

- Embeddings (not keyword matching) for duplicate detection — paraphrase resilience is worth the API call cost
- Model: `text-embedding-3-small` — same vendor (OpenAI), very cheap, 1536-dim vectors
- Latency handled by two mechanisms: (1) parallel fetch with `asyncio.gather()` hides the Jira call inside the Slack call; (2) embedding cache amortizes embedding cost — first run pays full cost, subsequent runs pay only for new tickets
- Cache freshness: at run start, fetch open ticket keys + timestamps, add new, update changed, remove closed. Agent-created tickets written to cache immediately after creation.
- Cache storage: `memory/ticket_embeddings.json` for Phase 2; migrates to `agent_memory.db` in Phase 6
- `classifier.py` `is_duplicate` field not used — duplicate detection is a pre-LLM gate
- Human always confirms duplicate matches (Rule 4 non-negotiable)
- No hard prerequisite — Phase 2 can start immediately
