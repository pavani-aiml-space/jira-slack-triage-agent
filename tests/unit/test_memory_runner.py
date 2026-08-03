"""
Unit tests for pipeline/memory_runner.py
All external calls mocked — no real disk I/O, no real embeddings.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Chunk 3.1 — MemoryContext + pre_run() ────────────────────────────────────

@pytest.mark.asyncio
async def test_pre_run_returns_memory_context():
    from pipeline.memory_runner import pre_run, MemoryContext
    from pipeline.episode_store import EpisodeStore
    from pipeline.semantic_store import SemanticStore
    with patch("pipeline.memory_runner.load_episode_store", return_value=EpisodeStore()), \
         patch("pipeline.memory_runner.load_semantic_store", return_value=SemanticStore()), \
         patch("pipeline.memory_runner.build_semantic_injection", return_value="## Patterns\n- Bug:High"):
        ctx = await pre_run()
    assert isinstance(ctx, MemoryContext)
    assert ctx.semantic_injection == "## Patterns\n- Bug:High"
    assert isinstance(ctx.episode_store, EpisodeStore)


@pytest.mark.asyncio
async def test_pre_run_returns_empty_context_on_load_error():
    from pipeline.memory_runner import pre_run, MemoryContext
    with patch("pipeline.memory_runner.load_episode_store", side_effect=Exception("disk error")):
        ctx = await pre_run()
    assert isinstance(ctx, MemoryContext)
    assert ctx.semantic_injection == ""


@pytest.mark.asyncio
async def test_pre_run_returns_empty_injection_when_no_patterns():
    from pipeline.memory_runner import pre_run, MemoryContext
    from pipeline.episode_store import EpisodeStore
    from pipeline.semantic_store import SemanticStore
    with patch("pipeline.memory_runner.load_episode_store", return_value=EpisodeStore()), \
         patch("pipeline.memory_runner.load_semantic_store", return_value=SemanticStore()), \
         patch("pipeline.memory_runner.build_semantic_injection", return_value=""):
        ctx = await pre_run()
    assert ctx.semantic_injection == ""


# ── Chunk 3.2 — post_run() episode write ─────────────────────────────────────

def _make_run_log_with_ticket():
    from pipeline.run_logger import RunLog, BlockResult, LlmStats
    run_log = RunLog(
        run_id="r1", started_at="2026-01-01", completed_at=None, status="success",
        messages_fetched=1, blocks_processed=1, tickets_created_count=1,
        clarifications_asked_count=0, blocks_skipped_count=0, error_count=0,
    )
    run_log.blocks = [BlockResult(
        block_index=0, block_snippet="Login crash", action="ticket_created",
        ticket_key="SCRUM-8", ticket_summary="Login crashes",
        ticket_type="Bug", ticket_priority="High",
        llm=LlmStats(1, [], "stop", 10, 5),
    )]
    return run_log


def _make_run_log_with_clarification():
    from pipeline.run_logger import RunLog, BlockResult
    run_log = RunLog("r1", "2026-01-01", None, "success", 1, 1, 0, 1, 0, 0)
    run_log.blocks = [BlockResult(0, "snip", "clarification_asked")]
    return run_log


@pytest.mark.asyncio
async def test_post_run_writes_episode_for_ticket_created_block():
    from pipeline.memory_runner import post_run
    from pipeline.episode_store import EpisodeStore
    from pipeline.semantic_store import SemanticStore

    with patch("pipeline.memory_runner.embed_texts",
               new_callable=AsyncMock, return_value=[[0.1, 0.2]]) as mock_embed, \
         patch("pipeline.memory_runner.add_episode") as mock_add, \
         patch("pipeline.memory_runner.load_episode_store", return_value=EpisodeStore()), \
         patch("pipeline.memory_runner.save_episode_store") as mock_save, \
         patch("pipeline.memory_runner.load_semantic_store", return_value=SemanticStore()), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]):
        await post_run(_make_run_log_with_ticket())

    mock_embed.assert_called_once()
    mock_add.assert_called_once()
    mock_save.assert_called()


@pytest.mark.asyncio
async def test_post_run_skips_non_ticket_blocks():
    from pipeline.memory_runner import post_run
    from pipeline.episode_store import EpisodeStore
    from pipeline.semantic_store import SemanticStore

    with patch("pipeline.memory_runner.embed_texts", new_callable=AsyncMock) as mock_embed, \
         patch("pipeline.memory_runner.load_episode_store", return_value=EpisodeStore()), \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store", return_value=SemanticStore()), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]):
        await post_run(_make_run_log_with_clarification())

    mock_embed.assert_not_called()


@pytest.mark.asyncio
async def test_post_run_continues_when_embed_fails():
    """Rule 5 — embed failure must not crash post_run."""
    from pipeline.memory_runner import post_run
    from pipeline.episode_store import EpisodeStore
    from pipeline.semantic_store import SemanticStore

    with patch("pipeline.memory_runner.embed_texts",
               new_callable=AsyncMock, side_effect=Exception("network error")), \
         patch("pipeline.memory_runner.load_episode_store", return_value=EpisodeStore()), \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store", return_value=SemanticStore()), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]):
        await post_run(_make_run_log_with_ticket())  # must not raise


# ── Chunk 3.3 — post_run() semantic extraction trigger ───────────────────────

@pytest.mark.asyncio
async def test_post_run_triggers_extraction_when_threshold_met():
    from pipeline.memory_runner import post_run
    from pipeline.episode_store import EpisodeStore, Episode
    from pipeline.semantic_store import SemanticStore

    # 4 existing + 1 new from run_log = 5 total; delta = 5 >= threshold=5 → trigger
    existing = [Episode("r", 0, "s", "K", "Bug", "High", "s", [0.1], "2026-01-01")] * 4

    with patch("pipeline.memory_runner.embed_texts",
               new_callable=AsyncMock, return_value=[[0.1]]), \
         patch("pipeline.memory_runner.load_episode_store",
               return_value=EpisodeStore(episodes=list(existing))), \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store",
               return_value=SemanticStore(last_extracted_episode_count=0)), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]) as mock_extract:
        await post_run(_make_run_log_with_ticket())
    mock_extract.assert_called_once()


@pytest.mark.asyncio
async def test_post_run_skips_extraction_when_below_threshold():
    from pipeline.memory_runner import post_run
    from pipeline.episode_store import EpisodeStore, Episode
    from pipeline.semantic_store import SemanticStore

    # 2 existing + 1 new = 3 total; delta = 3 < threshold=5 → no extraction
    existing = [Episode("r", 0, "s", "K", "Bug", "High", "s", [0.1], "2026-01-01")] * 2

    with patch("pipeline.memory_runner.embed_texts",
               new_callable=AsyncMock, return_value=[[0.1]]), \
         patch("pipeline.memory_runner.load_episode_store",
               return_value=EpisodeStore(episodes=list(existing))), \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store",
               return_value=SemanticStore(last_extracted_episode_count=0)), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=[]) as mock_extract:
        await post_run(_make_run_log_with_ticket())
    mock_extract.assert_not_called()


@pytest.mark.asyncio
async def test_post_run_calls_summarise_when_enough_patterns():
    from pipeline.memory_runner import post_run
    from pipeline.episode_store import EpisodeStore, Episode
    from pipeline.semantic_store import SemanticStore, Pattern

    # 5 existing episodes give delta >= threshold so extraction triggers
    existing = [Episode("r", 0, "s", "K", "Bug", "High", "s", [0.1], "2026-01-01")] * 4
    patterns = [
        Pattern(f"Bug:High{i}", 5, [], "t", "2026-01-01", "count_based")
        for i in range(3)
    ]

    with patch("pipeline.memory_runner.embed_texts",
               new_callable=AsyncMock, return_value=[[0.1]]), \
         patch("pipeline.memory_runner.load_episode_store",
               return_value=EpisodeStore(episodes=list(existing))), \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store",
               return_value=SemanticStore(last_extracted_episode_count=0)), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=patterns), \
         patch("pipeline.memory_runner.summarise_with_llm",
               new_callable=AsyncMock, return_value=patterns) as mock_summ:
        await post_run(_make_run_log_with_ticket())
    mock_summ.assert_called_once()


@pytest.mark.asyncio
async def test_post_run_skips_summarise_when_too_few_patterns():
    from pipeline.memory_runner import post_run
    from pipeline.episode_store import EpisodeStore, Episode
    from pipeline.semantic_store import SemanticStore, Pattern

    # Only 1 pattern but SEMANTIC_LLM_MIN_PATTERNS default = 3 → skip LLM summarise
    existing = [Episode("r", 0, "s", "K", "Bug", "High", "s", [0.1], "2026-01-01")] * 4
    patterns = [Pattern("Bug:High", 5, [], "t", "2026-01-01", "count_based")]

    with patch("pipeline.memory_runner.embed_texts",
               new_callable=AsyncMock, return_value=[[0.1]]), \
         patch("pipeline.memory_runner.load_episode_store",
               return_value=EpisodeStore(episodes=list(existing))), \
         patch("pipeline.memory_runner.save_episode_store"), \
         patch("pipeline.memory_runner.load_semantic_store",
               return_value=SemanticStore(last_extracted_episode_count=0)), \
         patch("pipeline.memory_runner.save_semantic_store"), \
         patch("pipeline.memory_runner.extract_count_patterns", return_value=patterns), \
         patch("pipeline.memory_runner.summarise_with_llm",
               new_callable=AsyncMock) as mock_summ:
        await post_run(_make_run_log_with_ticket())
    mock_summ.assert_not_called()
