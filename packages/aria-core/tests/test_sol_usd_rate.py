"""services/sol_usd_rate.py (27/08) -- extracted from solana_agent_wallet.py
so pumpfun_bonding_ws.py's regime sensor could reuse the same
Jupiter-first/CoinGecko-fallback logic without importing real-wallet
machinery. Real incident this exists to prevent: the sensor used to call
CoinGecko alone, went dark for hours once CoinGecko's monthly credit cap
was reached."""
from __future__ import annotations

import pytest

from aria_core.services import sol_usd_rate as m


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(m, "_sol_usd_cache", None)


@pytest.mark.asyncio
async def test_uses_jupiter_first_never_touching_coingecko(monkeypatch):
    async def _fake_fetch_quote(*a, **k):
        return {"outAmount": str(150_000_000)}  # 150.0 USDC (6 decimals) for 1 SOL

    async def _coingecko_must_not_be_called(*a, **k):
        raise AssertionError("CoinGecko must not be called when Jupiter succeeds")

    monkeypatch.setattr(m.jupiter, "fetch_quote", _fake_fetch_quote)
    monkeypatch.setattr(m.coingecko_client, "get_simple_price", _coingecko_must_not_be_called)

    price = await m.sol_usd_cached()
    assert price == 150.0


@pytest.mark.asyncio
async def test_falls_back_to_coingecko_when_jupiter_fails(monkeypatch):
    class _Result:
        available = True
        prices = {"solana": {"usd": 148.2}}

    async def _broken_jupiter(*a, **k):
        raise RuntimeError("jupiter unavailable")

    async def _fake_coingecko(*a, **k):
        return _Result()

    monkeypatch.setattr(m.jupiter, "fetch_quote", _broken_jupiter)
    monkeypatch.setattr(m.coingecko_client, "get_simple_price", _fake_coingecko)

    price = await m.sol_usd_cached()
    assert price == 148.2


@pytest.mark.asyncio
async def test_returns_none_when_both_sources_fail_and_no_prior_cache(monkeypatch):
    async def _broken_jupiter(*a, **k):
        raise RuntimeError("jupiter unavailable")

    class _Unavailable:
        available = False
        prices = {}

    async def _broken_coingecko(*a, **k):
        return _Unavailable()

    monkeypatch.setattr(m.jupiter, "fetch_quote", _broken_jupiter)
    monkeypatch.setattr(m.coingecko_client, "get_simple_price", _broken_coingecko)

    assert await m.sol_usd_cached() is None


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_last_known_price(monkeypatch):
    async def _fake_fetch_quote(*a, **k):
        return {"outAmount": str(150_000_000)}

    monkeypatch.setattr(m.jupiter, "fetch_quote", _fake_fetch_quote)
    first = await m.sol_usd_cached()
    assert first == 150.0

    # Force the cache to look stale, then fail both sources on the refresh.
    monkeypatch.setattr(m, "_sol_usd_cache", (0.0, 150.0))

    async def _broken_jupiter(*a, **k):
        raise RuntimeError("jupiter unavailable")

    class _Unavailable:
        available = False
        prices = {}

    async def _broken_coingecko(*a, **k):
        return _Unavailable()

    monkeypatch.setattr(m.jupiter, "fetch_quote", _broken_jupiter)
    monkeypatch.setattr(m.coingecko_client, "get_simple_price", _broken_coingecko)

    assert await m.sol_usd_cached() == 150.0


@pytest.mark.asyncio
async def test_within_ttl_never_recalls_either_source(monkeypatch):
    calls = []

    async def _fake_fetch_quote(*a, **k):
        calls.append(1)
        return {"outAmount": str(150_000_000)}

    monkeypatch.setattr(m.jupiter, "fetch_quote", _fake_fetch_quote)
    await m.sol_usd_cached()
    await m.sol_usd_cached()
    assert len(calls) == 1
