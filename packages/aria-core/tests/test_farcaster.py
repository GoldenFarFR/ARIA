"""Client Farcaster/Warpcast (19/07, vérification d'un profil déclaré) -- aucun
appel réseau réel, httpx.AsyncClient mocké."""
from __future__ import annotations

import pytest

from aria_core.services.farcaster import (
    FarcasterProfile,
    FarcasterProfileVerification,
    _parse_username,
    format_profile_verification,
    get_profile_by_fid,
    verify_profile,
)

# Real payload shape confirmed live (07/24) via `api.warpcast.com/v2/user?fid=3`
# (Dan Romero) -- trimmed to the fields this module actually reads.
REAL_FID_PAYLOAD = {
    "result": {
        "user": {
            "fid": 3,
            "displayName": "Dan Romero",
            "followerCount": 109798,
            "username": "dwr",
            "connectedAccounts": [
                {"connectedAccountId": "id1", "platform": "x", "username": "dwr", "expired": False},
            ],
            "extras": {
                "fid": 3,
                "custodyAddress": "0x6b0bda3f2ffed5efc83fa8c024acff1dd45793f1",
                "ethWallets": [
                    "0x187c7B0393eBE86378128f2653D0930E33218899",
                    "0x6Ce09Ed5526DE4aFe4a981AD86d17B2F5c92feA5",
                ],
                "publicSpamLabel": "2 (unlikely to engage in spammy behavior)",
            },
        }
    }
}

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


def test_parse_username_recognizes_farcaster_xyz_domain():
    """09/08, real bug found via backtest against real Base pump tokens
    (CLANKER): Warpcast migrated its public domain to farcaster.xyz --
    a project declaring that form must not silently fall through to
    "unreadable Farcaster URL"."""
    assert _parse_username("https://farcaster.xyz/clanker") == "clanker"
    assert _parse_username("https://farcaster.xyz/bankr/") == "bankr"


def test_parse_username_never_matches_a_channel_url():
    """A channel link (DEGEN's real case) is NOT a user profile -- verifying
    one needs separate logic never attempted here. The leading "~" falls
    outside [\\w.\\-]+, so this must yield no match rather than a wrong
    username lookup (e.g. "~" itself)."""
    assert _parse_username("https://farcaster.xyz/~/channel/degen") is None


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


@pytest.mark.asyncio
async def test_get_profile_by_fid_real_schema_parses_correctly(monkeypatch):
    """07/24 -- confirmed live: Warpcast's own connectedAccounts field
    exposes a linked X account for FREE, no Neynar/x402 payment needed."""
    _patch_client(monkeypatch, FakeResponse(200, REAL_FID_PAYLOAD))

    result = await get_profile_by_fid(3)

    assert result.available is True
    assert result.exists is True
    assert result.fid == 3
    assert result.username == "dwr"
    assert result.display_name == "Dan Romero"
    assert result.follower_count == 109798
    assert result.x_username == "dwr"
    assert result.eth_wallets == [
        "0x187c7B0393eBE86378128f2653D0930E33218899",
        "0x6Ce09Ed5526DE4aFe4a981AD86d17B2F5c92feA5",
    ]


@pytest.mark.asyncio
async def test_get_profile_by_fid_expired_x_link_is_ignored(monkeypatch):
    payload = {
        "result": {
            "user": {
                "fid": 3, "username": "dwr", "extras": {},
                "connectedAccounts": [{"platform": "x", "username": "old", "expired": True}],
            }
        }
    }
    _patch_client(monkeypatch, FakeResponse(200, payload))

    result = await get_profile_by_fid(3)

    assert result.x_username is None


@pytest.mark.asyncio
async def test_get_profile_by_fid_404_is_exists_false(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(404))

    result = await get_profile_by_fid(999999999)

    assert result.available is True
    assert result.exists is False


@pytest.mark.asyncio
async def test_get_profile_by_fid_empty_user_is_exists_false(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(200, {"result": {}}))

    result = await get_profile_by_fid(3)

    assert result.available is True
    assert result.exists is False


@pytest.mark.asyncio
async def test_get_profile_by_fid_network_exception_never_raises(monkeypatch):
    def _raise(**kw):
        raise RuntimeError("réseau down")

    monkeypatch.setattr("aria_core.services.farcaster.httpx.AsyncClient", _raise)

    result = await get_profile_by_fid(3)

    assert result.available is False


def test_farcaster_profile_dataclass_defaults():
    p = FarcasterProfile(available=False)
    assert p.eth_wallets == []
    assert p.x_username is None
