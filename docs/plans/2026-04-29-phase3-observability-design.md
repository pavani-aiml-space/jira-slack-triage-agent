# Technical Design: Phase 3 — Observability

> Design doc. Written 2026-04-29.
> Input: `docs/plans/2026-04-29-phase3-observability-brainstorm.md`

---

## Problem

After every agent run, the operator has no structured record of what happened — no ticket list, no error summary, no run history — and no way to trigger or inspect runs without a terminal.

---

## Approach Chosen

**Option A — Separate `run_logger.py` service module.**

`agents/services/run_logger.py` owns all log I/O: the `RunLog` dataclass, `write_run_log()`, and `load_run_logs()`. `triage_agent.py` imports it and populates a `RunLog` object as it processes blocks. This mirrors the existing `agents/services/` pattern (same location as `slack_reader.py` and `context_builder.py`), is independently testable, and gives Phase 6 a clean import point for reading logs without touching agent code.

Satisfies:
- **Rule 8** (log privacy) — `block_snippet` is the only message content written; no raw text.
- **Rule 9** (dashboard read-only) — dashboard triggers via `subprocess.Popen`; cannot modify logs or tickets.

---

## Components

### Code Diagram
See: [docs/diagrams/2026-04-29-phase3-observability.md](../diagrams/2026-04-29-phase3-observability.md)

### New Files

| File | Purpose |
|------|---------|
| `agents/services/run_logger.py` | `RunLog` dataclass, `write_run_log()`, `load_run_logs()`, `SENTINEL_FILE` constant |
| `dashboard.py` | Streamlit dashboard — run history table, "Run Agent" button, auto-refresh |
| `logs/` (directory) | One `run_<run_id>.json` per run; `.running` sentinel file |

### Modified Files

| File | What changes |
|------|-------------|
| `agents/triage/triage_agent.py` | `_run_llm_loop()` returns `BlockResult`; `run()` builds `RunLog`, writes per-block stdout outcome line, replaces bare "Done" with summary block, writes fatal log before `sys.exit(1)` |
| `run_triage.py` | Creates `logs/.running` before `asyncio.run(run())`, deletes it in a `finally` block |
| `config/settings.py` | Add `LOG_DIR: str = "logs"` |
| `.gitignore` | Add `logs/` |

---

## Data Contracts

### `agents/services/run_logger.py`

```python
from dataclasses import dataclass, field
from typing import Optional

SENTINEL_FILE = "logs/.running"

@dataclass
class LlmStats:
    iterations: int
    tools_called: list[str]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int

@dataclass
class BlockResult:
    block_index: int
    block_snippet: str                    # first 60 chars of combined_text
    action: str                           # "ticket_created" | "clarification_asked" | "error" | "skipped"
    ticket_key: Optional[str] = None
    ticket_summary: Optional[str] = None
    ticket_type: Optional[str] = None
    ticket_priority: Optional[str] = None
    llm: Optional[LlmStats] = None

@dataclass
class ErrorEntry:
    block_index: int
    block_snippet: str
    error_type: str
    error_message: str
    phase2_rule: str                      # "Rule 1" | "Rule 5" | "Rule 6"

@dataclass
class RunLog:
    run_id: str                           # ISO-8601 timestamp, e.g. "2026-04-29T13:20:01"
    started_at: str
    completed_at: Optional[str]
    status: str                           # "success" | "partial" | "fatal"
    messages_fetched: int
    blocks_processed: int
    tickets_created_count: int
    clarifications_asked_count: int
    blocks_skipped_count: int             # always 0 in Phase 3; Phase 4 populates
    error_count: int
    blocks: list[BlockResult] = field(default_factory=list)
    errors: list[ErrorEntry] = field(default_factory=list)

def write_run_log(run_log: RunLog, log_dir: str = "logs") -> str:
    """
    Serialise run_log to JSON and write to log_dir/run_<run_id>.json.
    Creates log_dir if it doesn't exist.
    Returns the file path written.
    """

def load_run_logs(log_dir: str = "logs") -> list[dict]:
    """
    Read all run_*.json files from log_dir.
    Returns list of parsed dicts, sorted newest-first by run_id.
    Returns [] if log_dir doesn't exist or is empty.
    """
```

---

### `agents/triage/triage_agent.py` — changed signatures

#### `_run_llm_loop()` — return type changes from `None` to `BlockResult`

```python
async def _run_llm_loop(
    block_text: str,
    block_index: int,
    block_snippet: str,
) -> BlockResult:
    """
    Runs the OpenAI tool-calling loop for one block.
    Accumulates: iteration count, tools called, finish reason, token totals.
    Determines action from which tool was called last.
    Returns a BlockResult — never raises (caller handles exceptions).
    """
```

**Action inference rules inside `_run_llm_loop`:**
- `create_jira_ticket` in `tools_called` → `action = "ticket_created"`; parse key from result string `r"(SCRUM-\d+)"` or equivalent project key pattern
- `ask_for_clarification` in `tools_called` → `action = "clarification_asked"`
- `post_slack_message` only (no ticket) → `action = "clarification_asked"`
- No tools called, `finish_reason = "stop"` → `action = "no_action"`

**Token accumulation:** sum `response.usage.prompt_tokens` and `response.usage.completion_tokens` across all iterations of the loop.

#### `run()` — RunLog is built incrementally

```python
async def run() -> None:
    run_log = RunLog(
        run_id=datetime.utcnow().isoformat(timespec="seconds"),
        started_at=...,
        completed_at=None,
        status="success",
        messages_fetched=0,
        blocks_processed=0,
        ...
    )

    # ... fetch messages, build blocks ...

    for i, block in enumerate(blocks):
        snippet = block["combined_text"][:60]
        try:
            result: BlockResult = await _run_llm_loop(
                block["combined_text"], i, snippet
            )
            run_log.blocks.append(result)
            _print_block_outcome(result, i, len(blocks))    # [Block N/M] ✅ ...

        except openai.APIError as e:
            # Rule 6 — write fatal log, then exit
            run_log.status = "fatal"
            run_log.completed_at = now()
            write_run_log(run_log)
            # ... Phase 2 Slack alert + sys.exit(1) ...

        except Exception as e:
            # Rule 5 — accumulate error, continue
            error = ErrorEntry(block_index=i, ..., phase2_rule="Rule 5")
            run_log.errors.append(error)
            run_log.blocks.append(BlockResult(block_index=i, snippet=snippet, action="error"))
            slack_errors.append(...)
            continue

    # ... Phase 2 consolidated Slack post if slack_errors ...

    run_log.completed_at = now()
    run_log.status = _compute_status(run_log)
    write_run_log(run_log)
    _post_slack_summary(run_log)      # US3.3
    _print_run_summary(run_log)       # replaces "=== Triage Agent Done ==="
```

#### Two new private helpers in `triage_agent.py`

```python
def _print_block_outcome(result: BlockResult, index: int, total: int) -> None:
    """Print [Block N/M] ✅ / 💬 / ⚠️ line to stdout."""

async def _post_slack_summary(run_log: RunLog) -> None:
    """Post brief end-of-run summary to Slack. Suppressed on fatal status."""

def _print_run_summary(run_log: RunLog) -> None:
    """Print the === Run Summary === block to stdout."""

def _compute_status(run_log: RunLog) -> str:
    """Return 'success' | 'partial' based on error_count."""
```

---

### `run_triage.py` — sentinel file management

```python
import os
from agents.services.run_logger import SENTINEL_FILE

async def main():
    os.makedirs("logs", exist_ok=True)
    open(SENTINEL_FILE, "w").close()        # create sentinel
    try:
        await run()
    finally:
        if os.path.exists(SENTINEL_FILE):
            os.remove(SENTINEL_FILE)        # always clean up, even on crash
```

The `finally` block ensures the sentinel is removed even when `sys.exit(1)` is called — `finally` runs before Python exits.

---

### `dashboard.py`

```python
import streamlit as st
import subprocess, os, time
from agents.services.run_logger import load_run_logs, SENTINEL_FILE

st.title("JiraSlack — Run Dashboard")

# Running state
is_running = os.path.exists(SENTINEL_FILE)
if is_running:
    st.warning("⏳ Agent is running...")
    time.sleep(2)
    st.rerun()

# Trigger
if st.button("▶ Run Agent", disabled=is_running):
    subprocess.Popen(["python", "run_triage.py"])
    st.rerun()

# Run history
logs = load_run_logs()
if not logs:
    st.info("No runs yet. Click 'Run Agent' to start.")
else:
    # Summary table
    st.dataframe(summary_rows(logs))

    # Detail expander per run
    selected = st.selectbox("View run details", [l["run_id"] for l in logs])
    if selected:
        log = next(l for l in logs if l["run_id"] == selected)
        render_run_detail(log)
```

**Auto-refresh mechanism:** while `SENTINEL_FILE` exists, the app sleeps 2 seconds then calls `st.rerun()`. This creates a 2-second polling loop — no extra dependencies. Stops automatically when sentinel is removed.

---

## External Calls

| Service | What's called | When | Auth | Failure handling |
|---------|--------------|------|------|-----------------|
| Slack MCP | `post_slack_message()` | End of run (US3.3 summary) | Bot token (existing) | If post fails, log `"Slack summary failed: {e}"` to stdout — do NOT exit; log is already written |
| Filesystem | `json.dump()` to `logs/run_*.json` | After every run | None | If write fails, print to stdout: `"[LOG ERROR] Could not write run log: {e}"` — do NOT exit; agent completed successfully |

**Note:** The dashboard triggers `python run_triage.py` via `subprocess.Popen`. This is not an external API call — it's a local subprocess. No auth, no network. Failure mode: if `run_triage.py` is not found, `subprocess.Popen` raises `FileNotFoundError` — caught by dashboard with `st.error("Could not start agent: {e}")`.

---

## Failure Modes

### Log write fails (disk full, permissions)
- Action: print `[LOG ERROR] Could not write run log: {e}` to stdout
- Agent run is already complete — do NOT exit, do NOT rerun
- Dashboard shows no new entry (stale view) — operator sees the stdout error
- Satisfies Rule 1 (transparency): never fail silently

### Slack summary post fails (Slack MCP down at end of run)
- Action: log `"Slack summary post failed: {e}"` to stdout
- Log file is already written — operator can read the dashboard
- Do NOT exit — agent completed successfully
- This is distinct from Phase 2 Rule 5 (mid-run Slack errors) — this is post-run only

### `logs/` directory missing
- `write_run_log()` calls `os.makedirs(log_dir, exist_ok=True)` — creates it automatically
- `run_triage.py` also calls `os.makedirs("logs", exist_ok=True)` for the sentinel

### Sentinel file not cleaned up (crash before `finally`)
- `finally` in `run_triage.py` runs even on `sys.exit(1)` — sentinel always removed
- Edge case: OS kill (SIGKILL) — sentinel stays. Dashboard shows "⏳ Running…" indefinitely.
- Mitigation: dashboard shows a "Clear stuck state" button that deletes the sentinel (Rule 9 exception — this is a recovery action, not editing data)

### Dashboard subprocess fails to start
- `subprocess.Popen` raises `FileNotFoundError` (e.g. Python not on PATH)
- Dashboard catches and shows `st.error("Could not start agent: {e}")`

---

## Stdout Messages (exact format)

### Per-block outcome (new — `_print_block_outcome`)
```
[Block 1/4] ✅ Ticket created : SCRUM-11 "Prescription pricing not visible" (Bug · High)
[Block 2/4] 💬 Clarification asked
[Block 3/4] ✅ Ticket created : SCRUM-12 "Add dark mode to settings" (Story · Medium)
[Block 4/4] ⚠️  Error         : Slack MCP connection closed — logged
```

### End-of-run summary (new — `_print_run_summary`, replaces bare "=== Triage Agent Done ===")
```
──────────────────────────────────────────────────
=== Run Summary ===
  Blocks processed : 4
  Tickets created  : 2  (SCRUM-11, SCRUM-12)
  Clarifications   : 1
  Errors           : 1
  Status           : partial
  Log written      : logs/run_2026-04-29T13-20-01.json
──────────────────────────────────────────────────
```

### Slack end-of-run summary (new — `_post_slack_summary`)
```
✅ Run complete [2026-04-29 13:20] — 3 tickets created, 1 clarification asked, 0 errors
```
Or with errors:
```
⚠️ Run complete [2026-04-29 13:20] — 2 tickets created, 1 error — see dashboard for details
```
**Suppressed when `status = "fatal"`** — Phase 2 alert already posted.

---

## Out of Scope

- Hosted deployment — local only
- Real-time log streaming / live terminal tail
- Log rotation, archival, compression
- Dashboard authentication or multi-user access
- Metrics charts or visualisations (Phase 6)
- Modifying or deleting runs from the dashboard (except clearing stuck sentinel)

---

## Build vs Borrow

| Need | Library | Decision |
|------|---------|----------|
| Dashboard UI | `streamlit` | **Borrow** — install `streamlit` |
| JSON serialisation | `json` (stdlib) | **Reuse** — already in codebase |
| Subprocess trigger | `subprocess` (stdlib) | **Reuse** |
| Dataclasses | `dataclasses` (stdlib) | **Reuse** |
| Auto-refresh | `time.sleep` + `st.rerun()` | **Reuse** — no extra deps |

---

## Open Questions Resolved

All open questions from brainstorm are resolved. See `docs/plans/2026-04-29-phase3-observability-brainstorm.md`.

Key resolution: `sys.exit(1)` still runs `finally` blocks in Python — so the sentinel is always cleaned up even on fatal exits. Verified in Python docs: `finally` runs before the interpreter exits.

---

## Files Added to CLAUDE.md (after this phase)

| Section | Addition |
|---------|---------|
| Key Modules | `agents/services/run_logger.py` — write/read run logs |
| Key Modules | `dashboard.py` — Streamlit run history dashboard |
| Tech Stack | Dashboard: Streamlit (`streamlit run dashboard.py`) |
