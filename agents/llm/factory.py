"""
LLM provider factory — returns the configured LLMProvider from settings.

To add a new provider:
  1. Create agents/llm/anthropic_provider.py implementing the LLMProvider protocol
  2. Add a branch here: if settings.LLM_PROVIDER == "anthropic": return AnthropicProvider(...)
"""
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


def get_judge_llm_provider(settings) -> LLMProvider:
    """
    Provider used only for post-run LLM-as-Judge scoring.

    Defaults to OpenAI + JUDGE_LLM_MODEL (gpt-4o-mini) so triage and judge can differ.
    """
    if settings.JUDGE_LLM_PROVIDER == "openai":
        return OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.JUDGE_LLM_MODEL,
        )
    raise NotImplementedError(
        f"Judge LLM provider '{settings.JUDGE_LLM_PROVIDER}' is not implemented. "
        f"Supported: 'openai'."
    )
