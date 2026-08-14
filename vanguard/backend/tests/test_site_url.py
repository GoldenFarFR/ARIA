from app.config import Settings


def test_custom_domain_beats_holding_domain():
    s = Settings(
        site_base_url="https://ariavanguardzhc.com",
        holding_domain="other-domain.com",
        debug=False,
    )
    assert s.public_site_url == "https://ariavanguardzhc.com"


def test_holding_domain_fallback_in_production():
    s = Settings(site_base_url="", holding_domain="ariavanguardzhc.com", debug=False)
    assert s.public_site_url == "https://ariavanguardzhc.com"


def test_api_url_separate_from_holding_vitrine():
    s = Settings(
        site_base_url="https://api.ariavanguardzhc.com",
        holding_domain="ariavanguardzhc.com",
        debug=False,
    )
    assert s.public_site_url == "https://api.ariavanguardzhc.com"
    assert s.public_holding_url == "https://ariavanguardzhc.com"
    assert s.telegram_webhook_url == "https://api.ariavanguardzhc.com/api/telegram/webhook"
