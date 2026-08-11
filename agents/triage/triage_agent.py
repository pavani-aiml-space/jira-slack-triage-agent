"""
Triage Agent — LLM-driven orchestrator.

Execution order:
  1. IMPORTS & CONFIG        — load everything needed
  2. SYSTEM PROMPT           — rules given to the LLM provider
  3. SUBMIT_DECISIONS_SCHEMA — the one tool schema the LLM provider can call
  4. _classify_block()       — one structured LLM call per block, no loop
  5. _execute_decisions()    — deterministic dispatch, no model involved
  6. run()                   — main entry point: reads Slack → groups → loops

LLM provider is the brain. This file is the hands.
Provider is configured via settings.LLM_PROVIDER — default "anthropic" (Claude).
Set LLM_PROVIDER=openai and LLM_MODEL=gpt-4o to use OpenAI instead.

Classification strategy (one call, not a loop):
  - The LLM is offered exactly one tool, submit_triage_decisions, and returns
    a list of decisions (0 or more) for the block in a single round trip.
  - Everything after that is plain code: which tool to call for which
    decision, whether to post a Slack confirmation, how to route by
    confidence. None of it is left to further model judgment.

Memory strategy (deterministic retrieval):
  - Semantic patterns injected into SYSTEM_PROMPT once per run (small, run-level).
  - Episodes are retrieved per block by reusing the embedding already computed
    for the duplicate gate — no extra embedding call, no model judgment call.
    Only injected if the closest match clears EPISODE_SIMILARITY_THRESHOLD.
    Zero episode tokens for blocks with no close match (~80% of cases).
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
from pipeline.episode_store import retrieve_similar, format_episode_context

from agents.triage.tools.jira_tools import create_jira_ticket
from agents.triage.tools.slack_tools import (
    post_slack_message,
    ask_for_clarification,
    drain_confirmation_ts,
)
from pipeline.memory_runner import MemoryContext
from pipeline.pending_confirmation_store import load_pending_store, save_pending_store
from pipeline.confirmation_resolver import resolve_pending_confirmations

_provider: LLMProvider = get_llm_provider(settings)


# ── 2. SYSTEM PROMPT — rules given to the LLM provider ──────────────────────
SYSTEM_PROMPT = """
You are a software triage agent monitoring a Slack channel for incoming messages.

Read the full conversation block, including replies and context, and decide what actionable items it contains. Most blocks contain exactly one. Some contain none (casual discussion, a question already answered). A few contain more than one distinct issue — if a message reports several unrelated problems, that's several decisions, not one.

Call submit_triage_decisions exactly once, with one decision per actionable item. Pass an empty list if nothing in this block is actionable.

Each decision has an action:
- create_ticket: the item is a Bug (something broken, failing, degraded, or behaving unexpectedly), a Story (a user-facing feature request or enhancement), or a Task (internal engineering, ops, docs, cleanup, or investigation work).
- ask_clarification: not enough information to create a useful ticket. Ask only for the missing details needed.
- duplicate: the issue clearly matches an existing or already-created ticket mentioned in the conversation itself. Reference the known ticket or reason if available.

For each create_ticket decision:
- Include a concise title, and a clear description (what, steps to reproduce, expected vs. actual behavior if available, affected users/scope), and a priority.
- Always include a confidence score (0.0-1.0) — how sure you are about the type, priority, and details. Be honest, not optimistic: a vague or ambiguous report should score low, not 0.9 just because you produced a plausible-looking ticket.
- Also include reasoning — one or two sentences on why you classified it this way. This is shown to the team if confidence is too low to file automatically.

Priority guide:
- Critical: Production down, all or most users affected, data loss, security issue, payments blocked, or no workaround.
- High: Major feature broken, many users affected, serious degradation, workaround exists.
- Medium: Partial feature broken, some users affected, reasonable workaround available.
- Low: Cosmetic issue, minor annoyance, affects few users, small enhancement, documentation request.

Rules:
- Do not create tickets from casual discussion, questions, jokes, or vague complaints unless there is a clear actionable issue.
- Do not infer missing technical details beyond what the Slack conversation supports.
- Prefer ask_clarification over creating a low-quality ticket.
- If multiple distinct actionable issues are reported, submit one create_ticket decision per issue.
- If one message contains related symptoms of the same root problem, submit one decision.
- Keep Jira titles short and specific.

If a "## Similar past decisions" section appears below the Slack message, it was retrieved automatically because this issue closely matches past triage decisions. Use it as precedent, don't ignore it.
"""


# ── 3. SUBMIT_DECISIONS_SCHEMA — the one tool the LLM provider can call ──────
SUBMIT_DECISIONS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_triage_decisions",
        "description": (
            "Submit one decision per distinct actionable item found in this "
            "Slack conversation block. Call this exactly once. Pass an empty "
            "list if nothing here is actionable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["create_ticket", "ask_clarification", "duplicate"],
                                "description": "What this decision does.",
                            },
                            "summary": {
                                "type": "string",
                                "description": "Short ticket title, max 80 chars, imperative tone e.g. 'Fix login crash on empty password'. Required for create_ticket.",
                            },
                            "issue_type": {
                                "type": "string",
                                "enum": ["Bug", "Story", "Task"],
                                "description": "Required for create_ticket.",
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["Critical", "High", "Medium", "Low"],
                                "description": "Required for create_ticket.",
                            },
                            "description": {
                                "type": "string",
                                "description": "Structured description with What, Steps to Reproduce, Expected, Context. Required for create_ticket.",
                            },
                            "labels": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Lowercase labels, e.g. ['login', 'regression']. Optional.",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Self-assessed confidence 0.0-1.0 in the type, priority, and details. Required for create_ticket.",
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "One or two sentences on why you classified it this way. Required for create_ticket.",
                            },
                            "question": {
                                "type": "string",
                                "description": "The clarifying question to ask. Required for ask_clarification.",
                            },
                            "note": {
                                "type": "string",
                                "description": "Explanation referencing the known ticket or reason. Required for duplicate.",
                            },
                        },
                        "required": ["action"],
                    },
                },
            },
            "required": ["decisions"],
        },
    },
}


# ── 4. _classify_block() — one structured call per block, no loop ───────────

async def _classify_block(
    block_text: str,
    effective_system_prompt: str = "",
    episode_context: str = "",
) -> tuple[list[dict], LlmStats]:
    """
    Send one conversation block to the LLM provider and get back a decision list.

    effective_system_prompt: SYSTEM_PROMPT + optional semantic injection (set by run()).
    episode_context: pre-formatted "## Similar past decisions" text, already
    retrieved deterministically by run() via the duplicate gate's block embedding.
    Appended to the user message only when non-empty — no lookup happens here.

    One call, no loop: the model is offered exactly one tool and must call it
    once. If it doesn't (a malformed turn), decisions defaults to [] rather
    than raising — Rule 5, degrade one block, don't crash the whole run.
    Returns (decisions, LlmStats) — never raises (caller handles exceptions).
    """
    print(f"\n{'─' * 50}")
    print(f"Processing:\n  {block_text.replace(chr(10), chr(10) + '  ')}\n")

    system_prompt = effective_system_prompt or SYSTEM_PROMPT

    user_content = f"Slack message(s):\n\n{block_text}"
    if episode_context:
        user_content += f"\n\n{episode_context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_content},
    ]

    turn = await _provider.chat(messages, [SUBMIT_DECISIONS_SCHEMA], system_prompt)

    decisions: list[dict] = []
    tools_called: list[str] = []
    if turn.tool_calls:
        tc = turn.tool_calls[0]
        tools_called = [tc.name]
        decisions = tc.args.get("decisions") or []

    print(f"  decisions: {len(decisions)}")

    llm_stats = LlmStats(
        iterations=1,
        tools_called=tools_called,
        finish_reason=turn.finish_reason,
        prompt_tokens=turn.prompt_tokens,
        completion_tokens=turn.completion_tokens,
    )
    return decisions, llm_stats


# ── 5. _execute_decisions() — deterministic dispatch, no model involved ─────

async def _execute_decisions(
    decisions: list[dict],
    block_index: int,
    block_snippet: str,
    run_id: str,
    llm_stats: LlmStats,
) -> list[BlockResult]:
    """
    Execute each decision from _classify_block() in plain code.

    Every branch here — routing by confidence, deciding whether to post a
    Slack confirmation, deciding what BlockResult.action to record — is
    deterministic, none of it is a model judgment call. llm_stats is
    attached only to the first result: every decision in this call came
    from the same one LLM call, so duplicating token counts across results
    would double-count them if anything ever sums per-block stats.

    Returns one BlockResult per decision, or a single "no_action" result
    if decisions is empty. Never raises (caller handles exceptions).
    """
    if not decisions:
        return [BlockResult(
            block_index=block_index, block_snippet=block_snippet,
            action="no_action", llm=llm_stats,
        )]

    block_context = {"run_id": run_id, "block_index": block_index, "block_snippet": block_snippet}
    results: list[BlockResult] = []

    for i, decision in enumerate(decisions):
        stats = llm_stats if i == 0 else None
        action = decision.get("action")

        if action == "create_ticket":
            jira_result = await create_jira_ticket(
                summary=decision.get("summary", ""),
                issue_type=decision.get("issue_type", ""),
                priority=decision.get("priority", ""),
                description=decision.get("description", ""),
                confidence=decision.get("confidence", 0.0),
                labels=decision.get("labels"),
                reasoning=decision.get("reasoning", ""),
                block_context=block_context,
            )
            print(f"  → create_ticket: {jira_result}")

            if jira_result.startswith("[ESCALATED]"):
                # Low confidence — escalate_for_confirmation() already posted
                # the proposal to Slack and persisted it. Nothing more to do.
                results.append(BlockResult(
                    block_index=block_index, block_snippet=block_snippet,
                    action="escalated_for_confirmation",
                    ticket_summary=decision.get("summary"),
                    ticket_type=decision.get("issue_type"),
                    ticket_priority=decision.get("priority"),
                    ticket_description=decision.get("description"),
                    llm=stats,
                ))
            elif jira_result.startswith("[JIRA_ERROR]") or jira_result.startswith("[ESCALATION_ERROR]"):
                # Slack was already notified inside the executor.
                results.append(BlockResult(
                    block_index=block_index, block_snippet=block_snippet,
                    action="error", llm=stats,
                ))
            else:
                key_match = re.search(r"Created (\w+-\d+):", jira_result)
                ticket_key = key_match.group(1) if key_match else None
                confirmation_ts = None
                try:
                    await post_slack_message(f"✅ {jira_result}")
                    confirmation_ts = drain_confirmation_ts()
                except Exception as e:
                    print(f"[triage_agent] confirmation post failed (Rule 5): {e}")
                results.append(BlockResult(
                    block_index=block_index, block_snippet=block_snippet,
                    action="ticket_created", ticket_key=ticket_key,
                    ticket_summary=decision.get("summary"),
                    ticket_type=decision.get("issue_type"),
                    ticket_priority=decision.get("priority"),
                    ticket_description=decision.get("description"),
                    confirmation_ts=confirmation_ts, llm=stats,
                ))

        elif action == "ask_clarification":
            await ask_for_clarification(decision.get("question", ""))
            results.append(BlockResult(
                block_index=block_index, block_snippet=block_snippet,
                action="clarification_asked", llm=stats,
            ))

        elif action == "duplicate":
            note = decision.get("note") or "This looks like a duplicate of an existing issue."
            await post_slack_message(f"🔁 {note}")
            results.append(BlockResult(
                block_index=block_index, block_snippet=block_snippet,
                action="duplicate_flagged", ticket_key=decision.get("ticket_key"), llm=stats,
            ))

        else:
            results.append(BlockResult(
                block_index=block_index, block_snippet=block_snippet,
                action="no_action", llm=stats,
            ))

    return results


# ── 7. run() — main entry point ──────────────────────────────────────────────

def _compute_status(run_log: RunLog) -> str:
    """Return 'success' if no errors, 'partial' otherwise."""
    return "success" if run_log.error_count == 0 else "partial"


def _print_block_outcome(result: BlockResult, index: int, total: int) -> None:
    """Print [Block N/M] ✅ / 💬 / ⚠️ / 🔁 outcome line to stdout."""
    n = index + 1
    icons = {
        "ticket_created":            "✅ Ticket created   :",
        "clarification_asked":       "💬 Clarification asked",
        "error":                     "⚠️  Error            :",
        "no_action":                 "—  No action",
        "duplicate_flagged":         "🔁 Duplicate flagged :",
        "escalated_for_confirmation": "🤔 Escalated for confirmation:",
    }
    label = icons.get(result.action, f"—  {result.action}")
    if result.action == "ticket_created":
        print(f"[Block {n}/{total}] {label} {result.ticket_key} "
              f'"{result.ticket_summary}" ({result.ticket_type} · {result.ticket_priority})')
    elif result.action == "duplicate_flagged":
        print(f"[Block {n}/{total}] {label} {result.ticket_key or '(referenced in Slack)'}")
    elif result.action == "escalated_for_confirmation":
        print(f"[Block {n}/{total}] {label} "
              f'"{result.ticket_summary}" ({result.ticket_type} · {result.ticket_priority})')
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
    print(f"  Escalated (pending confirmation): {run_log.escalated_for_confirmation_count}")
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
      0. Resolve any pending low-confidence confirmations from previous runs
      1. Fetch Slack messages + open Jira tickets in parallel
      2. Build embedding cache (diff-only re-embedding)
      3. Group messages into conversation blocks
      4. For each block: run duplicate gate, then classify + execute deterministically if no match
      5. Post consolidated Slack error report if any blocks failed
      6. Write run log and post Slack summary

    memory_context: optional MemoryContext from memory_runner.pre_run().
      - semantic_injection is appended to SYSTEM_PROMPT for the whole run.
      - episode_store is searched per block using that block's own embedding
        (reused from the duplicate gate below) — deterministic, no extra
        embedding call, no model judgment call. Only injected above threshold.

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

    print("=== Triage Agent Starting ===\n")
    print(f"Channel      : {settings.SLACK_CHANNEL_ID}")
    print(f"Max messages : {settings.MAX_MESSAGES_TO_FETCH}")
    print(f"Time window  : {settings.CONTEXT_WINDOW_MINUTES} min\n")

    # Step 0 — resolve any low-confidence escalations left pending from a
    # previous run, before processing new messages (Phase 10).
    pending_store = load_pending_store(settings.PENDING_CONFIRMATION_STORE_PATH)
    if pending_store.items:
        print(f"Resolving {len(pending_store.items)} pending confirmation(s) from previous runs...")
        pending_store = await resolve_pending_confirmations(pending_store)
        save_pending_store(pending_store, settings.PENDING_CONFIRMATION_STORE_PATH)

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

            # ── Episodic retrieval ─────────────────────────────────────────
            # Reuses block_emb from the duplicate gate above — no extra
            # embedding call. Deterministic: the same input always gets the
            # same episode context, gated by a similarity threshold instead
            # of the model deciding for itself whether to look.
            episode_context = ""
            if memory_context and memory_context.episode_store.episodes and block_emb:
                similar = retrieve_similar(
                    memory_context.episode_store,
                    block_emb,
                    settings.MAX_INJECTED_EPISODES,
                    threshold=settings.EPISODE_SIMILARITY_THRESHOLD,
                )
                episode_context = format_episode_context(similar)

            # ── Classify + execute ───────────────────────────────────────────
            decisions, llm_stats = await _classify_block(
                block["combined_text"],
                effective_system_prompt=effective_system_prompt,
                episode_context=episode_context,
            )
            results = await _execute_decisions(
                decisions, block_index=i, block_snippet=snippet,
                run_id=run_id, llm_stats=llm_stats,
            )
            run_log.blocks.extend(results)
            for result in results:
                if result.action == "ticket_created":
                    run_log.tickets_created_count += 1
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
                elif result.action == "escalated_for_confirmation":
                    run_log.escalated_for_confirmation_count += 1
                elif result.action == "duplicate_flagged":
                    run_log.duplicates_flagged_count += 1
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
