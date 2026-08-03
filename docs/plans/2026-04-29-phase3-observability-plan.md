# Implementation Plan: Phase 3 — Observability

> Input docs:
> - Brainstorm: `docs/plans/2026-04-29-phase3-observability-brainstorm.md`
> - Design: `docs/plans/2026-04-29-phase3-observability-design.md`
> - Diagram: `docs/diagrams/2026-04-29-phase3-observability.md`

---

## Goal

Add a JSON run log, Streamlit dashboard, Slack end-of-run summary, and structured stdout output so the operator can see exactly what every agent run did — without reading raw terminal output.

## Architecture

`run_logger.py` (new service) owns all log I/O. `_run_llm_loop()` returns a `BlockResult` instead of `None`, giving `run()` a structured result per block to accumulate into a `RunLog`. `run_triage.py` manages the sentinel file that signals run state to the dashboard. `dashboard.py` reads logs and triggers runs via subprocess.

## Files Affected

| File | Action |
|------|--------|
| `agents/services/run_logger.py` | CREATE — dataclasses + write/load |
| `dashboard.py` | CREATE — Streamlit UI |
| `agents/triage/triage_agent.py` | MODIFY — `_run_llm_loop` return type, `run()` builds RunLog, 4 new helpers |
| `run_triage.py` | MODIFY — sentinel create/delete in finally |
| `config/settings.py` | MODIFY — add `LOG_DIR` |
| `.gitignore` | MODIFY — add `logs/` |
| `tests/unit/test_run_logger.py` | CREATE |
| `tests/unit/test_triage_agent.py` | MODIFY — update mocks for new `_run_llm_loop` signature |

---

## Block 1 — Data Layer: `run_logger.py`

### Chunk 1.1 — `write_run_log()`: dataclasses + JSON write

```
Test layer : UNIT
Create     : agents/services/run_logger.py
Create     : tests/unit/test_run_logger.py
```

**Step 1 (RED) — Write this failing test:**
```python
# tests/unit/test_run_logger.py
import json, os, pytest
from agents.services.run_logger import RunLog, BlockResult, write_run_log

def test_write_run_log_creates_file(tmp_path):
    log = RunLog(
        run_id="2026-04-29T13:00:00",
        started_at="2026-04-29T13:00:00",
        completed_at="2026-04-29T13:00:10",
        status="success",
        messages_fetched=5,
        blocks_processed=2,
        tickets_created_count=1,
        clarifications_asked_count=1,
        blocks_skipped_count=0,
        error_count=0,
    )
    path = write_run_log(log, log_dir=str(tmp_path))
    assert os.path.exists(path)

def test_write_run_log_is_valid_json(tmp_path):
    log = RunLog(
        run_id="2026-04-29T13:00:00", started_at="2026-04-29T13:00:00",
        completed_at="2026-04-29T13:00:10", status="success",
        messages_fetched=1, blocks_processed=1,
        tickets_created_count=1, clarifications_asked_count=0,
        blocks_skipped_count=0, error_count=0,
    )
    path = write_run_log(log, log_dir=str(tmp_path))
    with open(path) as f:
        data = json.load(f)
    assert data["run_id"] == "2026-04-29T13:00:00"
    assert data["status"] == "success"

def test_write_run_log_filename_contains_run_id(tmp_path):
    log = RunLog(
        run_id="2026-04-29T13:00:00", started_at="2026-04-29T13:00:00",
        completed_at=None, status="fatal",
        messages_fetched=0, blocks_processed=0,
        tickets_created_count=0, clarifications_asked_count=0,
        blocks_skipped_count=0, error_count=0,
    )
    path = write_run_log(log, log_dir=str(tmp_path))
    assert "2026-04-29T13" in path
```
Run: `pytest tests/unit/test_run_logger.py -v`
Expect: `FAILED — ModuleNotFoundError: No module named 'agents.services.run_logger'`

**Step 2 (GREEN) — Minimal implementation:**
```python
# agents/services/run_logger.py
from __future__ import annotations
import json, os
from dataclasses import dataclass, field, asdict
from typing import Optional

SENTINEL_FILE = "logs/.running"

@dataclass
class LlmStats:
    iterations: int
    tools_called: list[str]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int

@dataclass
class BlockResult:
    block_index: int
    block_snippet: str
    action: str  # "ticket_created" | "clarification_asked" | "error" | "skipped" | "no_action"
    ticket_key: Optional[str] = None
    ticket_summary: Optional[str] = None
    ticket_type: Optional[str] = None
    ticket_priority: Optional[str] = None
    llm: Optional[LlmStats] = None

@dataclass
class ErrorEntry:
    block_index: int
    block_snippet: str
    error_type: str
    error_message: str
    phase2_rule: str

@dataclass
class RunLog:
    run_id: str
    started_at: str
    completed_at: Optional[str]
    status: str
    messages_fetched: int
    blocks_processed: int
    tickets_created_count: int
    clarifications_asked_count: int
    blocks_skipped_count: int
    error_count: int
    blocks: list[BlockResult] = field(default_factory=list)
    errors: list[ErrorEntry] = field(default_factory=list)

def write_run_log(run_log: RunLog, log_dir: str = "logs") -> str:
    os.makedirs(log_dir, exist_ok=True)
    safe_id = run_log.run_id.replace(":", "-")
    path = os.path.join(log_dir, f"run_{safe_id}.json")
    with open(path, "w") as f:
        json.dump(asdict(run_log), f, indent=2)
    return path

def load_run_logs(log_dir: str = "logs") -> list[dict]:
    ...  # Chunk 1.2
```
Run: `pytest tests/unit/test_run_logger.py -v`
Expect: `3 passed`

**Step 3 (REFACTOR):**
- Add module docstring explaining purpose and log schema
- Ensure `safe_id` handles colons in ISO timestamps for all OS (Windows disallows `:` in filenames)

**Step 4 (COMMIT):**
```
git commit -m "[Add] run_logger.py: RunLog dataclass + write_run_log()"
```

---

### Chunk 1.2 — `load_run_logs()`: read and sort all run files

```
Test layer : UNIT
Modify     : agents/services/run_logger.py
Modify     : tests/unit/test_run_logger.py
```

**Step 1 (RED):**
```python
def test_load_run_logs_returns_empty_list_when_dir_missing():
    from agents.services.run_logger import load_run_logs
    result = load_run_logs(log_dir="/tmp/nonexistent_logs_xyz")
    assert result == []

def test_load_run_logs_returns_sorted_newest_first(tmp_path):
    from agents.services.run_logger import RunLog, write_run_log, load_run_logs
    for ts in ["2026-04-29T10:00:00", "2026-04-29T12:00:00", "2026-04-29T11:00:00"]:
        log = RunLog(run_id=ts, started_at=ts, completed_at=ts, status="success",
                     messages_fetched=1, blocks_processed=1, tickets_created_count=0,
                     clarifications_asked_count=0, blocks_skipped_count=0, error_count=0)
        write_run_log(log, log_dir=str(tmp_path))
    logs = load_run_logs(log_dir=str(tmp_path))
    assert logs[0]["run_id"] == "2026-04-29T12:00:00"
    assert logs[-1]["run_id"] == "2026-04-29T10:00:00"

def test_load_run_logs_skips_non_json_files(tmp_path):
    from agents.services.run_logger import RunLog, write_run_log, load_run_logs
    (tmp_path / ".running").write_text("")          # sentinel file — must be skipped
    (tmp_path / "notes.txt").write_text("ignore")
    log = RunLog(run_id="2026-04-29T10:00:00", started_at="2026-04-29T10:00:00",
                 completed_at="2026-04-29T10:00:05", status="success",
                 messages_fetched=1, blocks_processed=1, tickets_created_count=1,
                 clarifications_asked_count=0, blocks_skipped_count=0, error_count=0)
    write_run_log(log, log_dir=str(tmp_path))
    logs = load_run_logs(log_dir=str(tmp_path))
    assert len(logs) == 1
```
Run: `pytest tests/unit/test_run_logger.py -v`
Expect: `FAILED — load_run_logs returns ... (not implemented)`

**Step 2 (GREEN):**
```python
def load_run_logs(log_dir: str = "logs") -> list[dict]:
    if not os.path.isdir(log_dir):
        return []
    logs = []
    for fname in os.listdir(log_dir):
        if not fname.startswith("run_") or not fname.endswith(".json"):
            continue
        with open(os.path.join(log_dir, fname)) as f:
            logs.append(json.load(f))
    return sorted(logs, key=lambda l: l["run_id"], reverse=True)
```
Run: `pytest tests/unit/test_run_logger.py -v`
Expect: `6 passed`

**Step 3 (REFACTOR):** Add docstrings. Handle malformed JSON files with `try/except` — skip and log warning to stdout.

**Step 4 (COMMIT):**
```
git commit -m "[Add] run_logger.py: load_run_logs() sorted newest-first"
```

---

## Block 2 — LLM Loop Upgrade: `_run_llm_loop` → `BlockResult`

### Chunk 2.1 — Signature + LlmStats accumulation

```
Test layer : UNIT
Modify     : agents/triage/triage_agent.py
Modify     : tests/unit/test_triage_agent.py
```

**⚠️ Breaking change note:** `_run_llm_loop` signature changes from `(block_text: str) -> None`
to `(block_text: str, block_index: int, block_snippet: str) -> BlockResult`.
Existing `test_run_llm_loop_*` tests that call `await _run_llm_loop(block_text)` must be updated
to pass `block_index=0, block_snippet="test"` and assert on the returned `BlockResult`.

**Step 1 (RED):**
```python
# Add to tests/unit/test_triage_agent.py
from agents.services.run_logger import BlockResult

@pytest.mark.asyncio
async def test_run_llm_loop_returns_block_result():
    """_run_llm_loop must return a BlockResult, not None."""
    # ... mock _client, one tool_calls response then stop ...
    result = await _run_llm_loop("login is broken", block_index=0, block_snippet="login is broken")
    assert isinstance(result, BlockResult)
    assert result.block_index == 0

@pytest.mark.asyncio
async def test_run_llm_loop_accumulates_llm_stats():
    """LlmStats captures iterations, finish_reason, and token totals."""
    # ... mock two iterations: first tool_calls, then stop ...
    result = await _run_llm_loop("login is broken", 0, "login is broken")
    assert result.llm is not None
    assert result.llm.iterations == 2
    assert result.llm.finish_reason == "stop"
    assert result.llm.prompt_tokens > 0
```
Run: `pytest tests/unit/test_triage_agent.py::test_run_llm_loop_returns_block_result -v`
Expect: `FAILED — _run_llm_loop() takes 1 positional argument but 3 were given`

**Step 2 (GREEN):**
- Add `from agents.services.run_logger import BlockResult, LlmStats` to `triage_agent.py`
- Change signature: `async def _run_llm_loop(block_text: str, block_index: int, block_snippet: str) -> BlockResult:`
- Accumulate `total_prompt_tokens`, `total_completion_tokens`, `tools_called_names: list[str]` across iterations
- Track `finish_reason` from last iteration
- Return `BlockResult(block_index=block_index, block_snippet=block_snippet, action="no_action", llm=LlmStats(...))` at end (action determined in Chunk 2.2)
- Update existing `test_run_llm_loop_*` tests to pass `block_index=0, block_snippet="test"`

Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: all passing

**Step 3 (REFACTOR):** Keep accumulation variables close to the loop. Add a comment marking where Chunk 2.2 infers `action`.

**Step 4 (COMMIT):**
```
git commit -m "[Add] _run_llm_loop: new signature + LlmStats accumulation"
```

---

### Chunk 2.2 — Action inference + ticket field extraction

```
Test layer : UNIT
Modify     : agents/triage/triage_agent.py
Modify     : tests/unit/test_triage_agent.py
```

**Step 1 (RED):**
```python
@pytest.mark.asyncio
async def test_run_llm_loop_action_ticket_created():
    """When create_jira_ticket is called, action is 'ticket_created' with key extracted."""
    # mock: tool_calls with create_jira_ticket(summary="Login crash", issue_type="Bug", priority="High")
    # mock result: "Created SCRUM-11: Login crash → https://..."
    result = await _run_llm_loop("login is broken", 0, "login is broken")
    assert result.action == "ticket_created"
    assert result.ticket_key == "SCRUM-11"
    assert result.ticket_type == "Bug"
    assert result.ticket_priority == "High"

@pytest.mark.asyncio
async def test_run_llm_loop_action_clarification_asked():
    """When ask_for_clarification is called, action is 'clarification_asked'."""
    # mock: tool_calls with ask_for_clarification(...)
    result = await _run_llm_loop("unclear message", 0, "unclear message")
    assert result.action == "clarification_asked"
    assert result.ticket_key is None
```
Run: `pytest tests/unit/test_triage_agent.py -k "test_run_llm_loop_action" -v`
Expect: `FAILED — result.action == 'no_action' (not yet inferred)`

**Step 2 (GREEN):**
- Track `last_tool_called: str | None` during the tool loop
- After loop: infer `action`:
  - `"create_jira_ticket"` in `tools_called_names` → `action = "ticket_created"`, extract `ticket_key` from result string with `re.search(r"Created (\w+-\d+):", result)`, read `ticket_summary/type/priority` from `tool_args`
  - `"ask_for_clarification"` in `tools_called_names` → `action = "clarification_asked"`
  - `"post_slack_message"` only → `action = "clarification_asked"`
  - nothing → `action = "no_action"`

**Step 3 (REFACTOR):** Extract `_infer_action(tools_called, tool_args_map, results_map) -> tuple[str, ...]` as a pure function — easier to test in isolation.

**Step 4 (COMMIT):**
```
git commit -m "[Add] _run_llm_loop: action inference + ticket field extraction"
```

---

## Block 3 — `run()` Orchestration

### Chunk 3.1 — `run()` builds RunLog and calls `write_run_log`

```
Test layer : UNIT
Modify     : agents/triage/triage_agent.py
Modify     : tests/unit/test_triage_agent.py
```

**⚠️ Mock update required:** The `patch_run_deps` helper and all `run()` tests that mock `_run_llm_loop` must be updated from `return_value=None` to `return_value=BlockResult(block_index=0, block_snippet="test", action="ticket_created", ticket_key="SCRUM-1", ...)`.

**Step 1 (RED):**
```python
@pytest.mark.asyncio
async def test_run_writes_log_file(tmp_path):
    """run() writes a log file to LOG_DIR after completion."""
    from unittest.mock import patch
    from agents.services.run_logger import BlockResult

    mock_result = BlockResult(block_index=0, block_snippet="bug", action="ticket_created",
                               ticket_key="SCRUM-1", ticket_summary="bug", ticket_type="Bug",
                               ticket_priority="High")
    patches = patch_run_deps(blocks=make_one_block(), llm_return=mock_result)
    with patches[0], patches[1], patches[2]:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            await run()
    mock_write.assert_called_once()
    run_log_arg = mock_write.call_args[0][0]
    assert run_log_arg.tickets_created_count == 1
    assert run_log_arg.status == "success"

@pytest.mark.asyncio
async def test_run_log_has_block_results():
    """run() appends each BlockResult to run_log.blocks."""
    mock_result = BlockResult(block_index=0, block_snippet="x", action="clarification_asked")
    patches = patch_run_deps(blocks=make_one_block(), llm_return=mock_result)
    with patches[0], patches[1], patches[2]:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            await run()
    run_log_arg = mock_write.call_args[0][0]
    assert len(run_log_arg.blocks) == 1
    assert run_log_arg.blocks[0].action == "clarification_asked"
```
Run: `pytest tests/unit/test_triage_agent.py -k "test_run_writes_log" -v`
Expect: `FAILED — write_run_log not called`

**Step 2 (GREEN):**
- Add `from agents.services.run_logger import RunLog, write_run_log` to `triage_agent.py`
- At top of `run()`: create `RunLog(run_id=..., started_at=now(), ...)`
- Change `await _run_llm_loop(block["combined_text"])` call to `result = await _run_llm_loop(block["combined_text"], i, block["combined_text"][:60])`
- Append `result` to `run_log.blocks`; update `run_log.tickets_created_count` / `clarifications_asked_count` from `result.action`
- At end of `run()`: set `run_log.completed_at`, call `write_run_log(run_log, settings.LOG_DIR)`
- Update existing `run()` tests: change `_run_llm_loop` mocks to return a `BlockResult`; patch `write_run_log` in all `run()` tests

**Step 3 (REFACTOR):** Extract `_update_funnel_counts(run_log, result)` to avoid inline if-chain.

**Step 4 (COMMIT):**
```
git commit -m "[Add] run(): builds RunLog per block and writes log at end"
```

---

### Chunk 3.2 — `_print_block_outcome()`: per-block stdout line

```
Test layer : UNIT
Modify     : agents/triage/triage_agent.py
Modify     : tests/unit/test_triage_agent.py
```

**Step 1 (RED):**
```python
def test_print_block_outcome_ticket_created(capsys):
    from agents.triage.triage_agent import _print_block_outcome
    from agents.services.run_logger import BlockResult
    result = BlockResult(block_index=0, block_snippet="x", action="ticket_created",
                         ticket_key="SCRUM-11", ticket_summary="Login crash",
                         ticket_type="Bug", ticket_priority="High")
    _print_block_outcome(result, index=0, total=4)
    out = capsys.readouterr().out
    assert "[Block 1/4]" in out
    assert "SCRUM-11" in out
    assert "Bug" in out

def test_print_block_outcome_clarification(capsys):
    from agents.triage.triage_agent import _print_block_outcome
    from agents.services.run_logger import BlockResult
    result = BlockResult(block_index=1, block_snippet="x", action="clarification_asked")
    _print_block_outcome(result, index=1, total=4)
    out = capsys.readouterr().out
    assert "[Block 2/4]" in out
    assert "Clarification" in out

def test_print_block_outcome_error(capsys):
    from agents.triage.triage_agent import _print_block_outcome
    from agents.services.run_logger import BlockResult
    result = BlockResult(block_index=2, block_snippet="x", action="error")
    _print_block_outcome(result, index=2, total=4)
    out = capsys.readouterr().out
    assert "[Block 3/4]" in out
    assert "Error" in out
```
Run: `pytest tests/unit/test_triage_agent.py -k "test_print_block" -v`
Expect: `FAILED — cannot import name '_print_block_outcome'`

**Step 2 (GREEN):**
```python
def _print_block_outcome(result: BlockResult, index: int, total: int) -> None:
    n = index + 1
    if result.action == "ticket_created":
        print(f"[Block {n}/{total}] ✅ Ticket created  : {result.ticket_key} "
              f'"{result.ticket_summary}" ({result.ticket_type} · {result.ticket_priority})')
    elif result.action == "clarification_asked":
        print(f"[Block {n}/{total}] 💬 Clarification asked")
    elif result.action == "error":
        print(f"[Block {n}/{total}] ⚠️  Error           : logged")
    else:
        print(f"[Block {n}/{total}] — {result.action}")
```

**Step 3 (REFACTOR):** Use an action→emoji/label dict to avoid if-chain.

**Step 4 (COMMIT):**
```
git commit -m "[Add] _print_block_outcome(): per-block stdout outcome line"
```

---

### Chunk 3.3 — `_compute_status()` + `_print_run_summary()`

```
Test layer : UNIT
Modify     : agents/triage/triage_agent.py
Modify     : tests/unit/test_triage_agent.py
```

**Step 1 (RED):**
```python
def test_compute_status_success_when_no_errors():
    from agents.triage.triage_agent import _compute_status
    from agents.services.run_logger import RunLog
    log = RunLog(run_id="x", started_at="x", completed_at="x", status="",
                 messages_fetched=1, blocks_processed=1, tickets_created_count=1,
                 clarifications_asked_count=0, blocks_skipped_count=0, error_count=0)
    assert _compute_status(log) == "success"

def test_compute_status_partial_when_errors():
    from agents.triage.triage_agent import _compute_status
    from agents.services.run_logger import RunLog
    log = RunLog(run_id="x", started_at="x", completed_at="x", status="",
                 messages_fetched=2, blocks_processed=2, tickets_created_count=1,
                 clarifications_asked_count=0, blocks_skipped_count=0, error_count=1)
    assert _compute_status(log) == "partial"

def test_print_run_summary_contains_key_fields(capsys):
    from agents.triage.triage_agent import _print_run_summary
    from agents.services.run_logger import RunLog
    log = RunLog(run_id="2026-04-29T13:00:00", started_at="x", completed_at="x",
                 status="partial", messages_fetched=5, blocks_processed=3,
                 tickets_created_count=2, clarifications_asked_count=1,
                 blocks_skipped_count=0, error_count=1)
    _print_run_summary(log, log_path="logs/run_test.json")
    out = capsys.readouterr().out
    assert "Run Summary" in out
    assert "Tickets created  : 2" in out
    assert "Status           : partial" in out
    assert "logs/run_test.json" in out
```
Run: `pytest tests/unit/test_triage_agent.py -k "compute_status or print_run_summary" -v`
Expect: `FAILED — cannot import _compute_status`

**Step 2 (GREEN):**
```python
def _compute_status(run_log: RunLog) -> str:
    return "success" if run_log.error_count == 0 else "partial"

def _print_run_summary(run_log: RunLog, log_path: str) -> None:
    keys = [b.ticket_key for b in run_log.blocks
            if b.action == "ticket_created" and b.ticket_key]
    keys_str = f"  ({', '.join(keys)})" if keys else ""
    print(f"\n{'─' * 50}")
    print("=== Run Summary ===")
    print(f"  Blocks processed : {run_log.blocks_processed}")
    print(f"  Tickets created  : {run_log.tickets_created_count}{keys_str}")
    print(f"  Clarifications   : {run_log.clarifications_asked_count}")
    print(f"  Errors           : {run_log.error_count}")
    print(f"  Status           : {run_log.status}")
    print(f"  Log written      : {log_path}")
    print(f"{'─' * 50}")
```

**Step 3 (REFACTOR):** Align column widths consistently.

**Step 4 (COMMIT):**
```
git commit -m "[Add] _compute_status() + _print_run_summary()"
```

---

### Chunk 3.4 — `_post_slack_summary()`: US3.3 Slack end-of-run post

```
Test layer : UNIT
Modify     : agents/triage/triage_agent.py
Modify     : tests/unit/test_triage_agent.py
```

**Step 1 (RED):**
```python
@pytest.mark.asyncio
async def test_post_slack_summary_success_run():
    from agents.triage.triage_agent import _post_slack_summary
    from agents.services.run_logger import RunLog
    log = RunLog(run_id="2026-04-29T13:00:00", started_at="x", completed_at="x",
                 status="success", messages_fetched=2, blocks_processed=2,
                 tickets_created_count=2, clarifications_asked_count=0,
                 blocks_skipped_count=0, error_count=0)
    with patch("agents.triage.triage_agent.post_slack_message",
               new_callable=AsyncMock) as mock_post:
        await _post_slack_summary(log)
    mock_post.assert_called_once()
    assert "✅" in mock_post.call_args[0][0]
    assert "2 tickets" in mock_post.call_args[0][0]

@pytest.mark.asyncio
async def test_post_slack_summary_suppressed_on_fatal():
    from agents.triage.triage_agent import _post_slack_summary
    from agents.services.run_logger import RunLog
    log = RunLog(run_id="x", started_at="x", completed_at=None, status="fatal",
                 messages_fetched=0, blocks_processed=0, tickets_created_count=0,
                 clarifications_asked_count=0, blocks_skipped_count=0, error_count=0)
    with patch("agents.triage.triage_agent.post_slack_message",
               new_callable=AsyncMock) as mock_post:
        await _post_slack_summary(log)
    mock_post.assert_not_called()

@pytest.mark.asyncio
async def test_post_slack_summary_failure_logs_stdout(capsys):
    """If Slack post fails, print to stdout and do NOT exit."""
    from agents.triage.triage_agent import _post_slack_summary
    from agents.services.run_logger import RunLog
    log = RunLog(run_id="x", started_at="x", completed_at="x", status="success",
                 messages_fetched=1, blocks_processed=1, tickets_created_count=1,
                 clarifications_asked_count=0, blocks_skipped_count=0, error_count=0)
    with patch("agents.triage.triage_agent.post_slack_message",
               new_callable=AsyncMock, side_effect=Exception("Slack down")):
        await _post_slack_summary(log)   # must NOT raise
    out = capsys.readouterr().out
    assert "Slack summary" in out
```
Run: `pytest tests/unit/test_triage_agent.py -k "post_slack_summary" -v`
Expect: `FAILED — cannot import _post_slack_summary`

**Step 2 (GREEN):**
```python
async def _post_slack_summary(run_log: RunLog) -> None:
    if run_log.status == "fatal":
        return
    ts = run_log.run_id[:16].replace("T", " ")
    if run_log.error_count == 0:
        msg = (f"✅ Run complete [{ts}] — "
               f"{run_log.tickets_created_count} ticket(s) created, "
               f"{run_log.clarifications_asked_count} clarification(s) asked, "
               f"0 errors")
    else:
        msg = (f"⚠️ Run complete [{ts}] — "
               f"{run_log.tickets_created_count} ticket(s) created, "
               f"{run_log.error_count} error(s) — see dashboard for details")
    try:
        await post_slack_message(msg)
    except Exception as e:
        print(f"[LOG] Slack summary post failed: {e}")
```

**Step 3 (REFACTOR):** No changes needed — function is already small and readable.

**Step 4 (COMMIT):**
```
git commit -m "[Add] _post_slack_summary(): US3.3 Slack end-of-run summary"
```

---

### Chunk 3.5 — Fatal handler writes log before `sys.exit(1)`

```
Test layer : UNIT
Modify     : agents/triage/triage_agent.py
Modify     : tests/unit/test_triage_agent.py
```

**Step 1 (RED):**
```python
@pytest.mark.asyncio
async def test_run_openai_error_writes_fatal_log():
    """When OpenAI raises APIError, a status='fatal' log is written before exiting."""
    patches = patch_run_deps(
        blocks=make_one_block(),
        llm_side_effect=openai.APIConnectionError.__new__(openai.APIConnectionError),
    )
    with patches[0], patches[1], patches[2]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                with pytest.raises(SystemExit):
                    await run()
    mock_write.assert_called_once()
    run_log_arg = mock_write.call_args[0][0]
    assert run_log_arg.status == "fatal"
```
Run: `pytest tests/unit/test_triage_agent.py::test_run_openai_error_writes_fatal_log -v`
Expect: `FAILED — write_run_log not called`

**Step 2 (GREEN):**
- In the `except openai.APIError` block in `run()`, before the Slack alert:
  ```python
  run_log.status = "fatal"
  run_log.completed_at = datetime.utcnow().isoformat(timespec="seconds")
  write_run_log(run_log, settings.LOG_DIR)
  ```

**Step 3 (REFACTOR):** Extract `_finalise_log(run_log, status, log_dir)` helper used by both the fatal path and the normal end-of-run path to avoid duplication.

**Step 4 (COMMIT):**
```
git commit -m "[Add] run(): write fatal log before sys.exit(1) on OpenAI error"
```

---

## Block 4 — Infrastructure

### Chunk 4.1 — `LOG_DIR` in settings + sentinel in `run_triage.py`

```
Test layer : UNIT
Modify     : config/settings.py
Modify     : run_triage.py
Modify     : .gitignore
```

**Step 1 (RED):**
```python
# tests/unit/test_settings.py  (add to existing file if it exists, else create)
def test_log_dir_defaults_to_logs():
    from config.settings import settings
    assert settings.LOG_DIR == "logs"
```
Run: `pytest tests/unit/ -k "test_log_dir" -v`
Expect: `FAILED — AttributeError: 'Settings' object has no attribute 'LOG_DIR'`

**Step 2 (GREEN):**
- Add `LOG_DIR: str = "logs"` to `config/settings.py`
- Add to `run_triage.py`:
  ```python
  import os
  from agents.services.run_logger import SENTINEL_FILE

  if __name__ == "__main__":
      os.makedirs("logs", exist_ok=True)
      open(SENTINEL_FILE, "w").close()
      try:
          asyncio.run(run())
      finally:
          if os.path.exists(SENTINEL_FILE):
              os.remove(SENTINEL_FILE)
  ```
- Add `logs/` to `.gitignore`

**Step 3 (REFACTOR):** No changes needed.

**Step 4 (COMMIT):**
```
git commit -m "[Add] LOG_DIR setting + sentinel file management in run_triage.py"
```

---

## Block 5 — Dashboard

### Chunk 5.1 — Run history table + "Run Agent" button

```
Test layer : E2E only (Streamlit UI — no unit tests)
Create     : dashboard.py
```

**No RED/GREEN cycle** — Streamlit UIs have no meaningful unit test surface. The underlying data functions (`load_run_logs`, `write_run_log`) are fully tested in Block 1. Dashboard verified during `/audit` E2E.

**Implementation:**
```python
# dashboard.py
import os, time, subprocess
import streamlit as st
from agents.services.run_logger import load_run_logs, SENTINEL_FILE
from config.settings import settings

st.set_page_config(page_title="JiraSlack Dashboard", layout="wide")
st.title("🤖 JiraSlack — Run Dashboard")

# ── Running state ────────────────────────────────────────────────────
is_running = os.path.exists(SENTINEL_FILE)

if is_running:
    st.warning("⏳ Agent is running...")
    time.sleep(2)
    st.rerun()

# ── Trigger ──────────────────────────────────────────────────────────
if st.button("▶  Run Agent", disabled=is_running, type="primary"):
    subprocess.Popen(["python", "run_triage.py"])
    st.rerun()

st.divider()

# ── Run history ──────────────────────────────────────────────────────
logs = load_run_logs(settings.LOG_DIR)

if not logs:
    st.info("No runs yet. Click 'Run Agent' to start.")
else:
    # Summary table
    rows = [
        {
            "Run ID": l["run_id"],
            "Status": l["status"],
            "Tickets": l["tickets_created_count"],
            "Clarifications": l["clarifications_asked_count"],
            "Errors": l["error_count"],
            "Blocks": l["blocks_processed"],
        }
        for l in logs
    ]
    st.dataframe(rows, use_container_width=True)

    # Detail expander
    st.subheader("Run Details")
    selected_id = st.selectbox("Select run", [l["run_id"] for l in logs])
    selected = next(l for l in logs if l["run_id"] == selected_id)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Blocks**")
        for b in selected.get("blocks", []):
            icon = {"ticket_created": "✅", "clarification_asked": "💬",
                    "error": "⚠️"}.get(b["action"], "—")
            label = b.get("ticket_key") or b["action"]
            st.write(f"{icon} Block {b['block_index']+1}: {label}")
    with col2:
        st.markdown("**Errors**")
        errors = selected.get("errors", [])
        if errors:
            for e in errors:
                st.error(f"Block {e['block_index']}: {e['error_message']}")
        else:
            st.success("No errors")
```

**Install dependency:**
```
pip install streamlit
```

**Step 4 (COMMIT):**
```
git commit -m "[Add] dashboard.py: Streamlit run history dashboard with Run Agent button"
```

---

## Success Criteria

- [ ] Log file created after every `python run_triage.py` — verified by checking `logs/` directory
- [ ] Log is valid JSON — verified by `test_write_run_log_is_valid_json`
- [ ] `load_run_logs` returns logs sorted newest-first — verified by `test_load_run_logs_returns_sorted_newest_first`
- [ ] Per-block stdout line printed — verified by `test_print_block_outcome_*`
- [ ] End-of-run summary printed — verified by `test_print_run_summary_contains_key_fields`
- [ ] Slack summary posted after normal run — verified by `test_post_slack_summary_success_run`
- [ ] Slack summary suppressed on fatal — verified by `test_post_slack_summary_suppressed_on_fatal`
- [ ] Fatal run writes `status:"fatal"` log before exit — verified by `test_run_openai_error_writes_fatal_log`
- [ ] `streamlit run dashboard.py` loads without error — verified in E2E
- [ ] "Run Agent" button triggers a real run and dashboard refreshes — verified in E2E
- [ ] All 56 existing unit tests still pass after `_run_llm_loop` signature change
- [ ] `logs/` in `.gitignore`

## Known Technical Debt

| ID | Description | Acceptable because |
|----|-------------|-------------------|
| DEBT-008 | Dashboard subprocess uses hardcoded `"python"` — may break in venvs where `python3` is the correct command | Acceptable for local dev; Phase 5 (scheduling) will replace subprocess trigger with a proper runner |
| DEBT-009 | `_run_llm_loop` returns `BlockResult` but `post_slack_message`-only tool calls (no ticket) map to `"clarification_asked"` — may misclassify a pure notification as a clarification | Acceptable for Phase 3; Phase 6 Eval will surface any misclassification via thumbs-up/down feedback |
| DEBT-010 | Dashboard auto-refresh polls every 2 seconds — may cause flicker on slow machines | Acceptable; Phase 5 will revisit if the dashboard becomes a long-lived UI |
