# Implementation Plan: Confidence-Based Routing

> Status: Draft — pending approval
> Date: 2026-08-03

---

## Build Order

1. `config/settings.py` — add two new settings
2. `pipeline/confidence_router.py` — pure function, tests first
3. `pipeline/pending_confirmation_store.py` — dataclasses + I/O, tests
4. `pipeline/slack_reader.py` — `fetch_thread_replies()`, tests
5. `agents/triage/tools/jira_tools.py` — schema fields, `_create_ticket_in_jira()` extraction, routing in `create_jira_ticket()`, tests
6. `agents/triage/tools/confirmation_tools.py` — `escalate_for_confirmation()`, tests
7. `pipeline/confirmation_resolver.py` — `is_affirmative()`, `resolve_pending_confirmations()`, tests
8. `agents/triage/triage_agent.py` — wire `block_context` through `_execute_tool`, call `resolve_pending_confirmations()` in `run()`, update `SYSTEM_PROMPT`
9. Full test suite run
10. Update README / `ARCHITECTURE.md` diagrams to match the now-real behavior

---

## File-by-File

### `config/settings.py`
```
PENDING_CONFIRMATION_STORE_PATH: str = os.getenv("PENDING_CONFIRMATION_STORE_PATH", "memory/pending_confirmations.json")
PENDING_CONFIRMATION_MAX_AGE_HOURS: int = int(os.getenv("PENDING_CONFIRMATION_MAX_AGE_HOURS", "72"))
```

### `pipeline/confidence_router.py`
- `route_confidence(confidence: float, auto_act_threshold: float, ask_human_threshold: float) -> Literal["auto_act", "flag", "escalate"]`
- Tests: exactly at each threshold (boundary is inclusive on the upper tier), just above/below, 0.0, 1.0, invalid range not raised (caller's responsibility, pure function trusts input)

### `pipeline/pending_confirmation_store.py`
- `PendingConfirmation` dataclass (see design doc)
- `PendingConfirmationStore` dataclass, `items: list[PendingConfirmation]`
- `load_pending_store(path) -> PendingConfirmationStore` — never raises, same pattern as `episode_store.load_episode_store`
- `save_pending_store(store, path) -> None` — never raises
- `add_pending(store, item) -> None`
- `mark_resolved(store, proposal_ts) -> None` — removes the item with matching `proposal_ts`
- Tests: round-trip save/load, missing file, corrupt JSON, add + mark_resolved removes correct item

### `pipeline/slack_reader.py`
- `fetch_thread_replies(channel_id: str, thread_ts: str) -> list[dict]` — same shape as `fetch_messages`, calls `slack_get_thread_replies` via `slack_mcp_session()`
- Tests: mocked MCP response → parsed list; excludes the root message (ts == thread_ts); empty thread → `[]`

### `agents/triage/tools/jira_tools.py`
- Schema: add `confidence` (required, number) and `reasoning` (optional, string)
- Extract `_create_ticket_in_jira(summary, issue_type, priority, description, labels)` — the existing MCP call + Jira-down handling, unchanged logic
- `create_jira_ticket(..., confidence=1.0, reasoning="", block_context=None)`:
  - `escalate` → delegate to `confirmation_tools.escalate_for_confirmation(...)`, return its result directly
  - `flag` → append `needs-review` to labels, call `_create_ticket_in_jira`, append confidence note to result
  - `auto_act` → call `_create_ticket_in_jira` unchanged
- Tests: auto_act unchanged behavior preserved (existing tests), flag adds label + note, escalate never calls `_create_ticket_in_jira` (assert via mock not called), escalate delegates with correct args

### `agents/triage/tools/confirmation_tools.py`
- `escalate_for_confirmation(summary, issue_type, priority, description, labels, confidence, reasoning, block_context) -> str`
  - Posts to Slack via `slack_mcp_session()` (same pattern as `slack_tools.post_slack_message`), message includes proposed summary/type/priority/reasoning/confidence and asks for confirmation or corrections
  - Parses `ts` from the MCP response
  - Builds `PendingConfirmation` from args + `block_context`, loads store, `add_pending`, saves store
  - Returns `"[ESCALATED] ..."` string
- Tests: posts expected message content, persists correct `PendingConfirmation` fields, handles missing `block_context` gracefully (defaults for run_id/block_index/block_snippet), Slack failure doesn't raise (Rule 5 — never fail silently, log and return an error string)

### `pipeline/confirmation_resolver.py`
- `is_affirmative(text: str) -> bool` — normalized membership check against a fixed set
- `resolve_pending_confirmations(store: PendingConfirmationStore, max_age_hours: int) -> PendingConfirmationStore`
  - For each pending item: fetch replies, branch per design doc's 5 cases, mutate/return updated store
- Tests: affirmative reply files as-proposed, correction reply triggers one LLM re-classify call and files corrected fields, no reply within age window leaves pending untouched, no reply past max age auto-files, Jira failure during resolution doesn't crash the loop (continues to next pending item)

### `agents/triage/triage_agent.py`
- `SYSTEM_PROMPT`: add confidence self-assessment instructions + the `[ESCALATED]` no-double-post rule
- `_execute_tool(tool_name, tool_args, block_context)`: special-case `create_jira_ticket` to receive `block_context`; all other tools unchanged
- `_run_llm_loop`: pass `block_context = {"run_id": ..., "block_index": ..., "block_snippet": ...}` into `_execute_tool`
- `run()`: call `resolve_pending_confirmations()` once per cycle, before reading new Slack messages, load/save the pending store around it
- Tests: existing tool-loop tests updated for the new `_execute_tool` signature; new test confirms `resolve_pending_confirmations` is called before `fetch_messages` in `run()`

---

## Definition of Done

- All new pure/unit-testable logic has tests (target: 30+ new tests)
- `pytest tests/unit/ -q` — 275 existing + new tests, all green
- README's "How it works" diagram and `ARCHITECTURE.md`'s end-to-end diagram updated to reflect this real mechanism (replacing the previously-aspirational confidence gate description)
- No secrets, no hardcoded credentials in any new file
