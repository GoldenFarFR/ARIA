"""CabalSpy client -- multi-chain labelled KOL/Smart Money/Whale wallets
(07/23, explicit operator decision: candidate source for wallet-scoring,
complementing `wallet_candidate_sourcing.py`).

ASSUMED policy change: `wallet_candidate_sourcing.py` had a "zero new
external dependency" doctrine (Nansen/Zerion already ruled out for this
reason). CabalSpy is adopted here on explicit operator decision after
real verification -- Free tier confirmed (0$/month, 10,000 credits, 5 req/s,
no credit card, `cabalspy.xyz/pricing`, operator screenshot 07/23).

Verified under real conditions (07/23, direct curl by the operator, key
never handled by this session):
- `GET /v1/wallets?blockchain=base&type=kol` -- 200 Base wallets with
  COMPLETE identity (name/twitter/telegram/image_url/copytrade_link) -- the
  real added value (wallet <-> identity bridge that neither Moni nor Zerion
  provide).
- `GET /v1/wallets?blockchain=base&type=smart` -- 38 ANONYMOUS wallets (all
  identity fields empty) -- probably overlaps with what `smart_money.py`
  already detects by behavior, for free. Little added value for this
  specific type, wired here anyway (in case a future use needs it)
  but never recommended as a priority source.
- `GET /v1/wallets/lookup?address=...` -- looks up an address across ALL
  chains in a single call, `found:false` if absent from their restricted
  database (~a few hundred known KOLs, NOT an exhaustive database).

Rate limit: NO official documented limit found beyond the subscribed tier
(5 req/s Free) -- cautious default throttle (1 req/s, 90% wide margin),
standard reactive backoff on 429/5xx. Authentication: `api_key` as a
query param (confirmed real) -- the alternative `Authorization: Bearer`
header mentioned in the docs has not been tested, not used here."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.cabalspy.xyz/v1"
_TIMEOUT_SECONDS = 10.0
_MIN_INTERVAL_SECONDS = 1.0
_MAX_PAGES = 20  # infinite-loop guard, well above 200/limit=100

_last_call_at = 0.0
_throttle_lock = asyncio.Lock()

VALID_BLOCKCHAINS = ("base", "bnb", "solana", "eth")
VALID_TYPES = ("kol", "smart", "whale")


@dataclass
class CabalSpyWallet:
    wallet_address: str
    blockchain: str
    type: str
    name: str = ""
    twitter: str = ""
    telegram: str = ""
    copytrade_link: str = ""


@dataclass
class CabalSpyLookupResult:
    found: bool
    wallet_address: str
    blockchain: str | None = None
    type: str | None = None
    name: str = ""
    twitter: str = ""
    telegram: str = ""


def is_cabalspy_configured() -> bool:
    return bool(os.environ.get("CABALSPY_API_KEY", "").strip())


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL_SECONDS - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = asyncio.get_event_loop().time()


async def _get(params: dict, *, path: str = "/wallets") -> dict | None:
    api_key = os.environ.get("CABALSPY_API_KEY", "").strip()
    if not api_key:
        return None

    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(f"{_BASE_URL}{path}", params={**params, "api_key": api_key})
    except httpx.TransportError as exc:
        logger.info("cabalspy: network failure (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("cabalspy: HTTP %s for %s", r.status_code, path)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- unreadable body, never let an exception propagate
        return None

    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    return payload


def _parse_wallet(item: dict, *, blockchain: str, wallet_type: str) -> CabalSpyWallet | None:
    address = item.get("wallet_address")
    if not address or not isinstance(address, str):
        return None
    return CabalSpyWallet(
        wallet_address=address,
        blockchain=blockchain,
        type=wallet_type,
        name=str(item.get("name") or ""),
        twitter=str(item.get("twitter") or ""),
        telegram=str(item.get("telegram") or ""),
        copytrade_link=str(item.get("copytrade_link") or ""),
    )


async def list_wallets(
    blockchain: str, *, wallet_type: str = "kol", page_limit: int = 100,
) -> list[CabalSpyWallet] | None:
    """Full PAGINATED list of labelled wallets for a given chain/type.
    ``None`` if the key is absent or any failure on the FIRST call
    (never a propagating exception); a failure on a LATER page
    returns what has already been collected (honest partial degradation,
    never losing everything for a late failure)."""
    if blockchain not in VALID_BLOCKCHAINS or wallet_type not in VALID_TYPES:
        return None

    wallets: list[CabalSpyWallet] = []
    cursor: str | None = None
    for page in range(_MAX_PAGES):
        params = {"blockchain": blockchain, "type": wallet_type, "limit": max(1, min(int(page_limit), 100))}
        if cursor:
            params["cursor"] = cursor

        payload = await _get(params)
        if payload is None:
            return wallets or None if page > 0 else None

        data = payload.get("data")
        if not isinstance(data, dict):
            break
        raw_wallets = data.get("wallets")
        if not isinstance(raw_wallets, list):
            break

        for item in raw_wallets:
            if isinstance(item, dict):
                parsed = _parse_wallet(item, blockchain=blockchain, wallet_type=wallet_type)
                if parsed:
                    wallets.append(parsed)

        pagination = data.get("pagination") or {}
        if not pagination.get("has_more"):
            break
        cursor = pagination.get("next_cursor")
        if not cursor:
            break

    return wallets


async def lookup_wallet(address: str) -> CabalSpyLookupResult | None:
    """Looks up an address across ALL chains in a single call. ``None`` if
    the key is absent or any network failure. Returns a result with
    ``found=False`` if the address is simply not in their database (never
    confused with a failure)."""
    addr = (address or "").strip()
    if not addr:
        return None

    payload = await _get({"address": addr}, path="/wallets/lookup")
    if payload is None:
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    found = bool(data.get("found"))
    if not found:
        return CabalSpyLookupResult(found=False, wallet_address=addr)

    return CabalSpyLookupResult(
        found=True,
        wallet_address=str(data.get("wallet_address") or addr),
        blockchain=data.get("blockchain"),
        type=data.get("type"),
        name=str(data.get("name") or ""),
        twitter=str(data.get("twitter") or ""),
        telegram=str(data.get("telegram") or ""),
    )
