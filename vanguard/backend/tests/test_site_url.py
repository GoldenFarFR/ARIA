import os

from app.config import Settings


def test_custom_domain_beats_render_external(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://test-1-nwf2.onrender.com")
    s = Settings(site_base_url="https://ariavanguardzhc.com", debug=False)
    assert s.public_site_url == "https://ariavanguardzhc.com"


def test_holding_domain_fallback_in_production(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    s = Settings(site_base_url="", holding_domain="ariavanguardzhc.com", debug=False)
    assert s.public_site_url == "https://ariavanguardzhc.com"


def test_api_url_separate_from_holding_vitrine(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    s = Settings(
        site_base_url="https://api.ariavanguardzhc.com",
        holding_domain="ariavanguardzhc.com",
        debug=False,
    )
    assert s.public_site_url == "https://api.ariavanguardzhc.com"
    assert s.public_holding_url == "https://ariavanguardzhc.com"
    assert s.telegram_webhook_url == "https://api.ariavanguardzhc.com/api/telegram/webhook"