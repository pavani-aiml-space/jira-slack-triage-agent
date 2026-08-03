# Feature: Confidence-Based Routing

> Status: Draft — pending approval
> Date: 2026-08-03
> Phase: 10

---

## Actors

| Actor | Role | What they need | Priority |
|---|---|---|---|
| Team member | Reports issues in Slack | Never have a real issue silently dropped, but also never get a low-quality ticket filed without a chance to correct it | Primary |
| Operator | Runs and maintains the agent | The confidence thresholds already in `config/settings.py` (`CONFIDENCE_AUTO_ACT`, `CONFIDENCE_ASK_HUMAN`) to actually govern behavior — they exist today but are unused | Secondary |
| Agent | Executes triage | An unambiguous, code-enforced rule for what to do at each confidence tier — not left to its own judgment | Secondary |

---

## Customer Problem

The README and architecture docs describe three-tier confidence routing (auto-act ≥0.90, flag 0.65–0.90, ask-human <0.65), and `CONFIDENCE_AUTO_ACT`/`CONFIDENCE_ASK_HUMAN` exist in settings — but neither is wired into the live agent. `create_jira_ticket`'s tool schema has no `confidence` field, and the settings are never read anywhere. In practice the LLM makes a binary, ungoverned choice between `create_jira_ticket` and `ask_for_clarification` based on its own judgment, with no code-level check.

This means: no safety net exists between "the LLM felt confident" and "a ticket gets filed." A model that's subtly overconfident on an ambiguous message has no mechanism forcing a human checkpoint.

---

## What We're Building

1. `confidence` (required) and `reasoning` (optional) fields on the `create_jira_ticket` tool schema — the LLM self-assesses confidence every time it proposes a ticket.
2. A pure `route_confidence()` function that maps a confidence score to one of three tiers using the existing settings thresholds.
3. **High (≥0.90) — auto-act:** file the ticket as proposed, no extra step.
4. **Medium (0.65–0.90) — flag:** file the ticket, but add a `needs-review` label and a confidence note, so a human knows to double-check it.
5. **Low (<0.65) — escalate:** do **not** file the ticket yet. Post the proposed classification + reasoning to Slack asking for confirmation or corrections. Persist it as a pending confirmation. On a later run, check for a reply:
   - Affirmative reply → file exactly as proposed.
   - Corrective reply (anything else) → re-classify using the human's feedback, then file the corrected version.
   - No reply after `PENDING_CONFIRMATION_MAX_AGE_HOURS` → file as originally proposed anyway (never silently drop it), noting it was auto-filed after no response.

---

## Why Escalation Needs Cross-Run State (Not Just a Bigger Ask)

The agent runs periodically and exits after each run — it can't sit and wait for a Slack reply mid-run. So the "ask for confirmation" tier can't just be a variant of the existing `ask_for_clarification` (fire-and-forget); it needs to remember *what it proposed* and *check back later*. That's a new persisted store (`pending_confirmations.json`, same shape/lifecycle as `episode_store.json`) and a new resolution step that runs at the start of every cycle, before new Slack messages are processed.

---

## Out of Scope

- Real-time/streaming Slack listening (still a periodic batch run)
- Multi-round back-and-forth beyond one correction round (a second ambiguous correction reply is treated as free-text feedback and re-classified once, not looped indefinitely)
- Retrying `search_memory` calls against pending items (pending items are resolved independently of the episodic/semantic memory lifecycle)

---

## Must-Haves vs Nice-to-Haves

| Category | Item |
|---|---|
| Must-have | `confidence` required on `create_jira_ticket`; `route_confidence()` pure + unit-tested |
| Must-have | Medium tier: `needs-review` label + confidence note, ticket still filed |
| Must-have | Low tier: no ticket filed immediately; proposal posted to Slack; persisted as pending |
| Must-have | Resolution step: fetch thread replies, classify affirmative vs. correction, act accordingly |
| Must-have | Safety-net auto-file after `PENDING_CONFIRMATION_MAX_AGE_HOURS` with no reply |
| Nice-to-have | Configurable affirmative-phrase list (hardcoded set is sufficient for now) |

---

## Success Metrics

| Metric | Target | How Measured |
|---|---|---|
| Tickets filed below `CONFIDENCE_ASK_HUMAN` without human input | 0 (barring the max-age safety net) | Unit test: escalate path never calls the Jira MCP directly |
| Silent-drop rate for low-confidence issues | 0 — every escalated item is eventually filed | Unit test: max-age fallback always files |
| Existing 275 tests | Still pass | `pytest tests/unit/ -q` |

---

## Risks & Open Questions

- **Risk:** Reply detection can't reliably distinguish "the bot's own proposal message" from "a human reply" using only ts comparison if Slack ever echoes edits. Resolution: exclude the message matching `proposal_ts` exactly; treat everything else in the thread as human input — acceptable given the official Slack MCP server doesn't expose bot-vs-human user-type metadata cleanly.
- **Risk:** A correction reply could itself be low-quality ("idk maybe"). Resolution: out of scope for v1 — one re-classification round, then file; not a multi-round negotiation.
- **Open question (resolved):** Should the medium tier's flag be enforced in code or left to the LLM's own Slack message wording? **Code-enforced** (label + note appended to the tool's return value) — consistent with this project's existing preference for explicit, testable behavior over prompt-compliance.
