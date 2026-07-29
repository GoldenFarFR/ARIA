"""Read-only Honeypot.is client -- TEMPORARY second security opinion for
Base/Ethereum tokens while GoPlus's monthly CU quota is exhausted (Item #212
follow-up, 29/07: operator flagged GoPlus won't renew for ~15 days -- far
longer than the watchlist alone can bridge without a real second source).

Verified live (real curl, not a guess) before writing a single line:
- Base (chainID 8453) AND Ethereum (chainID 1) both answer correctly (tested
  on WETH on each chain -- coherent `chain.name`/`honeypotResult`/
  `simulationResult` fields).
- No API key required today ("API Key system is not yet implemented" per the
  official docs, docs.honeypot.is/ishoneypot).
- Real rate limit observed via `x-ratelimit-*` response headers: 50 requests
  per a SHORT rolling window (a few seconds, confirmed by watching `remaining`
  reset mid-burst) -- far more generous than GoPlus, no aggressive throttle
  needed here.
- Legitimacy signals: referenced by QuickNode's builder guide, RugDoc, and
  several independent GitHub integrations (kukapay/honeypot-detector-mcp
  explicitly lists Base as a supported chain).

Known gap vs GoPlus (documented honestly, never silently papered over): no
field equivalent to GoPlus's `owner_change_balance` (the 22/07 hard veto added
after the CNX incident -- the owner's power to directly rewrite a wallet's
balance). Honeypot.is exposes honeypot/taxes/proxy/open-source, not that
specific signal -- during this fallback window, that ONE check is genuinely
uncovered, a real (small) reduction in coverage, not a contradiction.

Doctrine (same as rugcheck.py for Solana): a TEMPORARY second opinion, used
ONLY when GoPlus itself reports unavailable (`momentum_entry.
run_goplus_watchlist_cycle`) -- never a permanent replacement. GoPlus stays
the primary/reference source and takes back over automatically once its
quota renews (nothing to revert manually -- the fallback simply stops firing
once GoPlus starts answering ``available=True`` again)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.honeypot.is/v2"
UNAVAILABLE = "donnée Honeypot.is indisponible"

# 29/07 -- real limit observed live via a genuine burst test (not a guess):
# 150 requests at ~9.5 req/s sustained over 15.8s -> 0 failures; 150 requests
# at ~22.7 req/s -> 59x HTTP 429. Calibrated at ~5 req/s (90% margin doctrine,
# same as the rest of this codebase) -- comfortably under the confirmed-safe
# rate. Promoted from a pure fallback to the PRIMARY honeypot source (Item
# #212 follow-up, 29/07, explicit operator decision: GoPlus now only serves
# as a last resort when Honeypot.is itself fails), so `run_goplus_watchlist_
# cycle` processes a whole batch of candidates per heartbeat passage at this
# rate rather than a single one.
_MIN_INTERVAL_S = 0.2
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()

# DexScreener chain name -> Honeypot.is chainID. Only the two chains this
# fallback actually needs to cover (Solana keeps its own RugCheck fallback).
_CHAIN_ID_BY_DEXSCREENER_CHAIN = {"base": "8453", "ethereum": "1"}


@dataclass
class HoneypotIsResult:
    """TEMPORARY second opinion result. Fields stay None when Honeypot.is
    doesn't have the data -- never an invented True/False (same fail-closed
    doctrine as the rest of this pipeline)."""

    address: str
    available: bool = False
    error: str | None = None
    is_honeypot: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None

    @property
    def confirmed_clean(self) -> bool:
        """True only if Honeypot.is responded AND positively confirmed
        ``is_honeypot=False`` -- unavailable/unknown never counts as clean."""
        return self.available and self.is_honeypot is False


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        elapsed = time.monotonic() - _last_call_at
        if elapsed < _MIN_INTERVAL_S:
            await asyncio.sleep(_MIN_INTERVAL_S - elapsed)
        _last_call_at = time.monotonic()


async def _get_json(url: str, *, params: dict) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same dome policy as the rest of
    this codebase (rugcheck.py/dexscreener.py)."""
    await _throttle()
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("honeypot_is: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("honeypot_is: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("honeypot_is: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("honeypot_is: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def check_token(address: str, *, chain: str) -> HoneypotIsResult:
    """TEMPORARY second opinion for a Base/Ethereum contract -- only called
    from `momentum_entry.run_goplus_watchlist_cycle` when GoPlus itself
    reports unavailable. ``chain`` is the DexScreener chain name (``base``/
    ``ethereum``), translated to Honeypot.is's numeric ``chainID`` here."""
    addr = (address or "").strip()
    if not addr:
        return HoneypotIsResult(address=addr, available=False, error="adresse vide")

    chain_id = _CHAIN_ID_BY_DEXSCREENER_CHAIN.get((chain or "").strip().lower())
    if not chain_id:
        return HoneypotIsResult(address=addr, available=False, error=f"chaîne {chain} non couverte par Honeypot.is")

    data, error = await _get_json(f"{BASE_URL}/IsHoneypot", params={"address": addr, "chainID": chain_id})
    if error is not None:
        return HoneypotIsResult(address=addr, available=False, error=error)
    if not isinstance(data, dict):
        return HoneypotIsResult(address=addr, available=False, error=UNAVAILABLE)

    honeypot_result = data.get("honeypotResult")
    is_honeypot = (
        bool(honeypot_result.get("isHoneypot"))
        if isinstance(honeypot_result, dict) and isinstance(honeypot_result.get("isHoneypot"), bool)
        else None
    )

    simulation = data.get("simulationResult")
    buy_tax = None
    sell_tax = None
    if isinstance(simulation, dict):
        raw_buy = simulation.get("buyTax")
        raw_sell = simulation.get("sellTax")
        if isinstance(raw_buy, (int, float)):
            buy_tax = float(raw_buy) / 100.0
        if isinstance(raw_sell, (int, float)):
            sell_tax = float(raw_sell) / 100.0

    return HoneypotIsResult(
        address=addr, available=True, error=None, is_honeypot=is_honeypot, buy_tax=buy_tax, sell_tax=sell_tax,
    )
