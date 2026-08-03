# Feature: Phase 8 — Model-Agnostic LLM Provider

**Date:** 2026-04-30
**Status:** Awaiting /design

---

## Actors

| Actor | Role | What they need | Priority |
|-------|------|----------------|----------|
| Developer / maintainer | Builds and evolves the codebase | Swap LLM vendors without touching business logic; low refactor risk | Primary |
| Operator / team lead | Runs the agent in production | Single config change to experiment with a different model; no silent quality degradation | Secondary |
| End team member | Receives Slack messages + Jira tickets | No visible impact — should not notice a model switch | Tertiary |

---

## Priority Rule

When developer convenience and operator production stability conflict, **production stability wins**. A model switch must never silently degrade triage quality — if a provider is misconfigured or produces bad output, the system must fail loudly (Rule 6 pattern).

---

## Customer Problem

The developer built the entire pipeline on the OpenAI SDK. Every OpenAI-ism is embedded in business logic: tool schema format, response parsing, message history structure, and the exception type used by Rule 6. Switching to Claude (or any other model) today requires touching 8+ files including triage logic, tool definitions, and error handling — high risk, slow, and untestable without a real API key.

The cost of this lock-in grows with every new phase that adds LLM calls. Two concrete forcing functions already exist:
1. Anthropic's Managed Agents Memory (researched in Phase 7) only works on Claude — the model-agnostic abstraction is a prerequisite to use it.
2. GPT-4o is the best model we know today, but the model landscape changes fast. Locking to one vendor means expensive refactors when a better or cheaper model emerges.

---

## What We're Building

An `agents/llm/` abstraction layer with `LLMProvider` protocol and an `OpenAIProvider` implementation, so business logic in `triage_agent._run_llm_loop()` depends on the interface — not the OpenAI SDK. Adding a second provider later (Anthropic, Gemini, etc.) requires one new file and one factory line, zero business-logic changes.

---

## Out of Scope

- `AnthropicProvider` implementation — deferred; the abstraction is the deliverable. Anthropic slots in when we actually want to switch.
- `semantic_store.summarise_with_llm()` migration — it's an internal background summarisation call that doesn't affect triage quality. Stays on direct OpenAI client for now.
- Embeddings abstraction — embeddings stay on OpenAI `text-embedding-3-small` regardless of LLM choice (Anthropic has no embeddings API)
- Multi-model routing (different LLMs for classification vs. summarisation)
- Local model support (Ollama, LM Studio) or `openai-compatible` base URL override
- Automated model benchmarking / cross-provider eval / A-B testing harness
- Provider-specific prompt variants — same prompt must work on any provider

---

## Must-Haves vs. Nice-to-Haves

**Must-haves:**
- All existing unit tests pass — mocked at `LLMProvider.chat()` boundary (cleaner than patching `_client.chat.completions.create`)
- `triage_agent._run_llm_loop()` calls `provider.chat(messages, tools, system)` — zero direct OpenAI SDK calls remain in business logic
- `except openai.APIError` replaced with `except LLMProviderError` — Rule 6 still fires correctly
- `factory.py` returns `LLMProvider` from `settings.LLM_PROVIDER` — `"openai"` is the only live value, but `"anthropic"` raises a clear `NotImplementedError` pointing to the stub

**Nice-to-have (Phase 8+):**
- Implement `AnthropicProvider` when actually switching to Claude

---

## Assumptions

- We are building the abstraction + OpenAI implementation only. Anthropic is not wired up in this phase.
- The operator uses `OPENAI_API_KEY` for both the LLM (via `OpenAIProvider`) and embeddings (via `embed_texts`). This does not change in Phase 8.
- The system prompt and tool descriptions are provider-neutral. Verified empirically in `/audit` Part 3, not in /design.

---

## Success Metrics

| Metric | Target | How Measured | Whose need |
|--------|--------|--------------|-----------|
| Unit tests green | 100% (209+) | `pytest tests/unit/` with mocked `LLMProvider` | Developer |
| Zero direct OpenAI SDK calls in `triage_agent.py` | 0 | Code review | Developer |
| `except LLMProviderError` replaces `except openai.APIError` | Rule 6 fires on any `LLMProviderError` | Unit test | Operator |
| Adding `AnthropicProvider` later requires zero business-logic changes | 1 new file + 1 factory line only | Code review | Developer |

---

## Risks & Open Questions

1. **Message history format (must solve in /design)** — OpenAI appends a Python SDK object to the message list for multi-turn; any future provider appends a different shape. The `raw_message` field in `LLMTurn` must preserve the provider-specific object for the next loop iteration. The design must show exactly what `OpenAIProvider` stores in `raw_message` and how `_run_llm_loop` appends it.

2. **Tool schema neutralisation** — The three tool files (`jira_tools.py`, `slack_tools.py`, `memory_tools.py`) use OpenAI's `{"type": "function", "function": {...}}` format. The `LLMProvider.chat()` interface should accept neutral `ToolSchema` objects and let the provider convert. This means the tool files change format — we need to confirm this doesn't break existing unit tests that inspect the schema shape.

3. **`except openai.APIError` → `except LLMProviderError`** — Rule 6 must catch the new base exception. Both the exception hierarchy and the test for Rule 6 (`test_run_openai_error_posts_slack_alert_and_exits`) need updating. The test currently constructs an `openai.APIConnectionError` directly — it must instead construct an `LLMProviderError`.

4. **Memory quality gate (tracked, not Phase 8)** — Episodes are stored for all `ticket_created` decisions, including incorrect ones. Reaction-gated storage is a Phase 7 improvement to address separately. Not a blocker.

---

## New Priority Rules (feature-specific)

**Rule 12 — LLM provider switch must not silently degrade quality**
If `LLM_PROVIDER` is set to a provider that fails to authenticate or returns unexpected responses, the agent must fail loudly (Rule 6 pattern) and post a Slack alert. A bad provider config must never produce silently wrong tickets.

---

## Decisions Made This Session

- **Phase 8 scope = abstraction + OpenAI provider only.** `AnthropicProvider` is deferred — the interface is the deliverable, not a second live implementation. Rationale: building the right abstraction is 80% of the value; adding a provider is mechanical once the interface is correct.
- **`semantic_store.summarise_with_llm()` stays on direct OpenAI call.** It's an internal background call; migrating it is complexity without user-facing benefit in Phase 8.
- **`LLMProviderError` as the common exception base.** `OpenAIProvider` wraps `openai.APIError` in `LLMProviderError`. Rule 6's catch block becomes `except LLMProviderError`.
- **Embeddings stay on OpenAI always.** No Anthropic embeddings API exists. `EMBEDDING_PROVIDER` setting reserved for future use (Voyage, Cohere).
- **Memory quality gate deferred.** Reaction-gated episode storage is Phase 7 debt. Not a Phase 8 blocker.
