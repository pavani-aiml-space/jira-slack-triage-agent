# Feature: Phase 2 — Failure Transparency

> Status: Draft — pending approval
> Date: 2026-04-29
> Phase: 2

---

## Actors

| Actor | Role | What they need | Priority |
|---|---|---|---|
| Team member | Posts in Slack, depends on agent for triage | Know immediately when the agent failed to act — never assume it succeeded | Primary |
| Operator | Runs and maintains the agent | Zero silent failures — every exit has an observable reason | Secondary |
| Agent | Executes triage | Unambiguous rules for each failure mode so it never does nothing quietly | Secondary |

---

## Priority Rule

All actor conflicts are already resolved by the project-wide Priority Rules in `CLAUDE.md`:
- Rule 1: Jira unavailable → post to Slack, never fail silently
- Rule 5: Slack MCP fails mid-run → continue all blocks, post consolidated summary at end
- Rule 6: OpenAI unavailable → fail loudly in Slack, instruct team to triage manually

No new rules needed except one new edge case: if Slack itself is down at end-of-run, write to stdout and exit with non-zero code (last resort — covered in Decisions below).

---

## Customer Problem

Phase 1 has no error handling for external service failures. Three specific gaps documented in the Phase 1 design doc:

1. **Jira down:** Agent crashes mid-run without posting to Slack. Team member never knows the ticket wasn't created. They assume it was filed — it wasn't.
2. **OpenAI down:** Agent exits silently. Team has no idea triage stopped. Messages accumulate unprocessed.
3. **Slack MCP fails mid-run:** Remaining conversation blocks are dropped silently. Some messages get processed, others don't — inconsistently, with no report.

All three are trust-destroying behaviors: the team can't rely on an agent that fails without telling them.

---

## What We're Building

Three targeted error handlers — one per Priority Rule gap — each with its own specific response:

1. **Rule 1 — Jira unavailable:** Catch the exception inside `create_jira_ticket()`, post a Slack alert ("Jira is unavailable — please create this ticket manually: {summary}"), and continue processing remaining blocks.

2. **Rule 6 — OpenAI unavailable:** Catch `OpenAIError` wrapping `_run_llm_loop()`, post a Slack alert with the specific error message and manual triage instructions, then exit with code 1.

3. **Rule 5 — Slack MCP mid-run failure:** Catch exceptions from `post_slack_message()` and `ask_for_clarification()` per block, accumulate errors in a list, continue all remaining blocks, and post one consolidated error summary at the end of the run.

**New edge case rule:** If Slack itself is unavailable at the end-of-run consolidated post, write the error summary to stdout and exit with non-zero code.

---

## Why Individual try/except (Not a Decorator)

A decorator wraps every function with the same behavior. Our three failures each need different behavior:

| Failure | Response |
|---|---|
| Jira down (Rule 1) | Post to Slack, **continue** remaining blocks |
| OpenAI down (Rule 6) | Post to Slack, **exit the whole run** |
| Slack MCP mid-run (Rule 5) | **Accumulate** errors, continue all blocks, **consolidated post at end** |

A single decorator can't distinguish which rule to apply. Individual try/except is explicit, directly maps to named rules, and each handler can be tested in isolation.

---

## Out of Scope

- Retry logic with exponential backoff (post error and stop/continue is sufficient for Phase 2)
- Structured log files (Phase 3 — Observability)
- Alerting on repeated failures (Phase 3)
- Handling partial Jira MCP connection failures mid-ticket-creation (treat as full failure)
- Network timeout configuration (use MCP defaults)

---

## Must-Haves vs Nice-to-Haves

| Category | Item |
|---|---|
| Must-have | Jira down: catch in `create_jira_ticket()`, post Slack alert, return error string, continue |
| Must-have | OpenAI down: catch `OpenAIError` in `run()`, post Slack alert, exit code 1 |
| Must-have | Slack MCP mid-run: catch per block in `run()`, accumulate in `slack_errors` list, post consolidated at end |
| Must-have | Slack itself down at end-of-run: write to stdout, exit code 1 |
| Nice-to-have | Print `[ERROR]` prefix to all error outputs for grep-ability in CI logs |
| Nice-to-have | Include block text snippet in consolidated Slack error so team knows which messages need manual triage |

---

## Success Metrics

| Metric | Target | How Measured | Whose need |
|---|---|---|---|
| Silent failure rate | 0 — agent never exits without notification | E2E with each service mocked to fail | Team member |
| Failure notification rate | 100% of Jira/OpenAI/Slack failures post to Slack (or stdout if Slack down) | Unit tests with mocked failures | Operator |
| Remaining blocks processed after Jira failure | 100% — Jira failure on one block does not stop others | Unit test: mock Jira fail on block 1, assert blocks 2+ processed | Team member |
| Remaining blocks processed after Slack MCP failure | 100% — same as above | Unit test | Team member |
| Latency added | < 100ms (error handling is just try/except, no new I/O on happy path) | Timing | Operator |

---

## Risks & Open Questions

- **Risk:** Jira error handler calls `post_slack_message()` — what if Slack is also down at that moment? Resolution: `post_slack_message()` itself will raise; that exception propagates to the per-block Slack error accumulator (Rule 5 handler). The Jira alert gets added to the consolidated error list and posted at end of run.
- **Risk:** OpenAI error handler calls `post_slack_message()` — what if Slack is also down? Resolution: wrap the Slack alert post in its own try/except; if it fails, write to stdout, then exit.
- **Open question (resolved):** Should Jira failures stop the run or continue? **Continue** — Rule 1 explicitly says never fail silently but continue processing.
- **Open question (resolved):** Should OpenAI failure continue or stop? **Stop** — Rule 6 says fail loudly and exit. Without the LLM, the entire rest of the run is useless.

---

## New Priority Rules (feature-specific only)

- **Slack unavailable at end-of-run error summary:** Write consolidated error to stdout and exit with non-zero code. This is the only acceptable case where Slack is not notified. Rationale: if Slack is down, there is no other channel available — stdout is the last resort.
- **Jira failure while Slack also unavailable:** Jira alert goes into `slack_errors` accumulator; consolidated error posted at end or written to stdout. No separate handling needed — same Rule 5 path handles it.

---

## Decisions Made This Session

- Phase ordering: Failure Transparency before Observability before Duplicate Detection — rationale documented in `PROJECT_ROADMAP.md`
- Three separate error handlers, one per rule — not a decorator pattern (each rule has different behavior)
- Jira handler lives inside `create_jira_ticket()` — closest to the failure site, cleanest isolation
- OpenAI handler wraps `_run_llm_loop()` call in `run()` — exits the run cleanly
- Slack MCP handler accumulates errors per block in `run()` — consolidated post at end
- Stdout as last resort when Slack is down — only acceptable silent case
