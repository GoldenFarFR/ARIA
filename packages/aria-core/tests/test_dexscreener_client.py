"""Tests du client DexScreener (lecture seule, #157) -- extrait de
acp_onchain_scan.py le 14/07 pour être réutilisable (triangulation
wallet-scoring). Aucun appel réseau réel, tout est mocké."""

import pytest

from aria_core.services.dexscreener import (
    fetch_token_pairs,
    has_any_pair,
    search_pairs,
    token_boosts_latest,
    token_boosts_top,
    token_profiles_latest,
)


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


# ── Nouveaux endpoints multi-chaînes (#194, sourcing momentum) ──────────────────────

@pytest.mark.asyncio
async def test_search_pairs_parses_real_shape(monkeypatch):
    url = "https://api.dexscreener.com/latest/dex/search?q=PAMPU"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "schemaVersion": "1.0.0",
                    "pairs": [
                        {
                            "chainId": "base",
                            "pairAddress": "0xpool",
                            "dexId": "uniswap",
                            "liquidity": {"usd": 168632.44},
                            "volume": {"h24": 758493.78},
                            "priceUsd": "0.001741",
                            "priceChange": {"h24": 71.54},
                            "txns": {"h24": {"buys": 2617, "sells": 1339}},
                            "pairCreatedAt": 1783663821000,
                            "baseToken": {"symbol": "PAMPU"},
                            "quoteToken": {"symbol": "ETH"},
                        }
                    ],
                },
            )
        },
    )

    pairs = await search_pairs("PAMPU")

    assert len(pairs) == 1
    assert pairs[0].base_symbol == "PAMPU"
    assert pairs[0].liquidity_usd == 168632.44


@pytest.mark.asyncio
async def test_search_pairs_empty_on_no_results(monkeypatch):
    url = "https://api.dexscreener.com/latest/dex/search?q=zzz"
    _patch_client(monkeypatch, {url: FakeResponse(200, {"schemaVersion": "1.0.0", "pairs": []})})

    assert await search_pairs("zzz") == []


@pytest.mark.asyncio
async def test_search_pairs_degrades_softly_on_error(monkeypatch):
    _patch_no_sleep(monkeypatch)
    url = "https://api.dexscreener.com/latest/dex/search?q=zzz"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    assert await search_pairs("zzz") == []


@pytest.mark.asyncio
async def test_token_boosts_top_parses_real_shape(monkeypatch):
    url = "https://api.dexscreener.com/token-boosts/top/v1"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                [
                    {
                        "chainId": "solana",
                        "tokenAddress": "4LjLUvg56sBrzstX6Cw9YYr3k31PdZGQg5u2mCM4pump",
                        "description": "THE BULLDOG WILL RULE THE SOLANA CHAIN",
                        "links": [
                            {"url": "https://the-bulldog.vercel.app/"},
                            {"type": "telegram", "url": "https://t.me/THEBULLDOGSOL"},
                        ],
                        "totalAmount": 500,
                    }
                ],
            )
        },
    )

    listings = await token_boosts_top()

    assert len(listings) == 1
    assert listings[0].chain_id == "solana"
    assert listings[0].token_address == "4LjLUvg56sBrzstX6Cw9YYr3k31PdZGQg5u2mCM4pump"
    assert len(listings[0].links) == 2
    assert listings[0].links[1]["label"] == "Telegram"


@pytest.mark.asyncio
async def test_token_boosts_latest_empty_on_error(monkeypatch):
    _patch_no_sleep(monkeypatch)
    url = "https://api.dexscreener.com/token-boosts/latest/v1"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    assert await token_boosts_latest() == []


@pytest.mark.asyncio
async def test_token_profiles_latest_filters_invalid_link_schemes(monkeypatch):
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                [
                    {
                        "chainId": "solana",
                        "tokenAddress": "5u46U15jtwfLSXVxum23gM1jgw7DUBivphQmFnXDpump",
                        "description": "desc",
                        "links": [
                            {"type": "twitter", "url": "https://x.com/handle"},
                            {"type": "sketchy", "url": "javascript:alert(1)"},
                        ],
                    }
                ],
            )
        },
    )

    listings = await token_profiles_latest()

    assert len(listings) == 1
    assert len(listings[0].links) == 1  # le lien javascript: est exclu
    assert listings[0].links[0]["url"] == "https://x.com/handle"
