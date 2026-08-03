# Technical Design: Phase 5 — Eval & Feedback Loop

> Design date: 2026-04-29
> Input: `docs/plans/2026-04-29-phase5-eval-feedback-brainstorm.md`
> Next step: /diagram

---

## Problem (from brainstorm)

The agent produces run logs showing *what* it did but gives no signal on *whether its decisions were correct* — the operator has no quality visibility without manually auditing Jira, and the reporting engineer has no way to flag a misclassification.

---

## Approach Chosen

**`message_ts` capture:** Option A (primary) — parse the `slack_post_message` MCP response inside `post_slack_message()` and store the returned `ts` in a module-level capture buffer that `triage_agent` drains after each block loop. Falls back gracefully to `confirmation_ts = None` if the MCP response doesn't expose `ts` (Rule 9: no reaction ≠ bad ticket). Spike in Chunk 1 confirms response shape.

**Storage:** Extend `BlockResult` (per-block `confirmation_ts`) + new `memory/quality_store.json` (rolling quality aggregate). Consistent with existing JSON-based run log pattern. No new persistence model needed.

**Why this satisfies the Priority Rules:**
- Rule 5: all reaction collection failures are skipped + logged, never crash the run
- Rule 8 (new): no alert fires until `MIN_REACTIONS_FOR_QUALITY` total reactions exist
- Rule 9 (new): missing reactions excluded from denominator — not treated as bad tickets

---

## Components

### Code Diagram
See: [docs/diagrams/2026-04-29-phase5-eval-feedback.md](../diagrams/2026-04-29-phase5-eval-feedback.md)

### New Files

- `pipeline/reaction_collector.py` — fetches Slack channel history, matches `confirmation_ts` values, returns reactions per message
- `pipeline/quality_metrics.py` — loads/saves `quality_store.json`; computes per-run quality; checks alert threshold; manages pending/collected reaction state
- `pipeline/eval_runner.py` — orchestrates the eval lifecycle: runs Step 0 (collect reactions, alert) before triage and Step 6 (register pending) after triage; called by `run_triage.py`, never by `triage_agent`

### Modified Files

- `agents/triage/tools/slack_tools.py` — parse MCP response in `post_slack_message()`; add module-level `_confirmation_ts_buffer`; add `drain_confirmation_ts()` helper
- `pipeline/run_logger.py` — add `confirmation_ts: Optional[str] = None` to `BlockResult`
- `agents/triage/triage_agent.py` — (1) drain ts buffer per block; (2) change `run()` return type `None → RunLog` so `run_triage.py` can hand the log to `eval_runner`
- `run_triage.py` — call `eval_runner.run_eval_step()` before and after `triage_agent.run()`
- `dashboard.py` — quality trend chart (thumbs-up rate over time); reaction columns in run history table
- `config/settings.py` — 5 new settings

**Why `eval_runner.py` and not `triage_agent.py`:**
`triage_agent.run()` owns the LLM triage loop — classification, tool calling, Jira, Slack confirmation. It has no business knowing quality metrics exist. Reaction collection and quality alerts are pipeline-level concerns that run before and after the entire triage run. `eval_runner.py` sits in `pipeline/` alongside `slack_reader.py`, `context_builder.py`, `run_logger.py` — each owning one stage of the pipeline. `triage_agent.run()` returns `RunLog` (same as it always produced internally); `run_triage.py` passes that log to `eval_runner` post-run.

---

## Data Contracts

### New dataclasses — `pipeline/quality_metrics.py`

```python
@dataclass
class PendingReaction:
    run_id: str
    block_index: int
    ticket_key: Optional[str]
    confirmation_ts: str       # Slack message_ts of the confirmation post
    posted_at_iso: str         # ISO timestamp of the run — for window filtering

@dataclass
class CollectedReaction:
    run_id: str
    block_index: int
    ticket_key: Optional[str]
    thumbs_up: int
    thumbs_down: int
    collected_at: str          # ISO timestamp of collection run

@dataclass
class RunQuality:
    run_id: str
    collected_at: str
    thumbs_up: int
    thumbs_down: int
    reactions_found: int
    thumbs_up_rate: Optional[float]   # None if total < MIN_REACTIONS_FOR_QUALITY
```

### `quality_store.json` on-disk shape

```json
{
  "pending": [
    {
      "run_id": "2026-04-29T14:00:00",
      "block_index": 0,
      "ticket_key": "SCRUM-5",
      "confirmation_ts": "1714406400.123456",
      "posted_at_iso": "2026-04-29T14:00:05"
    }
  ],
  "runs": [
    {
      "run_id": "2026-04-29T14:00:00",
      "collected_at": "2026-04-29T15:00:00",
      "thumbs_up": 3,
      "thumbs_down": 1,
      "reactions_found": 4,
      "thumbs_up_rate": 0.75
    }
  ]
}
```

### `BlockResult` extension — `pipeline/run_logger.py`

```python
confirmation_ts: Optional[str] = None   # Slack ts of the confirmation post
                                         # populated only for ticket_created blocks
                                         # None if MCP response had no ts (Rule 9)
```

### Module-level capture buffer — `agents/triage/tools/slack_tools.py`

```python
_confirmation_ts_buffer: list[str] = []

async def post_slack_message(message: str) -> str:
    # Calls slack_post_message via MCP
    # On success: parses result.content[0].text → JSON → extracts ts → appends to buffer
    # On any parse failure: buffer unchanged, return value unchanged, no crash
    return f"Message posted: {message}"

def drain_confirmation_ts() -> Optional[str]:
    """Pop and return the last ts from the buffer; clear it. Returns None if empty."""
```

### `reaction_collector.py` function signatures

```python
async def fetch_reactions_for_pending(
    pending: list[PendingReaction],
    channel_id: str,
    history_limit: int,
    window_hours: int,
) -> list[CollectedReaction]:
    """
    One slack_get_channel_history call (limit=history_limit).
    Filters pending to entries within window_hours of now.
    Matches messages by ts. Counts name="+1" as thumbs_up, name="-1" as thumbs_down.
    Returns CollectedReaction for each matched ts. Unmatched entries are silently skipped (Rule 9).
    Returns [] on any MCP error (Rule 5 — caller skips alert).
    """
```

### `quality_metrics.py` function signatures

```python
def load_quality_store(path: str) -> QualityStore:
    """Returns empty store on missing/corrupt file — never raises."""

def save_quality_store(store: QualityStore, path: str) -> None:
    """Logs warning on failure — never raises."""

def add_pending_from_run(store: QualityStore, run_log: RunLog) -> None:
    """Extracts BlockResults with confirmation_ts; appends as PendingReaction to store."""

def apply_collected(store: QualityStore, collected: list[CollectedReaction]) -> None:
    """Moves matching pending entries → computes RunQuality → appends to store.runs.
    Removes processed entries from store.pending."""

def should_alert(
    store: QualityStore,
    threshold: float,
    min_reactions: int,
) -> tuple[bool, Optional[RunQuality]]:
    """Returns (True, latest_run_quality) if:
       - store.runs is non-empty
       - total reactions across all runs >= min_reactions  (Rule 8 warm-up gate)
       - latest store.runs entry thumbs_up_rate < threshold
    Returns (False, None) otherwise."""

def rolling_thumbs_up_rate(store: QualityStore) -> Optional[float]:
    """Aggregate rate across all runs. Returns None if total < MIN_REACTIONS_FOR_QUALITY."""
```

---

## External Calls

### `slack_post_message` — capture `ts` (spike in Chunk 1)

| | Detail |
|---|---|
| **Tool** | `slack_post_message` via `slack_mcp_session()` |
| **Payload** | `{channel_id: str, text: str}` |
| **Expected response** | `result.content[0].text` → JSON → `{"ts": "1714406400.123456", ...}` |
| **Auth** | `SLACK_BOT_TOKEN` (already in `slack_mcp_session()`) |
| **Spike needed** | Confirm response shape in Chunk 1 before full implementation |

### `slack_get_channel_history` — read reactions

| | Detail |
|---|---|
| **Tool** | `slack_get_channel_history` via `slack_mcp_session()` |
| **Payload** | `{channel_id: str, limit: REACTION_HISTORY_LIMIT}` |
| **Expected response** | `messages[*].ts: str`, `messages[*].reactions: [{name: str, count: int, users: [str]}]` |
| **Called** | Once per run, at run start, only if `store.pending` is non-empty |
| **Auth** | `SLACK_BOT_TOKEN` (already in `slack_mcp_session()`) |

---

## Failure Modes

| Scenario | Behaviour | Rule |
|---|---|---|
| `slack_post_message` MCP response has no `ts` | `confirmation_ts = None`; block excluded from reaction collection | Rule 9 |
| `slack_get_channel_history` raises at run start | `fetch_reactions_for_pending` returns `[]`; `apply_collected` skips; no alert fired | Rule 5 |
| Confirmation message not found in history (too old / outside limit) | `ts` unmatched; entry stays in `pending` until window expires; treated as Rule 9 | Rule 9 |
| `quality_store.json` write fails | Warning logged; run proceeds; pending entries lost for this cycle | Rule 5 |
| Quality alert `post_slack_message` fails | Caught by Rule 5 accumulator in `run()`; reported in consolidated error summary | Rule 5 |
| `quality_store.json` missing or corrupt | `load_quality_store` returns empty store; log warning; no false alerts | Rule 8 |
| Fewer than `MIN_REACTIONS_FOR_QUALITY` total reactions | `should_alert` returns `(False, None)`; dashboard shows "warming up" | Rule 8 |

---

## Run Flow — Revised Architecture

```
run_triage.py (entry point — unchanged contract, extended with eval hooks)
  │
  ├── await eval_runner.run_eval_step()          [NEW] Step 0: collect + alert
  │     load_quality_store()
  │     if pending:
  │       fetch_reactions_for_pending() → Slack MCP
  │       apply_collected()
  │       should_alert() → post_slack_message() if True
  │       save_quality_store()
  │
  ├── run_log = await triage_agent.run()         [MOD: returns RunLog, was None]
  │     Step 1: parallel fetch (Slack + Jira)
  │     Step 2: build embedding cache
  │     Step 3: group into blocks
  │     Step 4: per block:
  │       [MOD] drain_confirmation_ts()  ← clear buffer
  │       duplicate gate (existing)
  │       _run_llm_loop() → LLM + Jira + Slack
  │       [MOD] if ticket_created: result.confirmation_ts = drain_confirmation_ts()
  │     Step 5: consolidated error report (existing)
  │     Step 6: write run log + post Slack summary (existing)
  │     return run_log                           ← new
  │
  └── await eval_runner.run_eval_step(run_log)   [NEW] Step 7: register pending
        add_pending_from_run(quality_store, run_log)
        save_quality_store()
```

**What does NOT change in `triage_agent.run()`:** every existing step, all error handling, all Priority Rules, the Slack summary post. The only change is: (1) drain ts buffer per block, (2) populate `result.confirmation_ts` for ticket_created blocks, (3) return `run_log` instead of `None`.

**What `eval_runner.run_eval_step()` looks like:**
```python
async def run_eval_step(run_log: RunLog | None = None) -> None:
    """
    Pre-triage (run_log=None):  load store → collect reactions → alert → save.
    Post-triage (run_log given): add pending from run → save.
    """
```

---

## New Settings

```python
QUALITY_ALERT_THRESHOLD:   float = float(os.getenv("QUALITY_ALERT_THRESHOLD",   "0.70"))
MIN_REACTIONS_FOR_QUALITY: int   = int(os.getenv("MIN_REACTIONS_FOR_QUALITY",    "5"))
REACTION_WINDOW_HOURS:     int   = int(os.getenv("REACTION_WINDOW_HOURS",        "48"))
REACTION_HISTORY_LIMIT:    int   = int(os.getenv("REACTION_HISTORY_LIMIT",       "50"))
QUALITY_STORE_PATH:        str   = os.getenv("QUALITY_STORE_PATH", "memory/quality_store.json")
```

---

## Out of Scope (Phase 5)

- Auto-tuning `CONFIDENCE_AUTO_ACT` / `CONFIDENCE_ASK_HUMAN` → Phase 5b
- Labeled ground-truth dataset and precision/recall/F1 scoring → Phase 5b
- Per-user reaction weighting
- Diagnosing *why* a ticket was wrong (type? priority? description?) — only detecting *that* it was wrong
- Alert escalation beyond Slack (email, PagerDuty)
- Webhook-based real-time reaction delivery

---

## Open Questions Resolved

| Question | Resolution |
|---|---|
| Does `slack_post_message` MCP return `ts`? | Spike in Chunk 1 — primary approach (Option A); fallback to post-hoc channel history fetch (Option B) if ts absent |
| Where does quality data live? | `quality_store.json` (rolling) + `confirmation_ts` in `BlockResult` (per-block) |
| Reaction polling window? | `REACTION_WINDOW_HOURS` setting, default 48h — pending entries older than this are expired silently |
| What is the alert baseline? | Alert fires on the latest collected `RunQuality` entry. Warm-up gate: `MIN_REACTIONS_FOR_QUALITY` total reactions must exist before any alert (Rule 8) |
| How does dashboard handle cold start? | Show "Warming up (N/MIN reactions)" instead of a rate when total < threshold |
| Should reactions be read in same session as Slack fetch? | No — separate `slack_mcp_session()` call in `fetch_reactions_for_pending`, only when `store.pending` is non-empty |
| What counts as a valid reaction? | Only `name == "+1"` (thumbs_up) and `name == "-1"` (thumbs_down). All other reaction names ignored. |
