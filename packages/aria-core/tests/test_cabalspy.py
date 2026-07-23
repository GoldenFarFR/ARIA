"""Client CabalSpy -- wallets KOL/Smart Money labellisés multi-chain (23/07).

Aucun réseau réel : httpx.AsyncClient monkeypatché. La clé n'est jamais écrite
en dur -- posée via monkeypatch.setenv."""
from __future__ import annotations

import pytest

from aria_core.services import cabalspy


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeAsyncClient:
    _responses = None  # liste de réponses successives (pagination)
    _captured_params = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        type(self)._captured_params = params
        responses = type(self)._responses
        if not responses:
            return _FakeResponse(200, {"success": False})
        return responses.pop(0) if len(responses) > 1 else responses[0]


@pytest.fixture
def _fresh(monkeypatch):
    monkeypatch.setenv("CABALSPY_API_KEY", "test-key-sentinel")
    monkeypatch.setattr(cabalspy, "_last_call_at", -10_000.0)
    monkeypatch.setattr(cabalspy.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient._responses = None
    _FakeAsyncClient._captured_params = None


def _kol_page(addresses: list[str], *, has_more: bool, next_cursor: str | None = None) -> _FakeResponse:
    return _FakeResponse(
        200,
        {
            "success": True,
            "data": {
                "blockchain": "base", "type": "kol",
                "wallets": [
                    {"wallet_address": a, "name": f"user{i}", "twitter": f"@u{i}", "telegram": "", "copytrade_link": ""}
                    for i, a in enumerate(addresses)
                ],
                "pagination": {"total": 200, "limit": 100, "next_cursor": next_cursor, "has_more": has_more},
            },
        },
    )


def test_is_configured(monkeypatch):
    monkeypatch.delenv("CABALSPY_API_KEY", raising=False)
    assert cabalspy.is_cabalspy_configured() is False
    monkeypatch.setenv("CABALSPY_API_KEY", "x")
    assert cabalspy.is_cabalspy_configured() is True


@pytest.mark.asyncio
async def test_list_wallets_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("CABALSPY_API_KEY", raising=False)
    assert await cabalspy.list_wallets("base") is None


@pytest.mark.asyncio
async def test_list_wallets_invalid_blockchain_returns_none(_fresh):
    assert await cabalspy.list_wallets("dogecoin") is None


@pytest.mark.asyncio
async def test_list_wallets_single_page(_fresh):
    _FakeAsyncClient._responses = [_kol_page(["0xAAA", "0xBBB"], has_more=False)]
    wallets = await cabalspy.list_wallets("base", wallet_type="kol")
    assert wallets is not None
    assert len(wallets) == 2
    assert wallets[0].wallet_address == "0xAAA"
    assert wallets[0].name == "user0"
    assert wallets[0].blockchain == "base"
    assert wallets[0].type == "kol"


@pytest.mark.asyncio
async def test_list_wallets_paginates_with_cursor(_fresh, monkeypatch):
    pages = [
        _kol_page(["0xAAA"], has_more=True, next_cursor="cursor2"),
        _kol_page(["0xBBB"], has_more=False),
    ]
    calls = []

    class _PagedClient(_FakeAsyncClient):
        async def get(self, url, params=None):
            calls.append(dict(params))
            return pages.pop(0)

    monkeypatch.setattr(cabalspy.httpx, "AsyncClient", _PagedClient)
    wallets = await cabalspy.list_wallets("base")
    assert len(wallets) == 2
    assert calls[0].get("cursor") is None
    assert calls[1]["cursor"] == "cursor2"


@pytest.mark.asyncio
async def test_list_wallets_api_key_never_in_params_dict_key_name(_fresh):
    """La clé part bien dans les query params (confirmé réel), jamais loguée --
    vérifie juste qu'elle est transmise sous le nom attendu par l'API."""
    _FakeAsyncClient._responses = [_kol_page([], has_more=False)]
    await cabalspy.list_wallets("base")
    assert _FakeAsyncClient._captured_params["api_key"] == "test-key-sentinel"


@pytest.mark.asyncio
async def test_list_wallets_http_error_on_first_page_returns_none(_fresh):
    _FakeAsyncClient._responses = [_FakeResponse(401, {})]
    assert await cabalspy.list_wallets("base") is None


@pytest.mark.asyncio
async def test_list_wallets_transport_error_returns_none(_fresh, monkeypatch):
    import httpx

    class _Boom(_FakeAsyncClient):
        async def get(self, url, params=None):
            raise httpx.TransportError("panne réseau")

    monkeypatch.setattr(cabalspy.httpx, "AsyncClient", _Boom)
    assert await cabalspy.list_wallets("base") is None


# ── lookup_wallet ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_wallet_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("CABALSPY_API_KEY", raising=False)
    assert await cabalspy.lookup_wallet("0xAAA") is None


@pytest.mark.asyncio
async def test_lookup_wallet_not_found(_fresh):
    _FakeAsyncClient._responses = [
        _FakeResponse(200, {"success": True, "data": {"found": False, "wallet_address": "0xAAA"}}),
    ]
    result = await cabalspy.lookup_wallet("0xAAA")
    assert result is not None
    assert result.found is False


@pytest.mark.asyncio
async def test_lookup_wallet_found_real_shape(_fresh):
    """Reproduit la réponse réelle observée (23/07) sur une adresse trouvée."""
    _FakeAsyncClient._responses = [
        _FakeResponse(
            200,
            {
                "success": True,
                "data": {
                    "found": True, "wallet_address": "0xAAA", "blockchain": "base", "type": "kol",
                    "name": "klarker", "twitter": "@klarker", "telegram": "",
                },
            },
        ),
    ]
    result = await cabalspy.lookup_wallet("0xAAA")
    assert result.found is True
    assert result.name == "klarker"
    assert result.blockchain == "base"


@pytest.mark.asyncio
async def test_lookup_wallet_empty_address_returns_none(_fresh):
    assert await cabalspy.lookup_wallet("") is None
    assert await cabalspy.lookup_wallet(None) is None
