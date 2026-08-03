# Implementation Plan: Phase 8 — Model-Agnostic LLM Provider

**Date:** 2026-04-30
**Brainstorm:** `docs/plans/2026-04-30-phase8-llm-provider-brainstorm.md`
**Design:** `docs/plans/2026-04-30-phase8-llm-provider-design.md`
**Diagram:** `docs/diagrams/2026-04-30-phase8-llm-provider.md`
**Status:** ✅ Built

---

## Goal

Wrap the OpenAI SDK behind an `LLMProvider` protocol so `triage_agent._run_llm_loop()` calls `_provider.chat()` instead of `_client.chat.completions.create()` directly — enabling a future provider swap with one new file.

## Architecture

The new `agents/llm/` package defines a `LLMProvider` Protocol and `LLMTurn` / `ToolCall` dataclasses. `OpenAIProvider` implements the protocol, wrapping the existing `asyncio.to_thread(sync_client.chat.completions.create, ...)` pattern. `get_llm_provider(settings)` reads `LLM_PROVIDER` from config and returns the right instance. `triage_agent.py` replaces `_client` with `_provider` and reads from `LLMTurn` — no `json.loads()`, no `choices[0]`, no `openai.APIError`.

## Files Affected

| Action | File |
|--------|------|
| CREATE | `agents/llm/__init__.py` |
| CREATE | `agents/llm/base.py` |
| CREATE | `agents/llm/openai_provider.py` |
| CREATE | `agents/llm/factory.py` |
| CREATE | `tests/unit/test_llm_base.py` |
| CREATE | `tests/unit/test_openai_provider.py` |
| CREATE | `tests/unit/test_llm_factory.py` |
| MODIFY | `config/settings.py` |
| MODIFY | `agents/triage/triage_agent.py` |
| MODIFY | `tests/unit/test_triage_agent.py` |

---

## Block 1 — LLM Abstraction Package

### Chunk 1.1 — Data contracts (`agents/llm/base.py`)

```
Test layer: UNIT
Files:
  Create: agents/llm/__init__.py   (empty placeholder)
  Create: agents/llm/base.py
  Create: tests/unit/test_llm_base.py
```

**Step 1 (RED)** — Write this failing test:

```python
# tests/unit/test_llm_base.py
from agents.llm.base import ToolCall, LLMTurn, LLMProviderError

def test_tool_call_stores_parsed_args():
    tc = ToolCall(id="tc1", name="create_jira_ticket", args={"summary": "bug"})
    assert tc.id == "tc1"
    assert tc.name == "create_jira_ticket"
    assert isinstance(tc.args, dict)

def test_llm_turn_has_empty_defaults():
    turn = LLMTurn(finish_reason="stop", content="done")
    assert turn.tool_calls == []
    assert turn.prompt_tokens == 0
    assert turn.completion_tokens == 0
    assert turn.raw_message is None

def test_llm_turn_tool_calls_list():
    tc = ToolCall(id="tc1", name="create_jira_ticket", args={})
    turn = LLMTurn(finish_reason="tool_calls", content=None, tool_calls=[tc])
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "create_jira_ticket"

def test_llm_provider_error_is_exception():
    err = LLMProviderError("API unavailable")
    assert isinstance(err, Exception)
    assert str(err) == "API unavailable"
```

Run: `pytest tests/unit/test_llm_base.py -v`
Expect: `FAILED — ModuleNotFoundError: No module named 'agents.llm'`

**Step 2 (GREEN)** — Create `agents/llm/__init__.py` (empty) and `agents/llm/base.py`:

```python
# agents/llm/base.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

@dataclass
class ToolCall:
    id:   str
    name: str
    args: dict  # always a dict — never a JSON string

@dataclass
class LLMTurn:
    finish_reason:     str
    content:           str | None
    tool_calls:        list[ToolCall] = field(default_factory=list)
    prompt_tokens:     int            = 0
    completion_tokens: int            = 0
    raw_message:       Any            = None

class LLMProviderError(Exception):
    """Raised by any LLMProvider on API failure. Rule 6 catches this."""

@runtime_checkable
class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[dict],
        tools:    list[dict],
        system:   str = "",
    ) -> LLMTurn: ...
```

Run: `pytest tests/unit/test_llm_base.py -v`
Expect: `PASSED (4 tests)`

**Step 3 (REFACTOR)** — No changes needed; dataclasses are minimal.

Run: `pytest tests/unit/test_llm_base.py -v`
Expect: still `PASSED`

**Step 4 (COMMIT):**
```bash
git commit -m "[Add] agents/llm/base.py — LLMTurn, ToolCall, LLMProviderError, LLMProvider Protocol"
```

---

### Chunk 1.2 — OpenAIProvider stop-path normalisation

```
Test layer: UNIT
Files:
  Create: agents/llm/openai_provider.py
  Create: tests/unit/test_openai_provider.py
```

**Step 1 (RED)** — Write this failing test:

```python
# tests/unit/test_openai_provider.py
import pytest
from unittest.mock import MagicMock, patch
from agents.llm.openai_provider import OpenAIProvider
from agents.llm.base import LLMTurn

def _make_stop_response(content="Done"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp

@pytest.mark.asyncio
async def test_chat_stop_returns_llm_turn():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_stop_response()

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert isinstance(turn, LLMTurn)
        assert turn.finish_reason == "stop"
        assert turn.content == "Done"
        assert turn.tool_calls == []
        assert turn.prompt_tokens == 10
        assert turn.completion_tokens == 5

@pytest.mark.asyncio
async def test_chat_passes_messages_and_tools_to_sdk():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_stop_response()
        msgs = [{"role": "user", "content": "hello"}]
        tools = [{"type": "function", "function": {"name": "test"}}]

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        await provider.chat(messages=msgs, tools=tools, system="")

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["messages"] == msgs
        assert call_kwargs.kwargs["tools"] == tools
        assert call_kwargs.kwargs["model"] == "gpt-4o"
```

Run: `pytest tests/unit/test_openai_provider.py -v`
Expect: `FAILED — ModuleNotFoundError: No module named 'agents.llm.openai_provider'`

**Step 2 (GREEN)** — Create `agents/llm/openai_provider.py` with stop-path only:

```python
# agents/llm/openai_provider.py
from __future__ import annotations
import asyncio
import openai
from openai import OpenAI
from agents.llm.base import LLMTurn, ToolCall, LLMProviderError


class OpenAIProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    async def chat(
        self,
        messages: list[dict],
        tools:    list[dict],
        system:   str = "",
    ) -> LLMTurn:
        try:
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=self._model,
                messages=messages,
                tools=tools,
            )
        except openai.APIError as e:
            raise LLMProviderError(str(e)) from e

        choice      = response.choices[0]
        finish      = choice.finish_reason
        llm_message = choice.message

        return LLMTurn(
            finish_reason=finish,
            content=llm_message.content,
            tool_calls=[],
            prompt_tokens=int(getattr(response.usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(response.usage, "completion_tokens", 0) or 0),
            raw_message=llm_message,
        )
```

Run: `pytest tests/unit/test_openai_provider.py -v`
Expect: `PASSED (2 tests)`

**Step 3 (REFACTOR)** — No changes needed yet; tool_calls path added in next chunk.

**Step 4 (COMMIT):**
```bash
git commit -m "[Add] agents/llm/openai_provider.py — OpenAIProvider stop-path normalisation"
```

---

### Chunk 1.3 — OpenAIProvider tool_calls-path + token accumulation

```
Test layer: UNIT
Files:
  Modify: agents/llm/openai_provider.py
  Modify: tests/unit/test_openai_provider.py
```

**Step 1 (RED)** — Add these failing tests to `test_openai_provider.py`:

```python
import json

def _make_tool_calls_response(tool_calls_list, content=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "tool_calls"
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls_list
    resp.usage.prompt_tokens = 20
    resp.usage.completion_tokens = 8
    return resp

@pytest.mark.asyncio
async def test_chat_tool_calls_returns_parsed_tool_call():
    mock_tc = MagicMock()
    mock_tc.id = "call_xyz"
    mock_tc.function.name = "create_jira_ticket"
    mock_tc.function.arguments = '{"summary": "Login crash", "issue_type": "Bug"}'

    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_tool_calls_response([mock_tc])

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert turn.finish_reason == "tool_calls"
        assert len(turn.tool_calls) == 1
        tc = turn.tool_calls[0]
        assert tc.id == "call_xyz"
        assert tc.name == "create_jira_ticket"
        assert tc.args == {"summary": "Login crash", "issue_type": "Bug"}
        assert isinstance(tc.args, dict)   # never a JSON string

@pytest.mark.asyncio
async def test_chat_tool_calls_stores_raw_message():
    mock_tc = MagicMock()
    mock_tc.id = "call_abc"
    mock_tc.function.name = "post_slack_message"
    mock_tc.function.arguments = '{"message": "Done"}'
    mock_msg = MagicMock()
    mock_msg.tool_calls = [mock_tc]
    mock_msg.content = None

    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].finish_reason = "tool_calls"
        resp.choices[0].message = mock_msg
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 5
        mock_client.chat.completions.create.return_value = resp

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        turn = await provider.chat(messages=[], tools=[], system="")

        assert turn.raw_message is mock_msg  # exact SDK object — required for multi-turn
```

Run: `pytest tests/unit/test_openai_provider.py -v`
Expect: `FAILED — AssertionError: turn.tool_calls == [] (tool_calls path not implemented yet)`

**Step 2 (GREEN)** — Update `OpenAIProvider.chat()` to build `ToolCall` list:

In the `LLMTurn(...)` constructor call, replace `tool_calls=[]` with:

```python
tool_calls=[
    ToolCall(
        id=tc.id,
        name=tc.function.name,
        args=json.loads(tc.function.arguments),
    )
    for tc in (llm_message.tool_calls or [])
],
```

Add `import json` at top of `openai_provider.py`.

Run: `pytest tests/unit/test_openai_provider.py -v`
Expect: `PASSED (4 tests)`

**Step 3 (REFACTOR)** — Add docstring to `OpenAIProvider.chat()` explaining the normalisation table (from design doc).

**Step 4 (COMMIT):**
```bash
git commit -m "[Add] OpenAIProvider tool_calls path — ToolCall list with pre-parsed args dict"
```

---

### Chunk 1.4 — OpenAIProvider error wrapping

```
Test layer: UNIT
Files:
  Modify: agents/llm/openai_provider.py   (already handles APIError — verify)
  Modify: tests/unit/test_openai_provider.py
```

**Step 1 (RED)** — Add these failing tests to `test_openai_provider.py`:

```python
import openai
from agents.llm.base import LLMProviderError

@pytest.mark.asyncio
async def test_chat_wraps_api_error_as_llm_provider_error():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = (
            openai.APIConnectionError.__new__(openai.APIConnectionError)
        )

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        with pytest.raises(LLMProviderError):
            await provider.chat(messages=[], tools=[], system="")

@pytest.mark.asyncio
async def test_chat_does_not_swallow_non_api_errors():
    with patch("agents.llm.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ValueError("unexpected")

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
        with pytest.raises(ValueError):
            await provider.chat(messages=[], tools=[], system="")
```

Run: `pytest tests/unit/test_openai_provider.py -v`
Expect: `FAILED — ValueError not raised (current catch is too broad)`

Note: if the Chunk 1.2 GREEN implementation already has `except openai.APIError` (it does), the first test may already pass. The second test is the important one — it confirms `ValueError` is NOT caught.

**Step 2 (GREEN)** — Verify `openai_provider.py` catches only `openai.APIError` (not `Exception`). The catch block from Chunk 1.2 is already correct: `except openai.APIError as e: raise LLMProviderError(str(e)) from e`. No code change needed if already correct.

Run: `pytest tests/unit/test_openai_provider.py -v`
Expect: `PASSED (6 tests)`

**Step 3 (REFACTOR)** — No changes needed.

**Step 4 (COMMIT):**
```bash
git commit -m "[Add] OpenAIProvider error wrapping — openai.APIError → LLMProviderError (Rule 12)"
```

---

### Chunk 1.5 — Factory + `__init__.py` exports

```
Test layer: UNIT
Files:
  Create: agents/llm/factory.py
  Modify: agents/llm/__init__.py
  Create: tests/unit/test_llm_factory.py
```

**Step 1 (RED)** — Write this failing test:

```python
# tests/unit/test_llm_factory.py
import pytest
from unittest.mock import MagicMock
from agents.llm.factory import get_llm_provider
from agents.llm.openai_provider import OpenAIProvider

def _settings(provider="openai"):
    s = MagicMock()
    s.LLM_PROVIDER = provider
    s.OPENAI_API_KEY = "sk-test"
    s.LLM_MODEL = "gpt-4o"
    return s

def test_get_llm_provider_openai_returns_openai_provider():
    provider = get_llm_provider(_settings("openai"))
    assert isinstance(provider, OpenAIProvider)

def test_get_llm_provider_unknown_raises_not_implemented_error():
    with pytest.raises(NotImplementedError) as exc_info:
        get_llm_provider(_settings("anthropic"))
    assert "anthropic" in str(exc_info.value).lower()
    assert "openai" in str(exc_info.value).lower()

def test_get_llm_provider_not_implemented_message_points_to_stub():
    with pytest.raises(NotImplementedError) as exc_info:
        get_llm_provider(_settings("gemini"))
    # message must tell developer where to add the new provider
    assert "anthropic_provider.py" in str(exc_info.value)
```

Run: `pytest tests/unit/test_llm_factory.py -v`
Expect: `FAILED — ModuleNotFoundError: No module named 'agents.llm.factory'`

**Step 2 (GREEN)** — Create `agents/llm/factory.py`:

```python
# agents/llm/factory.py
from __future__ import annotations
from agents.llm.base import LLMProvider
from agents.llm.openai_provider import OpenAIProvider


def get_llm_provider(settings) -> LLMProvider:
    if settings.LLM_PROVIDER == "openai":
        return OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.LLM_MODEL,
        )
    raise NotImplementedError(
        f"LLM provider '{settings.LLM_PROVIDER}' is not implemented. "
        f"Supported: 'openai'. "
        f"To add Anthropic, create agents/llm/anthropic_provider.py "
        f"implementing the LLMProvider protocol."
    )
```

Update `agents/llm/__init__.py` to export the public API:

```python
# agents/llm/__init__.py
from agents.llm.base import LLMProvider, LLMTurn, ToolCall, LLMProviderError
from agents.llm.factory import get_llm_provider

__all__ = ["LLMProvider", "LLMTurn", "ToolCall", "LLMProviderError", "get_llm_provider"]
```

Run: `pytest tests/unit/test_llm_factory.py -v`
Expect: `PASSED (3 tests)`

**Step 3 (REFACTOR)** — No changes needed.

**Step 4 (COMMIT):**
```bash
git commit -m "[Add] agents/llm/factory.py — get_llm_provider() dispatch; agents/llm/__init__.py exports"
```

---

## Block 2 — Config

### Chunk 2.1 — Add `LLM_PROVIDER` setting

```
Test layer: UNIT
Files:
  Modify: config/settings.py
  Modify: tests/unit/test_triage_agent.py  (one new test)
```

**Step 1 (RED)** — Add this test to `test_triage_agent.py` (top of file, with the other settings tests):

```python
def test_settings_has_llm_provider_defaulting_to_openai():
    from config.settings import Settings
    assert Settings.LLM_PROVIDER == "openai"
```

Run: `pytest tests/unit/test_triage_agent.py::test_settings_has_llm_provider_defaulting_to_openai -v`
Expect: `FAILED — AttributeError: type object 'Settings' has no attribute 'LLM_PROVIDER'`

**Step 2 (GREEN)** — Add to `config/settings.py` after the `LLM_MODEL` line:

```python
# ── LLM provider ───────────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
# Currently only "openai" is supported.
# To add Anthropic: create agents/llm/anthropic_provider.py and update factory.py.
```

Run: `pytest tests/unit/test_triage_agent.py::test_settings_has_llm_provider_defaulting_to_openai -v`
Expect: `PASSED`

**Step 3 (REFACTOR)** — No changes needed.

**Step 4 (COMMIT):**
```bash
git commit -m "[Add] config/settings.py — LLM_PROVIDER setting (default: openai)"
```

---

## Block 3 — Wire Provider into `triage_agent.py`

### Chunk 3.1 — Replace `_client` with `_provider` at module level

```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
  Modify: tests/unit/test_triage_agent.py
```

**Step 1 (RED)** — Add this test to `test_triage_agent.py`:

```python
def test_triage_agent_exposes_provider_not_raw_client():
    import agents.triage.triage_agent as m
    assert hasattr(m, "_provider"), "_provider must exist at module level"
    assert not hasattr(m, "_client"), "_client must be removed"
```

Run: `pytest tests/unit/test_triage_agent.py::test_triage_agent_exposes_provider_not_raw_client -v`
Expect: `FAILED — AssertionError: _provider must exist at module level`

**Step 2 (GREEN)** — In `agents/triage/triage_agent.py`:

**Remove:**
```python
import openai
from openai import OpenAI
...
_client = OpenAI(api_key=settings.OPENAI_API_KEY)
```

**Add (after the existing imports):**
```python
from agents.llm.base import LLMProvider, LLMProviderError
from agents.llm.factory import get_llm_provider

_provider: LLMProvider = get_llm_provider(settings)
```

Run: `pytest tests/unit/test_triage_agent.py::test_triage_agent_exposes_provider_not_raw_client -v`
Expect: `PASSED`

**Step 3 (REFACTOR)** — Update the module docstring to replace "OpenAI is the brain" with "LLM provider is the brain — configured via settings.LLM_PROVIDER."

**Step 4 (COMMIT):**
```bash
git commit -m "[Refactor] triage_agent.py — replace _client with _provider via get_llm_provider()"
```

---

### Chunk 3.2 — Refactor `_run_llm_loop()` inner loop

```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
  Modify: tests/unit/test_triage_agent.py
```

**Step 1 (RED)** — Add a `make_llm_turn()` helper to `test_triage_agent.py` and write one test using it:

```python
# Add near top of test_triage_agent.py — replace or supplement make_mock_openai_response()

from agents.llm.base import LLMTurn, ToolCall

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
    """Convenience: turn that calls one tool then stops."""
    return LLMTurn(
        finish_reason="tool_calls",
        content=None,
        tool_calls=[ToolCall(id="call_test", name=tool_name, args=tool_args)],
    )
```

Then update ONE representative `_run_llm_loop` test to use `_provider` instead of `_client` (pick `test_run_llm_loop_returns_block_result_on_stop`):

```python
# BEFORE (find and update this test):
#   with patch("agents.triage.triage_agent._client") as mock_client:
#       mock_client.chat.completions.create.return_value = make_mock_openai_response()

# AFTER:
from unittest.mock import AsyncMock

async def test_run_llm_loop_returns_block_result_on_stop():
    with patch("agents.triage.triage_agent._provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value=make_llm_turn(finish_reason="stop"))
        result = await triage_agent._run_llm_loop(
            block_text="Test message",
            block_index=0,
            block_snippet="Test message"[:60],
        )
    assert result.action == "no_action"
    assert result.block_index == 0
```

Run: `pytest tests/unit/test_triage_agent.py::test_run_llm_loop_returns_block_result_on_stop -v`
Expect: `FAILED — AttributeError: '_provider' object has no attribute 'chat'` or similar (because `_run_llm_loop` still uses `_client`)

**Step 2 (GREEN)** — Refactor `_run_llm_loop()` inner loop in `triage_agent.py`:

**Remove** the `asyncio.to_thread(_client.chat.completions.create, ...)` block and all response-parsing lines. **Replace** with:

```python
turn = await _provider.chat(messages, ALL_TOOLS, system_prompt)
finish              = turn.finish_reason
last_finish_reason  = finish
total_prompt_tokens     += turn.prompt_tokens
total_completion_tokens += turn.completion_tokens
messages.append(turn.raw_message)

if finish == "stop":
    if turn.content:
        print(f"  LLM: {turn.content}")
    break

if finish == "tool_calls":
    for tc in turn.tool_calls:
        tool_name = tc.name
        tool_args = tc.args      # already a dict — no json.loads needed
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
```

Also **remove** `import json` (no longer needed — `tc.args` is already a dict).

Run: `pytest tests/unit/test_triage_agent.py::test_run_llm_loop_returns_block_result_on_stop -v`
Expect: `PASSED`

**Step 3 (REFACTOR)** — Clean up `_run_llm_loop` docstring: remove reference to `asyncio.to_thread` and update the comment about `json.loads`.

**Step 4 (COMMIT):**
```bash
git commit -m "[Refactor] triage_agent._run_llm_loop — call _provider.chat(), read LLMTurn (no json.loads)"
```

---

### Chunk 3.3 — Replace `except openai.APIError` with `except LLMProviderError`

```
Test layer: UNIT
Files:
  Modify: agents/triage/triage_agent.py
  Modify: tests/unit/test_triage_agent.py
```

**Step 1 (RED)** — Update the Rule 6 test to use `LLMProviderError` instead of `openai.APIConnectionError`:

Find the test named `test_run_openai_error_posts_slack_alert_and_exits` (or equivalent) and change:

```python
# BEFORE:
llm_side_effect=openai.APIConnectionError.__new__(openai.APIConnectionError)

# AFTER:
from agents.llm.base import LLMProviderError
llm_side_effect=LLMProviderError("API unavailable")
```

Also update the mock to set `mock_provider.chat = AsyncMock(side_effect=LLMProviderError("API unavailable"))` instead of patching `_client`.

Run: `pytest tests/unit/test_triage_agent.py::test_run_openai_error_posts_slack_alert_and_exits -v`
Expect: `FAILED — the except clause still catches openai.APIError`

**Step 2 (GREEN)** — In `triage_agent.py run()`, replace:

```python
except openai.APIError as e:
```

with:

```python
except LLMProviderError as e:
```

Update the error message string to say `"LLM API unavailable"` instead of `"OpenAI API unavailable"`.

Run: `pytest tests/unit/test_triage_agent.py::test_run_openai_error_posts_slack_alert_and_exits -v`
Expect: `PASSED`

**Step 3 (REFACTOR)** — Update the inline comment from `# Rule 6 — OpenAI unavailable` to `# Rule 6 — LLM provider unavailable`.

**Step 4 (COMMIT):**
```bash
git commit -m "[Refactor] triage_agent.run — except LLMProviderError replaces except openai.APIError (Rule 6)"
```

---

## Block 4 — Update Remaining Tests

### Chunk 4.1 — Update all `_run_llm_loop` and `run()` tests to use `_provider` mock

```
Test layer: UNIT
Files:
  Modify: tests/unit/test_triage_agent.py
```

**Step 1 (RED)** — Run the full unit test suite and observe failures:

```
pytest tests/unit/test_triage_agent.py -v
```

Expect: multiple `FAILED` tests that still use `patch("agents.triage.triage_agent._client")`.

**Step 2 (GREEN)** — For every remaining test that patches `_client`:

Replace the mock pattern:
```python
# BEFORE (every occurrence):
with patch("agents.triage.triage_agent._client") as mock_client:
    mock_client.chat.completions.create.return_value = make_mock_openai_response(...)

# AFTER:
with patch("agents.triage.triage_agent._provider") as mock_provider:
    mock_provider.chat = AsyncMock(return_value=make_llm_turn(...))
```

For tests that mock multiple LLM responses (e.g. tool-call then stop), use `side_effect`:
```python
mock_provider.chat = AsyncMock(side_effect=[
    make_tool_call_turn("create_jira_ticket", {...}),
    make_llm_turn(finish_reason="stop"),
])
```

Also update `patch_run_deps()` (the helper that sets up mocks for `run()` tests):
- Remove: `patch("agents.triage.triage_agent._client", ...)`
- Add: `patch("agents.triage.triage_agent._provider", ...)` with `AsyncMock`

Run: `pytest tests/unit/test_triage_agent.py -v`
Expect: `ALL PASSED`

**Step 3 (REFACTOR)** — Remove `make_mock_openai_response()` helper (no longer needed) and clean up any leftover `import openai` in the test file.

Run: `pytest tests/unit/ -v`
Expect: `ALL PASSED` — total count at least 209

**Step 4 (COMMIT):**
```bash
git commit -m "[Test] test_triage_agent.py — replace _client mock with _provider AsyncMock + LLMTurn"
```

---

### Chunk 4.2 — Update `CLAUDE.md` mock guidance

```
Test layer: N/A (docs only)
Files:
  Modify: CLAUDE.md
```

**Step 1** — In `CLAUDE.md → External Dependencies (What to Mock in Tests)`, update:

```
# BEFORE:
- **OpenAI LLM** — `openai.OpenAI().chat.completions.create()` via `triage_agent._client`

# AFTER:
- **OpenAI LLM** — `agents.triage.triage_agent._provider` — patch as AsyncMock returning `LLMTurn(...)`.
  Use `make_llm_turn()` helper in `test_triage_agent.py`. Do NOT patch `_client` — it no longer exists.
  For Rule 6 test: `mock_provider.chat = AsyncMock(side_effect=LLMProviderError("msg"))`
- **LLM Provider** — `agents.triage.triage_agent._provider` (same as above)
```

Also add a new key module entry:
```
- `agents/llm/` — Phase 8 LLM abstraction package: `LLMProvider`, `LLMTurn`, `ToolCall`, `LLMProviderError`, `OpenAIProvider`, `get_llm_provider()`
```

Run: `pytest tests/unit/ -v`
Expect: `ALL PASSED` — this is a docs-only chunk, tests don't change

**Step 4 (COMMIT):**
```bash
git commit -m "[Docs] CLAUDE.md — update mock guidance for Phase 8 LLM provider abstraction"
```

---

## Success Criteria

- [ ] **Zero direct OpenAI SDK calls in `triage_agent.py`** — no `from openai import OpenAI`, no `_client`, no `openai.APIError` — verified by `grep -r "openai" agents/triage/triage_agent.py` returning nothing
- [ ] **All unit tests green** — `pytest tests/unit/ -v` passes with ≥ 232 tests (209 existing + ~23 new provider tests)
- [ ] **Rule 6 fires on `LLMProviderError`** — `test_run_openai_error_posts_slack_alert_and_exits` passes with `LLMProviderError` side effect
- [ ] **Adding Anthropic later requires zero business-logic changes** — verified by code review: `triage_agent.py` contains no provider-specific imports
- [ ] **`LLM_PROVIDER=anthropic` fails loudly at startup** — `test_get_llm_provider_unknown_raises_not_implemented_error` passes
- [ ] **E2E checklist passes** — verified in /audit Part 3 (unchanged behaviour, same OpenAI endpoint)

---

## Known Technical Debt

| Debt | Why acceptable now |
|------|--------------------|
| `AnthropicProvider` not implemented | Interface is the deliverable. Anthropic slots in when actually needed — one new file + one factory line. |
| `semantic_store.summarise_with_llm()` still uses direct `openai.OpenAI()` | Background call, no impact on triage quality. Deferred to Phase 8b or when switching providers. |
| Tool schemas still in OpenAI format (`{"type": "function", ...}`) | Future `AnthropicProvider` converts internally at call time. No business-logic change required. |
| `EMBEDDING_PROVIDER` setting reserved but unused | Anthropic has no embeddings API; embeddings always on OpenAI. No action needed. |
