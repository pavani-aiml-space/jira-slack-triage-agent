import pytest
from unittest.mock import MagicMock

from agents.llm.factory import get_judge_llm_provider, get_llm_provider
from agents.llm.anthropic_provider import AnthropicProvider
from agents.llm.openai_provider import OpenAIProvider


def _settings(provider="anthropic", model="claude-test"):
    s = MagicMock()
    s.LLM_PROVIDER = provider
    s.OPENAI_API_KEY = "sk-test"
    s.ANTHROPIC_API_KEY = "sk-ant-test"
    s.LLM_MODEL = model
    s.LLM_MAX_TOKENS = 4096
    return s


def _judge_settings(provider="openai", model="gpt-4o-mini"):
    s = MagicMock()
    s.JUDGE_LLM_PROVIDER = provider
    s.OPENAI_API_KEY = "sk-test"
    s.ANTHROPIC_API_KEY = "sk-ant-test"
    s.JUDGE_LLM_MODEL = model
    s.LLM_MAX_TOKENS = 4096
    return s


def test_get_llm_provider_anthropic_returns_anthropic_provider():
    provider = get_llm_provider(_settings("anthropic", "claude-sonnet-4-5-20250929"))
    assert isinstance(provider, AnthropicProvider)
    assert provider._model == "claude-sonnet-4-5-20250929"


def test_get_llm_provider_openai_returns_openai_provider():
    provider = get_llm_provider(_settings("openai", "gpt-4o"))
    assert isinstance(provider, OpenAIProvider)
    assert provider._model == "gpt-4o"


def test_get_llm_provider_unknown_raises_not_implemented_error():
    with pytest.raises(NotImplementedError) as exc_info:
        get_llm_provider(_settings("gemini"))
    msg = str(exc_info.value).lower()
    assert "gemini" in msg
    assert "anthropic" in msg
    assert "openai" in msg


def test_llm_package_exports_public_api():
    """__init__.py re-exports the full public API."""
    from agents.llm import (  # noqa: F401
        LLMProvider,
        LLMTurn,
        ToolCall,
        LLMProviderError,
        get_llm_provider,
        get_judge_llm_provider,
    )


def test_get_judge_llm_provider_openai_uses_judge_model():
    provider = get_judge_llm_provider(_judge_settings("openai", "gpt-4o-mini"))
    assert isinstance(provider, OpenAIProvider)
    assert provider._model == "gpt-4o-mini"


def test_get_judge_llm_provider_anthropic_supported():
    provider = get_judge_llm_provider(_judge_settings("anthropic", "claude-3-5-haiku-latest"))
    assert isinstance(provider, AnthropicProvider)
    assert provider._model == "claude-3-5-haiku-latest"


def test_get_judge_llm_provider_unknown_raises():
    with pytest.raises(NotImplementedError) as exc:
        get_judge_llm_provider(_judge_settings("gemini"))
    assert "judge" in str(exc.value).lower()
