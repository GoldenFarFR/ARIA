from aria_core.llm import is_llm_configured, is_llm_provider_configured
from aria_core.runtime import get_settings


def test_llm_disabled_even_with_provider(monkeypatch):
    """20/07 -- même correctif d'isolation que test_proactive.py (mutation directe du
    singleton settings, jamais nettoyée par monkeypatch)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "aria_llm_enabled", False)
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    assert is_llm_provider_configured() is True
    assert is_llm_configured() is False


def test_llm_enabled_with_provider(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "aria_llm_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    assert is_llm_configured() is True