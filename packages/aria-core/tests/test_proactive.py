from aria_core.proactive import proactive_ideas_enabled
from aria_core.runtime import get_settings


def test_proactive_ideas_requires_llm_and_telegram(monkeypatch):
    """20/07 -- bug réel trouvé en investiguant les 7 échecs `test_run_founder_ping_*`
    (visibles uniquement en suite complète, jamais en isolation) : ce test mutait le
    singleton ``settings`` partagé DIRECTEMENT (``settings.x = y``), sans jamais passer
    par ``monkeypatch`` -- la mutation survivait donc à la fixture ``_isolated_runtime``
    (autouse) pour ce test précis, contrairement à la convention déjà établie partout
    ailleurs dans ce fichier de tests."""
    settings = get_settings()

    monkeypatch.setattr(settings, "aria_proactive_ideas", True)
    monkeypatch.setattr(settings, "aria_llm_enabled", False)
    monkeypatch.setattr(settings, "llm_api_key", "x")
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    monkeypatch.setattr(settings, "telegram_admin_ids", "1")
    assert proactive_ideas_enabled() is False

    monkeypatch.setattr(settings, "aria_llm_enabled", True)
    assert proactive_ideas_enabled() is True