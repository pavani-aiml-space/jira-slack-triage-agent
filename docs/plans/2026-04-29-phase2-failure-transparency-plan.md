# Implementation Plan: Phase 2 — Failure Transparency

> Status: Draft — pending approval
> Date: 2026-04-29
> Design doc: [2026-04-29-phase2-failure-transparency-design.md](2026-04-29-phase2-failure-transparency-design.md)
> Diagram: [2026-04-29-phase2-failure-transparency.md](../diagrams/2026-04-29-phase2-failure-transparency.md)

---

## Goal

Add three targeted error handlers — one per Priority Rule — so the agent never exits silently when Jira, OpenAI, or the Slack MCP fails.

## Architecture

Three try/except blocks added to two existing files. No new files, no new tools, no schema changes.
- Rule 1 (Jira): caught inside `create_jira_ticket()` — posts Slack alert, returns `[JIRA_ERROR]` string to LLM, run continues.
- Rule 6 (OpenAI): caught in `run()` per block — posts Slack alert, `sys.exit(1)`.
- Rule 5 (Slack MCP): caught in `run()` per block — accumulates errors, consolidated post at end; stdout fallback if Slack also down.

## Files Affected

| File | Change |
|---|---|
| `agents/triage/tools/jira_tools.py` | Add try/except in `create_jira_ticket()` |
| `agents/triage/triage_agent.py` | Add `slack_errors` list, per-block error handlers, consolidated post, `import sys` |
| `tests/unit/test_jira_tools.py` | Add 2 new test cases |
| `tests/unit/test_triage_agent.py` | Add 5 new test cases |

---

## Block 1 — Jira Error Handler (Rule 1)

### Chunk 1.1 — try/except in `create_jira_ticket()`
```
Test layer: UNIT
Files:
  Modify: agents/triage/tools/jira_tools.py
Test file: tests/unit/test_jira_tools.py
```

**Step 1 (RED) — Write these failing tests:**
```python
# tests/unit/test_jira_tools.py

@pytest.mark.asyncio
async def test_create_jira_ticket_jira_mcp_unavailable_posts_slack_alert():
    """When Jira MCP raises, post_slack_message is called with the alert."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("uvx subprocess failed")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock) as mock_post:
            result = await create_jira_ticket(
                summary="Fix login crash",
                issue_type="Bug",
                priority="High",
                description="Crashes on empty password",
            )
    mock_post.assert_called_once()
    assert "Jira unavailable" in mock_post.call_args[0][0]
    assert "Fix login crash" in mock_post.call_args[0][0]


@pytest.mark.asyncio
async def test_create_jira_ticket_jira_mcp_unavailable_returns_jira_error_string():
    """When Jira MCP raises, return value starts with [JIRA_ERROR]."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("uvx subprocess failed")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   new_callable=AsyncMock):
            result = await create_jira_ticket(
                summary="Fix login crash",
                issue_type="Bug",
                priority="High",
                description="Crashes on empty password",
            )
    assert result.startswith("[JIRA_ERROR]")
    assert "Fix login crash" in result


@pytest.mark.asyncio
async def test_create_jira_ticket_jira_and_slack_both_down_propagates():
    """When both Jira and Slack MCP fail, exception propagates to caller."""
    with patch("agents.triage.tools.jira_tools.jira_mcp_session",
               side_effect=Exception("Jira down")):
        with patch("agents.triage.tools.jira_tools.post_slack_message",
                   side_effect=Exception("Slack also down")):
            with pytest.raises(Exception, match="Slack also down"):
                await create_jira_ticket(
                    summary="Fix login crash",
                    issue_type="Bug",
                    priority="High",
                    description="desc",
                )
```
Run: `pytest tests/unit/test_jira_tools.py -v`
Expect: `FAILED` — `AttributeError: post_slack_message` (not yet imported/used in error path)

**Step 2 (GREEN) — Minimal implementation:**

In `agents/triage/tools/jira_tools.py`:
1. Add `from agents.triage.tools.slack_tools import post_slack_message` at top of file
2. Wrap the `async with jira_mcp_session()` block in `create_jira_ticket()` in try/except:
   - On exception: `await post_slack_message(f"⚠️ Jira unavailable — ...")`, return `[JIRA_ERROR]` string
   - Let any exception from `post_slack_message()` itself propagate (cascade handled by Rule 5)

Run: `pytest tests/unit/test_jira_tools.py -v`
Expect: `PASSED`

**Step 3 (REFACTOR):**
- Error message format: consistent with example in design doc (`⚠️ Jira unavailable — please create this ticket manually:\n  Summary: {summary}\n  Error: {e}`)
- `[JIRA_ERROR]` return string: include `Do not send additional notification` so LLM doesn't double-post

Run: `pytest tests/unit/test_jira_tools.py -v`
Expect: still `PASSED`

**Step 4 (COMMIT):**
```bash
git add agents/triage/tools/jira_tools.py tests/unit/test_jira_tools.py
git commit -m "[Add] Rule 1 — Jira error handler in create_jira_ticket()"
```

---

## Block 2 — OpenAI Error Handler (Rule 6)

### Chunk 2.1 — `openai.APIError` catch in `run()` with stdout fallback
```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py  (add import sys, import openai)
Test file: tests/unit/test_triage_agent.py
```

**Step 1 (RED) — Write these failing tests:**
```python
# tests/unit/test_triage_agent.py

import openai
import triage_agent as triage_agent_module
from agents.triage.triage_agent import run

@pytest.mark.asyncio
async def test_run_openai_error_posts_slack_alert_and_exits():
    """When OpenAI raises APIError, post_slack_message is called and process exits."""
    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}]):
        with patch("agents.triage.triage_agent.build_context_blocks",
                   return_value=[{"combined_text": "bug report", "start_ts": "1.0", "end_ts": "1.0"}]):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock,
                       side_effect=openai.APIConnectionError("Connection failed")):
                with patch("agents.triage.triage_agent.post_slack_message",
                           new_callable=AsyncMock) as mock_post:
                    with pytest.raises(SystemExit) as exc_info:
                        await run()
    assert exc_info.value.code == 1
    mock_post.assert_called_once()
    call_text = mock_post.call_args[0][0]
    assert "OpenAI" in call_text


@pytest.mark.asyncio
async def test_run_openai_error_slack_also_down_writes_stdout(capsys):
    """When OpenAI and Slack both fail, error is written to stdout and process exits."""
    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}]):
        with patch("agents.triage.triage_agent.build_context_blocks",
                   return_value=[{"combined_text": "bug report", "start_ts": "1.0", "end_ts": "1.0"}]):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock,
                       side_effect=openai.APIConnectionError("Connection failed")):
                with patch("agents.triage.triage_agent.post_slack_message",
                           new_callable=AsyncMock,
                           side_effect=Exception("Slack also down")):
                    with pytest.raises(SystemExit) as exc_info:
                        await run()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "TRIAGE AGENT FATAL" in captured.out
```
Run: `pytest tests/unit/test_triage_agent.py::test_run_openai_error_posts_slack_alert_and_exits tests/unit/test_triage_agent.py::test_run_openai_error_slack_also_down_writes_stdout -v`
Expect: `FAILED` — `SystemExit` not raised (no handler exists yet)

**Step 2 (GREEN) — Minimal implementation:**

In `agents/triage/triage_agent.py`:
1. Add `import sys` and `import openai` at top
2. Wrap `await _run_llm_loop(block["combined_text"])` in the for-loop with:
   ```
   except openai.APIError as e:
       try:
           await post_slack_message(f"⚠️ OpenAI unavailable — ...")
       except Exception as slack_err:
           print(f"[TRIAGE AGENT FATAL] ...")
       sys.exit(1)
   ```

Run: same two tests
Expect: `PASSED`

**Step 3 (REFACTOR):**
- Error message: consistent with design doc example (include specific error, instruct team to triage manually or retry)
- Stdout fallback format: `[TRIAGE AGENT FATAL]` prefix + OpenAI error + Slack error on separate lines

Run: same tests
Expect: still `PASSED`

**Step 4 (COMMIT):**
```bash
git add agents/triage/triage_agent.py tests/unit/test_triage_agent.py
git commit -m "[Add] Rule 6 — OpenAI error handler in run() with stdout fallback"
```

---

## Block 3 — Slack MCP Accumulator (Rule 5)

### Chunk 3.1 — Per-block `Exception` accumulator in `run()`
```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
Test file: tests/unit/test_triage_agent.py
```

**Step 1 (RED) — Write these failing tests:**
```python
@pytest.mark.asyncio
async def test_run_slack_error_continues_to_next_block():
    """When Slack MCP raises on block 1, block 2 is still processed."""
    call_count = 0
    async def llm_loop_fail_first(block_text):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Slack MCP broken pipe")

    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "bug1", "ts": "1.0"},
                              {"user": "U2", "text": "bug2", "ts": "2.0"}]):
        with patch("agents.triage.triage_agent.build_context_blocks",
                   return_value=[{"combined_text": "bug1", "start_ts": "1.0", "end_ts": "1.0"},
                                  {"combined_text": "bug2", "start_ts": "2.0", "end_ts": "2.0"}]):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock,
                       side_effect=llm_loop_fail_first):
                with patch("agents.triage.triage_agent.post_slack_message",
                           new_callable=AsyncMock):
                    await run()

    assert call_count == 2  # both blocks attempted


@pytest.mark.asyncio
async def test_run_slack_error_does_not_swallow_openai_error():
    """openai.APIError is NOT caught by the broad except Exception handler."""
    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}]):
        with patch("agents.triage.triage_agent.build_context_blocks",
                   return_value=[{"combined_text": "bug", "start_ts": "1.0", "end_ts": "1.0"}]):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock,
                       side_effect=openai.APIConnectionError("down")):
                with patch("agents.triage.triage_agent.post_slack_message",
                           new_callable=AsyncMock):
                    with pytest.raises(SystemExit):
                        await run()
    # If we reach here via SystemExit, APIError was handled by Rule 6, not swallowed by Rule 5
```
Run: `pytest tests/unit/test_triage_agent.py::test_run_slack_error_continues_to_next_block tests/unit/test_triage_agent.py::test_run_slack_error_does_not_swallow_openai_error -v`
Expect: `FAILED` — `Exception` not caught per block, run aborts after first error

**Step 2 (GREEN) — Minimal implementation:**

In `agents/triage/triage_agent.py`, add below the `except openai.APIError` block:
```
except Exception as e:
    slack_errors.append(f"Block '{block['combined_text'][:60]}...': {str(e)}")
    continue
```
And initialise `slack_errors: list[str] = []` at the top of `run()`.

Run: same two tests
Expect: `PASSED`

**Step 3 (REFACTOR):**
- Block snippet: truncate at 60 chars with `...` for readable error messages
- Naming: `slack_errors` is clear — no rename needed

Run: same tests
Expect: still `PASSED`

**Step 4 (COMMIT):**
```bash
git add agents/triage/triage_agent.py tests/unit/test_triage_agent.py
git commit -m "[Add] Rule 5 — per-block Slack error accumulator in run()"
```

---

### Chunk 3.2 — Consolidated Slack post at end of `run()` with stdout fallback
```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
Test file: tests/unit/test_triage_agent.py
```

**Step 1 (RED) — Write these failing tests:**
```python
@pytest.mark.asyncio
async def test_run_consolidated_error_post_when_slack_errors():
    """When blocks fail with Slack errors, consolidated post is sent at end of run."""
    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}]):
        with patch("agents.triage.triage_agent.build_context_blocks",
                   return_value=[{"combined_text": "login is broken", "start_ts": "1.0", "end_ts": "1.0"}]):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock,
                       side_effect=Exception("Slack MCP pipe broken")):
                with patch("agents.triage.triage_agent.post_slack_message",
                           new_callable=AsyncMock) as mock_post:
                    await run()

    mock_post.assert_called_once()
    call_text = mock_post.call_args[0][0]
    assert "⚠️" in call_text
    assert "1" in call_text  # 1 failed block
    assert "login is broken" in call_text  # block snippet included


@pytest.mark.asyncio
async def test_run_consolidated_post_fails_writes_stdout(capsys):
    """When consolidated post also fails, error is written to stdout and process exits."""
    slack_post_calls = 0
    async def post_always_fails(msg):
        nonlocal slack_post_calls
        slack_post_calls += 1
        raise Exception("Slack completely down")

    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}]):
        with patch("agents.triage.triage_agent.build_context_blocks",
                   return_value=[{"combined_text": "login is broken", "start_ts": "1.0", "end_ts": "1.0"}]):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock,
                       side_effect=Exception("Slack MCP pipe broken")):
                with patch("agents.triage.triage_agent.post_slack_message",
                           new_callable=AsyncMock,
                           side_effect=post_always_fails):
                    with pytest.raises(SystemExit) as exc_info:
                        await run()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "TRIAGE AGENT ERROR" in captured.out
    assert "login is broken" in captured.out
```
Run: `pytest tests/unit/test_triage_agent.py::test_run_consolidated_error_post_when_slack_errors tests/unit/test_triage_agent.py::test_run_consolidated_post_fails_writes_stdout -v`
Expect: `FAILED` — no consolidated post logic exists yet

**Step 2 (GREEN) — Minimal implementation:**

In `agents/triage/triage_agent.py`, at the end of `run()` after the for loop:
```
if slack_errors:
    summary = build_consolidated_error_message(slack_errors)
    try:
        await post_slack_message(summary)
    except Exception as e:
        print(f"[TRIAGE AGENT ERROR] ...")
        sys.exit(1)
```
Build the consolidated message inline (no need for a separate function at this stage).

Run: same two tests
Expect: `PASSED`

**Step 3 (REFACTOR):**
- Message format: matches design doc example — `⚠️ Agent run completed with N Slack notification failure(s).\n\n` + numbered list of failed blocks
- Stdout format: `[TRIAGE AGENT ERROR]` prefix + list of failed blocks + Slack post error

Run: same tests
Expect: still `PASSED`

**Step 4 (COMMIT):**
```bash
git add agents/triage/triage_agent.py tests/unit/test_triage_agent.py
git commit -m "[Add] Rule 5 — consolidated Slack error post at end of run() with stdout fallback"
```

---

## Final: Full Test Run

After all chunks, run the complete suite to confirm no regressions:
```bash
pytest tests/unit/ -v
```
Expect: all existing tests pass + all 8 new tests pass.

---

## Success Criteria

- [ ] Silent failure rate = 0 — verified by unit tests for each failure mode
- [ ] Jira failure: Slack alert posted and `[JIRA_ERROR]` returned — verified by Chunk 1.1 tests
- [ ] OpenAI failure: Slack alert posted + `sys.exit(1)` — verified by Chunk 2.1 tests
- [ ] OpenAI + Slack both down: stdout output + `sys.exit(1)` — verified by Chunk 2.1 tests
- [ ] Slack MCP failure: remaining blocks still processed — verified by Chunk 3.1 tests
- [ ] `openai.APIError` not swallowed by broad `except Exception` — verified by Chunk 3.1 test
- [ ] Consolidated error post at end of run — verified by Chunk 3.2 tests
- [ ] Consolidated post also fails: stdout output + `sys.exit(1)` — verified by Chunk 3.2 tests
- [ ] All existing 47 unit tests still pass — verified by final full test run

---

## Known Technical Debt

- None introduced. `sys.exit(1)` is acceptable for an agent entry point — if this later runs inside a scheduler or server, replace with raising a custom exception instead of calling sys.exit directly.
