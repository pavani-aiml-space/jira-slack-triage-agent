# Bugs & Technical Debt — JiraSlack

---

## Active

| ID | Type | Description | Priority | Phase |
|----|------|-------------|----------|-------|
| — | — | No active bugs | — | — |

---

## Improvements (Non-blocking)

| ID | Type | Description | Logged |
|----|------|-------------|--------|
| DEBT-001 | Improvement | `classifier.py` exists as a standalone module but is not used by `triage_agent.py` — the agent has its own inline classification logic. Should consolidate or remove the standalone classifier. | 2026-04-25 |
| DEBT-002 | Improvement | No deduplication — agent will create a new ticket for the same Slack message every time `run_triage.py` is run. Phase 2 addresses this. | 2026-04-25 |
| DEBT-003 | Improvement | No tracking of last-processed message timestamp — agent re-reads all 20 messages on every run, including ones already triaged. | 2026-04-25 |
| DEBT-008 | Improvement | `dashboard.py` uses hardcoded `"python"` in `subprocess.Popen(["python", "run_triage.py"])`. On some systems the Python 3 binary is `python3`. Should use `sys.executable` instead. | 2026-04-29 |
| DEBT-009 | Improvement | `RunLog` JSON schema flattens funnel fields directly (e.g., `messages_fetched`) instead of nesting them under a `"funnel"` sub-object as specified in the brainstorm doc. Phase 6 eval may need migration if it expects the nested schema. | 2026-04-29 |
| DEBT-015 | Improvement | `settings.py` defines `ANTHROPIC_API_KEY` and includes it in the default `validate()` checks, but no runtime code reads it (Anthropic was an early candidate, OpenAI was chosen). Remove the setting and the validate entry to avoid false config errors. | 2026-04-29 |
| DEBT-016 | Improvement | `LLMProvider.chat()` accepts `tools: list[dict]` in OpenAI `{"type":"function","function":{...}}` format. When Anthropic is added, `AnthropicProvider` must convert this format internally. Consider introducing a neutral `ToolSchema` dataclass so providers receive a format-agnostic structure instead. Defer until a second provider is actually added. `agents/llm/base.py` line 51. | 2026-04-29 |
| DEBT-014 | Improvement | `eval_runner.run_eval_step` loads `quality_store.json` twice per run (pre + post). Acceptable for current store size; optimise only if store grows large. | 2026-04-29 |

---

## Resolved

| ID | Type | Description | Resolved |
|----|------|-------------|----------|
| DEBT-010 | Improvement | `jira_search` capped at 50 tickets. Fixed: default limit raised to 100; `fetch_open_tickets` now paginates via `start_at` up to `JIRA_MAX_PAGES` (default 10 × 100 = 1000 tickets). Partial results returned on mid-pagination error. | 2026-04-29 |
| DEBT-011 | Improvement | `_client.chat.completions.create()` was a blocking sync call on the async event loop. Fixed (Phase 8): moved into `OpenAIProvider.chat()` which wraps it in `asyncio.to_thread()`. `triage_agent._run_llm_loop` now calls `await _provider.chat()` — fully non-blocking. | 2026-04-29 |
| DEBT-012 | Improvement | `build_embedding_cache` accumulated closed tickets indefinitely. Fixed: entries not present in the current open-ticket list are pruned before embedding new ones. | 2026-04-29 |
| DEBT-013 | Improvement | `build_embedding_cache` mutated the caller's `existing_cache` dict in-place. Fixed: `dict(...)` shallow copy taken before any mutation. | 2026-04-29 |
| DEBT-014 | Improvement | `episode_store.retrieve_similar` uses a function-level `from pipeline.duplicate_detector import cosine_similarity`. Move to module-level import to follow project convention. | 2026-04-30 |
| DEBT-015 | Improvement | `semantic_store.summarise_with_llm` processes patterns sequentially. Parallelise with `asyncio.gather` if pattern volume grows beyond ~20. Acceptable now. | 2026-04-30 |
| DEBT-006 | Improvement | `pytest-asyncio` not installed; 37 async tests skipped. Installed via `pip install pytest-asyncio`. All 47 unit tests now pass. | 2026-04-27 |
| DEBT-007 | Improvement | `import asyncio` in `triage_agent.py` was unused — removed in kaizen pass. | 2026-04-29 |
| DEBT-005 | Improvement | All test files were `asyncio.run(main())` smoke scripts. Replaced with 47 pytest tests across unit and integration suites with proper mocking. | 2026-04-27 |
| BUG-001 | Bug | Jira 401 Unauthorized — `JIRA_EMAIL` was `pavaniaml75@gmail.com` (typo) instead of `pavaniaiml75@gmail.com`. Token was valid but email didn't match Atlassian account. | 2026-04-25 |
