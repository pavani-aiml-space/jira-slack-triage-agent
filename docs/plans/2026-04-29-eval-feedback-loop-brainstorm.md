# Feature: Eval & Feedback Loop

> Status: Draft — pending approval
> Date: 2026-04-29
> Phases: 5 (reaction-based eval + threshold tuning) and 5b (labeled dataset + regression testing)

---

## Actors

| Actor | Role | What they need | Priority |
|---|---|---|---|
| Operator | Runs and maintains the agent | Know if agent quality is degrading; confidence it improves over time without manual Jira audits | Primary |
| Team member | Posts in Slack, reacts to agent messages | One-tap signal (👍/👎) on the agent's Slack confirmation — zero extra friction | Secondary |
| Agent | Improves based on collected feedback | Access to aggregated past feedback to auto-tune confidence thresholds | Secondary |

---

## Priority Rule

When operator need (system reliability) conflicts with team member need (zero friction): **operator wins** — we can collect reactions passively without requiring team members to act. A missing reaction is not a failure; it is simply no signal.

---

## Customer Problem

Every agent run is a black box. The operator has no automated signal that the agent is doing the right thing. If it starts classifying bugs as stories, or setting all priorities to High, nobody knows until someone manually audits Jira. There is no data-driven way to improve the agent — no feedback, no metrics, no loop. Trust in the agent degrades silently.

---

## What We're Building

A closed-loop quality feedback system in two sub-phases:

**Phase 5 — Reaction-based eval:**
Team members react 👍/👎 to the agent's Slack confirmation messages → reactions captured and stored → quality metrics computed per run → confidence thresholds (`CONFIDENCE_AUTO_ACT`, `CONFIDENCE_ASK_HUMAN`) auto-tuned based on trends.

**Phase 5b — Labeled dataset + regression testing:**
Reactions are promoted to a curated labeled dataset (human-verified ground truth) → standard agent eval metrics computed (precision, recall, F1 by ticket type) → OpenAI Evals or equivalent used to run regression tests on every prompt change — so you can prove a change improved things, not just hope it did.

---

## Out of Scope

- Automatic system prompt rewriting (too risky without human review — out of scope for all phases)
- A/B testing of different prompts or models
- Per-user feedback weighting (all reactions treated equally)
- Real-time dashboard or charting UI
- RAGAS — designed for RAG systems, not classification agents; not applicable here

---

## Must-Haves vs Nice-to-Haves

### Phase 5 — Reaction-based eval

| Category | Item |
|---|---|
| Must-have | Capture 👍/👎 reactions on agent Slack messages via Slack MCP |
| Must-have | Store reactions with ticket key, run timestamp, and reactor |
| Must-have | Compute: thumbs-up rate, accuracy by ticket type, accuracy by priority |
| Must-have | Auto-adjust `CONFIDENCE_AUTO_ACT` and `CONFIDENCE_ASK_HUMAN` based on rolling trend |
| Must-have | Minimum-reactions gate (5) before any threshold adjustment fires |
| Nice-to-have | Slack post when quality drops below threshold ("Agent accuracy dropped to 60% this week") |
| Nice-to-have | Per-ticket-type breakdown (bugs scored separately from stories) |

### Phase 5b — Labeled dataset + regression testing

| Category | Item |
|---|---|
| Must-have | Promotion workflow: operator marks a reaction as ground truth → stored in labeled dataset |
| Must-have | Labeled dataset schema: `{slack_text, expected_type, expected_priority, actual_type, actual_priority, correct: bool}` |
| Must-have | Compute standard metrics against labeled set: precision, recall, F1 per ticket type |
| Must-have | Regression test runner: replay labeled dataset against current agent, fail if F1 drops below baseline |
| Nice-to-have | Integration with OpenAI Evals or LangSmith as the test runner (replaces custom runner) |
| Nice-to-have | Baseline stored per prompt version so you can compare prompt A vs prompt B |

---

## Success Metrics

### Phase 5

| Metric | Target | How Measured | Whose need |
|---|---|---|---|
| Reaction capture rate | 100% of 👍/👎 stored within 1 run cycle | Count reactions in feedback store vs Slack | Operator |
| Thumbs-up rate | ≥ 85% of agent actions | Rolling average over last 20 reactions | Operator |
| Threshold convergence | Thresholds stabilise within 10 feedback cycles | Monitor threshold history | Operator |
| Eval overhead | < 2 seconds added to each run | Timing | Operator |
| Zero friction for team | No extra step required from team members | Qualitative — reactions are voluntary | Team member |

### Phase 5b

| Metric | Target | How Measured | Whose need |
|---|---|---|---|
| Labeled dataset size | ≥ 50 ground-truth examples before regression tests are meaningful | Count entries in labeled store | Operator |
| Precision per ticket type | ≥ 90% for Bug, Story, Task | F1 computed against labeled set | Operator |
| Regression test pass rate | 100% — no prompt change ships if F1 drops | CI run of regression test runner | Operator |
| Time to run regression suite | < 60 seconds | Timing | Operator |

---

## Risks & Open Questions

### Phase 5
- **Risk:** Team members don't react → sparse feedback, thresholds never adjust. Mitigation: explicit Slack prompt "React 👍 if this ticket looks right"
- **Risk:** Conflicting reactions on one message (👍 from Alice, 👎 from Bob). Resolved by new priority rule below.
- **Risk:** Automatic threshold adjustment could overshoot on noisy data. Resolved by 5-reaction minimum gate.
- **Risk:** 👍/👎 is coarse — ticket might be right type but wrong priority. Open question: do we need more granular reaction codes (e.g. 🐛 = wrong type, 🔥 = wrong priority)?
- **Dependency:** Phase 3 state tracking must be complete — reactions need to be linked to specific run outputs.
- **Dependency:** Slack MCP must support reading message reactions. Needs spike to confirm.

### Phase 5b
- **Risk:** Labeled dataset is small → metrics are noisy. Resolved by minimum 50-example gate before regression tests are enforced.
- **Risk:** Ground truth labels drift over time — what was correct 6 months ago may not be today. Open question: labeled examples have a TTL? Or are reviewed quarterly?
- **Risk:** Regression suite calls the live OpenAI API → slow and costly at scale. Open question: mock LLM responses in CI, or accept the cost?
- **Open question:** Do we build a custom regression runner first, then optionally swap in OpenAI Evals/LangSmith? Or adopt one of those from day one of Phase 5b?

---

## New Priority Rules (feature-specific only)

- **Conflicting reactions:** If both 👍 and 👎 exist on the same message, treat as neutral (no signal) and do not adjust thresholds. Rationale: ambiguous feedback is worse than no feedback.
- **Sparse feedback:** Do not adjust thresholds until at least 5 reactions are collected. Rationale: single reactions are noise, not signal.

---

## Decisions Made This Session

- Eval covers two sub-phases: Phase 5 (reaction-based, threshold tuning) → Phase 5b (labeled dataset, precision/recall/F1, regression testing)
- Phase 5 reaction signal feeds Phase 5b labeled dataset — reactions promoted to ground truth by operator review, not automatically
- Threshold auto-tuning (Phase 5) and regression testing (Phase 5b) are both "acting on eval" — different mechanisms for different granularity of signal
- Automatic system prompt rewriting is out of scope for all phases — human stays in the loop for prompt changes; regression tests tell you if a manual change helped
- RAGAS is explicitly not applicable (wrong problem domain — RAG retrieval, not classification)
- OpenAI Evals / LangSmith are valid targets for Phase 5b but building a custom runner first avoids an external dependency on day one
- Phase 3 (state tracking) remains a hard prerequisite for Phase 5
