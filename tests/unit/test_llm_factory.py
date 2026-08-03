import pytest
from unittest.mock import MagicMock

from agents.llm.factory import get_judge_llm_provider, get_llm_provider
from agents.llm.openai_provider import OpenAIProvider


def _settings(provider="openai"):
    s = MagicMock()
    s.LLM_PROVIDER = provider
    s.OPENAI_API_KEY = "sk-test"
    s.LLM_MODEL = "gpt-4o"
    return s


def _judge_settings(provider="openai", model="gpt-4o-mini"):
    s = MagicMock()
    s.JUDGE_LLM_PROVIDER = provider
    s.OPENAI_API_KEY = "sk-test"
    s.JUDGE_LLM_MODEL = model
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
    assert "anthropic_provider.py" in str(exc_info.value)


def test_llm_package_exports_public_api():
    """__init__.py re-exports the full public API."""
    from agents.llm import LLMProvider, LLMTurn, ToolCall, LLMProviderError, get_llm_provider  # noqa: F401


def test_get_judge_llm_provider_openai_uses_judge_model():
    provider = get_judge_llm_provider(_judge_settings("openai", "gpt-4o-mini"))
    assert isinstance(provider, OpenAIProvider)
    assert provider._model == "gpt-4o-mini"


def test_get_judge_llm_provider_unknown_raises():
    with pytest.raises(NotImplementedError) as exc:
        get_judge_llm_provider(_judge_settings("anthropic"))
    assert "judge" in str(exc.value).lower()
