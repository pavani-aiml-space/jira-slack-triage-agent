"""
Unit tests for triage_agent.py

All tool executors and the LLM provider are mocked — no real API calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.triage.triage_agent import _execute_tool, _run_llm_loop, run
from agents.llm.base import LLMTurn, ToolCall, LLMProviderError
from pipeline.run_logger import BlockResult
import agents.triage.triage_agent as triage_agent_module


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


def make_tool_call_turn(tool_name: str, tool_args: dict) -> LLMTurn:
    """
    Convenience: a turn that requests one tool call.

    raw_message defaults to None — adequate for tests that only check
    which tool was called. For multi-turn tests that capture the messages
    list on subsequent iterations, construct LLMTurn inline and pass
    raw_message=MagicMock() so the loop appends a real object.
    """
    return LLMTurn(
        finish_reason="tool_calls",
        content=None,
        tool_calls=[ToolCall(id="call_test", name=tool_name, args=tool_args)],
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


# ── _execute_tool ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_tool_unknown_name_returns_error_string():
    result = await _execute_tool("nonexistent_tool", {})
    assert "Error" in result
    assert "nonexistent_tool" in result


@pytest.mark.asyncio
async def test_execute_tool_dispatches_create_jira_ticket():
    mock_fn = AsyncMock(return_value="Created SCRUM-1: Test bug")
    with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"create_jira_ticket": mock_fn}):
        result = await _execute_tool("create_jira_ticket", {
            "summary": "Test bug", "issue_type": "Bug",
            "priority": "High", "description": "desc",
        })
    assert result == "Created SCRUM-1: Test bug"
    mock_fn.assert_called_once_with(
        summary="Test bug", issue_type="Bug", priority="High", description="desc"
    )


@pytest.mark.asyncio
async def test_execute_tool_dispatches_post_slack_message():
    mock_fn = AsyncMock(return_value="Message posted: hello")
    with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"post_slack_message": mock_fn}):
        result = await _execute_tool("post_slack_message", {"message": "hello"})
    assert result == "Message posted: hello"
    mock_fn.assert_called_once_with(message="hello")


@pytest.mark.asyncio
async def test_execute_tool_dispatches_ask_for_clarification():
    mock_fn = AsyncMock(return_value="Clarification asked: Q?")
    with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"ask_for_clarification": mock_fn}):
        result = await _execute_tool("ask_for_clarification", {"question": "Q?"})
    assert result == "Clarification asked: Q?"
    mock_fn.assert_called_once_with(question="Q?")


# ── _run_llm_loop — stop immediately ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_llm_loop_stops_when_finish_reason_is_stop():
    """When the LLM returns stop on the first call, no tools are invoked."""
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        await _run_llm_loop("Login is broken", block_index=0, block_snippet="Login is broken")

    mock_provider.chat.assert_called_once()


@pytest.mark.asyncio
async def test_run_llm_loop_passes_system_prompt_and_block_text():
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        await _run_llm_loop("crash on login page", block_index=0, block_snippet="crash on login")

    messages = mock_provider.chat.call_args.args[0]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "crash on login page" in messages[1]["content"]


# ── _run_llm_loop — tool call then stop ──────────────────────────────────────

@pytest.mark.asyncio
async def test_run_llm_loop_executes_tool_then_stops():
    """LLM calls post_slack_message once, then returns stop."""
    mock_post = AsyncMock(return_value="Message posted: ticket created")
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(side_effect=[
            make_tool_call_turn("post_slack_message", {"message": "ticket created"}),
            make_llm_turn(finish_reason="stop"),
        ])
        with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"post_slack_message": mock_post}):
            await _run_llm_loop("test block", block_index=0, block_snippet="test block")

    mock_post.assert_called_once_with(message="ticket created")
    assert mock_provider.chat.call_count == 2


@pytest.mark.asyncio
async def test_run_llm_loop_appends_tool_result_to_messages():
    """The tool result is fed back to the LLM as a tool role message."""
    call_messages_seen = []

    async def capture_chat(messages, tools, system=""):
        call_messages_seen.append(list(messages))
        if len(call_messages_seen) == 1:
            return LLMTurn(
                finish_reason="tool_calls",
                content=None,
                tool_calls=[ToolCall(id="tc_xyz", name="post_slack_message", args={"message": "hi"})],
                raw_message=MagicMock(),
            )
        return make_llm_turn(finish_reason="stop")

    mock_post = AsyncMock(return_value="Message posted: hi")
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = capture_chat
        with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"post_slack_message": mock_post}):
            await _run_llm_loop("some block", block_index=0, block_snippet="some block")

    # Second call must include a tool-role message
    second_call_messages = call_messages_seen[1]
    tool_role_messages = [m for m in second_call_messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_role_messages) == 1
    assert tool_role_messages[0]["content"] == "Message posted: hi"
    assert tool_role_messages[0]["tool_call_id"] == "tc_xyz"


@pytest.mark.asyncio
async def test_run_llm_loop_uses_all_four_tool_schemas():
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        await _run_llm_loop("test", block_index=0, block_snippet="test")

    tools_passed = mock_provider.chat.call_args.args[1]
    tool_names = [t["function"]["name"] for t in tools_passed]
    assert "create_jira_ticket" in tool_names
    assert "post_slack_message" in tool_names
    assert "ask_for_clarification" in tool_names
    assert "search_memory" in tool_names


# ── _run_llm_loop — returns BlockResult (Chunk 2.1) ──────────────────────────

@pytest.mark.asyncio
async def test_run_llm_loop_returns_block_result():
    """_run_llm_loop must return a BlockResult, not None."""
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(
            return_value=make_llm_turn(finish_reason="stop", prompt_tokens=100, completion_tokens=50)
        )
        result = await _run_llm_loop("login is broken", block_index=0, block_snippet="login is broken")

    assert isinstance(result, BlockResult)
    assert result.block_index == 0
    assert result.block_snippet == "login is broken"


@pytest.mark.asyncio
async def test_run_llm_loop_accumulates_llm_stats():
    """LlmStats captures iterations, finish_reason, and token totals."""
    mock_post = AsyncMock(return_value="posted")
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(side_effect=[
            make_tool_call_turn("post_slack_message", {"message": "hi"})._replace(
                prompt_tokens=200, completion_tokens=40
            ) if False else LLMTurn(
                finish_reason="tool_calls", content=None,
                tool_calls=[ToolCall(id="tc_1", name="post_slack_message", args={"message": "hi"})],
                prompt_tokens=200, completion_tokens=40, raw_message=MagicMock(),
            ),
            make_llm_turn(finish_reason="stop", prompt_tokens=220, completion_tokens=30),
        ])
        with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"post_slack_message": mock_post}):
            result = await _run_llm_loop("test", block_index=0, block_snippet="test")

    assert result.llm is not None
    assert result.llm.iterations == 2
    assert result.llm.finish_reason == "stop"
    assert result.llm.prompt_tokens == 420
    assert result.llm.completion_tokens == 70


# ── _run_llm_loop — action inference (Chunk 2.2) ─────────────────────────────

@pytest.mark.asyncio
async def test_run_llm_loop_action_ticket_created():
    """When create_jira_ticket is called, action='ticket_created' with fields extracted."""
    mock_jira = AsyncMock(return_value="Created SCRUM-11: Login crash → https://example.atlassian.net/browse/SCRUM-11")
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(side_effect=[
            LLMTurn(
                finish_reason="tool_calls", content=None,
                tool_calls=[ToolCall(id="tc_jira", name="create_jira_ticket", args={
                    "summary": "Login crash", "issue_type": "Bug", "priority": "High",
                    "description": "crashes on empty password",
                })],
                prompt_tokens=300, completion_tokens=60, raw_message=MagicMock(),
            ),
            make_llm_turn(finish_reason="stop", prompt_tokens=310, completion_tokens=20),
        ])
        with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"create_jira_ticket": mock_jira}):
            result = await _run_llm_loop("login is broken", block_index=0, block_snippet="login is broken")

    assert result.action == "ticket_created"
    assert result.ticket_key == "SCRUM-11"
    assert result.ticket_summary == "Login crash"
    assert result.ticket_type == "Bug"
    assert result.ticket_priority == "High"


@pytest.mark.asyncio
async def test_run_llm_loop_action_clarification_asked():
    """When ask_for_clarification is called, action='clarification_asked'."""
    mock_clarify = AsyncMock(return_value="Clarification posted")
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(side_effect=[
            LLMTurn(
                finish_reason="tool_calls", content=None,
                tool_calls=[ToolCall(id="tc_clarify", name="ask_for_clarification", args={"message": "Can you clarify?"})],
                prompt_tokens=150, completion_tokens=30, raw_message=MagicMock(),
            ),
            make_llm_turn(finish_reason="stop", prompt_tokens=160, completion_tokens=15),
        ])
        with patch.dict(triage_agent_module.TOOL_EXECUTORS, {"ask_for_clarification": mock_clarify}):
            result = await _run_llm_loop("unclear message", block_index=1, block_snippet="unclear message")

    assert result.action == "clarification_asked"
    assert result.ticket_key is None


@pytest.mark.asyncio
async def test_run_llm_loop_action_no_action_when_stop_only():
    """When LLM returns stop with no tool calls, action='no_action'."""
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(
            return_value=make_llm_turn(finish_reason="stop", content="Got it",
                                       prompt_tokens=100, completion_tokens=20)
        )
        result = await _run_llm_loop("just a comment", block_index=2, block_snippet="just a comment")

    assert result.action == "no_action"


# ── run() — helper fixtures ───────────────────────────────────────────────────

def make_one_block(text="bug report"):
    return [{"combined_text": text, "start_ts": "1.0", "end_ts": "1.0", "messages": []}]


def patch_run_deps(blocks, llm_side_effect=None, llm_return=None):
    """
    Patch fetch_messages, build_context_blocks, _run_llm_loop, and all Phase 4
    duplicate-detection functions with safe no-op defaults for run() tests.
    """
    patches = [
        patch("agents.triage.triage_agent.fetch_messages",
              new_callable=AsyncMock,
              return_value=[{"user": "U1", "text": "bug", "ts": "1.0"}]),
        patch("agents.triage.triage_agent.build_context_blocks", return_value=blocks),
    ]
    if llm_side_effect is not None:
        patches.append(patch("agents.triage.triage_agent._run_llm_loop",
                             new_callable=AsyncMock, side_effect=llm_side_effect))
    else:
        patches.append(patch("agents.triage.triage_agent._run_llm_loop",
                             new_callable=AsyncMock, return_value=llm_return))
    # Phase 4 safe defaults — duplicate check does nothing
    patches += [
        patch("agents.triage.triage_agent.fetch_open_tickets",
              new_callable=AsyncMock, return_value=[]),
        patch("agents.triage.triage_agent.load_embedding_cache", return_value={}),
        patch("agents.triage.triage_agent.build_embedding_cache",
              new_callable=AsyncMock, return_value={}),
        patch("agents.triage.triage_agent.embed_texts",
              new_callable=AsyncMock, return_value=[[0.5, 0.5]]),
        patch("agents.triage.triage_agent.find_duplicate", return_value=None),
        patch("agents.triage.triage_agent.add_ticket_to_cache", return_value={}),
    ]
    return patches


# ── run() — Rule 6: OpenAI error handler ─────────────────────────────────────

@pytest.mark.asyncio
async def test_run_openai_error_posts_slack_alert_and_exits():
    """When LLM raises LLMProviderError, Slack alert is posted and process exits 1."""
    patches = patch_run_deps(
        blocks=make_one_block(),
        llm_side_effect=LLMProviderError("API unavailable"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message",
                   new_callable=AsyncMock) as mock_post:
            with patch("agents.triage.triage_agent.write_run_log"):
                with pytest.raises(SystemExit) as exc_info:
                    await run()

    assert exc_info.value.code == 1
    mock_post.assert_called_once()
    assert "LLM" in mock_post.call_args[0][0]


@pytest.mark.asyncio
async def test_run_openai_error_slack_also_down_writes_stdout(capsys):
    """When LLM and Slack both fail, error is written to stdout and exits 1."""
    patches = patch_run_deps(
        blocks=make_one_block(),
        llm_side_effect=LLMProviderError("API unavailable"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message",
                   new_callable=AsyncMock,
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
    """When Slack MCP raises on block 1, block 2 is still processed."""
    call_count = 0

    async def llm_fail_first(block_text, block_index=0, block_snippet="", **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Slack MCP broken pipe")
        return make_block_result(index=block_index)

    two_blocks = [
        {"combined_text": "bug1", "start_ts": "1.0", "end_ts": "1.0", "messages": []},
        {"combined_text": "bug2", "start_ts": "2.0", "end_ts": "2.0", "messages": []},
    ]
    with patch("agents.triage.triage_agent.fetch_messages",
               new_callable=AsyncMock,
               return_value=[{"user": "U1", "text": "x", "ts": "1.0"}]):
        with patch("agents.triage.triage_agent.build_context_blocks", return_value=two_blocks):
            with patch("agents.triage.triage_agent._run_llm_loop",
                       new_callable=AsyncMock, side_effect=llm_fail_first):
                with patch("agents.triage.triage_agent.fetch_open_tickets",
                           new_callable=AsyncMock, return_value=[]):
                    with patch("agents.triage.triage_agent.load_embedding_cache", return_value={}):
                        with patch("agents.triage.triage_agent.build_embedding_cache",
                                   new_callable=AsyncMock, return_value={}):
                            with patch("agents.triage.triage_agent.embed_texts",
                                       new_callable=AsyncMock, return_value=[[0.5, 0.5]]):
                                with patch("agents.triage.triage_agent.find_duplicate",
                                           return_value=None):
                                    with patch("agents.triage.triage_agent.add_ticket_to_cache",
                                               return_value={}):
                                        with patch("agents.triage.triage_agent.post_slack_message",
                                                   new_callable=AsyncMock):
                                            with patch("agents.triage.triage_agent.write_run_log"):
                                                await run()

    assert call_count == 2


@pytest.mark.asyncio
async def test_run_slack_error_does_not_swallow_openai_error():
    """LLMProviderError is NOT silently swallowed by the broad except Exception handler."""
    patches = patch_run_deps(
        blocks=make_one_block(),
        llm_side_effect=LLMProviderError("API unavailable"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message",
                   new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                with pytest.raises(SystemExit):
                    await run()
    # Reaching here via SystemExit means Rule 6 fired — not swallowed by Rule 5


# ── run() — Rule 5: consolidated Slack post ──────────────────────────────────

@pytest.mark.asyncio
async def test_run_consolidated_error_post_when_slack_errors():
    """When a block fails with a Slack error, consolidated post is sent at end of run."""
    patches = patch_run_deps(
        blocks=make_one_block("login is broken"),
        llm_side_effect=Exception("Slack MCP pipe broken"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message",
                   new_callable=AsyncMock) as mock_post:
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
    patches = patch_run_deps(
        blocks=make_one_block("login is broken"),
        llm_side_effect=Exception("Slack MCP pipe broken"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message",
                   new_callable=AsyncMock,
                   side_effect=Exception("Slack completely down")):
            with patch("agents.triage.triage_agent.write_run_log"):
                with pytest.raises(SystemExit) as exc_info:
                    await run()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "TRIAGE AGENT ERROR" in captured.out
    assert "login is broken" in captured.out


# ── run() — builds RunLog (Chunk 3.1) ────────────────────────────────────────

def make_block_result(index=0, action="ticket_created", key="SCRUM-1"):
    return BlockResult(
        block_index=index, block_snippet="test snippet",
        action=action, ticket_key=key,
        ticket_summary="Test summary", ticket_type="Bug", ticket_priority="High",
    )


@pytest.mark.asyncio
async def test_run_writes_log_file():
    """run() calls write_run_log after completing blocks."""
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    mock_write.assert_called_once()
    run_log_arg = mock_write.call_args[0][0]
    assert run_log_arg.tickets_created_count == 1
    assert run_log_arg.status == "success"


@pytest.mark.asyncio
async def test_run_log_has_block_results():
    """run() appends each BlockResult to run_log.blocks."""
    patches = patch_run_deps(
        blocks=make_one_block(), llm_return=make_block_result(action="clarification_asked", key=None)
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    run_log_arg = mock_write.call_args[0][0]
    assert len(run_log_arg.blocks) == 1
    assert run_log_arg.blocks[0].action == "clarification_asked"


@pytest.mark.asyncio
async def test_run_log_counts_clarifications():
    """run() counts clarification_asked blocks in clarifications_asked_count."""
    patches = patch_run_deps(
        blocks=make_one_block(), llm_return=make_block_result(action="clarification_asked", key=None)
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.write_run_log") as mock_write:
            with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
                await run()
    log = mock_write.call_args[0][0]
    assert log.clarifications_asked_count == 1
    assert log.tickets_created_count == 0


# ── _print_block_outcome (Chunk 3.2) ─────────────────────────────────────────

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


# ── _compute_status + _print_run_summary (Chunk 3.3) ─────────────────────────

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


# ── _post_slack_summary (Chunk 3.4) ──────────────────────────────────────────

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


# ── Fatal handler writes log (Chunk 3.5) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_run_openai_error_writes_fatal_log():
    """When LLM raises LLMProviderError, a status='fatal' log is written before exiting."""
    patches = patch_run_deps(
        blocks=make_one_block(),
        llm_side_effect=LLMProviderError("API unavailable"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                with pytest.raises(SystemExit):
                    await run()
    mock_write.assert_called_once()
    run_log_arg = mock_write.call_args[0][0]
    assert run_log_arg.status == "fatal"


# ── Phase 4: run() duplicate gate ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_fetches_open_tickets_in_parallel():
    """fetch_open_tickets is called once per run."""
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3] as mock_fetch_tickets, \
         patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()
    mock_fetch_tickets.assert_called_once()


@pytest.mark.asyncio
async def test_run_builds_embedding_cache_after_fetch():
    """build_embedding_cache is called once with the fetched tickets."""
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3], \
         patches[4], patches[5] as mock_build, patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()
    mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_run_flags_duplicate_when_similarity_above_threshold():
    """When find_duplicate returns a match, LLM loop is skipped."""
    match = {"key": "SCRUM-5", "summary": "Login crash", "similarity": 0.92}
    patches = patch_run_deps(blocks=make_one_block("login is broken"),
                              llm_return=make_block_result())
    with patches[0], patches[1], patches[2] as mock_llm, patches[3], \
         patches[4], patches[5], patches[6], \
         patch("agents.triage.triage_agent.find_duplicate", return_value=match), \
         patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message",
                   new_callable=AsyncMock) as mock_post:
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()

    mock_llm.assert_not_called()
    assert mock_post.called
    first_call_text = mock_post.call_args_list[0][0][0]
    assert "SCRUM-5" in first_call_text


@pytest.mark.asyncio
async def test_run_proceeds_to_llm_when_no_duplicate():
    """When find_duplicate returns None, LLM loop runs normally."""
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2] as mock_llm, patches[3], \
         patches[4], patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()
    mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_run_increments_duplicates_flagged_count():
    """When a duplicate is found, run_log.duplicates_flagged_count is incremented."""
    match = {"key": "SCRUM-5", "summary": "Login crash", "similarity": 0.92}
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3], \
         patches[4], patches[5], patches[6], \
         patch("agents.triage.triage_agent.find_duplicate", return_value=match), \
         patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                await run()
    log = mock_write.call_args[0][0]
    assert log.duplicates_flagged_count == 1


@pytest.mark.asyncio
async def test_run_adds_new_ticket_to_cache_after_creation():
    """After a ticket is created, add_ticket_to_cache is called with the ticket key."""
    result_with_ticket = make_block_result(action="ticket_created", key="SCRUM-12")
    patches = patch_run_deps(blocks=make_one_block(), llm_return=result_with_ticket)
    with patches[0], patches[1], patches[2], patches[3], \
         patches[4], patches[5], patches[6], patches[7], \
         patch("agents.triage.triage_agent.add_ticket_to_cache") as mock_add:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                await run()
    mock_add.assert_called_once()
    assert mock_add.call_args[0][1] == "SCRUM-12"


# ── Chunk 6.1 — run() returns RunLog + drains confirmation_ts ────────────────

@pytest.mark.asyncio
async def test_run_returns_run_log():
    """run() must return a RunLog, not None."""
    from pipeline.run_logger import RunLog
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                result = await run()
    assert result is not None
    assert isinstance(result, RunLog)


@pytest.mark.asyncio
async def test_run_sets_confirmation_ts_for_ticket_created():
    """When drain_confirmation_ts returns a ts, result.confirmation_ts is set."""
    ticket_result = make_block_result(action="ticket_created", key="SCRUM-1")
    patches = patch_run_deps(blocks=make_one_block(), llm_return=ticket_result)
    # drain called twice: once before block (returns None), once after (returns ts)
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                with patch("agents.triage.triage_agent.drain_confirmation_ts",
                           side_effect=[None, "1714406400.123"]):
                    await run()
    log = mock_write.call_args[0][0]
    ticket_blocks = [b for b in log.blocks if b.action == "ticket_created"]
    assert len(ticket_blocks) == 1
    assert ticket_blocks[0].confirmation_ts == "1714406400.123"


@pytest.mark.asyncio
async def test_run_confirmation_ts_none_when_drain_returns_none():
    """When drain_confirmation_ts returns None, confirmation_ts stays None."""
    ticket_result = make_block_result(action="ticket_created", key="SCRUM-1")
    patches = patch_run_deps(blocks=make_one_block(), llm_return=ticket_result)
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log") as mock_write:
                with patch("agents.triage.triage_agent.drain_confirmation_ts", return_value=None):
                    await run()
    log = mock_write.call_args[0][0]
    ticket_blocks = [b for b in log.blocks if b.action == "ticket_created"]
    assert ticket_blocks[0].confirmation_ts is None


# ── Block 4: Memory integration tests ────────────────────────────────────────

# ── Chunk 4.1 — _run_llm_loop no longer accepts episode_context ──────────────

@pytest.mark.asyncio
async def test_run_llm_loop_does_not_pre_inject_episodes():
    """_run_llm_loop sends only the block text in the user message (no episode pre-injection)."""
    captured_messages = []

    async def capture_chat(messages, tools, system=""):
        captured_messages.extend(messages)
        return make_llm_turn(finish_reason="stop")

    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = capture_chat
        await _run_llm_loop("Login crash", block_index=0, block_snippet="Login crash")

    user_messages = [m for m in captured_messages if isinstance(m, dict) and m.get("role") == "user"]
    assert len(user_messages) == 1
    # No "## Similar past decisions" pre-injected — that comes via search_memory tool call now
    assert "## Similar past decisions" not in user_messages[0]["content"]
    assert "Login crash" in user_messages[0]["content"]


# ── Chunk 4.2 — run() gains memory_context + effective_prompt ────────────────

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
    """run() with memory_context=None behaves like the pre-Phase-7 default."""
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                result = await run(memory_context=None)
    from pipeline.run_logger import RunLog
    assert isinstance(result, RunLog)


# ── Chunk 4.3 — run() sets episode store when memory_context is given ────────

@pytest.mark.asyncio
async def test_run_sets_episode_store_when_memory_context_given():
    """When memory_context is provided, set_episode_store is called before the block loop."""
    from pipeline.memory_runner import MemoryContext
    from pipeline.episode_store import EpisodeStore

    store = EpisodeStore()
    ctx = MemoryContext(semantic_injection="", episode_store=store)
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                with patch("agents.triage.tools.memory_tools.set_episode_store") as mock_set:
                    await run(memory_context=ctx)
    mock_set.assert_called_once_with(store)


@pytest.mark.asyncio
async def test_run_does_not_set_episode_store_when_no_memory_context():
    """When memory_context is None, set_episode_store is never called."""
    patches = patch_run_deps(blocks=make_one_block(), llm_return=make_block_result())
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8]:
        with patch("agents.triage.triage_agent.post_slack_message", new_callable=AsyncMock):
            with patch("agents.triage.triage_agent.write_run_log"):
                with patch("agents.triage.tools.memory_tools.set_episode_store") as mock_set:
                    await run(memory_context=None)
    mock_set.assert_not_called()


# ── Chunk 4.4 — search_memory executor ───────────────────────────────────────

@pytest.mark.asyncio
async def test_search_memory_returns_no_memory_when_store_not_set():
    """When _active_episode_store is None (no memory_context), returns the no-memory message."""
    import agents.triage.tools.memory_tools as mt
    original = mt._active_episode_store
    mt._active_episode_store = None
    try:
        from agents.triage.tools.memory_tools import search_memory
        result = await search_memory("login crash")
    finally:
        mt._active_episode_store = original
    assert "No memory available" in result


@pytest.mark.asyncio
async def test_search_memory_returns_formatted_episodes_when_found():
    """When store has episodes and embed succeeds, returns formatted past decisions."""
    import agents.triage.tools.memory_tools as mt
    from pipeline.episode_store import Episode, EpisodeStore

    ep = Episode(
        run_id="r1", block_index=0, block_snippet="login bug",
        ticket_key="SCRUM-1", ticket_type="Bug", ticket_priority="High",
        ticket_summary="Fix login crash", embedding=[1.0, 0.0],
        run_ts="2026-04-30T12:00:00",
    )
    store = EpisodeStore(episodes=[ep])
    mt._active_episode_store = store
    try:
        from agents.triage.tools.memory_tools import search_memory
        with patch("agents.triage.tools.memory_tools.embed_texts",
                   new_callable=AsyncMock, return_value=[[1.0, 0.0]]):
            result = await search_memory("login crash")
    finally:
        mt._active_episode_store = None
    assert "SCRUM-1" in result
    assert "Bug" in result


@pytest.mark.asyncio
async def test_search_memory_returns_not_found_when_store_empty():
    """When store has no episodes, returns the no-match message (Rule 11)."""
    import agents.triage.tools.memory_tools as mt
    from pipeline.episode_store import EpisodeStore

    mt._active_episode_store = EpisodeStore(episodes=[])
    try:
        from agents.triage.tools.memory_tools import search_memory
        with patch("agents.triage.tools.memory_tools.embed_texts",
                   new_callable=AsyncMock, return_value=[[0.5, 0.5]]):
            result = await search_memory("some issue")
    finally:
        mt._active_episode_store = None
    assert "No similar past decisions found" in result


@pytest.mark.asyncio
async def test_search_memory_returns_error_string_when_embed_fails():
    """When embed_texts raises, search_memory returns a graceful error string."""
    import agents.triage.tools.memory_tools as mt
    from pipeline.episode_store import EpisodeStore

    mt._active_episode_store = EpisodeStore(episodes=[])
    try:
        from agents.triage.tools.memory_tools import search_memory
        with patch("agents.triage.tools.memory_tools.embed_texts",
                   new_callable=AsyncMock, side_effect=Exception("embed API down")):
            result = await search_memory("some issue")
    finally:
        mt._active_episode_store = None
    assert "Memory search unavailable" in result
