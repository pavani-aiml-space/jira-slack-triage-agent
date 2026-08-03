"""
Triage Agent — LLM-driven orchestrator.

Execution order:
  1. IMPORTS & CONFIG     — load everything needed
  2. SYSTEM PROMPT        — rules given to the LLM provider
  3. ALL_TOOLS            — schemas the LLM provider can call
  4. TOOL_EXECUTORS       — map of name → Python function
  5. _execute_tool()      — runs whichever tool the LLM chose
  6. _run_llm_loop()      — the LLM tool-calling loop for one block
  7. run()                — main entry point: reads Slack → groups → loops

LLM provider is the brain. This file is the hands.
Provider is configured via settings.LLM_PROVIDER — default "openai".
To swap providers: create agents/llm/anthropic_provider.py and update factory.py.

Memory strategy (Option A — lazy retrieval):
  - Semantic patterns injected into SYSTEM_PROMPT once per run (small, run-level).
  - Episodes NOT pre-injected per block. Instead, the LLM calls search_memory()
    when uncertain. Zero episode tokens for clear-cut blocks (~80% of cases).
"""

# ── 1. IMPORTS & CONFIG ───────────────────────────────────────────────────────
import asyncio
import re
import sys
from datetime import datetime
from agents.llm.base import LLMProvider, LLMProviderError
from agents.llm.factory import get_llm_provider

from config.settings import settings
from pipeline.slack_reader import fetch_messages
from pipeline.context_builder import build_context_blocks
from pipeline.run_logger import BlockResult, LlmStats, RunLog, ErrorEntry, write_run_log
from pipeline.duplicate_detector import (
    fetch_open_tickets,
    embed_texts,
    load_embedding_cache,
    build_embedding_cache,
    find_duplicate,
    add_ticket_to_cache,
)

from agents.triage.tools.jira_tools import CREATE_JIRA_TICKET_SCHEMA, create_jira_ticket
from agents.triage.tools.slack_tools import (
    POST_SLACK_MESSAGE_SCHEMA,
    ASK_FOR_CLARIFICATION_SCHEMA,
    post_slack_message,
    ask_for_clarification,
    drain_confirmation_ts,
)
import agents.triage.tools.memory_tools as memory_tools
from agents.triage.tools.memory_tools import SEARCH_MEMORY_SCHEMA, search_memory
from pipeline.memory_runner import MemoryContext

_provider: LLMProvider = get_llm_provider(settings)


# ── 2. SYSTEM PROMPT — rules given to the LLM provider ──────────────────────
SYSTEM_PROMPT = """
You are a software triage agent monitoring a Slack channel for incoming messages.

Your job is to identify whether each conversation block contains an actionable software issue or request, then take exactly one appropriate action.

Classification options:
- Bug: Something is broken, failing, degraded, producing incorrect results, or behaving unexpectedly.
- Story: A user-facing feature request, product enhancement, or new capability.
- Task: Internal engineering, operational, documentation, cleanup, investigation, or maintenance work.
- Unclear: Not enough information to create a useful Jira ticket.
- Duplicate: The issue/request clearly matches an existing or already-created ticket mentioned in the conversation.

For each conversation block:
1. Read the full Slack conversation block, including replies and context.
2. Decide whether it is a Bug, Story, Task, Unclear, or Duplicate.
3. If it is Bug, Story, or Task:
   - Create a Jira ticket using create_jira_ticket.
   - Include a concise title, clear description, relevant Slack context, expected vs. actual behavior if available, affected users/scope, and priority.
   - Then call post_slack_message with the Jira ticket link and a short summary.
4. If it is Unclear:
   - Call ask_for_clarification.
   - Ask only for the missing details needed to create a good ticket.
   - Then call post_slack_message saying clarification was requested.
5. If it is Duplicate:
   - Do not create a Jira ticket.
   - Call post_slack_message explaining that it appears to be a duplicate and reference the known ticket or reason if available.

Priority guide:
- Critical: Production down, all or most users affected, data loss, security issue, payments blocked, or no workaround.
- High: Major feature broken, many users affected, serious degradation, workaround exists.
- Medium: Partial feature broken, some users affected, reasonable workaround available.
- Low: Cosmetic issue, minor annoyance, affects few users, small enhancement, documentation request.

Rules:
- Always reply in Slack after taking action.
- Do not create tickets from casual discussion, questions, jokes, or vague complaints unless there is a clear actionable issue.
- Do not infer missing technical details beyond what the Slack conversation supports.
- Prefer asking for clarification over creating a low-quality ticket.
- If multiple distinct actionable issues are reported, create separate Jira tickets.
- If one message contains related symptoms of the same root problem, create one ticket.
- Keep Jira titles short and specific.
- Include the original Slack message permalink if available.

Memory tool:
- Call search_memory when uncertain about ticket type, priority, or whether a similar issue has been triaged before.
- Pass a brief description of the current issue as the query.
- Do NOT call search_memory for clear, unambiguous bug reports or straightforward feature requests.
"""


# ── 3. ALL_TOOLS — schemas OpenAI can call ────────────────────────────────────
ALL_TOOLS = [
    CREATE_JIRA_TICKET_SCHEMA,
    POST_SLACK_MESSAGE_SCHEMA,
    ASK_FOR_CLARIFICATION_SCHEMA,
    SEARCH_MEMORY_SCHEMA,
]


# ── 4. TOOL_EXECUTORS — map of tool name → Python function ───────────────────
TOOL_EXECUTORS = {
    "create_jira_ticket":    create_jira_ticket,
    "post_slack_message":    post_slack_message,
    "ask_for_clarification": ask_for_clarification,
    "search_memory":         search_memory,
}


# ── 5. _execute_tool() — runs whichever tool OpenAI chose ────────────────────
async def _execute_tool(tool_name: str, tool_args: dict) -> str:
    """Look up the tool by name and execute it with the given args."""
    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return f"Error: unknown tool '{tool_name}'"

    print(f"  → Executing: {tool_name}({tool_args})")
    result = await executor(**tool_args)
    print(f"  → Result   : {result}")
    return result


# ── 6. _run_llm_loop() — OpenAI tool-calling loop for one block ──────────────

async def _run_llm_loop(
    block_text: str,
    block_index: int,
    block_snippet: str,
    effective_system_prompt: str = "",
) -> BlockResult:
    """
    Send one conversation block to the LLM provider and loop until it's done.

    effective_system_prompt: SYSTEM_PROMPT + optional semantic injection (set by run()).
    Episode context is NOT pre-injected here — the LLM calls search_memory() on demand
    when it needs precedent context (Option A lazy retrieval).
    Accumulates per-loop stats (iterations, tools called, token totals).
    Infers action from which tools were called.
    Returns a BlockResult — never raises (caller handles exceptions).
    """
    print(f"\n{'─' * 50}")
    print(f"Processing:\n  {block_text.replace(chr(10), chr(10) + '  ')}\n")

    system_prompt = effective_system_prompt or SYSTEM_PROMPT

    user_content = f"Slack message(s):\n\n{block_text}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_content},
    ]

    # Accumulators for LlmStats
    total_prompt_tokens     = 0
    total_completion_tokens = 0
    tools_called_names: list[str] = []
    jira_tool_args: dict = {}
    jira_tool_result: str = ""
    last_finish_reason = "stop"
    iteration_count = 0

    for iteration in range(settings.MAX_AGENT_ITERATIONS):
        iteration_count += 1
        turn = await _provider.chat(messages, ALL_TOOLS, system_prompt)
        finish             = turn.finish_reason
        last_finish_reason = finish

        total_prompt_tokens     += turn.prompt_tokens
        total_completion_tokens += turn.completion_tokens

        messages.append(turn.raw_message)
        print(f"  [iteration {iteration + 1}] finish_reason: {finish}")

        if finish == "stop":
            if turn.content:
                print(f"  LLM: {turn.content}")
            break

        if finish == "tool_calls":
            for tc in turn.tool_calls:
                tool_name = tc.name
                tool_args = tc.args          # already a dict — no json.loads needed
                tools_called_names.append(tool_name)

                if tool_name == "create_jira_ticket":
                    jira_tool_args = tool_args

                result = await _execute_tool(tool_name, tool_args)

                if tool_name == "create_jira_ticket":
                    jira_tool_result = result

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      result,
                })

    # ── Infer action ──────────────────────────────────────────────────────────
    llm_stats = LlmStats(
        iterations=iteration_count,
        tools_called=tools_called_names,
        finish_reason=last_finish_reason,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
    )

    if "create_jira_ticket" in tools_called_names:
        key_match = re.search(r"Created (\w+-\d+):", jira_tool_result)
        ticket_key = key_match.group(1) if key_match else None
        return BlockResult(
            block_index=block_index,
            block_snippet=block_snippet,
            action="ticket_created",
            ticket_key=ticket_key,
            ticket_summary=jira_tool_args.get("summary"),
            ticket_type=jira_tool_args.get("issue_type"),
            ticket_priority=jira_tool_args.get("priority"),
            ticket_description=jira_tool_args.get("description"),
            llm=llm_stats,
        )

    if "ask_for_clarification" in tools_called_names:
        return BlockResult(
            block_index=block_index,
            block_snippet=block_snippet,
            action="clarification_asked",
            llm=llm_stats,
        )

    if "post_slack_message" in tools_called_names:
        return BlockResult(
            block_index=block_index,
            block_snippet=block_snippet,
            action="clarification_asked",
            llm=llm_stats,
        )

    return BlockResult(
        block_index=block_index,
        block_snippet=block_snippet,
        action="no_action",
        llm=llm_stats,
    )


# ── 7. run() — main entry point ──────────────────────────────────────────────

def _compute_status(run_log: RunLog) -> str:
    """Return 'success' if no errors, 'partial' otherwise."""
    return "success" if run_log.error_count == 0 else "partial"


def _print_block_outcome(result: BlockResult, index: int, total: int) -> None:
    """Print [Block N/M] ✅ / 💬 / ⚠️ / 🔁 outcome line to stdout."""
    n = index + 1
    icons = {
        "ticket_created":      "✅ Ticket created   :",
        "clarification_asked": "💬 Clarification asked",
        "error":               "⚠️  Error            :",
        "no_action":           "—  No action",
        "duplicate_flagged":   "🔁 Duplicate flagged :",
    }
    label = icons.get(result.action, f"—  {result.action}")
    if result.action == "ticket_created":
        print(f"[Block {n}/{total}] {label} {result.ticket_key} "
              f'"{result.ticket_summary}" ({result.ticket_type} · {result.ticket_priority})')
    elif result.action == "duplicate_flagged":
        print(f"[Block {n}/{total}] {label} {result.ticket_key}")
    elif result.action == "error":
        print(f"[Block {n}/{total}] {label} logged")
    else:
        print(f"[Block {n}/{total}] {label}")


def _print_run_summary(run_log: RunLog, log_path: str) -> None:
    """Print the === Run Summary === block to stdout."""
    keys = [b.ticket_key for b in run_log.blocks
            if b.action == "ticket_created" and b.ticket_key]
    keys_str = f"  ({', '.join(keys)})" if keys else ""
    print(f"\n{'─' * 50}")
    print("=== Run Summary ===")
    print(f"  Blocks processed : {run_log.blocks_processed}")
    print(f"  Tickets created  : {run_log.tickets_created_count}{keys_str}")
    print(f"  Duplicates flagged: {run_log.duplicates_flagged_count}")
    print(f"  Clarifications   : {run_log.clarifications_asked_count}")
    print(f"  Errors           : {run_log.error_count}")
    print(f"  Status           : {run_log.status}")
    print(f"  Log written      : {log_path}")
    print(f"{'─' * 50}")


async def _post_slack_summary(run_log: RunLog) -> None:
    """Post brief end-of-run summary to Slack. Suppressed when status='fatal'."""
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


async def run(
    memory_context: MemoryContext | None = None,
    oldest: str | None = None,
) -> RunLog:
    """
    Main entry point for the triage agent.

    Steps:
      1. Fetch Slack messages + open Jira tickets in parallel
      2. Build embedding cache (diff-only re-embedding)
      3. Group messages into conversation blocks
      4. For each block: run duplicate gate, then LLM loop if no match
      5. Post consolidated Slack error report if any blocks failed
      6. Write run log and post Slack summary

    memory_context: optional MemoryContext from memory_runner.pre_run().
      - semantic_injection is appended to SYSTEM_PROMPT for the whole run.
      - episode_store is loaded into memory_tools for on-demand retrieval via search_memory().
        The LLM calls search_memory() when uncertain — zero episode tokens for clear blocks.

    oldest: optional watermark timestamp (Unix string, e.g. "1714045800.123").
      Only messages with ts > oldest are fetched from Slack.
      None = fetch the last MAX_MESSAGES_TO_FETCH messages (first run / bootstrap).

    Returns the completed RunLog for the run.
    """
    run_id = datetime.utcnow().isoformat(timespec="seconds")
    run_log = RunLog(
        run_id=run_id,
        started_at=run_id,
        completed_at=None,
        status="success",
        messages_fetched=0,
        blocks_processed=0,
        tickets_created_count=0,
        clarifications_asked_count=0,
        blocks_skipped_count=0,
        error_count=0,
        duplicates_flagged_count=0,
    )

    # Build effective system prompt — semantic patterns injected at run level
    effective_system_prompt = SYSTEM_PROMPT
    if memory_context and memory_context.semantic_injection:
        effective_system_prompt = SYSTEM_PROMPT + "\n\n" + memory_context.semantic_injection

    # Load episode store into memory_tools for on-demand retrieval via search_memory()
    if memory_context:
        memory_tools.set_episode_store(memory_context.episode_store)

    print("=== Triage Agent Starting ===\n")
    print(f"Channel      : {settings.SLACK_CHANNEL_ID}")
    print(f"Max messages : {settings.MAX_MESSAGES_TO_FETCH}")
    print(f"Time window  : {settings.CONTEXT_WINDOW_MINUTES} min\n")

    # Step 1 — parallel fetch: Slack messages + open Jira tickets
    # fetch_open_tickets catches its own errors and returns [] (Rule 5 — skip duplicate check)
    messages, open_tickets = await asyncio.gather(
        fetch_messages(settings.SLACK_CHANNEL_ID, oldest=oldest),
        fetch_open_tickets(settings.JIRA_PROJECT_KEY),
    )

    run_log.messages_fetched = len(messages)
    if messages:
        run_log.last_message_ts = messages[-1]["ts"]   # newest ts; used as watermark for next run
    print(f"Fetched {len(messages)} message(s) from Slack, "
          f"{len(open_tickets)} open Jira ticket(s)")

    # Step 2 — build embedding cache (Rule 5: skip check entirely if Jira fetch returned [])
    existing_cache = load_embedding_cache(settings.EMBEDDING_CACHE_PATH)
    cache = await build_embedding_cache(open_tickets, existing_cache, settings.EMBEDDING_CACHE_PATH)

    # Step 3 — group into conversation blocks
    blocks = build_context_blocks(messages)
    run_log.blocks_processed = len(blocks)
    print(f"Built {len(blocks)} conversation block(s)")

    # Rule 5 — accumulate Slack MCP errors across blocks; report at end
    slack_errors: list[str] = []

    # Step 4 — process each block
    for i, block in enumerate(blocks):
        snippet = block["combined_text"][:60]
        try:
            drain_confirmation_ts()  # clear buffer before this block (Rule 9)

            # ── Duplicate gate ─────────────────────────────────────────────
            # Embedding failure → Rule 5 (skip check, continue) not Rule 6
            block_emb: list[float] = []
            try:
                [block_emb] = await embed_texts([snippet])
                match = find_duplicate(block_emb, cache, settings.DUPLICATE_SIMILARITY_THRESHOLD)
            except Exception as emb_err:
                print(f"[duplicate_detector] embed_texts failed (Rule 5): {emb_err}")
                match = None

            if match:
                dup_msg = (
                    f"⚠️ Possible duplicate of [{match['key']}]"
                    f"({settings.JIRA_URL}/browse/{match['key']}): "
                    f"\"{match['summary']}\"\n"
                    f"Similarity: {match['similarity']:.0%}. "
                    f"Is this the same issue, or something new?"
                )
                await post_slack_message(dup_msg)
                dup_result = BlockResult(
                    block_index=i,
                    block_snippet=snippet,
                    action="duplicate_flagged",
                    ticket_key=match["key"],
                    ticket_summary=match["summary"],
                )
                run_log.blocks.append(dup_result)
                run_log.duplicates_flagged_count += 1
                _print_block_outcome(dup_result, index=i, total=len(blocks))
                continue

            # ── LLM loop ───────────────────────────────────────────────────
            result: BlockResult = await _run_llm_loop(
                block["combined_text"], block_index=i, block_snippet=snippet,
                effective_system_prompt=effective_system_prompt,
            )
            run_log.blocks.append(result)
            if result.action == "ticket_created":
                run_log.tickets_created_count += 1
                # Phase 5: capture confirmation post ts for reaction tracking
                result.confirmation_ts = drain_confirmation_ts()
                # Add new ticket to cache for intra-run duplicate prevention
                if result.ticket_key:
                    try:
                        [ticket_emb] = await embed_texts([result.ticket_summary or ""])
                        cache = add_ticket_to_cache(
                            cache, result.ticket_key, result.ticket_summary or "",
                            ticket_emb, settings.EMBEDDING_CACHE_PATH,
                        )
                    except Exception as emb_err:
                        print(f"[duplicate_detector] cache update embed failed (Rule 5): {emb_err}")
            elif result.action == "clarification_asked":
                run_log.clarifications_asked_count += 1
            _print_block_outcome(result, index=i, total=len(blocks))

        except LLMProviderError as e:
            # Rule 6 — LLM provider unavailable: write fatal log, alert Slack, exit
            run_log.status = "fatal"
            run_log.completed_at = datetime.utcnow().isoformat(timespec="seconds")
            write_run_log(run_log, settings.LOG_DIR)
            alert = (
                f"⚠️ LLM API unavailable — triage agent has stopped.\n"
                f"Please triage {settings.SLACK_CHANNEL_ID} manually or retry.\n"
                f"Error: {type(e).__name__}: {e}"
            )
            try:
                await post_slack_message(alert)
            except Exception as slack_err:
                print(
                    f"[TRIAGE AGENT FATAL] OpenAI unavailable AND Slack unreachable.\n"
                    f"OpenAI error : {type(e).__name__}: {e}\n"
                    f"Slack error  : {slack_err}\n"
                    f"Please triage manually."
                )
            sys.exit(1)

        except Exception as e:
            # Rule 5 — Slack MCP or other transient error: accumulate, continue
            run_log.error_count += 1
            error_entry = ErrorEntry(
                block_index=i,
                block_snippet=snippet,
                error_type=type(e).__name__,
                error_message=str(e),
                phase2_rule="Rule 5",
            )
            run_log.errors.append(error_entry)
            run_log.blocks.append(BlockResult(
                block_index=i, block_snippet=snippet, action="error"
            ))
            slack_errors.append(f"Block '{snippet}...': {e}")
            continue

    # Step 5 — post consolidated error report if any blocks failed (Rule 5)
    if slack_errors:
        n = len(slack_errors)
        lines = "\n".join(f"{i+1}. {entry}" for i, entry in enumerate(slack_errors))
        summary = (
            f"⚠️ Agent run completed with {n} Slack notification failure(s).\n\n"
            f"{lines}\n\n"
            f"Please manually confirm ticket status for the block(s) above."
        )
        try:
            await post_slack_message(summary)
        except Exception as e:
            error_lines = "\n".join(f"  {entry}" for entry in slack_errors)
            print(
                f"[TRIAGE AGENT ERROR] Run completed but Slack notifications failed "
                f"for {n} block(s).\n"
                f"{error_lines}\n"
                f"Slack post also failed: {e}\n"
                f"Please triage manually."
            )
            sys.exit(1)

    # Step 6 — finalise log and post summary
    run_log.completed_at = datetime.utcnow().isoformat(timespec="seconds")
    run_log.status = _compute_status(run_log)
    log_path = write_run_log(run_log, settings.LOG_DIR)
    await _post_slack_summary(run_log)
    _print_run_summary(run_log, log_path=log_path)
    return run_log
