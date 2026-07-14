"""Tests du client DexScreener (lecture seule, #157) -- extrait de
acp_onchain_scan.py le 14/07 pour être réutilisable (triangulation
wallet-scoring). Aucun appel réseau réel, tout est mocké."""

import pytest

from aria_core.services.dexscreener import fetch_token_pairs, has_any_pair


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    def __init__(self, responses: dict):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.dexscreener.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.dexscreener.asyncio.sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_fetch_token_pairs_parses_real_shape(monkeypatch):
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                [
                    {
                        "pairAddress": "0xpool",
                        "dexId": "uniswap",
                        "liquidity": {"usd": 50000.0},
                        "volume": {"h24": 1200.0},
                        "priceUsd": "1.5",
                        "priceChange": {"h24": 3.2},
                        "txns": {"h24": {"buys": 10, "sells": 4}},
                        "pairCreatedAt": 1700000000,
                        "baseToken": {"symbol": "TOK"},
                        "quoteToken": {"symbol": "WETH"},
                    }
                ],
            )
        },
    )

    pairs = await fetch_token_pairs("0xtoken", chain="base")

    assert len(pairs) == 1
    assert pairs[0].pair_address == "0xpool"
    assert pairs[0].liquidity_usd == 50000.0
    assert pairs[0].price_usd == 1.5


@pytest.mark.asyncio
async def test_fetch_token_pairs_empty_list_when_none_found(monkeypatch):
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(monkeypatch, {url: FakeResponse(200, [])})

    pairs = await fetch_token_pairs("0xtoken", chain="base")

    assert pairs == []


@pytest.mark.asyncio
async def test_fetch_token_pairs_degrades_softly_on_error(monkeypatch):
    _patch_no_sleep(monkeypatch)
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    pairs = await fetch_token_pairs("0xtoken", chain="base")

    assert pairs == []  # jamais une exception qui remonte


@pytest.mark.asyncio
async def test_has_any_pair_true_when_pairs_exist(monkeypatch):
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(monkeypatch, {url: FakeResponse(200, [{"pairAddress": "0xpool"}])})

    result = await has_any_pair("0xtoken", chain="base")

    assert result is True


@pytest.mark.asyncio
async def test_has_any_pair_false_when_empty(monkeypatch):
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(monkeypatch, {url: FakeResponse(200, [])})

    result = await has_any_pair("0xtoken", chain="base")

    assert result is False


@pytest.mark.asyncio
async def test_has_any_pair_none_when_call_fails_never_confused_with_false(monkeypatch):
    # #157, 14/07 : jamais confondre "aucune paire" (False, vérifié) avec
    # "on n'a pas pu vérifier" (None, erreur réseau) -- triangulation honnête.
    _patch_no_sleep(monkeypatch)
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    result = await has_any_pair("0xtoken", chain="base")

    assert result is None


@pytest.mark.asyncio
async def test_429_retries_then_succeeds(monkeypatch):
    _patch_no_sleep(monkeypatch)
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(
        monkeypatch,
        {url: [FakeResponse(429), FakeResponse(200, [{"pairAddress": "0xpool"}])]},
    )

    result = await has_any_pair("0xtoken", chain="base")

    assert result is True
