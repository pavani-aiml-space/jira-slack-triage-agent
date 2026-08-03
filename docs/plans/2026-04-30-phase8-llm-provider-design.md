# Technical Design: Phase 8 — Model-Agnostic LLM Provider

**Date:** 2026-04-30
**Brainstorm:** `docs/plans/2026-04-30-phase8-llm-provider-brainstorm.md`
**Status:** Awaiting /build

**Plan:** `docs/plans/2026-04-30-phase8-llm-provider-plan.md`

---

## Problem (from brainstorm)

`triage_agent._run_llm_loop()` is hardwired to the OpenAI SDK — switching to a different LLM today requires changes to business logic, tool schemas, and error handling across 6+ files. The goal is to decouple business logic from the SDK so the LLM backend becomes a swappable dependency.

---

## Approach Chosen

**Option A — Provider wraps OpenAI SDK; tool schemas stay in OpenAI format.**

`provider.chat(messages, tools, system) → LLMTurn` is the single boundary. `OpenAIProvider` wraps `asyncio.to_thread(_sync_client.chat.completions.create, ...)` and normalises the response. Tool files are unchanged — future `AnthropicProvider` converts OpenAI-format dicts to Anthropic format internally. Satisfies Priority Rule 12 (fail loudly on provider error) via `LLMProviderError`.

---

## Components

### New Files

```
agents/llm/
├── __init__.py          — exports LLMProvider, LLMTurn, ToolCall, LLMProviderError
├── base.py              — Protocol, dataclasses, exception
├── openai_provider.py   — OpenAI SDK wrapped in the protocol
└── factory.py           — get_llm_provider(settings) → LLMProvider
```

### Modified Files

- `agents/triage/triage_agent.py` — remove `_client`, add `_provider`; refactor `_run_llm_loop` to call `_provider.chat()`; replace `except openai.APIError` with `except LLMProviderError`
- `config/settings.py` — add `LLM_PROVIDER: str`
- `tests/unit/test_triage_agent.py` — replace `patch(_client)` + `MagicMock(choices=[...])` with `patch(_provider)` + `AsyncMock(return_value=LLMTurn(...))`

### Not Modified

- `jira_tools.py`, `slack_tools.py`, `memory_tools.py` — tool schemas remain OpenAI-format dicts
- `pipeline/semantic_store.py` — `summarise_with_llm()` keeps its direct `openai.OpenAI()` call
- `pipeline/duplicate_detector.py` — embeddings stay on OpenAI always

---

## Code Diagram
See: [docs/diagrams/2026-04-30-phase8-llm-provider.md](../diagrams/2026-04-30-phase8-llm-provider.md)

---

## Data Contracts

### `agents/llm/base.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol

@dataclass
class ToolCall:
    id:   str
    name: str
    args: dict  # already parsed — never a JSON string

@dataclass
class LLMTurn:
    finish_reason:      str              # "stop" | "tool_calls"
    content:            str | None
    tool_calls:         list[ToolCall]   = field(default_factory=list)
    prompt_tokens:      int              = 0
    completion_tokens:  int              = 0
    raw_message:        Any              = None
    # raw_message: the provider-specific object appended to messages history
    # For OpenAI: the openai.types.chat.ChatCompletionMessage object
    # For Anthropic (future): a dict in Anthropic message shape

class LLMProviderError(Exception):
    """Raised by any LLMProvider on API failure. Rule 6 catches this."""
    pass

class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[dict],
        tools:    list[dict],   # OpenAI-format dicts; provider converts as needed
        system:   str = "",
    ) -> LLMTurn: ...
```

### `agents/llm/openai_provider.py`

```python
class OpenAIProvider:
    def __init__(self, api_key: str, model: str) -> None: ...

    async def chat(
        self,
        messages: list[dict],
        tools:    list[dict],
        system:   str = "",
    ) -> LLMTurn:
        """
        Calls openai.chat.completions.create() via asyncio.to_thread().
        Normalises finish_reason, tool_calls, tokens, and raw_message.
        Wraps openai.APIError in LLMProviderError (Rule 12).
        """
```

**Normalisation inside `OpenAIProvider.chat()`:**

| OpenAI response field | → LLMTurn field |
|----------------------|----------------|
| `response.choices[0].finish_reason` | `finish_reason` |
| `response.choices[0].message` | `raw_message` |
| `response.choices[0].message.content` | `content` |
| `response.choices[0].message.tool_calls` | `tool_calls` (list of `ToolCall`) |
| `json.loads(tc.function.arguments)` | `ToolCall.args` |
| `response.usage.prompt_tokens` | `prompt_tokens` |
| `response.usage.completion_tokens` | `completion_tokens` |

### `agents/llm/factory.py`

```python
def get_llm_provider(settings) -> LLMProvider:
    if settings.LLM_PROVIDER == "openai":
        return OpenAIProvider(settings.OPENAI_API_KEY, settings.LLM_MODEL)
    raise NotImplementedError(
        f"LLM provider '{settings.LLM_PROVIDER}' is not implemented. "
        f"Supported: 'openai'. To add Anthropic, create agents/llm/anthropic_provider.py "
        f"implementing the LLMProvider protocol."
    )
```

### `agents/triage/triage_agent.py` (modified sections)

**Module level:**
```python
# BEFORE
from openai import OpenAI
_client = OpenAI(api_key=settings.OPENAI_API_KEY)

# AFTER
from agents.llm.base import LLMProviderError
from agents.llm.factory import get_llm_provider
_provider: LLMProvider = get_llm_provider(settings)
```

**`_run_llm_loop` inner loop (key changes):**
```python
# BEFORE
response = await asyncio.to_thread(
    _client.chat.completions.create,
    model=settings.LLM_MODEL,
    tools=ALL_TOOLS,
    messages=messages,
)
choice      = response.choices[0]
finish      = choice.finish_reason
llm_message = choice.message
total_prompt_tokens     += int(getattr(response.usage, "prompt_tokens", 0) or 0)
total_completion_tokens += int(getattr(response.usage, "completion_tokens", 0) or 0)
messages.append(llm_message)

for tool_call in llm_message.tool_calls:
    tool_name = tool_call.function.name
    tool_args = json.loads(tool_call.function.arguments)
    ...
    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

# AFTER
turn = await _provider.chat(messages, ALL_TOOLS, system_prompt)
finish = turn.finish_reason
total_prompt_tokens     += turn.prompt_tokens
total_completion_tokens += turn.completion_tokens
messages.append(turn.raw_message)

for tc in turn.tool_calls:
    tool_name = tc.name
    tool_args = tc.args         # already a dict — no json.loads
    ...
    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
```

**Exception handler:**
```python
# BEFORE
except openai.APIError as e:
    ...

# AFTER
except LLMProviderError as e:
    ...
```

**Also remove:** `import json` (no longer needed — `tc.args` is already a dict)

### `config/settings.py` (addition)

```python
# ── LLM provider ───────────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
# Currently only "openai" is supported.
# To add Anthropic: create agents/llm/anthropic_provider.py and update factory.py.
```

---

## External Calls

No new external calls introduced. `OpenAIProvider.chat()` calls the same OpenAI Chat Completions endpoint that `_client.chat.completions.create()` calls today:
- **Service:** OpenAI Chat Completions API
- **Endpoint:** `POST https://api.openai.com/v1/chat/completions`
- **Auth:** Bearer token via `OPENAI_API_KEY`
- **Payload:** `{model, messages, tools}` — unchanged
- **Response:** normalised to `LLMTurn`

---

## Failure Modes

| Failure | What happens | Priority Rule |
|---------|-------------|--------------|
| `openai.APIError` raised | `OpenAIProvider.chat()` catches it and re-raises as `LLMProviderError` | Rule 12 |
| `triage_agent.run()` catches `LLMProviderError` | Posts Slack alert, exits 1 — same as today's Rule 6 behaviour | Rule 6 |
| `LLM_PROVIDER` set to unsupported value | `factory.get_llm_provider()` raises `NotImplementedError` at import time — agent never starts | Rule 12 (fail loudly) |
| `OpenAIProvider` fails to initialise (bad API key) | `openai.APIError` on first call → wraps to `LLMProviderError` → Rule 6 | Rule 6 |

---

## Test Strategy

All `_run_llm_loop` tests change from:
```python
with patch("agents.triage.triage_agent._client") as mock_client:
    mock_client.chat.completions.create = MagicMock(return_value=response)
```
to:
```python
with patch("agents.triage.triage_agent._provider") as mock_provider:
    mock_provider.chat = AsyncMock(return_value=LLMTurn(finish_reason="stop", content="Done"))
```

This is simpler: `LLMTurn` is a plain dataclass — no nested `MagicMock(choices=[MagicMock(...)])` required.

Rule 6 test changes from:
```python
llm_side_effect=openai.APIConnectionError.__new__(openai.APIConnectionError)
```
to:
```python
llm_side_effect=LLMProviderError("API unavailable")
```

---

## Out of Scope

- `AnthropicProvider` implementation — one new file when Claude is actually needed
- `semantic_store.summarise_with_llm()` migration — stays on direct `openai.OpenAI()` call
- Neutral `ToolSchema` dataclass — tool files stay in OpenAI-format dicts; Anthropic provider converts internally
- Embeddings abstraction — embeddings always on OpenAI `text-embedding-3-small`

---

## Open Questions Resolved

| Question (from brainstorm) | Resolution |
|---------------------------|-----------|
| Tool schema format: neutral or OpenAI-format? | OpenAI-format dicts for now. Anthropic provider converts internally. Less scope now. |
| `semantic_store.summarise_with_llm()` migration? | Out of scope — deferred. It's a background call with no impact on triage quality. |
| Message history format for multi-turn? | `messages` stays in OpenAI format. `raw_message` stores the OpenAI SDK object. Future Anthropic provider converts incoming OpenAI-format messages to Anthropic format at call time. |
| `LLM_PROVIDER=anthropic` before implementation? | `factory.py` raises `NotImplementedError` with a clear message pointing to the stub location. |
