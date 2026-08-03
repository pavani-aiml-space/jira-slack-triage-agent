# Feature: Phase 4 — Duplicate Detection

> Brainstorm doc. Written 2026-04-29. Approved before /design starts.

---

## Actors

| Actor | Role | What they need | Priority |
|-------|------|----------------|----------|
| Team member | Posted the original Slack message | Their issue tracked once — not duplicated in Jira | Primary |
| Operator | Runs the agent | Never explain to the team why there are duplicate tickets | Primary |
| Agent | Decides whether to create or skip | A reliable, fast way to know if this issue already exists | Secondary |
| Phase 6 Eval | Will compute accuracy metrics | Duplicate decisions logged per-block (action = "duplicate_flagged") in same BlockResult schema | Secondary |

## Priority Rule

When "catch every duplicate" conflicts with "create a ticket fast" — **accuracy wins over speed.**
A false skip (missing a real new issue) is worse than a duplicate (already caught by Rule 4's human confirmation).
When in doubt, ask — don't silently skip.

---

## Customer Problem

Every time `python run_triage.py` runs, it re-reads the last 20 Slack messages. If the same bug gets mentioned twice, or the agent is run twice, it creates a second ticket for the exact same issue. The team sees "SCRUM-5 Fix login crash" and "SCRUM-11 Login page crashing" — both for the same thing. Someone has to manually merge or close one.

**Why it matters now:** Phase 3 gave us run logs. You can now see that block 1 created SCRUM-11 and block 2 created SCRUM-12. Without duplicate detection, every re-run inflates Jira with noise.

**Root cause:** There is no pre-ticket gate. The agent reads Slack, classifies, and creates — no check against what already exists.

---

## What We're Building

A pre-ticket gate: before creating any Jira ticket, the agent checks whether a semantically similar open ticket already exists using embedding similarity — and if one does, posts the match to Slack for human confirmation instead of creating a new one.

---

## Out of Scope

- Cross-project duplicate detection (only checks `JIRA_PROJECT_KEY`)
- Detecting duplicates between Slack messages in the same run (only checks against existing Jira tickets)
- Auto-merging or auto-closing duplicate tickets
- Real-time Jira webhook subscriptions to keep the cache warm between runs
- UI for managing the embedding cache in the dashboard

---

## Success Metrics

| Metric | Target | How Measured | Whose need |
|--------|--------|--------------|------------|
| Duplicate ticket rate | 0% for messages with a match ≥ threshold | Manual E2E: post same bug twice, second run posts match link + asks human | Team member |
| False positive rate | < 5% (clearly different messages still get tickets) | Manual spot-check: post different bugs, verify both get tickets | Operator |
| Cache refresh overhead | < 2 seconds added to run time | Time `run_triage.py` before/after Phase 4 | Operator |
| Duplicate decisions logged | 100% in `BlockResult` (action = "duplicate_flagged") | Check `logs/run_*.json` after E2E | Phase 6 Eval |

---

## Risks & Open Questions

**OQ1 — Jira MCP search tool name (SPIKE NEEDED):**
The exact tool name and arguments for searching open Jira issues via the MCP must be confirmed before design. Previous design session flagged this as unresolved.

**OQ2 — Cache invalidation:**
Open tickets get resolved. If SCRUM-5 is closed, it shouldn't block new tickets for the same issue. Does the cache need to check ticket status, or just refresh from Jira at run start?

**OQ3 — Cold start:**
On a fresh project with 0 Jira tickets, the similarity check trivially returns no matches → create ticket. Code path must handle empty cache cleanly.

**OQ4 — Threshold (DECIDED: 0.85 default, configurable):**
`DUPLICATE_SIMILARITY_THRESHOLD = 0.85` set in `config/settings.py` via `DUPLICATE_SIMILARITY_THRESHOLD` env var. Configurable so operator can tune.

**OQ5 — Embedding cost:**
`text-embedding-3-small` costs ~$0.02/1M tokens. A run embedding 50 ticket summaries ≈ $0.000001. Not a concern.

---

## New Priority Rules (feature-specific)

**Rule 4 (already in CLAUDE.md):** Duplicate detected → human confirms. Post in Slack with the match found, the existing ticket link, and let the team member decide. Never silently skip.

No new rules needed. All conflict cases covered.

---

## Decisions Made This Session

| Decision | Rationale |
|----------|-----------|
| Similarity threshold = 0.85 (configurable) | Conservative enough to catch clear duplicates; high enough to avoid false positives on related-but-distinct issues |
| Embedding model = `text-embedding-3-small` | Already in roadmap; cost is negligible at this scale |
| Cache stored in `memory/ticket_embeddings.json` | Already in roadmap; local file, not committed to git |
| Cache refresh strategy = at run start | Ensures the gate uses current Jira state; cost is < 2s overhead |
| Only check open tickets | Closed tickets should not block new reports of the same issue |
