"""
LLM provider factory — returns the configured LLMProvider from settings.

Supported:
  - anthropic (default) — Claude via AnthropicProvider
  - openai — GPT via OpenAIProvider

Embeddings stay on OpenAI regardless of LLM_PROVIDER (Anthropic has no embeddings API).
"""
from __future__ import annotations

from agents.llm.base import LLMProvider
from agents.llm.anthropic_provider import AnthropicProvider
from agents.llm.openai_provider import OpenAIProvider


def get_llm_provider(settings) -> LLMProvider:
    provider = (settings.LLM_PROVIDER or "").strip().lower()
    max_tokens = int(getattr(settings, "LLM_MAX_TOKENS", 4096) or 4096)

    if provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.LLM_MODEL,
            max_tokens=max_tokens,
        )
    if provider == "openai":
        return OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.LLM_MODEL,
        )
    raise NotImplementedError(
        f"LLM provider '{settings.LLM_PROVIDER}' is not implemented. "
        f"Supported: 'anthropic', 'openai'. "
        f"To add another provider, create agents/llm/<name>_provider.py "
        f"implementing the LLMProvider protocol and register it here."
    )


def get_judge_llm_provider(settings) -> LLMProvider:
    """
    Provider used only for post-run LLM-as-Judge scoring.

    Defaults to OpenAI + JUDGE_LLM_MODEL so judge and triage can use different
    model families (reduces self-consistency bias). Set JUDGE_LLM_PROVIDER=anthropic
    to judge with Claude instead.
    """
    provider = (settings.JUDGE_LLM_PROVIDER or "").strip().lower()
    max_tokens = int(getattr(settings, "LLM_MAX_TOKENS", 4096) or 4096)

    if provider == "openai":
        return OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.JUDGE_LLM_MODEL,
        )
    if provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.JUDGE_LLM_MODEL,
            max_tokens=max_tokens,
        )
    raise NotImplementedError(
        f"Judge LLM provider '{settings.JUDGE_LLM_PROVIDER}' is not implemented. "
        f"Supported: 'openai', 'anthropic'."
    )
