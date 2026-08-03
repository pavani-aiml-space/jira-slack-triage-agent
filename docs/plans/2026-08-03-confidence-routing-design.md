# Technical Design: Confidence-Based Routing

> Status: Draft — pending approval
> Date: 2026-08-03

---

## Problem (from brainstorm)

`CONFIDENCE_AUTO_ACT`/`CONFIDENCE_ASK_HUMAN` exist but are never read. `create_jira_ticket` has no `confidence` field. The documented three-tier routing behavior doesn't exist in code.

---

## Approach Chosen

**Option A — routing decision lives inside `create_jira_ticket`, not as a separate LLM-facing tool.**

The LLM always calls `create_jira_ticket` with a self-assessed `confidence`. The routing tier (`auto_act` / `flag` / `escalate`) is computed in code from that value, and only `flag`/`auto_act` actually reach the Jira MCP call. `escalate` is intercepted before any Jira call and redirected to a new escalation path.

Why not Option B (a separate `propose_ticket` tool the LLM chooses to call instead of `create_jira_ticket` when unsure): that leaves the routing decision itself up to the LLM's own judgment again — exactly the ungoverned behavior this feature exists to remove. Keeping one tool with a code-enforced gate makes the boundary testable and deterministic.

---

## Components

### New Files

| File | Purpose |
|---|---|
| `pipeline/confidence_router.py` | Pure `route_confidence()` — confidence + two thresholds → tier |
| `pipeline/pending_confirmation_store.py` | `PendingConfirmation` / `PendingConfirmationStore` dataclasses + load/save (mirrors `episode_store.py`) |
| `pipeline/confirmation_resolver.py` | `is_affirmative()`, `resolve_pending_confirmations()` — checks Slack replies, files or re-classifies |
| `agents/triage/tools/confirmation_tools.py` | `escalate_for_confirmation()` — posts the proposal to Slack, persists the pending record |

### Modified Files

| File | What changes |
|---|---|
| `agents/triage/tools/jira_tools.py` | Add `confidence`/`reasoning` to schema; extract `_create_ticket_in_jira()` helper from the existing MCP call; `create_jira_ticket()` now routes before calling it |
| `pipeline/slack_reader.py` | Add `fetch_thread_replies(channel_id, thread_ts)` — same MCP session pattern as `fetch_messages()`, calls `slack_get_thread_replies` |
| `agents/triage/triage_agent.py` | System prompt explains confidence + the `[ESCALATED]` convention; `_execute_tool()` threads `block_context` through to `create_jira_ticket` only; `run()` calls `resolve_pending_confirmations()` once per cycle before processing new Slack messages |
| `config/settings.py` | Add `PENDING_CONFIRMATION_STORE_PATH`, `PENDING_CONFIRMATION_MAX_AGE_HOURS` |

---

## Data Contracts

### `create_jira_ticket()` — new signature

```
async def create_jira_ticket(
    summary: str,
    issue_type: str,
    priority: str,
    description: str,
    labels: list[str] | None = None,
    confidence: float = 1.0,
    reasoning: str = "",
    block_context: dict | None = None,
) -> str
```

- `auto_act` → unchanged behavior, e.g. `"Created SCRUM-3: Fix login crash → https://..."`
- `flag` → same, plus `" (flagged for review — confidence 0.78)"`, and `needs-review` appended to labels
- `escalate` → **no Jira call**. Returns `"[ESCALATED] Low confidence (0.52) — posted for human confirmation. Do not post an additional confirmation message."` The `[ESCALATED]` prefix tells the LLM (per updated system prompt) not to also call `post_slack_message` — the escalation path already posted.

### `block_context` — why it exists

`_execute_tool()` runs inside the per-block loop in `_run_llm_loop()`, which already has `run_id`, `block_index`, and `block_snippet` in scope. Rather than a module-level side-channel (the project's own retrospective explicitly flags this as a worse pattern — see `docs/ENGINEERING_PROCESS.md`), `block_context = {"run_id", "block_index", "block_snippet"}` is passed explicitly into `create_jira_ticket` only, since it's the only tool that needs it (to persist a `PendingConfirmation` with enough context to re-classify later).

### `PendingConfirmation`

```
run_id: str
block_index: int
block_snippet: str
proposed_summary: str
proposed_issue_type: str
proposed_priority: str
proposed_description: str
proposed_labels: list[str]
confidence: float
reasoning: str
channel_id: str
proposal_ts: str        # ts of the Slack message asking for confirmation
created_at: str          # ISO timestamp
status: str = "pending"  # "pending" | "resolved"
```

### `resolve_pending_confirmations()` — per pending item

1. `fetch_thread_replies(channel_id, proposal_ts)` — everything in the thread except the message matching `proposal_ts` itself is treated as human input; the latest one wins.
2. No replies and age < `PENDING_CONFIRMATION_MAX_AGE_HOURS` → leave pending, no action.
3. No replies and age ≥ max age → file with the originally proposed fields via `_create_ticket_in_jira()`, post a confirmation noting it was auto-filed after no response, mark resolved.
4. Reply is affirmative (`is_affirmative()`) → file with the originally proposed fields, post confirmation, mark resolved.
5. Reply is a correction → one LLM call (direct `_provider.chat()`, not the full tool loop) with the original block snippet + proposed fields + the human's reply, asking for corrected `issue_type`/`priority`/`summary`/`description`/`labels` as JSON. File with the corrected fields, post confirmation noting it was adjusted per feedback, mark resolved.

---

## Decisions Made This Session

- Routing lives inside `create_jira_ticket`, not a separate LLM-chosen tool (keeps the gate code-enforced, not prompt-compliance-dependent)
- `block_context` passed explicitly, not via module-level state (matches the project's own documented preference)
- One correction round, not an open-ended negotiation loop (out of scope per brainstorm)
- Max-age auto-file as the safety net, consistent with the project's "never fails silently" principle
