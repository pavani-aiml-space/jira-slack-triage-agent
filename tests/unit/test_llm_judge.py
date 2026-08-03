"""Unit tests for pipeline/llm_judge.py"""
import json
from unittest.mock import AsyncMock, patch

import pytest

import config.settings as cs
from agents.llm.base import LLMProviderError, LLMTurn
from pipeline.llm_judge import judge_one_block, run_judge_for_run
from pipeline.run_logger import BlockResult, RunLog


@pytest.mark.asyncio
async def test_judge_one_block_slack_context_overrides_snippet_in_prompt():
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMTurn(
            finish_reason="stop",
            content='{"type":5,"priority":5,"title":5,"description":5,"reason":"ok"}',
            tool_calls=[],
        )
    )
    block = BlockResult(0, "SHORT", "ticket_created", ticket_key="K-1")
    await judge_one_block(provider, "r1", block, slack_context="LONGER SLACK BODY FOR CALIBRATION")
    call_messages = provider.chat.call_args[0][0]
    user_content = call_messages[1]["content"]
    assert "LONGER SLACK BODY FOR CALIBRATION" in user_content
    assert "Slack conversation excerpt" in user_content


@pytest.mark.asyncio
async def test_judge_one_block_parses_json_scores():
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMTurn(
            finish_reason="stop",
            content='{"type":5,"priority":4,"title":5,"description":3,"reason":"Good fit"}',
            tool_calls=[],
            prompt_tokens=100,
            completion_tokens=20,
        )
    )
    block = BlockResult(
        block_index=0,
        block_snippet="Login returns 500",
        action="ticket_created",
        ticket_key="SCRUM-1",
        ticket_summary="Fix login 500",
        ticket_type="Bug",
        ticket_priority="High",
        ticket_description="What: login fails\nSteps: open /login",
    )
    entry = await judge_one_block(provider, "2026-04-30T10:00:00", block)
    assert entry.error is None
    assert entry.type_score == 5
    assert entry.priority_score == 4
    assert entry.title_score == 5
    assert entry.description_score == 3
    assert "Good" in entry.reason


@pytest.mark.asyncio
async def test_judge_one_block_parses_fenced_json():
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMTurn(
            finish_reason="stop",
            content="```json\n{\"type\":3,\"priority\":3,\"title\":3,\"description\":3,\"reason\":\"x\"}\n```",
            tool_calls=[],
        )
    )
    block = BlockResult(0, "x", "ticket_created", ticket_key="K-1")
    entry = await judge_one_block(provider, "r1", block)
    assert entry.error is None
    assert entry.type_score == 3


@pytest.mark.asyncio
async def test_judge_one_block_llm_provider_error():
    provider = AsyncMock()
    provider.chat = AsyncMock(side_effect=LLMProviderError("rate limited"))
    block = BlockResult(0, "x", "ticket_created", ticket_key="K-1")
    entry = await judge_one_block(provider, "r1", block)
    assert entry.error == "rate limited"
    assert entry.type_score is None


@pytest.mark.asyncio
async def test_judge_one_block_invalid_scores():
    provider = AsyncMock()
    provider.chat = AsyncMock(
        return_value=LLMTurn(
            finish_reason="stop",
            content='{"type":99,"priority":1,"title":1,"description":1,"reason":"bad"}',
            tool_calls=[],
        )
    )
    block = BlockResult(0, "x", "ticket_created", ticket_key="K-1")
    entry = await judge_one_block(provider, "r1", block)
    assert entry.error is not None
    assert "invalid" in entry.error.lower()


@pytest.mark.asyncio
async def test_run_judge_for_run_skips_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(cs.settings, "ENABLE_LLM_JUDGE", False)
    monkeypatch.setattr(cs.settings, "JUDGE_STORE_PATH", str(tmp_path / "j.json"))
    log = RunLog(
        run_id="r1",
        started_at="",
        completed_at="",
        status="success",
        messages_fetched=0,
        blocks_processed=1,
        tickets_created_count=1,
        clarifications_asked_count=0,
        blocks_skipped_count=0,
        error_count=0,
    )
    log.blocks.append(
        BlockResult(
            block_index=0,
            block_snippet="bug",
            action="ticket_created",
            ticket_key="SCRUM-1",
            ticket_summary="s",
            ticket_type="Bug",
            ticket_priority="High",
        )
    )
    with patch("pipeline.llm_judge.append_judge_entries") as mock_append:
        await run_judge_for_run(log)
    mock_append.assert_not_called()


@pytest.mark.asyncio
async def test_run_judge_for_run_writes_store(monkeypatch, tmp_path):
    monkeypatch.setattr(cs.settings, "ENABLE_LLM_JUDGE", True)
    monkeypatch.setattr(cs.settings, "JUDGE_STORE_PATH", str(tmp_path / "judge_store.json"))
    log = RunLog(
        run_id="2026-04-30T15:00:00",
        started_at="",
        completed_at="",
        status="success",
        messages_fetched=1,
        blocks_processed=1,
        tickets_created_count=1,
        clarifications_asked_count=0,
        blocks_skipped_count=0,
        error_count=0,
    )
    log.blocks.append(
        BlockResult(
            block_index=0,
            block_snippet="Export fails",
            action="ticket_created",
            ticket_key="SCRUM-2",
            ticket_summary="Fix export",
            ticket_type="Bug",
            ticket_priority="Medium",
        )
    )
    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(
        return_value=LLMTurn(
            finish_reason="stop",
            content=json.dumps(
                {
                    "type": 4,
                    "priority": 4,
                    "title": 4,
                    "description": 4,
                    "reason": "Coherent",
                }
            ),
            tool_calls=[],
        )
    )
    with patch("pipeline.llm_judge.get_judge_llm_provider", return_value=mock_provider):
        await run_judge_for_run(log)

    data = json.loads((tmp_path / "judge_store.json").read_text())
    assert len(data["scores"]) == 1
    assert data["scores"][0]["ticket_key"] == "SCRUM-2"
    assert data["scores"][0]["type_score"] == 4
    mock_provider.chat.assert_called_once()
