# Implementation Plan: Phase 5 — Eval & Feedback Loop

> Plan date: 2026-04-29
> Input:
>   - Brainstorm: `docs/plans/2026-04-29-phase5-eval-feedback-brainstorm.md`
>   - Design: `docs/plans/2026-04-29-phase5-eval-feedback-design.md`
>   - Diagram: `docs/diagrams/2026-04-29-phase5-eval-feedback.md`

---

## Goal

Capture 👍/👎 reactions on Slack confirmation posts, link them to ticket decisions, compute per-run thumbs-up rates, and alert the Operator in Slack when quality drops below `QUALITY_ALERT_THRESHOLD`.

## Architecture

`run_triage.py` calls `eval_runner.run_eval_step()` before and after `triage_agent.run()`. The pre-triage step loads `quality_store.json`, polls Slack for reactions on pending confirmation posts, computes metrics, and fires an alert if needed. The post-triage step registers new confirmation posts for polling next run. `triage_agent.run()` is minimally changed: it captures the `ts` of each confirmation post into a module-level buffer in `slack_tools.py`, drains it per block, and now returns `RunLog` instead of `None`.

## Files Affected

| File | Action | Reason |
|---|---|---|
| `config/settings.py` | Modify | 5 new settings |
| `pipeline/run_logger.py` | Modify | `BlockResult.confirmation_ts` field |
| `pipeline/quality_metrics.py` | Create | Dataclasses + quality store I/O + metrics logic |
| `pipeline/reaction_collector.py` | Create | Fetch reactions from Slack MCP |
| `pipeline/eval_runner.py` | Create | Orchestrate eval lifecycle |
| `agents/triage/tools/slack_tools.py` | Modify | ts capture buffer + `drain_confirmation_ts()` |
| `agents/triage/triage_agent.py` | Modify | Return RunLog + drain ts per block |
| `run_triage.py` | Modify | Add eval_runner hooks before/after triage |
| `dashboard.py` | Modify | Quality trend chart |
| `memory/quality_store.json` | Create (runtime) | Persisted quality state |
| `tests/unit/test_slack_tools.py` | Modify | ts capture tests |
| `tests/unit/test_run_logger.py` | Modify | confirmation_ts field test |
| `tests/unit/test_quality_metrics.py` | Create | All quality metrics logic |
| `tests/unit/test_reaction_collector.py` | Create | Reaction fetching |
| `tests/unit/test_eval_runner.py` | Create | Eval lifecycle orchestration |
| `tests/unit/test_triage_agent.py` | Modify | Update for `run()` return type + ts drain |

---

## Block 1 — Foundation: Settings + Data Layer

> Pure data structures and config. No external calls. All tests pass immediately in isolation.

---

### Chunk 1.1 — New settings in `config/settings.py`

```
Test layer: UNIT
Files:
  Modify: config/settings.py
Test file: tests/unit/test_quality_metrics.py  (first test in the new file)
```

**Step 1 (RED)** — Write this failing test:
```python
# tests/unit/test_quality_metrics.py
from config.settings import settings

def test_quality_settings_exist():
    assert hasattr(settings, "QUALITY_ALERT_THRESHOLD")
    assert hasattr(settings, "MIN_REACTIONS_FOR_QUALITY")
    assert hasattr(settings, "REACTION_WINDOW_HOURS")
    assert hasattr(settings, "REACTION_HISTORY_LIMIT")
    assert hasattr(settings, "QUALITY_STORE_PATH")
    assert 0.0 < settings.QUALITY_ALERT_THRESHOLD < 1.0
    assert settings.MIN_REACTIONS_FOR_QUALITY > 0
```
Run: `pytest tests/unit/test_quality_metrics.py::test_quality_settings_exist -v`
Expect: FAILED — `AttributeError: 'Settings' object has no attribute 'QUALITY_ALERT_THRESHOLD'`

**Step 2 (GREEN)** — Add to `config/settings.py`:
```python
QUALITY_ALERT_THRESHOLD:   float = float(os.getenv("QUALITY_ALERT_THRESHOLD",   "0.70"))
MIN_REACTIONS_FOR_QUALITY: int   = int(os.getenv("MIN_REACTIONS_FOR_QUALITY",    "5"))
REACTION_WINDOW_HOURS:     int   = int(os.getenv("REACTION_WINDOW_HOURS",        "48"))
REACTION_HISTORY_LIMIT:    int   = int(os.getenv("REACTION_HISTORY_LIMIT",       "50"))
QUALITY_STORE_PATH:        str   = os.getenv("QUALITY_STORE_PATH", "memory/quality_store.json")
```
Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Group with other agent behaviour settings; add inline comment `# Phase 5 — Eval & Feedback`

**Step 4 (COMMIT)**:
```
git commit -m "[Add] Phase 5 quality metric settings to config/settings.py"
```

---

### Chunk 1.2 — `BlockResult.confirmation_ts` field

```
Test layer: UNIT
Files:
  Modify: pipeline/run_logger.py
Test file: tests/unit/test_run_logger.py
```

**Step 1 (RED)** — Write this failing test:
```python
# tests/unit/test_run_logger.py  (add to existing file)
def test_block_result_confirmation_ts_defaults_to_none():
    result = BlockResult(block_index=0, block_snippet="text", action="ticket_created")
    assert result.confirmation_ts is None

def test_block_result_confirmation_ts_can_be_set():
    result = BlockResult(block_index=0, block_snippet="text",
                         action="ticket_created", confirmation_ts="1714406400.123")
    assert result.confirmation_ts == "1714406400.123"
```
Run: `pytest tests/unit/test_run_logger.py -v`
Expect: FAILED — `TypeError: BlockResult.__init__() got an unexpected keyword argument 'confirmation_ts'`

**Step 2 (GREEN)** — Add to `BlockResult` in `pipeline/run_logger.py`:
```python
confirmation_ts: Optional[str] = None   # Slack ts of the confirmation post; None if not captured
```

Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Place the field after `ticket_priority` and before `llm`; confirm serialisation round-trip via `asdict` still works.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] BlockResult.confirmation_ts field for Phase 5 reaction tracking"
```

---

### Chunk 1.3 — `quality_metrics.py`: dataclasses + store I/O

```
Test layer: UNIT
Files:
  Create: pipeline/quality_metrics.py
Test file: tests/unit/test_quality_metrics.py
```

**Step 1 (RED)** — Write this failing test:
```python
# tests/unit/test_quality_metrics.py  (add to existing file)
import os, json, tempfile
from pipeline.quality_metrics import (
    PendingReaction, CollectedReaction, RunQuality, QualityStore,
    load_quality_store, save_quality_store,
)

def test_load_quality_store_missing_file_returns_empty():
    store = load_quality_store("/nonexistent/path.json")
    assert store.pending == []
    assert store.runs == []

def test_quality_store_round_trip(tmp_path):
    path = str(tmp_path / "quality_store.json")
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0,
                                 ticket_key="SCRUM-1", confirmation_ts="123.456",
                                 posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    save_quality_store(store, path)
    loaded = load_quality_store(path)
    assert len(loaded.pending) == 1
    assert loaded.pending[0].confirmation_ts == "123.456"
```
Run: `pytest tests/unit/test_quality_metrics.py -v`
Expect: FAILED — `ModuleNotFoundError: No module named 'pipeline.quality_metrics'`

**Step 2 (GREEN)** — Create `pipeline/quality_metrics.py` with:
- `@dataclass PendingReaction` — `run_id, block_index, ticket_key, confirmation_ts, posted_at_iso`
- `@dataclass CollectedReaction` — `run_id, block_index, ticket_key, thumbs_up, thumbs_down, collected_at`
- `@dataclass RunQuality` — `run_id, collected_at, thumbs_up, thumbs_down, reactions_found, thumbs_up_rate: Optional[float]`
- `@dataclass QualityStore` — `pending: list[PendingReaction], runs: list[RunQuality]`
- `load_quality_store(path) -> QualityStore` — reads JSON, returns empty on any error, never raises
- `save_quality_store(store, path)` — writes JSON, logs warning on failure, never raises

Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Add `from __future__ import annotations`; ensure `save_quality_store` uses `dataclasses.asdict`; write docstrings.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] quality_metrics.py dataclasses and quality store I/O"
```

---

## Block 2 — ts Capture Spike

> Highest-risk chunk. If Slack MCP doesn't return `ts` in the response body, Option B kicks in.
> This chunk MUST be completed and confirmed before Block 4 (reaction collector) is built.

---

### Chunk 2.1 — ts capture buffer in `slack_tools.py`

```
Test layer: UNIT
Files:
  Modify: agents/triage/tools/slack_tools.py
Test file: tests/unit/test_slack_tools.py
```

**Step 1 (RED)** — Write this failing test:
```python
# tests/unit/test_slack_tools.py  (add to existing file)
import json
from unittest.mock import AsyncMock, MagicMock, patch
import agents.triage.tools.slack_tools as slack_tools_module

async def test_post_slack_message_captures_ts_when_present():
    slack_tools_module._confirmation_ts_buffer.clear()
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps({"ok": True, "ts": "1714406400.123"}))]
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=mock_result)
    with patch("agents.triage.tools.slack_tools.slack_mcp_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        await slack_tools_module.post_slack_message("hello")
    assert slack_tools_module._confirmation_ts_buffer == ["1714406400.123"]

async def test_post_slack_message_silent_when_ts_absent():
    slack_tools_module._confirmation_ts_buffer.clear()
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps({"ok": True}))]
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=mock_result)
    with patch("agents.triage.tools.slack_tools.slack_mcp_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        await slack_tools_module.post_slack_message("hello")
    assert slack_tools_module._confirmation_ts_buffer == []

def test_drain_confirmation_ts_pops_last_and_clears():
    slack_tools_module._confirmation_ts_buffer.clear()
    slack_tools_module._confirmation_ts_buffer.append("111.222")
    ts = slack_tools_module.drain_confirmation_ts()
    assert ts == "111.222"
    assert slack_tools_module._confirmation_ts_buffer == []

def test_drain_confirmation_ts_returns_none_when_empty():
    slack_tools_module._confirmation_ts_buffer.clear()
    assert slack_tools_module.drain_confirmation_ts() is None
```
Run: `pytest tests/unit/test_slack_tools.py -v`
Expect: FAILED — `AttributeError: module '...' has no attribute '_confirmation_ts_buffer'`

**Step 2 (GREEN)** — Modify `agents/triage/tools/slack_tools.py`:
1. Add `_confirmation_ts_buffer: list[str] = []` at module level (after imports)
2. Capture `result = await session.call_tool(...)` in `post_slack_message`
3. Parse `ts` from `result.content[0].text` → JSON → append to buffer (silent try/except)
4. Add `drain_confirmation_ts() -> Optional[str]` — pops last item, clears buffer, returns it or None

Run: same
Expect: PASSED

**Spike verification (manual):** Run `python run_triage.py` with a live Slack channel. After the first `post_slack_message` call, print `_confirmation_ts_buffer` contents. If ts is present → Option A confirmed. If buffer is empty → activate Option B (document in `docs/BUGS.md` as SPIKE-001 resolved with Option B).

**Step 3 (REFACTOR)** — Add `from typing import Optional`; name the buffer `_confirmation_ts_buffer` with a comment explaining its purpose; add docstring to `drain_confirmation_ts`.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] ts capture buffer and drain_confirmation_ts to slack_tools"
```

---

## Block 3 — Quality Metrics Logic

> Pure functions operating on `QualityStore`. All state passed as arguments — no global reads.

---

### Chunk 3.1 — `add_pending_from_run()`

```
Test layer: UNIT
Files:
  Modify: pipeline/quality_metrics.py
Test file: tests/unit/test_quality_metrics.py
```

**Step 1 (RED)**:
```python
from pipeline.run_logger import BlockResult, RunLog
from pipeline.quality_metrics import QualityStore, add_pending_from_run

def _make_run_log(blocks):
    return RunLog(
        run_id="2026-04-29T10:00:00", started_at="2026-04-29T10:00:00",
        completed_at=None, status="success", messages_fetched=1,
        blocks_processed=len(blocks), tickets_created_count=0,
        clarifications_asked_count=0, blocks_skipped_count=0,
        error_count=0, blocks=blocks,
    )

def test_add_pending_from_run_adds_ticket_blocks_with_ts():
    store = QualityStore(pending=[], runs=[])
    run_log = _make_run_log([
        BlockResult(block_index=0, block_snippet="bug", action="ticket_created",
                    ticket_key="SCRUM-1", confirmation_ts="111.000"),
        BlockResult(block_index=1, block_snippet="story", action="ticket_created",
                    ticket_key="SCRUM-2", confirmation_ts=None),   # no ts — skip
    ])
    add_pending_from_run(store, run_log)
    assert len(store.pending) == 1
    assert store.pending[0].confirmation_ts == "111.000"
    assert store.pending[0].ticket_key == "SCRUM-1"

def test_add_pending_from_run_skips_non_ticket_actions():
    store = QualityStore(pending=[], runs=[])
    run_log = _make_run_log([
        BlockResult(block_index=0, block_snippet="q", action="clarification_asked"),
    ])
    add_pending_from_run(store, run_log)
    assert store.pending == []
```
Run: `pytest tests/unit/test_quality_metrics.py -k "pending_from_run" -v`
Expect: FAILED — `ImportError: cannot import name 'add_pending_from_run'`

**Step 2 (GREEN)** — Add `add_pending_from_run(store, run_log)` to `quality_metrics.py`: iterate `run_log.blocks`, append `PendingReaction` for each `ticket_created` block with non-None `confirmation_ts`.

Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Use `datetime.utcnow().isoformat(timespec="seconds")` for `posted_at_iso`.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] add_pending_from_run to quality_metrics"
```

---

### Chunk 3.2 — `apply_collected()`

```
Test layer: UNIT
Files:
  Modify: pipeline/quality_metrics.py
Test file: tests/unit/test_quality_metrics.py
```

**Step 1 (RED)**:
```python
from pipeline.quality_metrics import (
    QualityStore, PendingReaction, CollectedReaction, apply_collected
)

def test_apply_collected_moves_matching_pending_to_runs():
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                  confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    collected = [CollectedReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                    thumbs_up=1, thumbs_down=0, collected_at="2026-04-29T11:00:00")]
    apply_collected(store, collected)
    assert len(store.runs) == 1
    assert store.runs[0].thumbs_up == 1
    assert store.runs[0].thumbs_up_rate == 1.0
    assert store.pending == []

def test_apply_collected_unmatched_pending_remains():
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                  confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    apply_collected(store, [])   # nothing collected
    assert store.pending != []   # still pending

def test_apply_collected_zero_reactions_sets_rate_none():
    store = QualityStore(
        pending=[PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                  confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00")],
        runs=[],
    )
    collected = [CollectedReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                    thumbs_up=0, thumbs_down=0, collected_at="2026-04-29T11:00:00")]
    apply_collected(store, collected)
    assert store.runs[0].thumbs_up_rate is None   # Rule 9 — 0 reactions excluded
```
Run: `pytest tests/unit/test_quality_metrics.py -k "apply_collected" -v`
Expect: FAILED — `ImportError: cannot import name 'apply_collected'`

**Step 2 (GREEN)** — Add `apply_collected(store, collected)` to `quality_metrics.py`:
- Group collected by `run_id`
- For each pending entry matched by `run_id`, build a `RunQuality` from summed reactions
- `thumbs_up_rate = thumbs_up / (thumbs_up + thumbs_down)` if denominator > 0 else None (Rule 9)
- Remove processed pending entries; append `RunQuality` to `store.runs`

Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Ensure unmatched pending (outside reaction window) stays; add window expiry pruning for entries older than `REACTION_WINDOW_HOURS`.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] apply_collected to quality_metrics"
```

---

### Chunk 3.3 — `should_alert()` + `rolling_thumbs_up_rate()`

```
Test layer: UNIT
Files:
  Modify: pipeline/quality_metrics.py
Test file: tests/unit/test_quality_metrics.py
```

**Step 1 (RED)**:
```python
from pipeline.quality_metrics import QualityStore, RunQuality, should_alert, rolling_thumbs_up_rate

def _run_quality(run_id, thumbs_up, thumbs_down, rate):
    return RunQuality(run_id=run_id, collected_at="2026-04-29T11:00:00",
                      thumbs_up=thumbs_up, thumbs_down=thumbs_down,
                      reactions_found=thumbs_up+thumbs_down, thumbs_up_rate=rate)

def test_should_alert_fires_when_rate_below_threshold():
    store = QualityStore(pending=[], runs=[
        _run_quality("r1", 2, 8, 0.20),   # 20% — well below default 0.70
    ] * 5)   # 5 runs → ≥ MIN_REACTIONS (5)
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is True
    assert rq is not None

def test_should_alert_no_fire_when_rate_above_threshold():
    store = QualityStore(pending=[], runs=[_run_quality("r1", 8, 2, 0.80)] * 5)
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is False

def test_should_alert_warmup_gate_blocks_alert():
    store = QualityStore(pending=[], runs=[_run_quality("r1", 0, 1, 0.0)])  # only 1 reaction
    alert, rq = should_alert(store, threshold=0.70, min_reactions=5)
    assert alert is False   # Rule 8

def test_rolling_thumbs_up_rate_aggregates_runs():
    store = QualityStore(pending=[], runs=[
        _run_quality("r1", 3, 1, 0.75),
        _run_quality("r2", 1, 3, 0.25),
    ])
    rate = rolling_thumbs_up_rate(store)
    assert rate == 0.50   # 4 up / 8 total

def test_rolling_thumbs_up_rate_returns_none_below_minimum():
    store = QualityStore(pending=[], runs=[_run_quality("r1", 1, 0, 1.0)])
    rate = rolling_thumbs_up_rate(store, min_reactions=5)
    assert rate is None
```
Run: `pytest tests/unit/test_quality_metrics.py -k "should_alert or rolling" -v`
Expect: FAILED — `ImportError: cannot import name 'should_alert'`

**Step 2 (GREEN)** — Add both functions to `quality_metrics.py`:
- `should_alert`: sum reactions across all runs; if total < min_reactions → (False, None) (Rule 8); check latest run's `thumbs_up_rate < threshold`; return `(True/False, latest_run_quality or None)`
- `rolling_thumbs_up_rate`: sum all thumbs_up / sum all (thumbs_up + thumbs_down); return None if total < min_reactions

Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Default `min_reactions` parameter to `settings.MIN_REACTIONS_FOR_QUALITY`.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] should_alert and rolling_thumbs_up_rate to quality_metrics"
```

---

## Block 4 — Reaction Collector

---

### Chunk 4.1 — `fetch_reactions_for_pending()`

```
Test layer: UNIT
Files:
  Create: pipeline/reaction_collector.py
Test file: tests/unit/test_reaction_collector.py
```

**Step 1 (RED)**:
```python
# tests/unit/test_reaction_collector.py
import json
from unittest.mock import AsyncMock, MagicMock, patch
from pipeline.quality_metrics import PendingReaction
from pipeline.reaction_collector import fetch_reactions_for_pending

def _pending(ts):
    return PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                            confirmation_ts=ts, posted_at_iso="2026-04-29T10:00:00")

async def test_fetch_reactions_matches_by_ts():
    history = {"messages": [
        {"ts": "111.000", "text": "✅ Created", "reactions": [
            {"name": "+1", "count": 2, "users": ["U1", "U2"]},
            {"name": "-1", "count": 1, "users": ["U3"]},
        ]},
    ]}
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps(history))]
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=mock_result)
    with patch("pipeline.reaction_collector.slack_mcp_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await fetch_reactions_for_pending(
            pending=[_pending("111.000")], channel_id="C123",
            history_limit=50, window_hours=48
        )
    assert len(result) == 1
    assert result[0].thumbs_up == 2
    assert result[0].thumbs_down == 1

async def test_fetch_reactions_returns_empty_on_mcp_error():
    with patch("pipeline.reaction_collector.slack_mcp_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(side_effect=Exception("MCP down"))
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await fetch_reactions_for_pending(
            pending=[_pending("111.000")], channel_id="C123",
            history_limit=50, window_hours=48
        )
    assert result == []

async def test_fetch_reactions_returns_empty_when_no_pending():
    result = await fetch_reactions_for_pending(
        pending=[], channel_id="C123", history_limit=50, window_hours=48
    )
    assert result == []
```
Run: `pytest tests/unit/test_reaction_collector.py -v`
Expect: FAILED — `ModuleNotFoundError: No module named 'pipeline.reaction_collector'`

**Step 2 (GREEN)** — Create `pipeline/reaction_collector.py`:
- `fetch_reactions_for_pending(pending, channel_id, history_limit, window_hours) -> list[CollectedReaction]`
- Return `[]` immediately if `pending` is empty
- One `slack_mcp_session()` call → `slack_get_channel_history(channel_id, limit=history_limit)`
- Build `ts → message` lookup dict; for each pending entry match by `ts`; count `name == "+1"` and `name == "-1"` reactions
- Return `[]` on any exception (Rule 5)

Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Filter pending to entries within `window_hours` (skip expired); add docstring.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] reaction_collector.py with fetch_reactions_for_pending"
```

---

## Block 5 — Eval Runner

---

### Chunk 5.1 — `pipeline/eval_runner.run_eval_step()`

```
Test layer: UNIT
Files:
  Create: pipeline/eval_runner.py
Test file: tests/unit/test_eval_runner.py
```

**Step 1 (RED)**:
```python
# tests/unit/test_eval_runner.py
from unittest.mock import AsyncMock, MagicMock, patch, call
from pipeline.eval_runner import run_eval_step
from pipeline.quality_metrics import QualityStore, PendingReaction, RunQuality

async def test_pre_step_skips_collection_when_no_pending():
    empty_store = QualityStore(pending=[], runs=[])
    with patch("pipeline.eval_runner.load_quality_store", return_value=empty_store) as mock_load, \
         patch("pipeline.eval_runner.fetch_reactions_for_pending") as mock_fetch, \
         patch("pipeline.eval_runner.save_quality_store") as mock_save:
        await run_eval_step(run_log=None)
    mock_fetch.assert_not_called()
    mock_save.assert_not_called()   # nothing changed — no save needed

async def test_pre_step_collects_and_alerts_when_below_threshold():
    pending = [PendingReaction(run_id="r1", block_index=0, ticket_key="SCRUM-1",
                                confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00")]
    store_with_pending = QualityStore(pending=pending, runs=[])
    with patch("pipeline.eval_runner.load_quality_store", return_value=store_with_pending), \
         patch("pipeline.eval_runner.fetch_reactions_for_pending", new=AsyncMock(return_value=[])), \
         patch("pipeline.eval_runner.apply_collected"), \
         patch("pipeline.eval_runner.should_alert", return_value=(True, MagicMock(run_id="r1", thumbs_up_rate=0.5))), \
         patch("pipeline.eval_runner.post_slack_message", new=AsyncMock()) as mock_alert, \
         patch("pipeline.eval_runner.save_quality_store"):
        await run_eval_step(run_log=None)
    mock_alert.assert_called_once()

async def test_post_step_calls_add_pending_and_save(make_run_log):
    store = QualityStore(pending=[], runs=[])
    mock_run_log = MagicMock()
    with patch("pipeline.eval_runner.load_quality_store", return_value=store), \
         patch("pipeline.eval_runner.add_pending_from_run") as mock_add, \
         patch("pipeline.eval_runner.save_quality_store") as mock_save:
        await run_eval_step(run_log=mock_run_log)
    mock_add.assert_called_once_with(store, mock_run_log)
    mock_save.assert_called_once()
```
Run: `pytest tests/unit/test_eval_runner.py -v`
Expect: FAILED — `ModuleNotFoundError: No module named 'pipeline.eval_runner'`

**Step 2 (GREEN)** — Create `pipeline/eval_runner.py`:
```python
async def run_eval_step(run_log=None) -> None:
    store = load_quality_store(settings.QUALITY_STORE_PATH)
    if run_log is None:
        # Pre-triage: collect reactions and alert if needed
        if store.pending:
            collected = await fetch_reactions_for_pending(
                store.pending, settings.SLACK_CHANNEL_ID,
                settings.REACTION_HISTORY_LIMIT, settings.REACTION_WINDOW_HOURS
            )
            apply_collected(store, collected)
            alert, rq = should_alert(store, settings.QUALITY_ALERT_THRESHOLD,
                                     settings.MIN_REACTIONS_FOR_QUALITY)
            if alert:
                await post_slack_message(_quality_alert_message(rq))
            save_quality_store(store, settings.QUALITY_STORE_PATH)
    else:
        # Post-triage: register new pending
        add_pending_from_run(store, run_log)
        save_quality_store(store, settings.QUALITY_STORE_PATH)
```
Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Extract `_quality_alert_message(rq: RunQuality) -> str` as a private helper; keep message format informative (includes rate, threshold, run_id).

**Step 4 (COMMIT)**:
```
git commit -m "[Add] eval_runner.py with run_eval_step pre/post triage hooks"
```

---

## Block 6 — Triage Agent + run_triage.py

> LEARNINGS.md: "When a plan chunk touches run(), all tests fail at once — implement the full set in one GREEN step."

---

### Chunk 6.1 — `triage_agent.run()` returns RunLog + drains ts per block

```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
Test file: tests/unit/test_triage_agent.py
```

**Step 1 (RED)**:
```python
# tests/unit/test_triage_agent.py  (add to existing tests)

async def test_run_returns_run_log(patch_run_deps):
    """run() must now return RunLog not None."""
    with patch_run_deps[0], ..., patch_run_deps[8]:
        result = await run()
    assert result is not None
    from pipeline.run_logger import RunLog
    assert isinstance(result, RunLog)

async def test_run_populates_confirmation_ts_for_ticket_created(patch_run_deps):
    """confirmation_ts on BlockResult is set when drain returns a ts."""
    import agents.triage.tools.slack_tools as st
    st._confirmation_ts_buffer.clear()
    # Make drain return a ts after the LLM loop for a ticket_created block
    # ... (set up mock so _run_llm_loop returns ticket_created BlockResult
    #       and _confirmation_ts_buffer has "111.000" after the LLM call)
    with patch_run_deps[0], ..., patch_run_deps[8]:
        run_log = await run()
    ticket_blocks = [b for b in run_log.blocks if b.action == "ticket_created"]
    # At minimum: confirmation_ts is None when buffer was empty (not missing the field)
    for b in ticket_blocks:
        assert hasattr(b, "confirmation_ts")
```
Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: FAILED — existing test `test_run_returns_none` passes; new `test_run_returns_run_log` FAILS

**Step 2 (GREEN)** — Make three surgical changes to `agents/triage/triage_agent.py`:
1. Change `async def run() -> None:` to `async def run() -> RunLog:`
2. Change the final line from `_print_run_summary(run_log, ...)` to `_print_run_summary(...); return run_log`
3. In the per-block loop (Step 4): before calling `_run_llm_loop`, call `drain_confirmation_ts()` (discard); after `result = await _run_llm_loop(...)`, if `result.action == "ticket_created"`, set `result.confirmation_ts = drain_confirmation_ts()`

Import: `from agents.triage.tools.slack_tools import drain_confirmation_ts` (add to existing imports)

Run: all existing tests + new ones
Expect: ALL PASSED (existing tests don't break — `run()` returning a value is backward-compatible with `asyncio.run(run())`)

**Step 3 (REFACTOR)** — Add `-> RunLog` to the function signature type hint; update docstring to mention "Returns the RunLog for the completed run".

**Step 4 (COMMIT)**:
```
git commit -m "[Add] triage_agent.run() returns RunLog and drains confirmation_ts per block"
```

---

### Chunk 6.2 — `run_triage.py` eval hooks

```
Test layer: UNIT
Files:
  Modify: run_triage.py
Test file: tests/unit/test_eval_runner.py  (add integration-style unit test)
```

**Step 1 (RED)**:
```python
# tests/unit/test_eval_runner.py  (add)
async def test_run_triage_main_calls_eval_runner_before_and_after():
    """main() should call run_eval_step(None) before and run_eval_step(run_log) after."""
    mock_run_log = MagicMock()
    with patch("run_triage.run_eval_step", new=AsyncMock()) as mock_eval, \
         patch("run_triage.run", new=AsyncMock(return_value=mock_run_log)):
        from run_triage import main
        await main()
    assert mock_eval.call_count == 2
    first_call_args = mock_eval.call_args_list[0]
    second_call_args = mock_eval.call_args_list[1]
    assert first_call_args == call(run_log=None)
    assert second_call_args == call(run_log=mock_run_log)
```
Run: `pytest tests/unit/test_eval_runner.py::test_run_triage_main_calls_eval_runner_before_and_after -v`
Expect: FAILED — `cannot import name 'main' from 'run_triage'`

**Step 2 (GREEN)** — Refactor `run_triage.py`:
```python
from pipeline.eval_runner import run_eval_step

async def main():
    await run_eval_step(run_log=None)
    run_log = await run()
    await run_eval_step(run_log=run_log)

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    open(SENTINEL_FILE, "w").close()
    try:
        asyncio.run(main())
    finally:
        if os.path.exists(SENTINEL_FILE):
            os.remove(SENTINEL_FILE)
```
Run: same
Expect: PASSED

**Step 3 (REFACTOR)** — Update `run_triage.py` docstring to mention eval hooks.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] run_triage.py eval hooks via eval_runner.run_eval_step"
```

---

## Block 7 — Dashboard Quality Chart

---

### Chunk 7.1 — Quality trend chart in `dashboard.py`

```
Test layer: UNIT
Files:
  Modify: dashboard.py
Test file: tests/unit/test_dashboard.py  (create if not exists, otherwise add)
```

**Step 1 (RED)**:
```python
# tests/unit/test_dashboard.py
from pipeline.quality_metrics import QualityStore, RunQuality, load_quality_store
from unittest.mock import patch
import json, tempfile

def test_quality_chart_data_returns_empty_for_cold_start(tmp_path):
    """load_quality_store with no file returns empty store — dashboard must handle this."""
    store = load_quality_store(str(tmp_path / "nonexistent.json"))
    assert store.runs == []
    # dashboard renders "warming up" — no crash

def test_quality_chart_data_returns_rates_per_run(tmp_path):
    path = str(tmp_path / "qs.json")
    store = QualityStore(pending=[], runs=[
        RunQuality(run_id="2026-04-29T10:00:00", collected_at="2026-04-29T11:00:00",
                   thumbs_up=3, thumbs_down=1, reactions_found=4, thumbs_up_rate=0.75),
    ])
    from pipeline.quality_metrics import save_quality_store
    save_quality_store(store, path)
    loaded = load_quality_store(path)
    assert len(loaded.runs) == 1
    assert loaded.runs[0].thumbs_up_rate == 0.75
```
Run: `pytest tests/unit/test_dashboard.py -v`
Expect: PASSED (these test the data layer, not Streamlit rendering — which can't be unit-tested)

Note: Dashboard UI rendering is verified in E2E (`/audit` Part 3) — "open dashboard, verify quality chart renders".

**Step 2 (GREEN)** — Add a quality trend section to `dashboard.py`:
- `load_quality_store(settings.QUALITY_STORE_PATH)` → `store`
- If `store.runs` is empty: `st.info("🔄 Quality trend warming up — no reactions collected yet")`
- Else: plot `[r.run_id[:16] for r in store.runs]` vs `[r.thumbs_up_rate for r in store.runs]` as a line chart; show current rolling rate and reaction count

Run: same + full suite
Expect: PASSED

**Step 3 (REFACTOR)** — Extract `_render_quality_section(store)` as a function; add "warming up" label with `N/{MIN_REACTIONS_FOR_QUALITY}` count when below minimum.

**Step 4 (COMMIT)**:
```
git commit -m "[Add] quality trend chart to dashboard"
```

---

## Success Criteria

Map directly to brainstorm success metrics:

- [ ] **Reactions captured** — ≥95% of confirmation posts have `confirmation_ts` stored; verified by running agent and checking `run_log.blocks[*].confirmation_ts`
- [ ] **Reaction attribution** — 100% of reactions linked to correct run + ticket; verified by spot-checking `quality_store.json → pending[*].ticket_key` matches `run_log`
- [ ] **Alert fires correctly** — quality alert posted to Slack within 1 run of rate dropping below threshold; verified by E2E: 👎 a confirmation, run agent, verify alert appears in Slack
- [ ] **Metrics survive restart** — thumbs-up rate unchanged after kill + restart; verified by `test_quality_store_round_trip`
- [ ] **Dashboard shows trend** — quality chart renders in Streamlit with per-run thumbs-up rate; verified in `/audit` E2E visual check
- [ ] **Confirmation is informative** — confirmation message shows type + priority; verified manually (existing behaviour from triage agent)
- [ ] All unit tests pass: `pytest tests/unit/ -v`
- [ ] All integration tests pass: `pytest tests/integration/ -v`
- [ ] E2E checklist passes (verified in `/audit` Part 3)

---

## Known Technical Debt

| ID | Description | Why Acceptable Now |
|---|---|---|
| SPIKE-001 | Slack MCP `ts` response shape unconfirmed | Chunk 2.1 is the spike — if Option A fails, Option B is documented and builds are paused to implement it |
| DEBT-014 | `run_eval_step` pre/post share one `load_quality_store` call instead of loading once and passing the store object | Pre and post run in the same process; double-load is safe. Refactor if performance is an issue. |
| DEBT-015 | `apply_collected` builds one `RunQuality` per run_id — if a run has many pending items, they collapse into one quality record | Acceptable for current volumes; revisit in Phase 5b if per-block granularity is needed |
| DEBT-016 | Dashboard quality chart has no y-axis range lock — chart rescales as reactions accumulate | Visual-only issue; acceptable for initial release |
