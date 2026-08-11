"""
Unit tests for triage_agent.py

All tool executors and the LLM provider are mocked — no real API calls.
"""
from contextlib import ExitStack

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.triage.triage_agent import _classify_block, _execute_decisions, run
from agents.llm.base import LLMTurn, ToolCall, LLMProviderError
from pipeline.run_logger import BlockResult, LlmStats


# ── helpers ───────────────────────────────────────────────────────────────────

def make_llm_turn(
    finish_reason: str = "stop",
    content: str | None = "Done",
    tool_calls: list | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> LLMTurn:
    return LLMTurn(
        finish_reason=finish_reason,
        content=content,
        tool_calls=tool_calls or [],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def make_decisions_turn(decisions: list, prompt_tokens: int = 10, completion_tokens: int = 5) -> LLMTurn:
    """A turn where the model calls submit_triage_decisions with the given decision list."""
    return LLMTurn(
        finish_reason="tool_calls",
        content=None,
        tool_calls=[ToolCall(id="tc_decisions", name="submit_triage_decisions", args={"decisions": decisions})],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def make_stats() -> LlmStats:
    return LlmStats(
        iterations=1, tools_called=["submit_triage_decisions"],
        finish_reason="tool_calls", prompt_tokens=10, completion_tokens=5,
    )


# ── settings ──────────────────────────────────────────────────────────────────

def test_settings_llm_provider_default_is_anthropic_when_env_unset(monkeypatch):
    """Code default is Claude; loaded .env may override — re-read getenv default."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    # Re-evaluate the same default expressions settings.py uses
    import os
    assert os.getenv("LLM_PROVIDER", "anthropic") == "anthropic"
    assert os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929") == "claude-sonnet-4-5-20250929"


def test_settings_has_llm_provider_configured():
    from config.settings import Settings
    assert Settings.LLM_PROVIDER in ("anthropic", "openai")


def test_triage_agent_exposes_provider_not_raw_client():
    import agents.triage.triage_agent as m
    assert hasattr(m, "_provider"), "_provider must exist at module level"
    assert not hasattr(m, "_client"), "_client must be removed"


# ── _classify_block — one structured call, no loop ───────────────────────────

@pytest.mark.asyncio
async def test_classify_block_makes_exactly_one_call():
    """No loop: one block always means one _provider.chat call, regardless of outcome."""
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        await _classify_block("Login is broken")

    mock_provider.chat.assert_called_once()


@pytest.mark.asyncio
async def test_classify_block_passes_system_prompt_and_block_text():
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        await _classify_block("crash on login page")

    messages = mock_provider.chat.call_args.args[0]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "crash on login page" in messages[1]["content"]


@pytest.mark.asyncio
async def test_classify_block_passes_only_submit_decisions_schema():
    """Exactly one tool is offered — there's nothing to loop between."""
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        await _classify_block("test")

    tools_passed = mock_provider.chat.call_args.args[1]
    assert len(tools_passed) == 1
    assert tools_passed[0]["function"]["name"] == "submit_triage_decisions"


@pytest.mark.asyncio
async def test_classify_block_parses_decisions_from_tool_call():
    decisions_in = [{"action": "create_ticket", "summary": "x"}]
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_decisions_turn(decisions_in))
        decisions, stats = await _classify_block("test")

    assert decisions == decisions_in
    assert stats.iterations == 1
    assert stats.tools_called == ["submit_triage_decisions"]


@pytest.mark.asyncio
async def test_classify_block_returns_empty_list_when_no_decisions_given():
    decisions_in: list = []
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_decisions_turn(decisions_in))
        decisions, stats = await _classify_block("just a comment")

    assert decisions == []


@pytest.mark.asyncio
async def test_classify_block_returns_empty_list_when_no_tool_call():
    """Graceful degrade (Rule 5) — a malformed turn with no tool call yields [], not a crash."""
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        decisions, stats = await _classify_block("test")

    assert decisions == []
    assert stats.tools_called == []
    assert stats.finish_reason == "stop"


@pytest.mark.asyncio
async def test_classify_block_accumulates_token_stats():
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(
            return_value=make_decisions_turn([], prompt_tokens=300, completion_tokens=60)
        )
        _, stats = await _classify_block("test")

    assert stats.prompt_tokens == 300
    assert stats.completion_tokens == 60


@pytest.mark.asyncio
async def test_classify_block_appends_episode_context_when_given():
    captured_messages = []

    async def capture_chat(messages, tools, system=""):
        captured_messages.extend(messages)
        return make_llm_turn(finish_reason="stop")

    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = capture_chat
        await _classify_block(
            "Login crash",
            episode_context="## Similar past decisions\n- [SCRUM-1] \"login bug\" → Bug, High (2026-04-30)",
        )

    user_messages = [m for m in captured_messages if isinstance(m, dict) and m.get("role") == "user"]
    assert len(user_messages) == 1
    assert "## Similar past decisions" in user_messages[0]["content"]
    assert "Login crash" in user_messages[0]["content"]


@pytest.mark.asyncio
async def test_classify_block_omits_episode_context_when_empty():
    captured_messages = []

    async def capture_chat(messages, tools, system=""):
        captured_messages.extend(messages)
        return make_llm_turn(finish_reason="stop")

    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = capture_chat
        await _classify_block("Login crash")

    user_messages = [m for m in captured_messages if isinstance(m, dict) and m.get("role") == "user"]
    assert len(user_messages) == 1
    assert "## Similar past decisions" not in user_messages[0]["content"]
    assert "Login crash" in user_messages[0]["content"]


# ── _execute_decisions — deterministic dispatch, no model involved ──────────

@pytest.mark.asyncio
async def test_execute_decisions_empty_list_returns_single_no_action():
    stats = make_stats()
    results = await _execute_decisions([], block_index=0, block_snippet="s", run_id="r1", llm_stats=stats)

    assert len(results) == 1
    assert results[0].action == "no_action"
    assert results[0].llm is stats


@pytest.mark.asyncio
async def test_execute_decisions_create_ticket_success_posts_confirmation():
    decision = {
        "action": "create_ticket", "summary": "Login crash", "issue_type": "Bug",
        "priority": "High", "description": "crashes on empty password", "confidence": 0.9,
    }
    with patch("agents.triage.triage_agent.create_jira_ticket", new_callable=AsyncMock,
               return_value="Created SCRUM-11: Login crash → https://example.atlassian.net/browse/SCRUM-11") as mock_jira:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock) as mock_post:
            with patch("agents.triage.triage_agent.drain_confirmation_ts", return_value="1714406400.123"):
                results = await _execute_decisions(
                    [decision], block_index=0, block_snippet="s", run_id="r1", llm_stats=make_stats()
                )

    assert len(results) == 1
    result = results[0]
    assert result.action == "ticket_created"
    assert result.ticket_key == "SCRUM-11"
    assert result.ticket_summary == "Login crash"
    assert result.ticket_type == "Bug"
    assert result.ticket_priority == "High"
    assert result.confirmation_ts == "1714406400.123"
    mock_post.assert_called_once()
    assert "Created SCRUM-11" in mock_post.call_args[0][0]
    assert mock_jira.call_args.kwargs["block_context"] == {"run_id": "r1", "block_index": 0, "block_snippet": "s"}


@pytest.mark.asyncio
async def test_execute_decisions_confirmation_ts_none_when_drain_returns_none():
    decision = {"action": "create_ticket", "summary": "x", "issue_type": "Bug",
                "priority": "High", "description": "d", "confidence": 0.95}
    with patch("agents.triage.triage_agent.create_jira_ticket", new_callable=AsyncMock,
               return_value="Created SCRUM-1: x"):
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.drain_confirmation_ts", return_value=None):
                results = await _execute_decisions(
                    [decision], block_index=0, block_snippet="s", run_id="r1", llm_stats=make_stats()
                )

    assert results[0].confirmation_ts is None


@pytest.mark.asyncio
async def test_execute_decisions_escalated_does_not_post_extra_confirmation():
    """escalate_for_confirmation() already posted the proposal — dispatch must not post again."""
    decision = {"action": "create_ticket", "summary": "Vague issue", "issue_type": "Bug",
                "priority": "Medium", "description": "not much detail", "confidence": 0.4}
    with patch("agents.triage.triage_agent.create_jira_ticket", new_callable=AsyncMock,
               return_value="[ESCALATED] Low confidence (0.40) — posted for human confirmation."):
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock) as mock_post:
            results = await _execute_decisions(
                [decision], block_index=0, block_snippet="s", run_id="r1", llm_stats=make_stats()
            )

    assert results[0].action == "escalated_for_confirmation"
    assert results[0].ticket_key is None
    assert results[0].ticket_summary == "Vague issue"
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_execute_decisions_jira_error_does_not_post_extra_confirmation():
    """_create_ticket_in_jira() already notified Slack on failure — dispatch must not post again."""
    decision = {"action": "create_ticket", "summary": "x", "issue_type": "Bug",
                "priority": "High", "description": "d", "confidence": 0.95}
    with patch("agents.triage.triage_agent.create_jira_ticket", new_callable=AsyncMock,
               return_value="[JIRA_ERROR] Jira unavailable — team notified in Slack."):
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock) as mock_post:
            results = await _execute_decisions(
                [decision], block_index=0, block_snippet="s", run_id="r1", llm_stats=make_stats()
            )

    assert results[0].action == "error"
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_execute_decisions_ask_clarification():
    decision = {"action": "ask_clarification", "question": "Which page crashes?"}
    with patch("agents.triage.triage_agent.ask_for_clarification", new_callable=AsyncMock) as mock_clarify:
        results = await _execute_decisions(
            [decision], block_index=1, block_snippet="s", run_id="r1", llm_stats=make_stats()
        )

    assert results[0].action == "clarification_asked"
    assert results[0].ticket_key is None
    mock_clarify.assert_called_once_with("Which page crashes?")


@pytest.mark.asyncio
async def test_execute_decisions_duplicate_posts_note():
    """A conversational duplicate ('already filed as SCRUM-9') the embedding gate can't see."""
    decision = {"action": "duplicate", "note": "Already filed as SCRUM-9", "ticket_key": "SCRUM-9"}
    with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock) as mock_post:
        results = await _execute_decisions(
            [decision], block_index=0, block_snippet="s", run_id="r1", llm_stats=make_stats()
        )

    assert results[0].action == "duplicate_flagged"
    assert results[0].ticket_key == "SCRUM-9"
    assert "SCRUM-9" in mock_post.call_args[0][0]


@pytest.mark.asyncio
async def test_execute_decisions_unknown_action_returns_no_action():
    results = await _execute_decisions(
        [{"action": "something_unexpected"}], block_index=0, block_snippet="s",
        run_id="r1", llm_stats=make_stats(),
    )
    assert results[0].action == "no_action"


@pytest.mark.asyncio
async def test_execute_decisions_multiple_decisions_produce_multiple_results():
    """A block with two distinct issues yields two independent BlockResults, not one overwritten record."""
    decisions = [
        {"action": "create_ticket", "summary": "Login bug", "issue_type": "Bug",
         "priority": "High", "description": "d1", "confidence": 0.9},
        {"action": "create_ticket", "summary": "Dark mode request", "issue_type": "Story",
         "priority": "Low", "description": "d2", "confidence": 0.85},
    ]
    with patch("agents.triage.triage_agent.create_jira_ticket", new_callable=AsyncMock, side_effect=[
        "Created SCRUM-1: Login bug → https://example.atlassian.net/browse/SCRUM-1",
        "Created SCRUM-2: Dark mode request → https://example.atlassian.net/browse/SCRUM-2",
    ]):
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.drain_confirmation_ts", return_value=None):
                results = await _execute_decisions(
                    decisions, block_index=0, block_snippet="s", run_id="r1", llm_stats=make_stats()
                )

    assert len(results) == 2
    assert results[0].ticket_key == "SCRUM-1"
    assert results[0].ticket_summary == "Login bug"
    assert results[1].ticket_key == "SCRUM-2"
    assert results[1].ticket_summary == "Dark mode request"


@pytest.mark.asyncio
async def test_execute_decisions_attaches_llm_stats_to_first_result_only():
    """All decisions came from one LLM call — duplicating token counts would double-count them."""
    decisions = [
        {"action": "ask_clarification", "question": "q1?"},
        {"action": "ask_clarification", "question": "q2?"},
    ]
    stats = make_stats()
    with patch("agents.triage.triage_agent.ask_for_clarification", new_callable=AsyncMock):
        results = await _execute_decisions(
            decisions, block_index=0, block_snippet="s", run_id="r1", llm_stats=stats
        )

    assert results[0].llm is stats
    assert results[1].llm is None


# ── run() — helper fixtures ───────────────────────────────────────────────────

def make_one_block(text="bug report"):
    return [{"combined_text": text, "start_ts": "1.0", "end_ts": "1.0", "messages": []}]


def patch_run_deps(
    blocks,
    decisions=None,
    execute_side_effect=None,
    execute_return=None,
    duplicate_match=None,
):
    """
    Pre-loaded ExitStack of safe no-op defaults for run() tests: message/block
    fetch, Phase 4 duplicate-detection functions, and the classify/execute
    call sites. Returns (stack, mocks) — `with stack:` in the test, and use
    mocks["classify_block"] / mocks["execute_decisions"] for assertions.

    _classify_block's return value rarely matters on its own here since
    _execute_decisions is independently mocked (it ignores whatever decisions
    it's handed unless execute_side_effect reads them) — override `decisions`
    only for tests that care what _classify_block itself produced.
    """
    stack = ExitStack()
    mocks = {}

    mocks["fetch_messages"] = stack.enter_context(patch(
        "agents.triage.triage_agent.fetch_messages", new_callable=AsyncMock,
        return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}],
    ))
    stack.enter_context(patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks))

    mocks["classify_block"] = stack.enter_context(patch(
        "agents.triage.triage_agent._classify_block", new_callable=AsyncMock,
        return_value=(decisions or [], make_stats()),
    ))
    if execute_side_effect is not None:
        mocks["execute_decisions"] = stack.enter_context(patch(
            "agents.triage.triage_agent._execute_decisions", new_callable=AsyncMock,
            side_effect=execute_side_effect,
        ))
    else:
        mocks["execute_decisions"] = stack.enter_context(patch(
            "agents.triage.triage_agent._execute_decisions", new_callable=AsyncMock,
            return_value=execute_return if execute_return is not None else [],
        ))

    # Phase 4 safe defaults — duplicate check does nothing unless overridden
    mocks["fetch_open_tickets"] = stack.enter_context(patch(
        "agents.triage.triage_agent.fetch_open_tickets", new_callable=AsyncMock, return_value=[]
    ))
    stack.enter_context(patch("agents.triage.triage_agent.load_embedding_cache", return_value={}))
    mocks["build_embedding_cache"] = stack.enter_context(patch(
        "agents.triage.triage_agent.build_embedding_cache", new_callable=AsyncMock, return_value={}
    ))
    stack.enter_context(patch(
        "agents.triage.triage_agent.embed_texts", new_callable=AsyncMock, return_value=[[0.5, 0.5]]
    ))
    stack.enter_context(patch("agents.triage.triage_agent.find_duplicate", return_value=duplicate_match))
    mocks["add_ticket_to_cache"] = stack.enter_context(patch(
        "agents.triage.triage_agent.add_ticket_to_cache", return_value={}
    ))

    return stack, mocks


def make_block_result(index=0, action="ticket_created", key="SCRUM-1"):
    return BlockResult(
        block_index=index, block_snippet="test snippet",
        action=action, ticket_key=key,
        ticket_summary="Test summary", ticket_type="Bug", ticket_priority="High",
    )


# ── run() — Rule 6: LLM provider error handler ───────────────────────────────

@pytest.mark.asyncio
async def test_run_openai_error_posts_slack_alert_and_exits():
    """When execution raises LLMProviderError, Slack alert is posted and process exits 1."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_side_effect=LLMProviderError("API unavailable"))
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock) as mock_post:
            with patch("agents.triage.triage_agent.write_run_log"):
                with pytest.raises(SystemExit) as exc_info:
                    await run()

    assert exc_info.value.code == 1
    mock_post.assert_called_once()
    assert "LLM" in mock_post.call_args[0][0]


@pytest.mark.asyncio
async def test_run_openai_error_slack_also_down_writes_stdout(capsys):
    """When LLM and Slack both fail, error is written to stdout and exits 1."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_side_effect=LLMProviderError("API unavailable"))
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock,
                   side_effect=Exception("Slack also down")):
            with patch("agents.triage.triage_agent.write_run_log"):
                with pytest.raises(SystemExit) as exc_info:
                    await run()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "TRIAGE AGENT FATAL" in captured.out


# ── run() — Rule 5: Slack MCP accumulator ────────────────────────────────────

@pytest.mark.asyncio
async def test_run_slack_error_continues_to_next_block():
    """When execution raises on block 1, block 2 is still processed."""
    call_count = 0

    async def execute_fail_first(decisions, block_index=0, block_snippet="", run_id="", llm_stats=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Slack MCP broken pipe")
        return [make_block_result(index=block_index)]

    two_blocks = [
        {"combined_text": "bug1", "start_ts": "1.0", "end_ts": "1.0", "messages": []},
        {"combined_text": "bug2", "start_ts": "2.0", "end_ts": "2.0", "messages": []},
    ]
    stack, mocks = patch_run_deps(blocks=two_blocks, execute_side_effect=execute_fail_first)
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()

    assert call_count == 2


@pytest.mark.asyncio
async def test_run_slack_error_does_not_swallow_openai_error():
    """LLMProviderError is NOT silently swallowed by the broad except Exception handler."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_side_effect=LLMProviderError("API unavailable"))
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                with pytest.raises(SystemExit):
                    await run()
    # Reaching here via SystemExit means Rule 6 fired — not swallowed by Rule 5


# ── run() — Rule 5: consolidated Slack post ──────────────────────────────────

@pytest.mark.asyncio
async def test_run_consolidated_error_post_when_slack_errors():
    """When a block fails with a Slack error, consolidated post is sent at end of run."""
    stack, mocks = patch_run_deps(
        blocks=make_one_block("login is broken"),
        execute_side_effect=Exception("Slack MCP pipe broken"),
    )
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock) as mock_post:
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()

    # First call is the consolidated Rule 5 error summary
    assert mock_post.called
    first_call_text = mock_post.call_args_list[0][0][0]
    assert "⚠️" in first_call_text
    assert "login is broken" in first_call_text


@pytest.mark.asyncio
async def test_run_consolidated_post_fails_writes_stdout(capsys):
    """When consolidated post also fails, error is written to stdout and exits 1."""
    stack, mocks = patch_run_deps(
        blocks=make_one_block("login is broken"),
        execute_side_effect=Exception("Slack MCP pipe broken"),
    )
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock,
                   side_effect=Exception("Slack completely down")):
            with patch("agents.triage.triage_agent.write_run_log"):
                with pytest.raises(SystemExit) as exc_info:
                    await run()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "TRIAGE AGENT ERROR" in captured.out
    assert "login is broken" in captured.out


# ── run() — builds RunLog ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_writes_log_file():
    """run() calls write_run_log after completing blocks."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    mock_write.assert_called_once()
    run_log_arg = mock_write.call_args[0][0]
    assert run_log_arg.tickets_created_count == 1
    assert run_log_arg.status == "success"


@pytest.mark.asyncio
async def test_run_log_has_block_results():
    """run() extends run_log.blocks with every BlockResult _execute_decisions returns."""
    stack, mocks = patch_run_deps(
        blocks=make_one_block(),
        execute_return=[make_block_result(action="clarification_asked", key=None)],
    )
    with stack:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    run_log_arg = mock_write.call_args[0][0]
    assert len(run_log_arg.blocks) == 1
    assert run_log_arg.blocks[0].action == "clarification_asked"


@pytest.mark.asyncio
async def test_run_log_multiple_results_from_one_block_all_recorded():
    """A block that yields two decisions produces two entries in run_log.blocks."""
    two_results = [
        make_block_result(index=0, action="ticket_created", key="SCRUM-1"),
        make_block_result(index=0, action="ticket_created", key="SCRUM-2"),
    ]
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=two_results)
    with stack:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    run_log_arg = mock_write.call_args[0][0]
    assert len(run_log_arg.blocks) == 2
    assert run_log_arg.tickets_created_count == 2


@pytest.mark.asyncio
async def test_run_log_counts_clarifications():
    """run() counts clarification_asked results in clarifications_asked_count."""
    stack, mocks = patch_run_deps(
        blocks=make_one_block(),
        execute_return=[make_block_result(action="clarification_asked", key=None)],
    )
    with stack:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    log = mock_write.call_args[0][0]
    assert log.clarifications_asked_count == 1
    assert log.tickets_created_count == 0


@pytest.mark.asyncio
async def test_run_log_counts_duplicate_flagged_from_execute_decisions():
    """A duplicate action recognized during classification (not the embedding gate) still counts."""
    stack, mocks = patch_run_deps(
        blocks=make_one_block(),
        execute_return=[BlockResult(block_index=0, block_snippet="s", action="duplicate_flagged", ticket_key="SCRUM-9")],
    )
    with stack:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    log = mock_write.call_args[0][0]
    assert log.duplicates_flagged_count == 1


# ── _print_block_outcome ──────────────────────────────────────────────────────

def test_print_block_outcome_ticket_created(capsys):
    from agents.triage.triage_agent import _print_block_outcome
    result = make_block_result(index=0, action="ticket_created", key="SCRUM-11")
    _print_block_outcome(result, index=0, total=4)
    out = capsys.readouterr().out
    assert "[Block 1/4]" in out
    assert "SCRUM-11" in out
    assert "Bug" in out


def test_print_block_outcome_clarification(capsys):
    from agents.triage.triage_agent import _print_block_outcome
    result = BlockResult(block_index=1, block_snippet="x", action="clarification_asked")
    _print_block_outcome(result, index=1, total=4)
    out = capsys.readouterr().out
    assert "[Block 2/4]" in out
    assert "larification" in out


def test_print_block_outcome_error(capsys):
    from agents.triage.triage_agent import _print_block_outcome
    result = BlockResult(block_index=2, block_snippet="x", action="error")
    _print_block_outcome(result, index=2, total=4)
    out = capsys.readouterr().out
    assert "[Block 3/4]" in out
    assert "Error" in out


def test_print_block_outcome_duplicate_without_ticket_key_does_not_crash(capsys):
    """A duplicate found by classification (not the embedding gate) may have no ticket_key."""
    from agents.triage.triage_agent import _print_block_outcome
    result = BlockResult(block_index=0, block_snippet="x", action="duplicate_flagged", ticket_key=None)
    _print_block_outcome(result, index=0, total=1)
    out = capsys.readouterr().out
    assert "[Block 1/1]" in out


# ── _compute_status + _print_run_summary ─────────────────────────────────────

def make_run_log(status="", error_count=0, tickets=1, clarifications=0):
    from pipeline.run_logger import RunLog
    return RunLog(
        run_id="2026-04-29T13:00:00", started_at="x", completed_at="x",
        status=status, messages_fetched=5, blocks_processed=2,
        tickets_created_count=tickets, clarifications_asked_count=clarifications,
        blocks_skipped_count=0, error_count=error_count,
    )


def test_compute_status_success_when_no_errors():
    from agents.triage.triage_agent import _compute_status
    assert _compute_status(make_run_log(error_count=0)) == "success"


def test_compute_status_partial_when_errors():
    from agents.triage.triage_agent import _compute_status
    assert _compute_status(make_run_log(error_count=1)) == "partial"


def test_print_run_summary_contains_key_fields(capsys):
    from agents.triage.triage_agent import _print_run_summary
    log = make_run_log(status="partial", error_count=1, tickets=2, clarifications=1)
    _print_run_summary(log, log_path="logs/run_test.json")
    out = capsys.readouterr().out
    assert "Run Summary" in out
    assert "2" in out  # tickets
    assert "partial" in out
    assert "logs/run_test.json" in out
    assert "Duplicates flagged" in out


# ── _post_slack_summary ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_slack_summary_success_run():
    from agents.triage.triage_agent import _post_slack_summary
    log = make_run_log(status="success", tickets=2)
    with patch("agents.triage.triage_agent.post_slack_message",
               new_callable=AsyncMock) as mock_post:
        await _post_slack_summary(log)
    mock_post.assert_called_once()
    assert "✅" in mock_post.call_args[0][0]
    assert "2" in mock_post.call_args[0][0]


@pytest.mark.asyncio
async def test_post_slack_summary_suppressed_on_fatal():
    from agents.triage.triage_agent import _post_slack_summary
    log = make_run_log(status="fatal")
    with patch("agents.triage.triage_agent.post_slack_message",
               new_callable=AsyncMock) as mock_post:
        await _post_slack_summary(log)
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_post_slack_summary_failure_logs_stdout(capsys):
    """If Slack post fails, print to stdout and do NOT exit."""
    from agents.triage.triage_agent import _post_slack_summary
    log = make_run_log(status="success", tickets=1)
    with patch("agents.triage.triage_agent.post_slack_message",
               new_callable=AsyncMock, side_effect=Exception("Slack down")):
        await _post_slack_summary(log)   # must NOT raise
    out = capsys.readouterr().out
    assert "summary" in out.lower() or "slack" in out.lower()


# ── Fatal handler writes log ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_openai_error_writes_fatal_log():
    """When execution raises LLMProviderError, a status='fatal' log is written before exiting."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_side_effect=LLMProviderError("API unavailable"))
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                with pytest.raises(SystemExit):
                    await run()
    mock_write.assert_called_once()
    run_log_arg = mock_write.call_args[0][0]
    assert run_log_arg.status == "fatal"


# ── run() — Phase 4: duplicate gate ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_fetches_open_tickets_in_parallel():
    """fetch_open_tickets is called once per run."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()
    mocks["fetch_open_tickets"].assert_called_once()


@pytest.mark.asyncio
async def test_run_builds_embedding_cache_after_fetch():
    """build_embedding_cache is called once with the fetched tickets."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()
    mocks["build_embedding_cache"].assert_called_once()


@pytest.mark.asyncio
async def test_run_flags_duplicate_when_similarity_above_threshold():
    """When find_duplicate returns a match, classify + execute are skipped entirely."""
    match = {"key": "SCRUM-5", "summary": "Login crash", "similarity": 0.92}
    stack, mocks = patch_run_deps(
        blocks=make_one_block("login is broken"), execute_return=[make_block_result()], duplicate_match=match,
    )
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock) as mock_post:
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()

    mocks["classify_block"].assert_not_called()
    mocks["execute_decisions"].assert_not_called()
    assert mock_post.called
    first_call_text = mock_post.call_args_list[0][0][0]
    assert "SCRUM-5" in first_call_text


@pytest.mark.asyncio
async def test_run_proceeds_to_classify_when_no_duplicate():
    """When find_duplicate returns None, classify + execute run normally."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()
    mocks["classify_block"].assert_called_once()
    mocks["execute_decisions"].assert_called_once()


@pytest.mark.asyncio
async def test_run_increments_duplicates_flagged_count():
    """When a duplicate is found by the embedding gate, run_log.duplicates_flagged_count is incremented."""
    match = {"key": "SCRUM-5", "summary": "Login crash", "similarity": 0.92}
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()], duplicate_match=match)
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                await run()
    log = mock_write.call_args[0][0]
    assert log.duplicates_flagged_count == 1


@pytest.mark.asyncio
async def test_run_adds_new_ticket_to_cache_after_creation():
    """After a ticket is created, add_ticket_to_cache is called with the ticket key."""
    result_with_ticket = make_block_result(action="ticket_created", key="SCRUM-12")
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[result_with_ticket])
    with stack:
        with patch("agents.triage.triage_agent.add_ticket_to_cache") as mock_add:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                with patch("agents.triage.triage_agent.write_run_log"):
                    await run()
    mock_add.assert_called_once()
    assert mock_add.call_args[0][1] == "SCRUM-12"


# ── run() — returns RunLog ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_returns_run_log():
    """run() must return a RunLog, not None."""
    from pipeline.run_logger import RunLog
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                result = await run()
    assert result is not None
    assert isinstance(result, RunLog)


# ── Memory integration tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_with_memory_context_injects_semantic_into_system_prompt():
    """When memory_context has a semantic_injection, it is appended to SYSTEM_PROMPT."""
    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import EpisodeStore

    ctx = MemoryContext(
        semantic_injection="## Learned Patterns\n- Bug:High (8 decisions)",
        episode_store=EpisodeStore(),
    )
    captured_prompts = []

    async def capture_chat(messages, tools, system=""):
        captured_prompts.append(messages[0]["content"])   # system message
        return make_llm_turn(finish_reason="stop")

    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}]), \
         patch("agents.triage.triage_agent.build_context_blocks", return_value=make_one_block()), \
         patch("agents.triage.triage_agent.fetch_open_tickets", new_callable=AsyncMock, return_value=[]), \
         patch("agents.triage.triage_agent.load_embedding_cache", return_value={}), \
         patch("agents.triage.triage_agent.build_embedding_cache", new_callable=AsyncMock, return_value={}), \
         patch("agents.triage.triage_agent.embed_texts", new_callable=AsyncMock, return_value=[[0.5, 0.5]]), \
         patch("agents.triage.triage_agent.find_duplicate", return_value=None), \
         patch("agents.triage.triage_agent.add_ticket_to_cache", return_value={}), \
         patch("agents.triage.triage_agent._provider") as mock_provider, \
         patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock), \
         patch("agents.triage.triage_agent.write_run_log"):
        mock_provider.chat = capture_chat
        await run(memory_context=ctx)

    assert len(captured_prompts) >= 1
    assert "## Learned Patterns" in captured_prompts[0]
    assert "Bug:High" in captured_prompts[0]


@pytest.mark.asyncio
async def test_run_with_no_memory_context_uses_original_prompt():
    """run() with memory_context=None behaves like the pre-memory default."""
    from pipeline.run_logger import RunLog
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                result = await run(memory_context=None)
    assert isinstance(result, RunLog)


# ── run() retrieves episodes deterministically via block_emb ────────────────
# patch_run_deps mocks embed_texts to return [[0.5, 0.5]] — that's block_emb.

@pytest.mark.asyncio
async def test_run_injects_episode_context_when_similarity_clears_threshold():
    """A stored episode with an embedding matching block_emb is passed into _classify_block."""
    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import Episode, EpisodeStore

    ep = Episode(
        run_id="r1", block_index=0, block_snippet="login bug",
        ticket_key="SCRUM-1", ticket_type="Bug", ticket_priority="High",
        ticket_summary="Fix login crash", embedding=[0.5, 0.5],  # identical direction to block_emb
        run_ts="2026-04-30T12:00:00",
    )
    ctx = MemoryContext(semantic_injection="", episode_store=EpisodeStore(episodes=[ep]))
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run(memory_context=ctx)

    episode_context = mocks["classify_block"].call_args.kwargs["episode_context"]
    assert "SCRUM-1" in episode_context
    assert "## Similar past decisions" in episode_context


@pytest.mark.asyncio
async def test_run_omits_episode_context_when_similarity_below_threshold():
    """A stored episode whose embedding doesn't resemble block_emb is not injected."""
    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import Episode, EpisodeStore

    ep = Episode(
        run_id="r1", block_index=0, block_snippet="dark mode request",
        ticket_key="SCRUM-9", ticket_type="Story", ticket_priority="Low",
        ticket_summary="Add dark mode toggle", embedding=[1.0, -1.0],  # orthogonal to block_emb
        run_ts="2026-04-30T12:00:00",
    )
    ctx = MemoryContext(semantic_injection="", episode_store=EpisodeStore(episodes=[ep]))
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run(memory_context=ctx)

    assert mocks["classify_block"].call_args.kwargs["episode_context"] == ""


@pytest.mark.asyncio
async def test_run_omits_episode_context_when_no_memory_context():
    """When memory_context is None, episode_context passed to _classify_block is empty."""
    stack, mocks = patch_run_deps(blocks=make_one_block(), execute_return=[make_block_result()])
    with stack:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run(memory_context=None)

    assert mocks["classify_block"].call_args.kwargs["episode_context"] == ""


# ── run() — Phase 10: resolve pending confirmations ──────────────────────────

@pytest.mark.asyncio
async def test_run_resolves_pending_confirmations_when_items_exist():
    from pipeline.pending_confirmation_store import PendingConfirmationStore

    stack, mocks = patch_run_deps(blocks=[])
    non_empty_store = PendingConfirmationStore(items=[MagicMock()])
    with stack:
        with patch("agents.triage.triage_agent.load_pending_store", return_value=non_empty_store):
            with patch("agents.triage.triage_agent.resolve_pending_confirmations",
                       new_callable=AsyncMock, return_value=non_empty_store) as mock_resolve:
                with patch("agents.triage.triage_agent.save_pending_store") as mock_save:
                    with patch("agents.triage.triage_agent.write_run_log"):
                        await run()

    mock_resolve.assert_called_once_with(non_empty_store)
    mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_run_skips_resolution_when_no_pending_items():
    from pipeline.pending_confirmation_store import PendingConfirmationStore

    stack, mocks = patch_run_deps(blocks=[])
    empty_store = PendingConfirmationStore(items=[])
    with stack:
        with patch("agents.triage.triage_agent.load_pending_store", return_value=empty_store):
            with patch("agents.triage.triage_agent.resolve_pending_confirmations",
                       new_callable=AsyncMock) as mock_resolve:
                with patch("agents.triage.triage_agent.write_run_log"):
                    await run()

    mock_resolve.assert_not_called()
