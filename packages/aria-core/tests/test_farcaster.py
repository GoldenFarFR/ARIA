"""Client Farcaster/Warpcast (19/07, vérification d'un profil déclaré) -- aucun
appel réseau réel, httpx.AsyncClient mocké."""
from __future__ import annotations

import pytest

from aria_core.services.farcaster import (
    FarcasterProfileVerification,
    _parse_username,
    format_profile_verification,
    verify_profile,
)

REAL_PAYLOAD = {
    "result": {
        "user": {
            "followerCount": 345209,
            "extras": {"publicSpamLabel": "2 (unlikely to engage in spammy behavior)"},
        }
    }
}


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


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
        "aria_core.services.farcaster.httpx.AsyncClient", lambda **kw: FakeClient(response),
    )


def test_parse_username_variants():
    assert _parse_username("https://warpcast.com/dwr") == "dwr"
    assert _parse_username("https://warpcast.com/cobot/") == "cobot"
    assert _parse_username("not a farcaster url") is None


@pytest.mark.asyncio
async def test_verify_profile_real_schema_parses_correctly(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(200, REAL_PAYLOAD))

    result = await verify_profile("https://warpcast.com/dwr")

    assert result.available is True
    assert result.exists is True
    assert result.follower_count == 345209
    assert result.spam_label == "2 (unlikely to engage in spammy behavior)"


@pytest.mark.asyncio
async def test_verify_profile_404_is_exists_false(monkeypatch):
    """Vérifié en direct (19/07) : un username valide mais inexistant renvoie 404
    ("No FID associated with username ...") -- pas un 400 comme un premier test mal
    formé l'avait suggéré à tort."""
    _patch_client(monkeypatch, FakeResponse(404))

    result = await verify_profile("https://warpcast.com/zzznonexist9")

    assert result.available is True
    assert result.exists is False


@pytest.mark.asyncio
async def test_verify_profile_malformed_username_400_is_unavailable(monkeypatch):
    """Un username qui ne respecte pas le format Warpcast (ex. extrait d'une URL
    cassée) renvoie 400 -- dégradation honnête, jamais un exists=False fabriqué."""
    _patch_client(monkeypatch, FakeResponse(400))

    result = await verify_profile("https://warpcast.com/this-is-way-too-long-for-warpcast")

    assert result.available is False
    assert result.exists is None


@pytest.mark.asyncio
async def test_verify_profile_empty_user_object_is_exists_false(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(200, {"result": {}}))

    result = await verify_profile("https://warpcast.com/dwr")

    assert result.available is True
    assert result.exists is False


@pytest.mark.asyncio
async def test_verify_profile_network_exception_never_raises(monkeypatch):
    def _raise(**kw):
        raise RuntimeError("réseau down")

    monkeypatch.setattr("aria_core.services.farcaster.httpx.AsyncClient", _raise)

    result = await verify_profile("https://warpcast.com/dwr")

    assert result.available is False


@pytest.mark.asyncio
async def test_verify_profile_unparseable_url_no_network_call(monkeypatch):
    def _fail_if_called(**kw):
        raise AssertionError("ne doit jamais être appelé, URL illisible")

    monkeypatch.setattr("aria_core.services.farcaster.httpx.AsyncClient", _fail_if_called)

    result = await verify_profile("not a url at all")

    assert result.available is False


def test_format_profile_verification_unavailable():
    assert format_profile_verification(FarcasterProfileVerification(available=False)) == "vérification indisponible"


def test_format_profile_verification_full_signal():
    v = FarcasterProfileVerification(
        available=True, exists=True, follower_count=345209,
        spam_label="2 (unlikely to engage in spammy behavior)",
    )
    formatted = format_profile_verification(v)
    assert "345209 abonnés" in formatted
    assert "spam" in formatted.lower()
