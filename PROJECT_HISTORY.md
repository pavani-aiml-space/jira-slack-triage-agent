# Project History — JiraSlack

**Project Goal:** An AI triage agent that monitors a Slack channel and automatically creates Jira tickets from bug reports, feature requests, and tasks — using GPT-4o as the decision engine.

**Target Audience:** Software teams who use Slack for communication and Jira for issue tracking. Eliminates the manual step of converting Slack messages into Jira tickets.

---

## Core Philosophy

### The Problem This Solves

Engineers report bugs and requests in Slack but someone has to manually read them, classify them, and create Jira tickets. This is slow, inconsistent, and easy to miss.

**Before this project:**
- Team members post bugs/requests in Slack
- Someone manually reads them and decides if they're worth a ticket
- They manually create the Jira ticket with a title, description, priority
- Often gets missed or delayed

**After this project:**
- Team members post in Slack as usual
- Run `python run_triage.py` — agent reads, classifies, and creates tickets automatically
- Jira ticket created with structured description, correct priority, and labels
- Slack channel notified with the ticket link

---

## Key Design Decisions

### 1. GPT-4o as the Brain (Tool-Calling Loop)

**The Problem:** How does the agent decide what to do with each Slack message?

**The Solution:** OpenAI's tool-calling API. GPT-4o is given three tools (`create_jira_ticket`, `post_slack_message`, `ask_for_clarification`) and decides which to call based on the message content. The agent loops until GPT-4o returns `finish_reason: stop`.

**Impact:** No custom classification logic needed. GPT-4o handles ambiguity, prioritisation, and edge cases through the system prompt.

### 2. Time-Window Grouping (Context Builder)

**The Problem:** A bug report in Slack might span multiple messages ("login is broken" → "it started after yesterday's deploy").

**The Solution:** Messages within `CONTEXT_WINDOW_MINUTES` (default: 5 min) of each other are grouped into one "conversation block" and sent to GPT-4o together.

**Impact:** Agent sees the full context of a conversation, not isolated messages.

### 3. Slack via MCP, Jira via REST

**The Problem:** Two different integrations needed.

**The Solution:**
- Slack: `@modelcontextprotocol/server-slack` MCP server (handles auth, rate limits)
- Jira: Direct `httpx` calls to Jira REST API v3 with Basic Auth (email + API token)

**Why different approaches:** Slack MCP was already available and well-maintained. Jira REST API is simpler and doesn't need MCP overhead for a single endpoint.

### 4. Jira Email Typo Fix (Apr 25 2026)

**The Problem:** Auth kept returning 401 despite a valid-looking token.

**The Root Cause:** `JIRA_EMAIL` was set to `pavaniaml75@gmail.com` but the actual Atlassian account is `pavaniaiml75@gmail.com` (missing an `i`).

**Lesson:** Always verify that the email in `.env` exactly matches the Atlassian account that generated the API token. A typo in the email causes 401 even with a perfectly valid token.

---

## Session Log

| Date | What Was Done | Key Decisions | Next Steps |
|------|--------------|---------------|------------|
| 2026-04-30 | Phase 8 (Model-Agnostic LLM Provider) design complete. Approach A chosen: `agents/llm/` package with `LLMProvider` Protocol, `LLMTurn`, `LLMProviderError`, `OpenAIProvider`, `factory.py`. Tool schemas stay as OpenAI-format dicts. `triage_agent._run_llm_loop()` refactored to call `provider.chat()`. Tests migrate from `patch(_client)` + nested `MagicMock` to `patch(_provider)` + `AsyncMock(return_value=LLMTurn(...))` — simpler. | Tool files unchanged (Anthropic provider converts internally when added); `json.loads()` eliminated from business logic (ToolCall.args is already a dict); `LLMProviderError` replaces `openai.APIError` in Rule 6 | `/diagram` for Phase 8 |
| 2026-04-30 | Phase 8 (Model-Agnostic LLM Provider) brainstorm complete. Actors: developer (primary), operator, end team member. Problem: 8+ files must change to swap LLM vendors today. Scope: `agents/llm/` abstraction layer for OpenAI + Anthropic; embeddings stay on OpenAI; same prompt both providers; Rule 12 added. Two pre-brainstorm questions answered: (1) LLM knows when to call `search_memory` via system prompt guidance + self-uncertainty; (2) memory quality gate (reaction-gated storage) identified as Phase 7 debt, not Phase 8 scope. | Embeddings stay on OpenAI regardless of LLM choice; `LLMProviderError` as common exception; memory quality gate deferred | `/design` for Phase 8 |
| 2026-04-30 | Phase 7b (Option A — search_memory tool) build complete. Replaced per-block episode pre-injection with on-demand `search_memory` tool. 209/209 unit tests. Token savings: zero episode tokens for clear blocks (~80% of cases). New file: `agents/triage/tools/memory_tools.py`. Phase 8 added to PROJECT_ROADMAP.md with full model-agnostic design research. | Module-level `_active_episode_store` side-channel (same pattern as `_confirmation_ts_buffer`); LLM calls `search_memory` only when uncertain — worst case is pre-memory baseline | `/brainstorm` for Phase 8 |
| 2026-04-30 | Phase 7 (Memory) kaizen + closeout complete. 206/206 unit tests + 11/11 integration tests green after kaizen. Kaizen fixes: (1) `block_emb` `UnboundLocalError` guard in `triage_agent.py`; (2) dead nested patch removed from `test_memory_runner.py`; (3) `MemoryContext` docstring corrected ("Immutable" → "Memory state"); (4) `extract_count_patterns` type hint corrected to `list[Episode]`; (5) unused `Optional` import removed from `episode_store.py`; (6) `run_triage.py` module docstring updated to describe 5-step lifecycle. DEBT-014/015 logged. ARCHITECTURE.md authored as plain-English end-to-end system walkthrough. | Only mock I/O boundaries — patching `add_episode` (pure list append) silently broke threshold assertions; side_effect functions must use `**kwargs` or be updated whenever production signature gains new keyword args | `/brainstorm` for Phase 6 (Reliability) |
| 2026-04-29 | Phase 4 (Duplicate Detection) build + audit PASS. 21 new `duplicate_detector.py` unit tests + 6 new `triage_agent.py` Phase 4 tests = 115/115 unit, 11/11 integration. Blocking fix: `jira_search` MCP returns flat format (`issue["summary"]`) not REST nested (`issue["fields"]["summary"]`). Blocking fix: `embed_texts` failures inside block loop now correctly follow Rule 5 (skip+continue) not Rule 6 (fatal). E2E: 12 tickets fetched, cache built, 97.43% similarity detected on true duplicate. DEBT-010–DEBT-013 logged. | `asyncio.to_thread` wraps sync OpenAI embeddings SDK; `patch_run_deps` helper extended with 6 Phase 4 safe-default patches; all Block 3 changes implemented together (LEARNINGS.md pattern); jira_search flat format confirmed by live spike | `/kaizen` then `/closeout` |
| 2026-04-29 | Phase 4 (Duplicate Detection) brainstorm + design complete. Spike confirmed `jira_search` JQL tool. Option A (embedding pre-gate) chosen. Key decisions: skip-and-continue if Jira/embeddings fail; 50-ticket cache limit (DEBT); `text-embedding-3-small`; threshold 0.85 configurable. | `jira_search` takes `jql` arg (confirmed live); cosine similarity written not borrowed (no numpy dep); `asyncio.gather` for parallel Slack+Jira fetch | `/diagram` |
| 2026-04-29 | Phase 3 (Observability) build + audit PASS. 11 new run_logger tests + 13 new triage_agent tests = 85/85 unit tests passing, 11/11 integration tests passing. E2E: log written, sentinel cleaned up, Slack summary posted, dashboard imports clean. Code review: fixed mid-file imports (blocking). DEBT-008/DEBT-009 logged. | `_run_llm_loop` → `BlockResult` return; `AsyncMock(side_effect=fn)` forwards new kwargs so all side-effect functions needed updating; `assert_called_once` → `call_args_list[0]` pattern when a function gains additional callers | `/kaizen` then `/closeout` |
| 2026-04-29 | Phase 3 (Observability) plan complete. 10 chunks across 5 blocks. Key risk: _run_llm_loop return type change (None → BlockResult) breaks 6 existing tests — all identified and handled in plan. | Dashboard (Block 5) has no unit tests — Streamlit UI verified E2E only; all data layer functions fully unit-tested in Block 1 | `/build` |
| 2026-04-29 | Phase 3 (Observability) diagram complete. ASCII + sequence + flowchart + data flow + plain-English flows. Key flows: Trigger, Sentinel create/delete, LLM loop → BlockResult, Log write, Slack summary, Dashboard refresh. | `_run_llm_loop` return type change from None → BlockResult is the main interface contract; `finally` block in run_triage.py guarantees sentinel cleanup even on sys.exit | `/plan` |
| 2026-04-29 | Phase 3 (Observability) brainstorm + design complete. 4 components: JSON run log, Streamlit dashboard, Slack end-of-run summary, stdout per-block outcome lines. Log schema locked (funnel + block trace + LLM trace + errors). | Option A chosen: separate `run_logger.py` service; `_run_llm_loop` returns `BlockResult`; sentinel file in `run_triage.py finally` block | `/diagram` then `/plan` |
| 2026-04-29 | /kaizen + /closeout for Phase 2 (Failure Transparency). Removed unused `import asyncio` (DEBT-007). Hoisted repeated inline imports in test file. Fixed stale jira_tools docstring. 56/56 unit tests green. PROJECT_ROADMAP Phase 2 marked ✅. | `try/except` at point of failure chosen over decorator; all three handlers in one GREEN step; `APIConnectionError.__new__()` pattern for test mocking | `/brainstorm Phase 3 (Observability)` |
| 2026-04-29 | Phase 2 (Failure Transparency) build + audit PASS. 8 new unit tests. 56/56 unit tests pass, 11/11 integration tests pass. E2E: SCRUM-6 created, duplicate detected (SCRUM-3 referenced), clarification asked — all clean. Three error handlers: Rule 1 (Jira), Rule 5 (Slack accumulator), Rule 6 (OpenAI). | `openai.APIConnectionError.__new__()` needed for test instantiation; all three handlers implemented in single GREEN step since all in `run()`; jira handler imports slack_tools — no circular dep | /kaizen then /closeout |
| 2026-04-30 | Phase 7 (Memory) build + audit PASS. 14 chunks, 5 blocks, +30 unit tests (176→206). 3 new files: `pipeline/episode_store.py`, `pipeline/semantic_store.py`, `pipeline/memory_runner.py`. Modified: `config/settings.py` (7 settings), `triage_agent.py` (memory_context + episode_context), `run_triage.py` (5-step lifecycle). Integration + E2E all green. Blocking fix: `block_emb` initialised to `[]` before try-block to prevent `UnboundLocalError` if embed fails. DEBT-014/015 logged (function-level import, sequential summarise). | Patching `add_episode` (pure list-append) as no-op silently broke threshold calculations — only mock I/O boundaries; extending `_run_llm_loop` signature broke one side_effect mock that didn't accept `**kwargs` — any test mock function must be updated when production signature changes | /kaizen for Phase 7 |
| 2026-04-29 | Phase 7 (Memory) /plan complete. 14 chunks, 5 blocks, TDD-first. Settings → EpisodeStore → SemanticStore → MemoryRunner → triage_agent integration → run_triage entry point. Key insight: `add_episode` is pure (no I/O) — do not mock it in tests that observe downstream effects. | — | `/build` for Phase 7 |
| 2026-04-29 | Phase 7 (Memory) design complete. Option A chosen: MemoryContext object passed to triage_agent.run(). 3 new files: episode_store.py, semantic_store.py, memory_runner.py. 2 modified: triage_agent.py (memory_context param + episode_context param to _run_llm_loop), run_triage.py (pre_run/post_run hooks). 7 new settings. Hybrid injection: semantic → SYSTEM_PROMPT (run-level), episodes → user message (block-level). Retrieval reuses existing block_emb from duplicate gate — zero extra embed calls per block. | Option A (MemoryContext object) beats side-channel: test isolation, explicit signatures, mirrors eval_runner pattern; episode_summary embedded for retrieval, block_snippet stored for display | `/diagram` for Phase 7 |
| 2026-04-29 | Phase 7 (Memory) brainstorm complete. Three layers: episodic (`episode_store.json` — every ticket_created decision), semantic (`semantic_store.json` — count-based + LLM-extracted patterns), working (hybrid injection: semantic → SYSTEM_PROMPT run-level; episodes → user message block-level). Storage: JSON (fully decoupled from Phase 6). Rule 10 + Rule 11 added (LLM extraction fail → count-based fallback; empty retrieval → no injection, continue). | JSON over SQLite to decouple from Phase 6; hybrid injection keeps system prompt lean while still giving per-block precedents; count-based patterns are free and resilient to LLM failures | `/design` for Phase 7 |
| 2026-04-29 | Phase 5 (Eval & Feedback Loop) audit + kaizen + closeout complete. Kaizen fixed: stripped code-fence artifacts from `SYSTEM_PROMPT` (burning tokens on every call); added `RunLog` type annotation to `add_pending_from_run`; fixed `Optional[int]` hint in `rolling_thumbs_up_rate`. DEBT-014 (double quality store load) + DEBT-015 (dead `ANTHROPIC_API_KEY` config) logged. 175/175 tests (164 unit + 11 integration). | SYSTEM_PROMPT was sending ~80 tokens of meta-commentary to GPT-4o on every call — found in kaizen code review; `TYPE_CHECKING` guard used in quality_metrics.py to avoid circular import while still annotating `RunLog` | `/brainstorm` for Phase 6 (Reliability) |
| 2026-04-29 | Phase 5 (Eval & Feedback Loop) audit PASS. 164/164 unit, 11/11 integration. Code review: fixed missing type annotations on `run_eval_step` and `_post_triage_step` (blocking). DEBT-014 logged: double quality_store load per run. Learnings drafted: module-level ts buffer pattern, patch namespace binding rule, why separation into eval_runner paid off in tests. | eval_runner.run_eval_step type annotation requires importing RunLog; `from __future__ import annotations` enables `RunLog | None` union at runtime | `/kaizen` then `/closeout` |
| 2026-04-29 | Phase 5 (Eval & Feedback Loop) build complete. 7 blocks, 11 chunks, +37 unit tests (127→164). New: `pipeline/quality_metrics.py`, `pipeline/reaction_collector.py`, `pipeline/eval_runner.py`. Modified: `slack_tools.py` (ts buffer), `run_logger.py` (confirmation_ts), `triage_agent.py` (returns RunLog + drains ts), `run_triage.py` (eval hooks), `dashboard.py` (quality chart), `settings.py` (5 settings). All 164 unit tests passing. | eval lifecycle belongs in eval_runner.py not triage_agent; triage_agent.run() now returns RunLog; drain_confirmation_ts() called at start of each block (clear) + after ticket_created (capture) | /audit for Phase 5 |
| 2026-04-29 | Phase 5 (Eval & Feedback Loop) diagram complete (first pass). ASCII overview + sequence diagram + two flowcharts. Superseded by revised diagram above. | — | — |
| 2026-04-29 | Phase 5 (Eval & Feedback Loop) design complete. Two new modules: `reaction_collector.py` + `quality_metrics.py`. Module-level ts buffer in `slack_tools.py`. `quality_store.json` for rolling aggregate. Spike needed on `slack_post_message` MCP response shape. | Option A (parse MCP response for ts) primary; Option B (post-hoc channel history) fallback; Rule 8 + Rule 9 codified in design; storage: extend BlockResult + new quality_store.json | /diagram for Phase 5 |
| 2026-04-29 | Phase 5 (Eval & Feedback Loop) brainstorm complete. Identified 8 production gaps (message_ts not stored, thresholds not persisted, no baseline, timing window undefined, etc.). Scoped Phase 5 = capture + metrics + alerts; auto-tuning = Phase 5b. Phase ordering updated: Eval before Reliability. | Both operator and reporting engineer are primary customers; auto-tuning deferred until real signal exists; poll-based reactions (no webhooks); Rule 8 + Rule 9 added for cold start and missing reactions | /design for Phase 5 |
| 2026-04-29 | Phase 4 (Duplicate Detection) build + audit + kaizen + closeout complete. DEBT-010–013 resolved: pagination (1000 tickets), asyncio.to_thread for LLM calls, cache pruning, no dict mutation. 119/119 unit tests pass. | fetch_open_tickets paginates with start_at; build_embedding_cache copies input dict; closed tickets pruned from cache each run | /brainstorm Phase 5 |
| 2026-04-27 | Built 47 pytest unit tests across 5 modules (context_builder, jira_tools, slack_tools, slack_reader, triage_agent). Built integration tests for Jira + Slack MCP connections. Created write-tests skill + workflow. Fixed patch.dict bug in triage_agent tests. Ran /kaizen: fixed jira_tools docstring, moved import json in slack_reader, resolved DEBT-005/DEBT-006. | patch.dict(TOOL_EXECUTORS) required when functions bound in dict at import time; thin SKILL.md + workflow file is canonical pattern | /closeout → next session: /brainstorm Phase 2 Intelligence |
| 2026-04-27 | /audit PASS. Fixed jira_tools.py: wrong CLI flag (--jira-api-token → --jira-token), priority/labels via additional_fields, removed dynamic tool discovery. Installed uvx via brew. Full E2E verified: SCRUM-5 created. | mcp-atlassian CLI args confirmed; additional_fields pattern for priority | /kaizen then /closeout |
| 2026-04-30 | Phase 8 (Model-Agnostic LLM Provider) /plan complete. 4 new files (`agents/llm/`), 3 modified (`triage_agent.py`, `settings.py`, `test_triage_agent.py`), ~23 new unit tests. 4 blocks / 9 chunks. Single boundary: `_provider.chat() → LLMTurn`. | Abstraction layer built before second provider needed; `raw_message` approach preserves multi-turn for all providers | Run /build chunk by chunk |
| 2026-04-30 | Phase 8 (Model-Agnostic LLM Provider) /diagram complete. ASCII + sequence + flowchart diagrams in `docs/diagrams/2026-04-30-phase8-llm-provider.md`. Wall diagram clarifies exact agnostic boundary: `triage_agent.py` left of wall knows nothing about OpenAI; `OpenAIProvider.chat()` right of wall knows everything. | — | Approved; proceeding to /plan |
| 2026-04-30 | Phase 8 (Model-Agnostic LLM Provider) /brainstorm + /design complete. Brainstorm: 4 actors, 4 must-haves, Rule 12 added. Design: `agents/llm/` protocol + `OpenAIProvider` + factory. `AnthropicProvider` deferred — interface is the deliverable. Tool schemas stay OpenAI-format (Anthropic converts internally). | Option A chosen: provider wraps SDK; triage_agent reads LLMTurn. `raw_message` stores SDK object for multi-turn. | Approved; proceeding to /diagram |
| 2026-04-27 | Retroactively documented Phase 1 brainstorm + design artifacts. Created `docs/plans/2026-04-25-core-pipeline-brainstorm.md` and `docs/plans/2026-04-25-core-pipeline-design.md`. | Tool-calling loop chosen over classifier.py; failure mode gaps documented (Rules 1, 5, 6) | Run /diagram to produce the code-level diagram |
| 2026-04-29 | /closeout Phase 8. Learnings written (3 entries: GOTCHA patch AttributeError, PROCESS test helpers, DECISION raw_message). Roadmap updated: Phase 8 ✅, Phase 9 listed. DEBT-016 logged (tool schema format for future Anthropic provider). | Session fully closed. | Next: `/brainstorm` for Phase 9 — Anthropic provider or next roadmap item |
| 2026-04-29 | /audit Phase 8 PASS. 228/228 unit tests, 11/11 integration tests, E2E run clean (exit 0, 4 blocks, 4 clarifications, 0 errors, run log written). All success metrics met. One blocking fix: stale "OpenAI" comment in triage_agent.py section header corrected. | `agents/llm/` package fully audited; zero direct OpenAI SDK calls in triage_agent.py confirmed | /kaizen then /closeout |
| 2026-04-29 | /build Phase 8 complete. All 11 TDD chunks executed. 228 unit tests passing (17 new). `triage_agent._client` removed; `_provider: LLMProvider` injected via factory. `OpenAIProvider` wraps SDK; `LLMProviderError` replaces `openai.APIError`. `CLAUDE.md` mock guidance updated. Adding Anthropic: one new file + one line in factory.py. | `agents/llm/` package (base, openai_provider, factory, __init__), `config/settings.py` (LLM_PROVIDER), `agents/triage/triage_agent.py` refactored, test suite migrated to _provider mocks | /audit Phase 8 |
| 2026-04-29 | /plan Phase 8 complete. Detailed TDD plan created in `docs/plans/2026-04-30-phase8-llm-provider-plan.md`. 4 blocks, 11 chunks covering: LLM abstraction package, config, triage_agent refactor, test migration. | Phased refactor: new agents/llm/ package → wire into triage_agent → update tests → docs | /build Phase 8 |
| 2026-04-25 | Built full Slack→Jira pipeline. Debugged 401 Jira auth (email typo). First successful ticket created (SCRUM-3). Applied SDLC template structure. | GPT-4o tool-calling loop, Slack MCP, Jira REST, time-window grouping | See PROJECT_ROADMAP.md for Phase 2 |
