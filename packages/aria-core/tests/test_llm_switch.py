from aria_core.llm import is_llm_configured, is_llm_provider_configured
from aria_core.runtime import get_settings


def test_llm_disabled_even_with_provider():
    settings = get_settings()
    settings.aria_llm_enabled = False
    settings.llm_provider = "groq"
    settings.llm_api_key = "test-key"
    assert is_llm_provider_configured() is True
    assert is_llm_configured() is False


def test_llm_enabled_with_provider():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "groq"
    settings.llm_api_key = "test-key"
    assert is_llm_configured() is True