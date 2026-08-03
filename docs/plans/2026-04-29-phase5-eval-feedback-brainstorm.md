# Feature: Phase 5 — Eval & Feedback Loop

> Brainstorm date: 2026-04-29
> Next step: /design

---

## Actors

| Actor | Role | What they need | Priority |
|---|---|---|---|
| **Operator** | Runs the agent, owns its quality | Know if the agent is doing a good job without manually auditing Jira | Primary |
| **Reporting engineer** | Posts bugs/requests in Slack | Confidence that their report was classified and handled correctly | Primary |
| **Agent (automated)** | Reads reactions, updates quality state | Correct signal to surface quality issues and (in Phase 5b) improve future decisions | System actor |

## Priority Rule

When operator and reporting engineer needs conflict: the operator's need for systemic quality signal wins over any individual engineer's reaction. One 👎 does not change agent behaviour — patterns do.

---

## Customer Problem

**Operator:** Run logs (Phase 3) show *what* the agent did — tickets created, clarifications asked, errors — but give no signal on *whether the decisions were correct*. The only way to know if the agent is getting it right is to open Jira and manually audit each ticket. That manual audit is exactly what the agent was supposed to eliminate.

**Reporting engineer:** When an engineer posts a bug in Slack, the agent responds with a confirmation. But there's no way for the engineer to signal "that was wrong — it's a Story, not a Bug." The feedback loop is broken: the agent acts, the engineer has no voice, and bad decisions accumulate silently.

---

## What We're Building (Phase 5)

A feedback capture layer that records 👍/👎 reactions on Slack confirmation messages, links them to the ticket decisions that produced them, computes quality metrics per run and over time, and alerts the operator when quality drops below a defined baseline.

**Auto-tuning of confidence thresholds is explicitly Phase 5b** — built once we have real signal and validated guardrails.

---

## Out of Scope (Phase 5)

- Auto-tuning `CONFIDENCE_AUTO_ACT` / `CONFIDENCE_ASK_HUMAN` → Phase 5b
- Labeled ground-truth dataset and precision/recall/F1 scoring → Phase 5b
- Diagnosing *why* a ticket was wrong (wrong type? wrong priority? bad description?) — only detecting *that* it was wrong
- Real-time reaction streaming via Slack webhooks — poll-based only
- Per-user reaction weighting — all reactions count equally
- Reaction escalation beyond Slack (email, PagerDuty, etc.)

---

## Production Gaps Identified (all must be addressed in Design)

| Gap | Severity | Resolution target |
|---|---|---|
| `message_ts` not stored — can't read reactions without it | **Blocking** | Phase 5 |
| Tuned thresholds not persisted — reset on every restart | **Blocking** | Phase 5b (but storage layer must be designed now) |
| No baseline before alerting — alert has no reference point | **High** | Phase 5 |
| Reaction timing window undefined — "when does a reaction count?" | **High** | Phase 5 |
| 👎 is a proxy signal — doesn't tell you *what* was wrong | **High** | Accepted limitation; surfaced in alert message |
| Dashboard shows run history but no quality trend | **Medium** | Phase 5 |
| Sparse signal — metric unstable on low-volume channels | **Medium** | Design: minimum reaction count before metric is "valid" |
| Alert fires to Slack only — no escalation if channel is muted | **Low** | Deferred |

---

## Must-Haves (Phase 5)

- Store `message_ts` of every Slack confirmation post alongside the `BlockResult` in the run log
- Poll for reactions at the start of each run within a configurable time window (e.g., reactions posted within the last N hours)
- Persist reactions and quality metrics to disk in a format that survives process restarts
- Compute thumbs-up rate per run and rolling across all runs
- Fire a Slack quality alert when thumbs-up rate drops below `QUALITY_ALERT_THRESHOLD` (configurable)
- Quality trend chart visible in Streamlit dashboard
- Alert includes which run triggered it, the current rate, and the threshold — not just "quality dropped"

## Nice-to-Haves (Phase 5)

- Per-type thumbs-up breakdown (Bug vs Story vs Task)
- Reaction polling window configurable via `.env`
- Dashboard shows per-run reaction count alongside the thumbs-up rate

---

## Success Metrics

| Metric | Target | How Measured | Whose need |
|---|---|---|---|
| Reactions captured | ≥ 95% of confirmation posts have `message_ts` stored | Count stored `message_ts` vs confirmation posts per run | Operator |
| Reaction attribution | 100% of reactions linked to correct run + ticket | Spot-check: `reaction.ticket_key` resolves in run log | Operator |
| Alert fires correctly | Quality alert posted to Slack within 1 run of rate dropping below threshold | Manual E2E: 👎 a confirmation, run agent, verify alert | Operator |
| Metrics survive restart | Thumbs-up rate unchanged after kill + restart | Unit test: write metrics, reload, assert same values | Operator |
| Dashboard shows trend | Quality chart renders in Streamlit with per-run thumbs-up rate | Visual E2E: open dashboard, verify chart | Operator |
| Confirmation is informative | Confirmation message shows type + priority so reporter can validate | Manual: post a bug, read confirmation, verify it shows type + priority | Reporting engineer |

---

## Risks & Open Questions (for /design)

1. Does the Slack MCP `post_slack_message` tool return `message_ts`? If not, we need to call a different tool or read the raw MCP response. **Spike needed before design.**
2. Where does reaction + quality data live? Options: (a) extend `RunLog` JSON files, (b) new `quality_log.json`, (c) start building toward `agent_memory.db` (Phase 7's SQLite). Choice shapes all Phase 5 storage.
3. What is the reaction polling window? Reactions posted more than N hours/days after the confirmation are stale signal. What is N?
4. What is the baseline for the quality alert? Options: (a) first 5 runs, (b) rolling 7-day average, (c) configurable fixed threshold (e.g., 70%). Baseline must exist before the alert can fire meaningfully.
5. How does the dashboard handle cold start (zero reactions yet)?
7. Should reactions be read in the same Slack MCP session as message fetching, or a separate call?

---

## New Priority Rules (Phase 5-specific)

**Rule 8 — Quality alert has no valid baseline yet**
If fewer than `MIN_REACTIONS_FOR_QUALITY` reactions have been collected (default: 5), do not fire a quality alert. Post a "warming up — not enough reactions yet" status instead.

**Rule 9 — No reaction ≠ bad ticket**
Missing reactions are excluded from the thumbs-up rate denominator. Only confirmed 👍 and 👎 count. Do not penalise the agent for unreacted confirmations.

---

## Decisions Made This Session

| Decision | Rationale |
|---|---|
| Phase 5 = capture + metrics + alerts only; auto-tuning = Phase 5b | Auto-tuning without enough real signal risks destabilising the agent. Build the signal layer first, validate it, then tune. |
| Both operator and reporting engineer are primary customers | The operator needs systemic visibility; the engineer needs per-decision feedback. Both are unserved today. |
| Reaction polling is poll-based only | The agent is a script — it runs, acts, and exits. Webhooks require a persistent HTTP server with a public URL. That's a Phase 6 infrastructure decision (scheduled execution), not a Phase 5 concern. |
| All production gaps are addressed in design — none deferred indefinitely | Each gap is either solved in Phase 5 or assigned to Phase 5b with an explicit reason. Nothing is silently ignored. |
