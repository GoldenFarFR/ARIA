"""Tests du client DexScreener (lecture seule, #157) -- extrait de
acp_onchain_scan.py le 14/07 pour être réutilisable (triangulation
wallet-scoring). Aucun appel réseau réel, tout est mocké."""

import pytest

from aria_core.services import dexscreener as ds
from aria_core.services.dexscreener import (
    fetch_token_pairs,
    fetch_tokens_batch,
    has_any_pair,
    meta_by_slug,
    metas_trending,
    search_pairs,
    token_boosts_latest,
    token_boosts_top,
    token_profiles_latest,
    token_profiles_recent_updates,
)


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """26/07 (Item #123) -- ``_consecutive_failures``/``_circuit_open_until``
    are module-level, shared across every test in this file -- same trap as
    the OHLCV cascade's ``_reset_provider_circuit_breaker`` fixture."""
    ds._consecutive_failures = 0
    ds._circuit_open_until = 0.0
    yield
    ds._consecutive_failures = 0
    ds._circuit_open_until = 0.0


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
    assert pairs[0].chain_id == "base"


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


# ── Endpoints ajoutés après vérification de la spec OpenAPI officielle (#194) ────────

@pytest.mark.asyncio
async def test_token_profiles_recent_updates_parses_real_shape(monkeypatch):
    url = "https://api.dexscreener.com/token-profiles/recent-updates/v1"
    _patch_client(
        monkeypatch,
        {url: FakeResponse(200, [{"chainId": "base", "tokenAddress": "0xabc", "links": []}])},
    )

    listings = await token_profiles_recent_updates()

    assert len(listings) == 1
    assert listings[0].chain_id == "base"


@pytest.mark.asyncio
async def test_fetch_tokens_batch_parses_real_shape(monkeypatch):
    addrs = ["0x" + "a" * 40, "0x" + "b" * 40]
    url = f"https://api.dexscreener.com/tokens/v1/base/{','.join(addrs)}"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                [
                    {
                        "chainId": "base",
                        "pairAddress": "0xpool1",
                        "liquidity": {"usd": 10000.0},
                        "priceUsd": "1.0",
                        "baseToken": {"symbol": "AAA"},
                    },
                    {
                        "chainId": "base",
                        "pairAddress": "0xpool2",
                        "liquidity": {"usd": 20000.0},
                        "priceUsd": "2.0",
                        "baseToken": {"symbol": "BBB"},
                    },
                ],
            )
        },
    )

    pairs = await fetch_tokens_batch(addrs, chain="base")

    assert len(pairs) == 2
    assert {p.base_symbol for p in pairs} == {"AAA", "BBB"}


@pytest.mark.asyncio
async def test_fetch_tokens_batch_truncates_over_30_addresses(monkeypatch):
    addrs = [f"0x{i:040x}" for i in range(35)]
    expected_url = f"https://api.dexscreener.com/tokens/v1/base/{','.join(addrs[:30])}"
    _patch_client(monkeypatch, {expected_url: FakeResponse(200, [])})

    result = await fetch_tokens_batch(addrs, chain="base")

    assert result == []  # ne lève pas -- l'URL tronquée à 30 a bien été appelée


@pytest.mark.asyncio
async def test_fetch_tokens_batch_empty_list_short_circuits():
    assert await fetch_tokens_batch([], chain="base") == []


@pytest.mark.asyncio
async def test_fetch_tokens_batch_degrades_softly_on_error(monkeypatch):
    _patch_no_sleep(monkeypatch)
    url = "https://api.dexscreener.com/tokens/v1/base/0xtoken"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    assert await fetch_tokens_batch(["0xtoken"], chain="base") == []


@pytest.mark.asyncio
async def test_metas_trending_parses_real_shape(monkeypatch):
    url = "https://api.dexscreener.com/metas/trending/v1"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                [
                    {
                        "description": "pspspspspsp",
                        "name": "Cat",
                        "slug": "cat",
                        "marketCap": 376197136,
                        "liquidity": 39334441.94,
                        "volume": 90687696.75,
                        "tokenCount": 60,
                        "marketCapChange": {"m5": -0.03, "h1": 2.9, "h6": 1.76, "h24": -17.0},
                    }
                ],
            )
        },
    )

    metas = await metas_trending()

    assert len(metas) == 1
    assert metas[0].slug == "cat"
    assert metas[0].token_count == 60
    assert metas[0].market_cap_change_24h == -17.0


@pytest.mark.asyncio
async def test_meta_by_slug_parses_real_shape(monkeypatch):
    url = "https://api.dexscreener.com/metas/meta/v1/ai"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "description": "Artificial intelligence and agents",
                    "name": "AI",
                    "slug": "ai",
                    "marketCap": 100000,
                    "liquidity": 100000,
                    "volume": 100000,
                    "tokenCount": 5,
                    "marketCapChange": {"m5": 0.0, "h1": 0.0, "h6": 0.0, "h24": 0.0},
                    "pairs": [
                        {
                            "chainId": "base",
                            "pairAddress": "0xpool",
                            "liquidity": {"usd": 5000.0},
                            "priceUsd": "0.5",
                            "baseToken": {"symbol": "AIT"},
                        }
                    ],
                },
            )
        },
    )

    meta, pairs = await meta_by_slug("ai")

    assert meta.name == "AI"
    assert len(pairs) == 1
    assert pairs[0].base_symbol == "AIT"


@pytest.mark.asyncio
async def test_meta_by_slug_none_on_error(monkeypatch):
    _patch_no_sleep(monkeypatch)
    url = "https://api.dexscreener.com/metas/meta/v1/unknown"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    meta, pairs = await meta_by_slug("unknown")

    assert meta is None
    assert pairs == []


# ── priceChange multi-fenêtres + synthèse dégradée (16/07, cascade OHLCV #194) ──

@pytest.mark.asyncio
async def test_fetch_token_pairs_parses_all_price_change_windows(monkeypatch):
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xtoken"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                [
                    {
                        "pairAddress": "0xpool",
                        "priceUsd": "2.0",
                        "priceChange": {"m5": 0.1, "h1": 1.0, "h6": 5.0, "h24": 10.0},
                        "baseToken": {"symbol": "TOK"},
                    }
                ],
            )
        },
    )

    pairs = await fetch_token_pairs("0xtoken", chain="base")

    assert pairs[0].price_change_m5 == 0.1
    assert pairs[0].price_change_h1 == 1.0
    assert pairs[0].price_change_h6 == 5.0
    assert pairs[0].price_change_24h == 10.0


def test_synthesize_candles_from_pair_builds_five_points():
    from aria_core.services.dexscreener import PairSnapshot, synthesize_candles_from_pair

    pair = PairSnapshot(
        price_usd=2.0, price_change_24h=10.0, price_change_h6=5.0, price_change_h1=1.0, price_change_m5=0.1,
    )
    candles = synthesize_candles_from_pair(pair)

    assert len(candles) == 5
    assert candles[-1].close == 2.0  # dernier point = prix courant
    assert candles[0].ts < candles[-1].ts  # ordre chronologique croissant


def test_synthesize_candles_from_pair_empty_when_no_price():
    from aria_core.services.dexscreener import PairSnapshot, synthesize_candles_from_pair

    assert synthesize_candles_from_pair(PairSnapshot(price_usd=0.0)) == []
    assert synthesize_candles_from_pair(None) == []


def test_synthesize_candles_from_pair_degrades_on_impossible_pct_change():
    """Une variation de -100% (prix passé à zéro/négatif implicite) est
    ignorée plutôt que de produire un point de prix négatif/infini -- jamais
    une bougie inventée non plausible."""
    from aria_core.services.dexscreener import PairSnapshot, synthesize_candles_from_pair

    pair = PairSnapshot(price_usd=2.0, price_change_24h=-100.0, price_change_h6=0.0, price_change_h1=0.0, price_change_m5=0.0)
    candles = synthesize_candles_from_pair(pair)

    assert all(c.close > 0 for c in candles)
    assert len(candles) == 4  # le point h24 impossible est exclu, les 4 autres restent


def test_token_url_builds_web_link_from_chain_and_contract():
    from aria_core.services.dexscreener import token_url

    assert token_url("0xABCDEF", chain="base") == "https://dexscreener.com/base/0xabcdef"
    assert token_url("SomeMixedCaseAddr", chain="Solana") == "https://dexscreener.com/solana/somemixedcaseaddr"


def test_token_url_defaults_to_base_when_chain_missing():
    from aria_core.services.dexscreener import token_url

    assert token_url("0xabc", chain="") == "https://dexscreener.com/base/0xabc"
    assert token_url("0xabc") == "https://dexscreener.com/base/0xabc"


# ── reactive circuit breaker (26/07, Item #123 -- full-pipeline audit: DexScreener
# is the most-called external service of the pipeline, yet had no mechanism to
# stop hammering a confirmed rate-limit/outage) ─────────────────────────────────

@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold_consecutive_failures(monkeypatch):
    """_CIRCUIT_FAIL_THRESHOLD distinct candidates that each exhaust their own
    429 retry loop must open the circuit breaker -- the NEXT candidate must
    never touch the network at all."""
    assert ds._CIRCUIT_FAIL_THRESHOLD == 3
    urls = [f"https://api.dexscreener.com/token-pairs/v1/base/0x{i}" for i in range(3)]
    responses = {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)] for url in urls}
    _patch_client(monkeypatch, responses)
    _patch_no_sleep(monkeypatch)

    for i in range(3):
        result = await fetch_token_pairs(f"0x{i}", chain="base")
        assert result == []  # graceful degradation, never an exception

    assert ds._in_cooldown() is True

    # a 4th call must never reach the network -- this URL is absent from the
    # responses dict, so a real call would raise an uncaught KeyError.
    result = await fetch_token_pairs("0xnever-called", chain="base")
    assert result == []


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_success(monkeypatch):
    """2 failures + 1 success + 2 failures must NEVER open the breaker --
    threshold is CONSECUTIVE failures, a success resets the counter."""
    responses = {
        "https://api.dexscreener.com/token-pairs/v1/base/0xfail-1": [FakeResponse(429)] * 3,
        "https://api.dexscreener.com/token-pairs/v1/base/0xfail-2": [FakeResponse(429)] * 3,
        "https://api.dexscreener.com/token-pairs/v1/base/0xok": FakeResponse(200, []),
        "https://api.dexscreener.com/token-pairs/v1/base/0xfail-3": [FakeResponse(429)] * 3,
        "https://api.dexscreener.com/token-pairs/v1/base/0xfail-4": [FakeResponse(429)] * 3,
    }
    _patch_client(monkeypatch, responses)
    _patch_no_sleep(monkeypatch)

    await fetch_token_pairs("0xfail-1", chain="base")
    await fetch_token_pairs("0xfail-2", chain="base")
    await fetch_token_pairs("0xok", chain="base")
    await fetch_token_pairs("0xfail-3", chain="base")
    await fetch_token_pairs("0xfail-4", chain="base")

    assert ds._in_cooldown() is False


@pytest.mark.asyncio
async def test_circuit_breaker_expires_after_cooldown(monkeypatch):
    """The pause isn't permanent -- once _CIRCUIT_COOLDOWN_SECONDS elapse,
    DexScreener is retried normally."""
    url = "https://api.dexscreener.com/token-pairs/v1/base/0xretry"
    _patch_client(monkeypatch, {url: FakeResponse(200, [])})
    _patch_no_sleep(monkeypatch)

    import time as time_module

    ds._circuit_open_until = time_module.monotonic() + 1000.0
    assert ds._in_cooldown() is True

    ds._circuit_open_until -= 1001.0
    assert ds._in_cooldown() is False

    result = await fetch_token_pairs("0xretry", chain="base")
    assert result == []  # empty payload parses to an empty list, network WAS called
