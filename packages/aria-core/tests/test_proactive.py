from aria_core.proactive import proactive_ideas_enabled
from aria_core.runtime import get_settings


def test_proactive_ideas_requires_llm_and_telegram():
    settings = get_settings()

    settings.aria_proactive_ideas = True
    settings.aria_llm_enabled = False
    settings.llm_api_key = "x"
    settings.llm_provider = "groq"
    settings.telegram_bot_token = "t"
    settings.telegram_admin_ids = "1"
    assert proactive_ideas_enabled() is False

    settings.aria_llm_enabled = True
    assert proactive_ideas_enabled() is True