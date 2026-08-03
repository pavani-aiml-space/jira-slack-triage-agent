# Feature: Phase 3 — Observability

> Brainstorm doc. Written 2026-04-29. Approved before /design starts.

---

## Actors

| Actor | Role | What they need | Priority |
|-------|------|----------------|----------|
| Operator | Runs the agent, responsible for output quality | See what every run did (tickets created, errors) without reading raw stdout; trigger runs from a UI | Primary |
| Future-self (Phase 6 Eval) | Will consume run logs to compute accuracy, thumbs-up rate, per-type precision | Logs structured enough to parse programmatically per run; each run uniquely identifiable | Secondary |
| Team members | Posted the Slack messages that triggered the run | Confirmation their message was acted on | Already satisfied by Phase 1 Slack ticket confirmations |

## Priority Rule

When operator "know what happened" conflicts with future-self "machine-readable for eval":
**Format satisfies both** — JSON is human-readable enough for the operator and machine-parseable for Phase 6.
No real conflict. Future-self's need is a constraint on log format, not a competing priority.

---

## Customer Problem

After `python run_triage.py` finishes, the operator has no clean answer to:
- "How many tickets were created this run?"
- "Did anything fail? What failed?"
- "What happened in the run I did an hour ago?" (stdout is gone)

Phase 2 fixed silent failures — errors now post to Slack inline. But there is no structured record of the run's outcome. You'd have to reconstruct what happened by scrolling stdout, reading Slack, and checking Jira manually.

**The real problem:** The agent acts but leaves no evidence of what it did. Every run evaporates when the terminal closes. And the only way to trigger a run is from the terminal — there's no interface a non-developer could use.

**Why it matters now (before Phase 4):** Phase 4 (Duplicate Detection) needs a way to verify the duplicate gate is working correctly. Without run logs, there's no way to confirm whether a duplicate was correctly detected or silently skipped.

---

## What We're Building

A local Streamlit dashboard that shows run history from a JSON log file, lets the operator trigger the agent with a button, and auto-refreshes when the run completes — plus a brief Slack post after every run.

---

## Out of Scope

- Hosted deployment (local only — `localhost`)
- Real-time log streaming / live tail (background fire-and-forget; dashboard auto-refreshes on completion)
- Dashboard authentication
- Multi-user access
- Metrics charts / visualisations (Phase 6)
- Log rotation, archival, compression
- Editing or deleting runs from the dashboard

---

## Key Term: What Is a Block?

A **block** is a group of consecutive Slack messages that are close enough in time to be about the same topic.

**Rule:** Messages within `CONTEXT_WINDOW_MINUTES` (default: **5 minutes**) of the previous message are merged into one block. A gap larger than 5 minutes starts a new block.

**What the agent does with it:** All messages in a block are joined into a single string (`combined_text`) and sent to GPT-4o as one unit. GPT-4o reads the full block and decides on exactly one action: create a ticket, ask for clarification, or (if an error occurs) fail.

**Example — 3 messages → 2 blocks:**
```
1:00pm  "login page is crashing"            ┐
1:02pm  "it started after yesterday's       ├─ Block 1 → GPT-4o creates Bug ticket
         deploy"                             ┘

1:15pm  "can we add dark mode?"             ── Block 2 → GPT-4o creates Story ticket
```

**Why it matters for logs:** The log traces outcomes at the block level — not the individual message level. One block = one LLM call = one action = one log entry in `blocks[]`.

---



### Component 1 — JSON Run Log
After every `run_triage.py` execution, write one file to `logs/run_<ISO-timestamp>.json`.

**Exact schema — all four levels:**

```json
{
  "run_id": "2026-04-29T13:20:01",
  "started_at": "2026-04-29T13:20:01",
  "completed_at": "2026-04-29T13:20:14",
  "status": "success | partial | fatal",

  "funnel": {
    "messages_fetched": 12,
    "blocks_processed": 4,
    "tickets_created_count": 2,
    "clarifications_asked_count": 1,
    "blocks_skipped_count": 0,
    "error_count": 1
  },

  "blocks": [
    {
      "block_index": 0,
      "block_snippet": "Login is broken after yesterday's deploy...",
      "action": "ticket_created | clarification_asked | error | skipped",
      "ticket_key": "SCRUM-11",
      "ticket_summary": "Login crash after deploy",
      "ticket_type": "Bug",
      "ticket_priority": "High",
      "llm": {
        "iterations": 2,
        "tools_called": ["create_jira_ticket"],
        "finish_reason": "stop",
        "prompt_tokens": 412,
        "completion_tokens": 89
      }
    }
  ],

  "errors": [
    {
      "block_index": 3,
      "block_snippet": "The export button crashes when...",
      "error_type": "SlackMCPError | JiraError | OpenAIError",
      "error_message": "Connection closed",
      "phase2_rule": "Rule 1 | Rule 5 | Rule 6"
    }
  ]
}
```

**Field notes:**
- `block_snippet` — first 60 chars of `combined_text`. Enough to identify the message; not enough to expose full PII.
- `llm` is `null` when the block errored before/during LLM call.
- `blocks_skipped_count` is always 0 in Phase 3. Populated in Phase 4 when duplicate detection lands.
- `status` values:
  - `"success"` — all blocks processed, no errors
  - `"partial"` — some blocks errored (Rule 5), run continued
  - `"fatal"` — OpenAI API down (Rule 6), agent exited early. Log written with whatever completed before exit.
- Fatal errors (Rule 6): log is written from inside the Phase 2 exception handler before `sys.exit(1)` — so the dashboard always has a record of even failed runs.

**Log location:** `logs/run_<run_id>.json`
**Sentinel file:** `logs/.running` — created at run start, deleted at run end. Dashboard shows "⏳ Running…" while it exists.

### Component 2 — Streamlit Dashboard (`dashboard.py`)
A local web UI at `http://localhost:8501`.

**Run History view:**
- Table of all past runs: timestamp, status, tickets created, errors
- Click a run → detailed view (tickets list, clarifications, error details)

**Trigger view:**
- "Run Agent" button
- Fires agent as a background subprocess
- Dashboard auto-refreshes when the run log updates (new entry appears)

### Component 3 — Slack Run Summary (US3.3)
After every run, post a brief summary to the Slack channel:
```
✅ Run complete [2026-04-29 13:20] — 3 tickets created, 1 clarification asked, 0 errors
```
If there were errors:
```
⚠️ Run complete [2026-04-29 13:20] — 2 tickets created, 1 error — see dashboard for details
```
This is separate from Phase 2 inline error alerts (which fire immediately when an error occurs mid-run). This is the end-of-run summary.

### Component 4 — Stdout Messages

**Three additions to the existing stdout output:**

#### 4a — Per-block outcome line (printed after each block finishes)
One clean line summarising what happened for that block:

```
[Block 1/4] ✅ Ticket created : SCRUM-11 "Prescription pricing not visible" (Bug · High)
[Block 2/4] 💬 Clarification asked
[Block 3/4] ✅ Ticket created : SCRUM-12 "Add dark mode to settings" (Story · Medium)
[Block 4/4] ⚠️  Error         : Slack MCP connection closed — logged
```

#### 4b — End-of-run summary block (replaces current bare "=== Triage Agent Done ===")

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

Status values in stdout:
- `success`  — all blocks processed, 0 errors
- `partial`  — some blocks errored (Rule 5), run continued
- `fatal`    — OpenAI down (Rule 6), run stopped early

#### 4c — Fatal exit stdout (already exists from Phase 2, no change needed)

```
[TRIAGE AGENT FATAL] OpenAI unavailable AND Slack unreachable.
OpenAI error : APIConnectionError: ...
Slack error  : ...
Please triage manually.
```

**What stays the same (not changed by Phase 3):**
- Per-iteration debug output (`[iteration N] finish_reason:`, `→ Executing:`, `→ Result:`) — stays as-is, useful for watching a live run

---

## Must-Haves

- [ ] JSON log written after every run (`logs/run_<ISO-timestamp>.json`, one file per run)
- [ ] Log is valid JSON with all fields in the schema above
- [ ] Sentinel file `logs/.running` created at run start, deleted at run end
- [ ] Per-block outcome line printed to stdout after each block (`[Block N/M] ✅ / 💬 / ⚠️`)
- [ ] End-of-run summary block printed to stdout (replaces bare "=== Triage Agent Done ===")
- [ ] `status: "fatal"` log written before `sys.exit(1)` when OpenAI is down
- [ ] Streamlit dashboard reads `logs/` and shows run history table
- [ ] Dashboard has a "Run Agent" button that fires `run_triage.py` as background subprocess
- [ ] Dashboard auto-refreshes when sentinel file disappears (run complete)
- [ ] Slack end-of-run summary post after every normal/partial run (US3.3)
- [ ] `logs/` in `.gitignore`

## Nice-to-Haves

- [ ] Run detail page (click a run to see full ticket list + error details)
- [ ] Status indicator while agent is running ("⏳ Running...")
- [ ] `dashboard.py` launchable with a single command: `streamlit run dashboard.py`

---

## Success Metrics

| Metric | Target | How Measured | Whose need |
|--------|--------|--------------|------------|
| Log file created after every run | 100% of runs | Check `logs/` after `python run_triage.py` | Operator |
| Log contains all created ticket keys | 100% accuracy | Compare `logs/*.json` to Jira after E2E | Operator |
| Dashboard loads and shows run history | Works on first `streamlit run dashboard.py` | Manual E2E check | Operator |
| "Run Agent" button triggers a real run | Works end-to-end | Manual E2E check | Operator |
| Slack summary post appears after every run | 100% of runs | Check Slack channel after E2E | Operator |
| Log is valid parseable JSON | 100% of runs | Unit test: `json.load()` on log content | Phase 6 |

---

## Risks & Open Questions

**Open Question 1 — Log storage: RESOLVED**
One file per run in `logs/run_<ISO-timestamp>.json`. Self-documenting run history; no append-mode corruption risk. `logs/` in `.gitignore`.

**Open Question 2 — Auto-refresh mechanism: RESOLVED**
Sentinel file `logs/.running` — created at run start, deleted at run end. Dashboard polls `os.path.exists("logs/.running")` and shows "⏳ Running…" while it exists. Works across page refreshes. No extra dependencies.

**Open Question 3 — Fatal error log entry: RESOLVED**
Write a `status: "fatal"` log entry even when OpenAI is down (Rule 6). The log writer is called from inside the Phase 2 `except openai.APIError` handler, before `sys.exit(1)`. Dashboard always shows a record of the run — including failed ones. The Slack end-of-run summary does NOT fire in this case (agent exited early); Phase 2's inline alert is the only Slack notification for that case.

**Open Question 4 — Slack summary vs Phase 2 error alerts: RESOLVED**
Phase 2 = reactive inline alerts (fire immediately when an error occurs mid-run).
Phase 3 = proactive end-of-run summary (fires at the end of a completed or partial run).
Fatal exits (Rule 6): log written, Slack summary suppressed, Phase 2 alert already sent. Clean boundary.

---

## New Priority Rules (feature-specific)

**Rule 8 — Log content privacy**
Log outcome only (ticket key, action type, error messages). Never log raw Slack message text.
Rationale: Slack messages may contain PII. The log is written to disk with no encryption. Outcomes are sufficient for debugging and Phase 6 eval.

**Rule 9 — Dashboard is read-only except for triggering runs**
The dashboard can trigger the agent. It cannot modify, delete, or edit logs or Jira tickets.
Rationale: Keep the UI simple and safe — it's an observer, not an editor.

---

## Decisions Made This Session

| Decision | Rationale |
|----------|-----------|
| Streamlit for dashboard | Python-only, no HTML/CSS, fastest to build for a local tool; already in Python ecosystem |
| Local only (localhost) | No hosting complexity; this is an operator tool, not a team-facing product |
| Background trigger + auto-refresh | Operator fires and watches; no blocking the UI while agent runs |
| Both dashboard AND Slack summary (US3.3) | Dashboard for detailed history; Slack for real-time awareness without opening the browser |
| One file per run in `logs/` | Self-documenting run history; no append-mode corruption risk |
| `run_id` included in Phase 3 | Free to add now; needed in Phase 6 to join reactions to specific runs |
| Raw Slack message text NOT in log | Privacy risk (PII in messages); 60-char snippet is enough to identify which message |
| All 4 log levels included | Funnel + block trace + LLM trace + errors — operator gets full picture; Phase 6 gets all the data it needs |
| Sentinel file `logs/.running` | Simplest cross-refresh state mechanism; no extra dependencies |
| Fatal run writes `status: "fatal"` log before exit | Dashboard always has a record of every run including failed ones; operator never wonders "did it even start?" |
| Slack summary suppressed on fatal exit | Phase 2 alert already fired; duplicate Slack post would confuse more than help |
