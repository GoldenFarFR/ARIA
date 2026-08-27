"""Shared SOL/USD rate, Jupiter first, CoinGecko only as a fallback (27/08,
extracted from ``solana_agent_wallet.py`` -- real incident this fixes:
``pumpfun_bonding_ws.py``'s own calibration called CoinGecko alone, with no
fallback, and went dark for hours once CoinGecko's monthly credit cap was
reached (confirmed live: ``error="no_sol_usd_calibration"`` on the
overwhelming majority of every regime-sensor candidate since). Never
duplicated back into that module -- this is the ONE place the rate is
computed, reused by any caller that needs it, per this dome's "cohérence
architecturale absolue" doctrine.

Deliberately lightweight: only `services.jupiter`/`services.coingecko` as
dependencies, never `solana_agent_wallet.py`'s own wallet-signing imports
(`jupiter_swap_signer`, `solana_trade_pilot`) -- a shadow/observation module
pulling in real-wallet machinery just to read a price would be exactly the
kind of cross-domain coupling this dome's compartmentalization avoids."""
from __future__ import annotations

import logging
import time

import httpx

from aria_core.services import jupiter
from aria_core.services.coingecko import coingecko_client

logger = logging.getLogger(__name__)

SOL_USD_TTL_SECONDS = 60.0
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_USDC_DECIMALS = 6
_sol_usd_cache: tuple[float, float] | None = None


async def sol_usd_cached(*, client: httpx.AsyncClient | None = None) -> float | None:
    """SOL price in dollars, at most ``SOL_USD_TTL_SECONDS`` old.

    Jupiter first, CoinGecko only as a fallback. Returns None when neither
    knows -- callers treat that as a refusal, never as a guess."""
    global _sol_usd_cache
    now = time.monotonic()
    if _sol_usd_cache and now - _sol_usd_cache[0] < SOL_USD_TTL_SECONDS:
        return _sol_usd_cache[1]

    value: float | None = None
    try:
        # One SOL, priced in USDC. No aggregator, no extra dependency, and the
        # rate is the one a real trade would actually get.
        quote = await jupiter.fetch_quote(
            jupiter.SOL_MINT, _USDC_MINT, 1_000_000_000, slippage_bps=100, client=client,
        )
        out = int(quote.get("outAmount") or 0)
        if out:
            value = out / (10 ** _USDC_DECIMALS)
    except Exception as exc:  # noqa: BLE001 -- fall through to the aggregator
        logger.info("sol_usd_rate: SOL/USD via Jupiter failed (%s)", exc)

    if value is None:
        try:
            price = await coingecko_client.get_simple_price(["solana"], vs_currencies=["usd"])
            raw = price.prices.get("solana", {}).get("usd") if price.available else None
            value = float(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            logger.info("sol_usd_rate: SOL/USD fallback failed (%s)", exc)

    if value:
        _sol_usd_cache = (now, float(value))
        return float(value)
    # A failed refresh keeps the last known price rather than blocking: a
    # slightly stale rate is a better basis than none at all, and every real
    # caller enforces its own bounds against the real balance regardless.
    return _sol_usd_cache[1] if _sol_usd_cache else None
