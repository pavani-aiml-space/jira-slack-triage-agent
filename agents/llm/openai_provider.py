"""
OpenAI LLM provider — wraps the synchronous OpenAI SDK in the LLMProvider protocol.

Normalisation table (OpenAI response → LLMTurn):
  response.choices[0].finish_reason          → finish_reason
  response.choices[0].message                → raw_message  (SDK object; appended to messages for multi-turn)
  response.choices[0].message.content        → content
  response.choices[0].message.tool_calls     → tool_calls (list[ToolCall], args pre-parsed to dict)
  json.loads(tc.function.arguments)          → ToolCall.args
  response.usage.prompt_tokens               → prompt_tokens
  response.usage.completion_tokens           → completion_tokens

Error contract:
  openai.APIError (any subclass) → LLMProviderError  (Rule 12)
  Any other exception propagates unchanged.
"""
from __future__ import annotations

import asyncio
import json

import openai
from openai import OpenAI

from agents.llm.base import LLMProviderError, LLMTurn, ToolCall


class OpenAIProvider:
    """
    LLMProvider implementation backed by the OpenAI Chat Completions API.

    The synchronous SDK call runs in a thread pool via asyncio.to_thread() so
    the event loop is never blocked.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    async def chat(
        self,
        messages: list[dict],
        tools:    list[dict],
        system:   str = "",
    ) -> LLMTurn:
        """
        One LLM round-trip. Returns a provider-neutral LLMTurn.

        messages: full conversation history in OpenAI format.
        tools:    OpenAI-format tool schemas ({"type": "function", "function": {...}}).
        system:   reserved for future use — OpenAI receives the system prompt
                  embedded in messages[0] by the caller; this kwarg is ignored here
                  but required by the LLMProvider protocol for Anthropic compatibility.
        """
        try:
            kwargs: dict = {"model": self._model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                **kwargs,
            )
        except openai.APIError as e:
            raise LLMProviderError(str(e)) from e

        choice      = response.choices[0]
        finish      = choice.finish_reason
        llm_message = choice.message

        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                args=json.loads(tc.function.arguments),
            )
            for tc in (llm_message.tool_calls or [])
        ]

        return LLMTurn(
            finish_reason=finish,
            content=llm_message.content,
            tool_calls=tool_calls,
            prompt_tokens=int(getattr(response.usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(response.usage, "completion_tokens", 0) or 0),
            raw_message=llm_message,
        )
