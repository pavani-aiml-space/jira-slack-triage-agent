"""
Unit tests for pipeline/eval_runner.py
All external calls (quality_metrics, reaction_collector, slack_tools) mocked.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from pipeline.eval_runner import run_eval_step
from pipeline.quality_metrics import QualityStore, PendingReaction, RunQuality


def _pending():
    return PendingReaction(
        run_id="r1", block_index=0, ticket_key="SCRUM-1",
        confirmation_ts="111.000", posted_at_iso="2026-04-29T10:00:00",
    )


def _rq(rate=0.5):
    return RunQuality(
        run_id="r1", collected_at="2026-04-29T11:00:00",
        thumbs_up=1, thumbs_down=1, reactions_found=2, thumbs_up_rate=rate,
    )


# ── Pre-triage path (run_log=None) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pre_step_skips_collection_when_no_pending():
    empty_store = QualityStore(pending=[], runs=[])
    with patch("pipeline.eval_runner.load_quality_store", return_value=empty_store), \
         patch("pipeline.eval_runner.fetch_reactions_for_pending") as mock_fetch, \
         patch("pipeline.eval_runner.save_quality_store") as mock_save:
        await run_eval_step(run_log=None)
    mock_fetch.assert_not_called()
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_pre_step_calls_collect_when_pending_exists():
    store = QualityStore(pending=[_pending()], runs=[])
    with patch("pipeline.eval_runner.load_quality_store", return_value=store), \
         patch("pipeline.eval_runner.fetch_reactions_for_pending",
               new=AsyncMock(return_value=[])) as mock_fetch, \
         patch("pipeline.eval_runner.apply_collected"), \
         patch("pipeline.eval_runner.should_alert", return_value=(False, None)), \
         patch("pipeline.eval_runner.save_quality_store"):
        await run_eval_step(run_log=None)
    mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_pre_step_posts_alert_when_should_alert_true():
    store = QualityStore(pending=[_pending()], runs=[])
    with patch("pipeline.eval_runner.load_quality_store", return_value=store), \
         patch("pipeline.eval_runner.fetch_reactions_for_pending",
               new=AsyncMock(return_value=[])), \
         patch("pipeline.eval_runner.apply_collected"), \
         patch("pipeline.eval_runner.should_alert", return_value=(True, _rq(0.4))), \
         patch("pipeline.eval_runner.post_slack_message",
               new=AsyncMock()) as mock_alert, \
         patch("pipeline.eval_runner.save_quality_store"):
        await run_eval_step(run_log=None)
    mock_alert.assert_called_once()
    assert "quality" in mock_alert.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_pre_step_saves_store_after_collection():
    store = QualityStore(pending=[_pending()], runs=[])
    with patch("pipeline.eval_runner.load_quality_store", return_value=store), \
         patch("pipeline.eval_runner.fetch_reactions_for_pending",
               new=AsyncMock(return_value=[])), \
         patch("pipeline.eval_runner.apply_collected"), \
         patch("pipeline.eval_runner.should_alert", return_value=(False, None)), \
         patch("pipeline.eval_runner.save_quality_store") as mock_save:
        await run_eval_step(run_log=None)
    mock_save.assert_called_once()


# ── Post-triage path (run_log given) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_step_calls_add_pending_and_save():
    store = QualityStore(pending=[], runs=[])
    mock_run_log = MagicMock()
    with patch("pipeline.eval_runner.load_quality_store", return_value=store), \
         patch("pipeline.eval_runner.add_pending_from_run") as mock_add, \
         patch("pipeline.eval_runner.save_quality_store") as mock_save, \
         patch("pipeline.eval_runner.run_judge_for_run", new=AsyncMock()) as mock_judge:
        await run_eval_step(run_log=mock_run_log)
    mock_add.assert_called_once_with(store, mock_run_log)
    mock_save.assert_called_once()
    mock_judge.assert_called_once_with(mock_run_log)


@pytest.mark.asyncio
async def test_post_step_does_not_call_reaction_collector():
    store = QualityStore(pending=[], runs=[])
    mock_run_log = MagicMock()
    with patch("pipeline.eval_runner.load_quality_store", return_value=store), \
         patch("pipeline.eval_runner.add_pending_from_run"), \
         patch("pipeline.eval_runner.save_quality_store"), \
         patch("pipeline.eval_runner.run_judge_for_run", new=AsyncMock()), \
         patch("pipeline.eval_runner.fetch_reactions_for_pending") as mock_fetch:
        await run_eval_step(run_log=mock_run_log)
    mock_fetch.assert_not_called()


# ── Chunk 6.2 — run_triage.py main() eval hooks ───────────────────────────────

@pytest.mark.asyncio
async def test_run_triage_main_calls_eval_runner_before_and_after():
    """main() must call run_eval_step(None) before triage and run_eval_step(run_log) after."""
    import importlib
    import run_triage
    importlib.reload(run_triage)

    mock_run_log = MagicMock()
    with patch("run_triage.run_eval_step", new=AsyncMock()) as mock_eval, \
         patch("run_triage.triage_run", new=AsyncMock(return_value=mock_run_log)):
        await run_triage.main()

    assert mock_eval.call_count == 2
    first_call  = mock_eval.call_args_list[0]
    second_call = mock_eval.call_args_list[1]
    assert first_call  == call(run_log=None)
    assert second_call == call(run_log=mock_run_log)


# ── Chunk 5.1 — run_triage.py main() memory hooks ─────────────────────────────

@pytest.mark.asyncio
async def test_run_triage_main_calls_memory_runner_wrapping_eval():
    """main() must call memory pre_run, then eval, then triage, then eval, then memory post_run."""
    import importlib
    import run_triage
    importlib.reload(run_triage)

    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import EpisodeStore
    mock_ctx = MemoryContext(semantic_injection="", episode_store=EpisodeStore())
    mock_run_log = MagicMock()
    call_order: list[str] = []

    async def mock_pre():
        call_order.append("memory_pre")
        return mock_ctx

    async def mock_eval(run_log=None):
        call_order.append("eval_pre" if run_log is None else "eval_post")

    async def mock_triage(memory_context=None, oldest=None):
        call_order.append("triage")
        return mock_run_log

    async def mock_post(run_log):
        call_order.append("memory_post")

    with patch("run_triage.memory_runner.pre_run", side_effect=mock_pre), \
         patch("run_triage.run_eval_step",         side_effect=mock_eval), \
         patch("run_triage.triage_run",            side_effect=mock_triage), \
         patch("run_triage.memory_runner.post_run", side_effect=mock_post):
        await run_triage.main()

    assert call_order == ["memory_pre", "eval_pre", "triage", "eval_post", "memory_post"]


@pytest.mark.asyncio
async def test_run_triage_main_passes_memory_context_to_triage():
    """main() passes the MemoryContext returned by pre_run to triage_run."""
    import importlib
    import run_triage
    importlib.reload(run_triage)

    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import EpisodeStore
    mock_ctx = MemoryContext(semantic_injection="## Patterns", episode_store=EpisodeStore())
    mock_run_log = MagicMock()
    received_ctx = []

    async def mock_triage(memory_context=None, oldest=None):
        received_ctx.append(memory_context)
        return mock_run_log

    with patch("run_triage.memory_runner.pre_run", new=AsyncMock(return_value=mock_ctx)), \
         patch("run_triage.run_eval_step",         new=AsyncMock()), \
         patch("run_triage.triage_run",            side_effect=mock_triage), \
         patch("run_triage.memory_runner.post_run", new=AsyncMock()):
        await run_triage.main()

    assert len(received_ctx) == 1
    assert received_ctx[0] is mock_ctx
