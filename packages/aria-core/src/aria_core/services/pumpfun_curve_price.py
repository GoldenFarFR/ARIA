"""Spot price of a bonding-curve token, read from the CHAIN (22/08).

**Why the chain rather than an aggregator.** The price of a pump.fun token is
not an opinion: it is `virtual_sol_reserves / virtual_token_reserves`, held in
the curve account itself. An aggregator reports the last price it happened to
observe, which lags and can be stale exactly when a curve is collapsing --
which is when an exit rule needs it most.

**Why it matters here, measured.** A price feed that competed with Jupiter's
own rate limit went blind for 3.5 minutes on 22/08; two real positions closed
at -81% and -79.7% against a stop set at -5%. This module removes the exit
path's dependency on any third party: `getMultipleAccounts` prices up to 100
positions in ONE call, costs ONE credit, and returned in 29ms measured against
the real endpoint.

**The 10% gap against DexScreener is expected, not an error.** This is the
instantaneous curve price; DexScreener reports the last executed trade, which
already carries that trade's own impact. For a stop, the curve price is the
truthful one.

Read-only, no key, no signing. Graduated tokens are deliberately NOT priced
here -- once `complete` is set the curve no longer holds the price, and
`pumpswap_ws` owns migrated pools.
"""
from __future__ import annotations

import base64
import logging

import httpx

from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# `getMultipleAccounts` caps at 100 accounts and bills ONE credit regardless of
# how many are asked for -- so batching is free and not batching is waste.
MAX_ACCOUNTS_PER_CALL = 100

# Offsets in pump.fun's bonding-curve account. Fixed by the deployed program,
# so they cannot drift under us; `complete` is what says the curve is done.
_OFF_VIRTUAL_TOKEN = (8, 16)
_OFF_VIRTUAL_SOL = (16, 24)
_OFF_COMPLETE = 48

_TOKEN_DECIMALS = 6
_LAMPORTS = 1_000_000_000


def curve_address(mint: str) -> str:
    """The bonding-curve account for `mint`, derived not looked up."""
    program = Pubkey.from_string(PUMPFUN_PROGRAM_ID)
    pda, _ = Pubkey.find_program_address(
        [b"bonding-curve", bytes(Pubkey.from_string(mint))], program
    )
    return str(pda)


def price_from_curve_data(raw: bytes, *, sol_usd: float) -> float | None:
    """Spot price in dollars, or None if this curve cannot price anything.

    None covers three real cases, all of which must refuse rather than guess:
    an account too short to decode, a graduated curve (`complete`), and empty
    token reserves.
    """
    if len(raw) <= _OFF_COMPLETE:
        return None
    if raw[_OFF_COMPLETE]:
        # Graduated: the curve is frozen and no longer reflects the market.
        return None
    virtual_tokens = int.from_bytes(raw[slice(*_OFF_VIRTUAL_TOKEN)], "little") / (
        10 ** _TOKEN_DECIMALS
    )
    virtual_sol = int.from_bytes(raw[slice(*_OFF_VIRTUAL_SOL)], "little") / _LAMPORTS
    if virtual_tokens <= 0 or sol_usd <= 0:
        return None
    return (virtual_sol / virtual_tokens) * sol_usd


async def fetch_prices(
    mints: list[str],
    *,
    sol_usd: float,
    rpc_http_url: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, float]:
    """Spot prices for `mints`, straight from their curve accounts.

    A mint missing from the result means "not priceable from a curve right
    now" -- graduated, unreadable, or empty. The caller must treat that as
    unknown and fall back, never as zero.
    """
    if not mints or sol_usd <= 0:
        return {}

    owns = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    out: dict[str, float] = {}
    try:
        for start in range(0, len(mints), MAX_ACCOUNTS_PER_CALL):
            batch = mints[start : start + MAX_ACCOUNTS_PER_CALL]
            try:
                addresses = [curve_address(m) for m in batch]
            except Exception as exc:  # noqa: BLE001 -- a malformed mint is not fatal
                logger.info("pumpfun_curve_price: cannot derive a curve (%s)", exc)
                continue
            try:
                resp = await client.post(
                    rpc_http_url,
                    json={
                        "jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                        "params": [addresses, {"encoding": "base64"}],
                    },
                )
                resp.raise_for_status()
                values = ((resp.json() or {}).get("result") or {}).get("value") or []
            except Exception as exc:  # noqa: BLE001 -- one batch failing is not all
                if "429" in str(exc):
                    # Tell the shared resolver, so EVERY caller falls back --
                    # not just this one.
                    from aria_core.services import pumpswap_ws

                    pumpswap_ws.note_rpc_http_exhausted()
                logger.info("pumpfun_curve_price: batch failed (%s)", exc)
                continue

            for mint, value in zip(batch, values):
                if not value:
                    continue
                try:
                    raw = base64.b64decode(value["data"][0])
                except Exception:  # noqa: BLE001
                    continue
                price = price_from_curve_data(raw, sol_usd=sol_usd)
                if price:
                    out[mint] = price
        return out
    finally:
        if owns:
            await client.aclose()
