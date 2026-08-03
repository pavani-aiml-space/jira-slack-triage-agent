"""
Anthropic LLM provider — wraps the Anthropic Messages API in the LLMProvider protocol.

Accepts OpenAI-format messages and tool schemas from triage_agent; converts
internally before calling the SDK. Returns LLMTurn with finish_reason normalised
to "stop" | "tool_calls" so the agent loop needs no provider-specific branches.

Normalisation table (Anthropic response → LLMTurn):
  response.stop_reason "tool_use"            → finish_reason "tool_calls"
  response.stop_reason "end_turn" / other    → finish_reason "stop"
  text content blocks                        → content (joined)
  tool_use content blocks                    → tool_calls (list[ToolCall])
  response.usage.input_tokens                → prompt_tokens
  response.usage.output_tokens               → completion_tokens
  assistant message dict                     → raw_message (appended for multi-turn)

Error contract:
  anthropic.APIError (any subclass) → LLMProviderError
  Any other exception propagates unchanged.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic
from anthropic import Anthropic

from agents.llm.base import LLMProviderError, LLMTurn, ToolCall


def openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI tool schemas to Anthropic tool defs."""
    converted: list[dict] = []
    for t in tools:
        fn = t.get("function", t)
        converted.append({
            "name": fn["name"],
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted


def _assistant_dict_from_openai_sdk(msg: Any) -> dict:
    """Turn an OpenAI ChatCompletionMessage into an Anthropic assistant message dict."""
    blocks: list[dict] = []
    text = getattr(msg, "content", None)
    if text:
        blocks.append({"type": "text", "text": text})
    for tc in getattr(msg, "tool_calls", None) or []:
        args = tc.function.arguments
        if isinstance(args, str):
            args = json.loads(args)
        blocks.append({
            "type": "tool_use",
            "id": tc.id,
            "name": tc.function.name,
            "input": args,
        })
    return {"role": "assistant", "content": blocks}


def messages_to_anthropic(
    messages: list,
    system_kwarg: str = "",
) -> tuple[str, list[dict]]:
    """
    Convert OpenAI-shaped history (dicts + optional SDK objects) to Anthropic messages.

    Returns (system_prompt, anthropic_messages).
    Consecutive OpenAI role=tool messages become one user message with tool_result blocks.
    """
    system = system_kwarg or ""
    out: list[dict] = []
    i = 0
    n = len(messages)

    while i < n:
        msg = messages[i]

        if not isinstance(msg, dict):
            out.append(_assistant_dict_from_openai_sdk(msg))
            i += 1
            continue

        role = msg.get("role")

        if role == "system":
            content = msg.get("content") or ""
            # Prefer explicit system= from caller; else take first system message
            if not system_kwarg:
                system = content if isinstance(content, str) else str(content)
            i += 1
            continue

        if role == "user":
            content = msg.get("content", "")
            out.append({"role": "user", "content": content})
            i += 1
            continue

        if role == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                out.append({"role": "assistant", "content": content})
            elif content:
                out.append({"role": "assistant", "content": [{"type": "text", "text": content}]})
            else:
                # Empty assistant (edge) — Anthropic rejects empty content; skip if nothing
                tool_calls = msg.get("tool_calls") or []
                blocks = []
                for tc in tool_calls:
                    args = tc.get("function", {}).get("arguments", {})
                    if isinstance(args, str):
                        args = json.loads(args)
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": tc.get("function", {}).get("name"),
                        "input": args,
                    })
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
            i += 1
            continue

        if role == "tool":
            tool_results: list[dict] = []
            while i < n and isinstance(messages[i], dict) and messages[i].get("role") == "tool":
                t = messages[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t["tool_call_id"],
                    "content": t.get("content") or "",
                })
                i += 1
            out.append({"role": "user", "content": tool_results})
            continue

        # Unknown role — skip
        i += 1

    return system, out


def _normalise_stop_reason(stop_reason: str | None) -> str:
    if stop_reason == "tool_use":
        return "tool_calls"
    return "stop"


class AnthropicProvider:
    """
    LLMProvider implementation backed by the Anthropic Messages API.

    Sync SDK call runs via asyncio.to_thread() so the event loop is never blocked.
    """

    def __init__(self, api_key: str, model: str, max_tokens: int = 4096) -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def chat(
        self,
        messages: list[dict],
        tools:    list[dict],
        system:   str = "",
    ) -> LLMTurn:
        system_prompt, anth_messages = messages_to_anthropic(messages, system_kwarg=system)
        anth_tools = openai_tools_to_anthropic(tools) if tools else []

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": anth_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if anth_tools:
            kwargs["tools"] = anth_tools

        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                **kwargs,
            )
        except anthropic.APIError as e:
            raise LLMProviderError(str(e)) from e

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        content_blocks: list[dict] = []

        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
                content_blocks.append({"type": "text", "text": block.text})
            elif btype == "tool_use":
                # block.input is already a dict
                inp = block.input if isinstance(block.input, dict) else dict(block.input)
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=inp))
                content_blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": inp,
                })

        finish = _normalise_stop_reason(response.stop_reason)
        usage = response.usage
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        raw_message = {"role": "assistant", "content": content_blocks}

        return LLMTurn(
            finish_reason=finish,
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw_message=raw_message,
        )
