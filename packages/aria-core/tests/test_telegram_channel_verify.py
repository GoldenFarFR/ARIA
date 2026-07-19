"""Client de vérification de canal Telegram tiers (19/07) -- aucun appel réseau
réel, httpx.AsyncClient mocké. Contenu de page réel capturé en direct (t.me/s/durov,
19/07)."""
from __future__ import annotations

import pytest

from aria_core.services.telegram_channel_verify import (
    TelegramChannelVerification,
    _parse_handle,
    format_channel_verification,
    verify_channel,
)

REAL_PAGE_HTML = (
    '<div class="tgme_channel_info_counters">'
    '<div class="tgme_channel_info_counter"><span class="counter_value">11.6M</span> '
    '<span class="counter_type">subscribers</span></div></div>'
    '<time datetime="2026-05-12T17:28:39+00:00"></time>'
    '<time datetime="2026-05-14T12:38:57+00:00"></time>'
)


class FakeResponse:
    def __init__(self, status_code: int, url: str, text: str = ""):
        self.status_code = status_code
        self.url = url
        self.text = text


class FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        return self._response


def _patch_client(monkeypatch, response):
    monkeypatch.setattr(
        "aria_core.services.telegram_channel_verify.httpx.AsyncClient", lambda **kw: FakeClient(response),
    )


def test_parse_handle_variants():
    assert _parse_handle("https://t.me/durov") == "durov"
    assert _parse_handle("https://t.me/s/durov") == "durov"
    assert _parse_handle("not a telegram url") is None


@pytest.mark.asyncio
async def test_verify_channel_real_page_parses_correctly(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(200, "https://t.me/s/durov", REAL_PAGE_HTML))

    result = await verify_channel("https://t.me/durov")

    assert result.available is True
    assert result.exists is True
    assert result.subscriber_count_display == "11.6M"
    # Doit prendre le DERNIER <time> (le plus récent), pas le premier.
    assert result.days_since_last_post is not None and result.days_since_last_post >= 0


@pytest.mark.asyncio
async def test_verify_channel_redirect_without_s_prefix_is_exists_false(monkeypatch):
    """Redirection confirmée en direct (19/07) : un canal inexistant/privé/sans
    historique redirige vers t.me/<canal> (sans /s/) -- signal fiable."""
    _patch_client(monkeypatch, FakeResponse(200, "https://t.me/nonexistent_channel_xyz", ""))

    result = await verify_channel("https://t.me/nonexistent_channel_xyz")

    assert result.available is True
    assert result.exists is False


@pytest.mark.asyncio
async def test_verify_channel_http_error_is_unavailable(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(500, "https://t.me/s/durov", ""))

    result = await verify_channel("https://t.me/durov")

    assert result.available is False


@pytest.mark.asyncio
async def test_verify_channel_missing_counters_degrades_honestly(monkeypatch):
    """Page réelle mais sans le format de compteur attendu (HTML Telegram change) --
    jamais un chiffre inventé, juste des champs None."""
    _patch_client(monkeypatch, FakeResponse(200, "https://t.me/s/durov", "<html>no counters here</html>"))

    result = await verify_channel("https://t.me/durov")

    assert result.available is True
    assert result.exists is True
    assert result.subscriber_count_display is None
    assert result.days_since_last_post is None


@pytest.mark.asyncio
async def test_verify_channel_network_exception_never_raises(monkeypatch):
    def _raise(**kw):
        raise RuntimeError("réseau down")

    monkeypatch.setattr("aria_core.services.telegram_channel_verify.httpx.AsyncClient", _raise)

    result = await verify_channel("https://t.me/durov")

    assert result.available is False


@pytest.mark.asyncio
async def test_verify_channel_unparseable_url_no_network_call(monkeypatch):
    def _fail_if_called(**kw):
        raise AssertionError("ne doit jamais être appelé, URL illisible")

    monkeypatch.setattr("aria_core.services.telegram_channel_verify.httpx.AsyncClient", _fail_if_called)

    result = await verify_channel("not a url at all")

    assert result.available is False


def test_format_channel_verification_unavailable():
    assert format_channel_verification(TelegramChannelVerification(available=False)) == "vérification indisponible"


def test_format_channel_verification_not_found():
    v = TelegramChannelVerification(available=True, exists=False)
    assert "introuvable" in format_channel_verification(v)


def test_format_channel_verification_full_signal():
    v = TelegramChannelVerification(
        available=True, exists=True, subscriber_count_display="11.6M", days_since_last_post=2,
    )
    formatted = format_channel_verification(v)
    assert "11.6M abonnés" in formatted
    assert "2j" in formatted
