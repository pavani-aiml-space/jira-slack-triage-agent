# Feature: Core Pipeline — Slack → AI → Jira Triage Agent

---

## Actors

| Actor | Role | What they need | Priority |
|-------|------|----------------|----------|
| **Engineer / Team member** | Posts bug reports, feature requests, and tasks in Slack | Their message converted into a well-formed Jira ticket without them having to do it manually | Primary |
| **Operator / Team lead** | Runs and monitors the triage agent | A reliable system that doesn't create noise — no duplicate tickets, no missed messages, no silent failures | Secondary |
| **AI Agent (GPT-4o)** | Classifies messages and decides what action to take | Enough context to classify correctly and call the right tool | Internal |

---

## Priority Rule

When actor needs conflict: **the team member's time wins.** The agent must never block a team member waiting for information — if a message is vague, act with what's available and ask for the rest asynchronously via Slack.

---

## Customer Problem

Engineers report bugs, feature requests, and tasks in Slack — but someone has to manually read each message, decide if it's worth a ticket, classify it, and create it in Jira. This is slow (hours of delay), inconsistent (different people apply different standards), and easy to miss entirely.

**The real problem:** Jira ticket creation is a tax on engineers that adds no value — the value is in the information, not the act of filing.

**Cost of not building this:** Every Slack message that doesn't become a ticket is invisible to project planning. Important bugs get missed. Sprint planning uses incomplete data.

---

## What We're Building

An AI triage agent that reads a Slack channel, classifies each message using GPT-4o, and automatically creates a Jira ticket — posting a confirmation link back to Slack — without any human involvement when the message is clear enough.

---

## Out of Scope

- Auto-assignment of tickets to specific engineers (no ownership mapping in Phase 1)
- Sprint assignment for Stories (Jira sprint API is a separate integration)
- Support for multiple Slack channels (single channel only in Phase 1)
- Continuous / scheduled execution (manual trigger only: `python run_triage.py`)
- Duplicate detection (planned for Phase 2)
- Structured logging or run summaries (planned for Phase 4)

---

## Success Metrics

| Metric | Target | How Measured | Whose need it satisfies |
|--------|--------|--------------|------------------------|
| Time from Slack message to Jira ticket | < 30 seconds | Wall-clock time from `python run_triage.py` invocation to ticket visible in Jira | Team member (speed) |
| Classification accuracy | ≥ 90% of messages correctly typed as Bug / Story / Task | Manual review of created tickets vs message intent | Team member (quality) |
| Slack confirmation posted | 100% of actions confirmed in Slack | Verify no silent failures after each run | Operator (trust) |
| Agent asks for clarification on vague messages | Vague messages get a structured Slack prompt, not a bad ticket | Manual review | Team member + Operator |

---

## Risks & Open Questions

- **[Resolved]** How does the agent decide what to do with each message? → GPT-4o tool-calling loop with three tools: `create_jira_ticket`, `post_slack_message`, `ask_for_clarification`
- **[Resolved]** How do we handle multi-message bug reports? → Time-window grouping: messages within 5 minutes are grouped into one conversation block
- **[Resolved]** What confidence threshold triggers auto-action vs clarification? → `CONFIDENCE_AUTO_ACT = 0.90`, `CONFIDENCE_ASK_HUMAN = 0.65`
- **[Open → Phase 2]** How do we prevent duplicate tickets across multiple runs of the agent?
- **[Open → Phase 3]** How do we avoid reprocessing the same Slack messages on subsequent runs?

---

## New Priority Rules (feature-specific only)

All conflicts resolved by project-wide Priority Rules in `CLAUDE.md`. Specifically:

- **Rule 1** (Jira unavailable → post in Slack, never fail silently) applied to all Jira API calls
- **Rule 2** (Message vague → create with what's available, prompt for more) drove the clarification flow
- **Rule 6** (OpenAI API down → fail loudly in Slack, instruct manual triage) applied to the GPT-4o loop

No new feature-specific priority rules were needed for Phase 1.

---

## Decisions Made This Session

| Decision | What Was Chosen | Why |
|----------|----------------|-----|
| Classification engine | GPT-4o tool-calling loop (not `classifier.py`) | Tool-calling handles ambiguity and multi-step reasoning natively; no custom logic needed |
| Message grouping | 5-minute time window | Balances capturing related follow-up messages vs grouping unrelated ones |
| Confidence thresholds | Auto-act ≥ 0.90, ask human < 0.65 | Conservative — prefer asking when unsure over creating a bad ticket |
| Entry point | `python run_triage.py` | Simple, explicit, manual trigger sufficient for Phase 1 |
