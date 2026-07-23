"""FAST wallet-transfer providers (Alchemy + Moralis) -- 22/07, explicit
operator decision ("let's relieve Blockscout as much as possible") after
finding that wallet-scoring (`smart_money.py`) consumes 73.6% of the
Blockscout Pro credit budget (`token-transfers` endpoint, 30 real credits/
call, cf. docs/HANDOFF_BLOCKSCOUT.md) and that this budget regularly runs
dry, forcing a fallback to the free Blockscout endpoint -- slow/unstable on
the most active wallets (34s then a 500 error observed under real conditions
on a real wallet, before this fix).

Verified BY REAL AUTHENTICATED CALLS (22/07, never just the docs): Alchemy
`alchemy_getAssetTransfers` and Moralis `erc20/transfers` both respond in
under 4s on EXACTLY the wallet that had crashed the free Covalent/GoldRush
endpoint (candidate dropped separately, cf. docs/HANDOFF_WALLET_SCORING.md)
-- confirmed working on Base, response structure re-read live, not assumed
from an external example.

Cascade: Alchemy (primary, 120 CU/call, 30M CU/month free confirmed in the
official docs) -> Moralis (second resort if Alchemy is unavailable/failing,
50 CU/call, 40,000 CU/day free confirmed by a real screenshot of the
operator's dashboard) -> unavailable (``available=False``). The final
fallback to Blockscout (historical behavior, never removed) is handled by
THE CALLER (`smart_money.py`), never here -- this module knows nothing about
Blockscout, strictly separate responsibility.

Scoped to the "base" chain ONLY -- the only chain verified on both providers
to date (Ethereum and the other chains in `DEFAULT_SCAN_CHAINS()` keep using
Blockscout untouched).

Gate ``ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED`` (OFF by default). With no
key (``ALCHEMY_API_KEY``/``MORALIS_API_KEY`` absent) or gate OFF,
``available=False`` immediately -- the caller then falls back to Blockscout,
strictly unchanged behavior for any session that doesn't activate this gate.

Same dome doctrine as the rest of the project: 429 -- exponential backoff, 3
attempts max; timeout/5xx -- 1 retry after 5s then explicit degradation;
missing data is never replaced by a guess."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from aria_core.services.blockscout import TokenTransfer, TokenTransfersResult

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée indisponible"

ALCHEMY_BASE_URL = "https://base-mainnet.g.alchemy.com/v2"
MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"

# Alchemy caps at 1000 results/call (`pageKey` to continue) -- same total cap
# as Blockscout today (2000 transfers/10 pages, cf. smart_money.py) so the
# data volume consumed downstream (FIFO/Sortino/wash-trading -- all calibrated
# on this bound) never silently changes.
_ALCHEMY_PAGE_SIZE = "0x3e8"  # 1000 in hexadecimal (API's native format)
_MAX_RETRIES_429 = 3
_TIMEOUT_RETRY_DELAY_S = 5.0


def wallet_transfers_fast_provider_enabled() -> bool:
    return os.environ.get("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _alchemy_api_key() -> str:
    return os.environ.get("ALCHEMY_API_KEY", "").strip()


def _moralis_api_key() -> str:
    return os.environ.get("MORALIS_API_KEY", "").strip()


async def _post_with_dome(client: httpx.AsyncClient, url: str, *, json_body: dict) -> tuple[dict | None, str | None]:
    """Generic POST with the same error policy as the rest of the project."""
    attempt_429 = 0
    timeout_retried = False
    while True:
        try:
            resp = await client.post(url, json=json_body, timeout=30)
        except httpx.TimeoutException:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (timeout répété)"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        except httpx.HTTPError as exc:
            return None, f"{UNAVAILABLE} ({exc})"

        if resp.status_code == 429:
            attempt_429 += 1
            if attempt_429 > _MAX_RETRIES_429:
                return None, f"{UNAVAILABLE} (429 persistant)"
            await asyncio.sleep(2.0 ** attempt_429)
            continue
        if resp.status_code >= 500:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        if resp.status_code != 200:
            return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
        try:
            return resp.json(), None
        except ValueError:
            return None, f"{UNAVAILABLE} (réponse non-JSON)"


async def _get_with_dome(client: httpx.AsyncClient, url: str, *, params: dict) -> tuple[dict | None, str | None]:
    """Generic GET with the same error policy as the rest of the project."""
    attempt_429 = 0
    timeout_retried = False
    while True:
        try:
            resp = await client.get(url, params=params, timeout=30)
        except httpx.TimeoutException:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (timeout répété)"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        except httpx.HTTPError as exc:
            return None, f"{UNAVAILABLE} ({exc})"

        if resp.status_code == 429:
            attempt_429 += 1
            if attempt_429 > _MAX_RETRIES_429:
                return None, f"{UNAVAILABLE} (429 persistant)"
            await asyncio.sleep(2.0 ** attempt_429)
            continue
        if resp.status_code >= 500:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        if resp.status_code != 200:
            return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
        try:
            return resp.json(), None
        except ValueError:
            return None, f"{UNAVAILABLE} (réponse non-JSON)"


def _alchemy_transfer_to_token_transfer(item: dict) -> TokenTransfer | None:
    """Converts ONE Alchemy transfer (`alchemy_getAssetTransfers`) to the
    common `TokenTransfer` type -- same schema as Blockscout, so smart_money.py
    sees NO difference downstream (FIFO/Sortino/wash-trading unchanged).
    Fields verified via a real authenticated call on 22/07 (hash/from/to/
    rawContract.address/asset/value/metadata.blockTimestamp) -- not a guess
    from the docs."""
    tx_hash = item.get("hash")
    from_address = item.get("from")
    to_address = item.get("to")
    raw_contract = item.get("rawContract") or {}
    token_address = raw_contract.get("address")
    if not tx_hash or not from_address or not to_address:
        return None
    metadata = item.get("metadata") or {}
    value = item.get("value")
    return TokenTransfer(
        tx_hash=tx_hash,
        from_address=from_address,
        to_address=to_address,
        token_address=token_address,
        token_symbol=item.get("asset"),
        token_name=None,  # not provided by this Alchemy endpoint -- never fabricated
        amount=float(value) if isinstance(value, (int, float)) else None,
        timestamp=metadata.get("blockTimestamp"),
    )


def _moralis_transfer_to_token_transfer(item: dict) -> TokenTransfer | None:
    """Converts ONE Moralis transfer (`erc20/transfers`) to the common
    `TokenTransfer` type. Fields verified via a real authenticated call on
    22/07 (transaction_hash/from_address/to_address/address/token_symbol/
    token_name/value_decimal/block_timestamp)."""
    tx_hash = item.get("transaction_hash")
    from_address = item.get("from_address")
    to_address = item.get("to_address")
    if not tx_hash or not from_address or not to_address:
        return None
    value_decimal = item.get("value_decimal")
    amount = None
    if value_decimal is not None:
        try:
            amount = float(value_decimal)
        except (TypeError, ValueError):
            amount = None
    return TokenTransfer(
        tx_hash=tx_hash,
        from_address=from_address,
        to_address=to_address,
        token_address=item.get("address"),
        token_symbol=item.get("token_symbol"),
        token_name=item.get("token_name"),
        amount=amount,
        timestamp=item.get("block_timestamp"),
    )


async def _alchemy_get_token_transfers(address: str, *, limit: int, max_pages: int) -> TokenTransfersResult:
    key = _alchemy_api_key()
    if not key:
        return TokenTransfersResult(available=False, error=f"{UNAVAILABLE} (ALCHEMY_API_KEY absente)")

    url = f"{ALCHEMY_BASE_URL}/{key}"
    transfers: list[TokenTransfer] = []
    page_key: str | None = None
    truncated = False

    async with httpx.AsyncClient() as client:
        for page in range(max_pages):
            params: dict = {
                "toAddress": address,
                "category": ["erc20"],
                "maxCount": _ALCHEMY_PAGE_SIZE,
                "withMetadata": True,
            }
            if page_key:
                params["pageKey"] = page_key
            data, error = await _post_with_dome(
                client, url,
                json_body={
                    "jsonrpc": "2.0", "id": 1, "method": "alchemy_getAssetTransfers",
                    "params": [params],
                },
            )
            if error is not None:
                if page == 0:
                    return TokenTransfersResult(available=False, error=error)
                truncated = True
                break
            result = (data or {}).get("result") or {}
            items = result.get("transfers") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                converted = _alchemy_transfer_to_token_transfer(item)
                if converted is not None:
                    transfers.append(converted)
                if len(transfers) >= limit:
                    break
            if len(transfers) >= limit:
                truncated = bool(result.get("pageKey"))
                break
            page_key = result.get("pageKey")
            if not page_key:
                break
            if page == max_pages - 1:
                truncated = True

    return TokenTransfersResult(transfers=transfers[:limit], available=True, error=None, truncated=truncated)


async def _moralis_get_token_transfers(address: str, *, limit: int, max_pages: int) -> TokenTransfersResult:
    key = _moralis_api_key()
    if not key:
        return TokenTransfersResult(available=False, error=f"{UNAVAILABLE} (MORALIS_API_KEY absente)")

    url = f"{MORALIS_BASE_URL}/{address}/erc20/transfers"
    transfers: list[TokenTransfer] = []
    cursor: str | None = None
    truncated = False

    async with httpx.AsyncClient(headers={"X-API-Key": key}) as client:
        for page in range(max_pages):
            params: dict = {"chain": "base", "limit": min(100, limit)}
            if cursor:
                params["cursor"] = cursor
            data, error = await _get_with_dome(client, url, params=params)
            if error is not None:
                if page == 0:
                    return TokenTransfersResult(available=False, error=error)
                truncated = True
                break
            items = (data or {}).get("result") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                converted = _moralis_transfer_to_token_transfer(item)
                if converted is not None:
                    transfers.append(converted)
                if len(transfers) >= limit:
                    break
            if len(transfers) >= limit:
                truncated = bool((data or {}).get("cursor"))
                break
            cursor = (data or {}).get("cursor")
            if not cursor:
                break
            if page == max_pages - 1:
                truncated = True

    return TokenTransfersResult(transfers=transfers[:limit], available=True, error=None, truncated=truncated)


async def get_fast_token_transfers(
    address: str, chain: str, *, limit: int = 2000, max_pages: int = 10,
) -> TokenTransfersResult:
    """Public entry point -- Alchemy as primary, Moralis as second resort.
    Scoped to "base" only (cf. module docstring): any other chain immediately
    returns ``available=False``, the caller falls back to Blockscout as before
    this project, never a fabricated behavior for an unverified chain."""
    if chain != "base" or not wallet_transfers_fast_provider_enabled():
        return TokenTransfersResult(available=False, error=f"{UNAVAILABLE} (fournisseur rapide non applicable)")

    alchemy_result = await _alchemy_get_token_transfers(address, limit=limit, max_pages=max_pages)
    if alchemy_result.available:
        return alchemy_result

    logger.info("wallet_transfers_fast: Alchemy unavailable (%s) -- falling back to Moralis", alchemy_result.error)
    moralis_result = await _moralis_get_token_transfers(address, limit=limit, max_pages=max_pages)
    if moralis_result.available:
        return moralis_result

    logger.info("wallet_transfers_fast: Moralis unavailable (%s) -- falling back to Blockscout (caller)", moralis_result.error)
    return TokenTransfersResult(available=False, error=moralis_result.error)
