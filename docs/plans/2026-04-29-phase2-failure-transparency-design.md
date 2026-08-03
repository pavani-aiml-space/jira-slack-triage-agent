# Technical Design: Phase 2 — Failure Transparency

> Status: Draft — pending approval
> Date: 2026-04-29

---

## Problem (from brainstorm)

Phase 1 has no error handling for external service failures — Jira down, OpenAI down, or Slack MCP failing mid-run all result in silent exits, violating Priority Rules 1, 5, and 6.

---

## Approach Chosen

**Option A — Error handling at each failure site.**

Three targeted try/except blocks, one per Priority Rule:
- Rule 1 (Jira): caught inside `create_jira_ticket()` — closest to the failure, posts Slack alert, returns error string to LLM, continues
- Rule 6 (OpenAI): caught in `run()` wrapping `_run_llm_loop()` — posts Slack alert, exits with code 1
- Rule 5 (Slack MCP mid-run): caught per block in `run()` — accumulates errors, continues all blocks, consolidated post at end

Why not Option B (all errors bubble to `run()`): `run()` cannot distinguish a Jira error from a Slack error without custom exception types. Each rule requires distinct behavior — accumulate vs exit vs continue — so individual handlers are clearest and directly testable.

---

## Components

### Code Diagram
See: [docs/diagrams/2026-04-29-phase2-failure-transparency.md](../diagrams/2026-04-29-phase2-failure-transparency.md)

### New Files

None. All changes are targeted modifications to three existing files.

### Modified Files

| File | What changes |
|---|---|
| `agents/triage/tools/jira_tools.py` | Add try/except inside `create_jira_ticket()` around MCP call. On failure: call `post_slack_message()`, return error string to LLM. |
| `agents/triage/triage_agent.py` | Add per-block try/except in `run()` for OpenAI errors (exit) and Slack MCP errors (accumulate). Add consolidated Slack post at end of run. Add `sys` import. |
| `agents/triage/tools/slack_tools.py` | No functional changes. `post_slack_message()` reused as-is. |

---

## Data Contracts

### `create_jira_ticket()` — modified return on failure

```
Signature (unchanged):
  async def create_jira_ticket(
      summary: str,
      issue_type: str,
      priority: str,
      description: str,
      labels: list[str] | None = None,
  ) -> str

Success return (unchanged):
  "Created SCRUM-3: Fix login crash → https://..."

Failure return (new):
  "[JIRA_ERROR] Jira unavailable — team notified in Slack. Do not send additional notification. Summary was: {summary}"
```

The `[JIRA_ERROR]` prefix in the return string tells the LLM the team has already been notified — prevents a second Slack post from the LLM.

### `run()` — new internal state

```
slack_errors: list[str] = []
  Each entry: f"Block '{block_text[:60]}...': {str(exception)}"
  Populated when: Slack MCP raises during _run_llm_loop() for a block
  Used when: posting consolidated error summary at end of run
```

### Consolidated error Slack message format

```
⚠️ Agent run completed with Slack notification failures.
The following {N} block(s) could not be posted to Slack:

1. Block: "{block_text[:60]}..."
   Error: {error_detail}

Please manually review these messages and confirm ticket status.
```

---

## External Calls

### Jira MCP — `jira_mcp_session()` in `create_jira_ticket()`

| | |
|---|---|
| **Service** | `uvx mcp-atlassian` stdio subprocess |
| **Call** | `session.call_tool("jira_create_issue", {...})` |
| **Auth** | `--jira-url`, `--jira-username`, `--jira-token` CLI args |
| **Success** | Returns content blocks with `{"issue": {"key": "SCRUM-X"}}` |
| **On any exception** | Caught in `create_jira_ticket()` try/except → calls `post_slack_message()` → returns error string |

### OpenAI API — `_client.chat.completions.create()` in `_run_llm_loop()`

| | |
|---|---|
| **Service** | OpenAI HTTPS API |
| **Call** | `POST /v1/chat/completions` with model, tools, messages |
| **Auth** | `OPENAI_API_KEY` |
| **Success** | Returns `ChatCompletion` with `choices[0]` |
| **On `openai.APIError`** | Propagates out of `_run_llm_loop()`, caught in `run()` → posts Slack alert → `sys.exit(1)` |

### Slack MCP — `slack_mcp_session()` in `post_slack_message()` / `ask_for_clarification()`

| | |
|---|---|
| **Service** | `npx @modelcontextprotocol/server-slack` stdio subprocess |
| **Call** | `session.call_tool("slack_post_message", {channel_id, text})` |
| **Auth** | `SLACK_BOT_TOKEN` env var |
| **Success** | Returns confirmation |
| **On exception mid-run** | Propagates from tool executor → `_execute_tool()` → `_run_llm_loop()` → caught per block in `run()` → added to `slack_errors` |
| **On exception at end-of-run consolidated post** | Caught in `run()` final try/except → writes to stdout → `sys.exit(1)` |

---

## Failure Modes

### Jira down (Rule 1)

| Scenario | Behaviour |
|---|---|
| MCP subprocess fails to start | `create_jira_ticket()` catches, calls `post_slack_message("⚠️ Jira unavailable...")`, returns `[JIRA_ERROR]` string |
| `call_tool` raises mid-execution | Same |
| Jira auth failure (401 from mcp-atlassian) | Same |
| Jira down AND Slack also down | `post_slack_message()` raises inside `create_jira_ticket()`, propagates to per-block `except Exception` in `run()`, added to `slack_errors`, processed at end |
| **Priority Rule satisfied** | Rule 1: never silent ✓ |

### OpenAI down (Rule 6)

| Scenario | Behaviour |
|---|---|
| `openai.APIConnectionError` | Propagates to `run()` → `except openai.APIError` → tries `post_slack_message("⚠️ OpenAI unavailable — please triage {channel} manually: {error}")` → `sys.exit(1)` |
| `openai.AuthenticationError` | Same (subclass of `openai.APIError`) |
| `openai.RateLimitError` | Same |
| OpenAI down AND Slack also down | `post_slack_message()` raises inside OpenAI handler → caught in inner try/except → writes full error to stdout → `sys.exit(1)` |
| **Priority Rule satisfied** | Rule 6: fail loudly ✓ |

### Slack MCP fails mid-run (Rule 5)

| Scenario | Behaviour |
|---|---|
| `post_slack_message()` raises during block processing | Propagates to `run()` → `except Exception` → appended to `slack_errors` with block snippet → loop continues |
| `ask_for_clarification()` raises | Same |
| All blocks processed — consolidated post fails | Caught in final try/except in `run()` → writes consolidated summary to stdout → `sys.exit(1)` |
| **Priority Rule satisfied** | Rule 5: continue and report ✓ |

### Exception ordering in `run()` per-block try/except

OpenAI errors are caught first (more specific), Slack/other errors second (broad):
```
try:
    await _run_llm_loop(block["combined_text"])
except openai.APIError as e:
    # Rule 6 — OpenAI down, exit the run
    ...
    sys.exit(1)
except Exception as e:
    # Rule 5 — Slack MCP or other transient error, continue
    slack_errors.append(...)
```

This ordering ensures OpenAI failures are never silently swallowed by the broad `except Exception`.

---

## Example Error Messages

### Jira down — Slack alert posted by `create_jira_ticket()`

```
⚠️ Jira unavailable — please create this ticket manually:
  Summary: Fix login crash on empty password
  Type: Bug | Priority: High
  Error: Connection refused (uvx mcp-atlassian subprocess failed to start)
```

Return string to LLM:
```
[JIRA_ERROR] Jira unavailable — team notified in Slack. Do not send additional notification. Summary was: Fix login crash on empty password
```

---

### OpenAI down — Slack alert posted by `run()`

```
⚠️ OpenAI API unavailable — triage agent has stopped.
Please triage #eng-bugs manually or retry in a few minutes.
Error: openai.APIConnectionError: Connection error. (hint: check OPENAI_API_KEY and network)
```

Stdout fallback (if Slack is also down):
```
[TRIAGE AGENT FATAL] OpenAI unavailable AND Slack unreachable.
Error: openai.APIConnectionError: ...
Slack error: subprocess.CalledProcessError: npx server-slack exited with code 1
Please triage manually.
```

---

### Slack MCP mid-run — consolidated Slack post at end of `run()`

```
⚠️ Agent run completed with Slack notification failures.
The following 2 block(s) could not be posted to Slack:

1. Block: "Login is broken again, same issue as last week..."
   Error: BrokenPipeError: [Errno 32] Broken pipe (npx server-slack subprocess died)

2. Block: "Dashboard charts not loading for enterprise accounts..."
   Error: BrokenPipeError: [Errno 32] Broken pipe

Please manually confirm ticket status for the blocks above.
```

Stdout fallback (if consolidated post itself fails):
```
[TRIAGE AGENT ERROR] Run completed but Slack notifications failed for 2 block(s).
Block 1: "Login is broken again..." — BrokenPipeError: [Errno 32] Broken pipe
Block 2: "Dashboard charts not loading..." — BrokenPipeError: [Errno 32] Broken pipe
Slack post also failed: subprocess.CalledProcessError: npx server-slack exited with code 1
Please triage manually.
```

---

## Out of Scope

- Retry logic with exponential backoff — post error and stop/continue is sufficient for Phase 2; retries deferred to Phase 5 (Reliability)
- Structured log files — Phase 3 (Observability) introduces structured logging; Phase 2 error strings are human-readable only
- Alerting on repeated failures across multiple runs — Phase 3
- Network timeout configuration — use MCP subprocess defaults
- Handling partial writes (e.g. Jira ticket created but `key` parsing fails) — treat as a success, no error handling needed for partial parses

---

## Open Questions Resolved

| Question | Answer |
|---|---|
| Jira failure: stop the run or continue? | **Continue** — Rule 1 explicitly says never fail silently but keep going. Other blocks may be independent bugs. |
| OpenAI failure: continue or stop? | **Stop** — Rule 6 says fail loudly and exit. Without the LLM, every remaining block is unprocessable — continuing would be pointless and misleading. |
| Slack down at end-of-run consolidated post? | Write to stdout, exit 1. This is the only acceptable case where Slack notification is skipped. |
| Will LLM double-post to Slack after `[JIRA_ERROR]` return? | Prevented by `[JIRA_ERROR]` prefix in return string which explicitly says "Do not send additional notification." LLM reads this as a tool result and will not post a duplicate. |
| Should `jira_tools.py` import from `slack_tools.py`? | Yes — no circular dependency. `slack_tools.py` imports from `pipeline.slack_reader` and `config.settings`; it does not import from `jira_tools.py`. |
| Where exactly does OpenAI error get caught — `_run_llm_loop()` or `run()`? | `run()`. `_run_llm_loop()` propagates it naturally. Catching it in `run()` allows `sys.exit(1)` at the top level, which is cleaner than exiting from inside a loop helper. |
