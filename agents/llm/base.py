from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A single tool call returned by the LLM — args always pre-parsed to dict."""
    id:   str
    name: str
    args: dict  # always a dict — never a JSON string


@dataclass
class LLMTurn:
    """
    Provider-neutral result of one LLM round-trip.

    raw_message: the provider-specific object that must be appended to the
    messages list for the next iteration (e.g. the OpenAI SDK ChatCompletionMessage).
    Storing the SDK object — not a re-serialised dict — preserves multi-turn
    compatibility across providers without converting back and forth.
    """
    finish_reason:     str
    content:           str | None
    tool_calls:        list[ToolCall] = field(default_factory=list)
    prompt_tokens:     int            = 0
    completion_tokens: int            = 0
    raw_message:       Any            = None


class LLMProviderError(Exception):
    """
    Raised by any LLMProvider on API failure.
    Rule 6 in triage_agent.run() catches this and posts a Slack alert.
    """


@runtime_checkable
class LLMProvider(Protocol):
    """
    Provider-neutral interface for one LLM chat round-trip.

    messages: full conversation history in OpenAI format (list[dict]).
    tools:    tool schemas in OpenAI format; each provider converts internally.
    system:   system prompt string (injected as messages[0] for OpenAI;
              passed as separate kwarg for Anthropic).
    """
    async def chat(
        self,
        messages: list[dict],
        tools:    list[dict],
        system:   str = "",
    ) -> LLMTurn: ...
