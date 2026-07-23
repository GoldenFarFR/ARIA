"""Blockscout Pro client (x402) — enriched token holders, a COMPLEMENT to the
free client (``services/blockscout.py::get_token_holders``, Free tier 5
req/s, never replaced). Built in response to the operator's request (21/07)
to build a mass extraction to construct in-house wallet/entity intelligence
(same objective family as Nansen/Arkham, already diligenced, never built for
lack of budget).

Endpoint verified in real conditions (21/07, 2 successful payments, real data
received): ``https://api.blockscout.com/{chain_id}/api/v2/tokens/{contract}/holders``,
$0.002/call. Response MUCH richer than the standard free tier: entity labels
(e.g. "Moonwell", "Morpho Markets Router", "UniswapV3Pool"), ``is_verified``/
``is_scam`` status, ``reputation`` score -- exactly the kind of signal
missing from ``smart_money.py`` (holder exclusion via raw ``is_contract``
heuristic alone today, never a real entity label).

Critical timeout (21/07, real bug found and fixed in x402_executor.py):
settling a payment on this endpoint REALLY takes ~28-45s (verified over
several real calls), well above the 12s default of ``fetch_paid_resource``
-- ``_HOLDERS_TIMEOUT_S`` below chosen with a generous margin above the
worst case observed, never the implicit default.

Pagination (21/07): each page returns ``next_page_params`` (cursor --
``value``/``address_hash``/``items_count``), verified in real conditions on
the FREE endpoint (same schema, zero verification cost) before coding
against it -- ``get_token_holders_x402_paginated`` uses it to go beyond the
first 50 holders (page 1), one payment PER PAGE.

Each call goes through ``x402_executor.fetch_paid_resource`` (SHARED weekly
cap ``x402_budget.py``, $5/week, ``/stop`` kill-switch, dedicated CDP wallet)
-- same dome as ``services/twitsh.py``/``services/cybercentry.py``. No extra
dedicated cap here: the shared cap, already fail-closed, bounds the worst
case (a paginated extraction that exceeds the budget stops cleanly, returns
what's already been paid for, never an exception)."""
from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_HOLDERS_URL = "https://api.blockscout.com/{chain_id}/api/v2/tokens/{contract}/holders"

# Verified live (21/07): Base only for now -- the only chain confirmed
# against a real successful payment. Add an entry here only after empirical
# verification, never guessed (14/07 norm).
_CHAIN_IDS: dict[str, str] = {"base": "8453"}

# Payment settlement observed between ~28s and ~45s over several real calls --
# generous margin above the measured worst case, never the 12s default of
# fetch_paid_resource (too short, see module docstring).
_HOLDERS_TIMEOUT_S = 75.0


def _parse_holders_page(body: bytes) -> tuple[list[dict], dict | None]:
    """Defensive parse -- ``([], None)`` on any unreadable/unexpected body,
    never an exception bubbling up (same dome as the rest of the external
    clients). Also returns ``next_page_params`` (raw cursor, as Blockscout
    returns it -- to be re-passed as a query string for the next page),
    ``None`` if it's the last page."""
    try:
        raw = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return [], None
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return [], None
    holders: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        tags = []
        metadata = address.get("metadata") if isinstance(address.get("metadata"), dict) else {}
        for tag in (metadata.get("tags") or []):
            if isinstance(tag, dict) and tag.get("name"):
                tags.append(str(tag["name"]))
        holders.append({
            "holder_address": address.get("hash") or "",
            "holder_name": address.get("name"),
            "is_contract": bool(address.get("is_contract")),
            "is_verified": bool(address.get("is_verified")),
            "is_scam": bool(address.get("is_scam")),
            "reputation": address.get("reputation"),
            "tags": tags,
            "value": item.get("value"),
        })
    next_page = raw.get("next_page_params") if isinstance(raw, dict) else None
    return holders, (next_page if isinstance(next_page, dict) and next_page else None)


def _parse_holders(body: bytes) -> list[dict]:
    """Compat -- single page, see ``get_token_holders_x402``."""
    holders, _next_page = _parse_holders_page(body)
    return holders


async def get_token_holders_x402(
    contract: str, *, chain: str = "base", token_symbol: str = "",
) -> list[dict]:
    """Enriched token holders via Blockscout Pro (x402, $0.002/call). Standard
    dome: never an exception bubbling up, empty list on any failure (budget
    exhausted, /stop active, insufficient balance, timeout, unreadable
    response). ``token_symbol`` (traceability, same pattern as #143):
    passed through as-is all the way to the x402_budget log."""
    addr = (contract or "").strip()
    chain_id = _CHAIN_IDS.get(chain)
    if not addr or not chain_id:
        return []

    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    url = _HOLDERS_URL.format(chain_id=chain_id, contract=addr)
    try:
        result = await x402_executor.fetch_paid_resource(
            url,
            resource="token-holders",
            provider="blockscout",
            balance_fn=usdc_balance_usd,
            pay_fn=build_x402_payment_header,
            contract=addr,
            token_symbol=token_symbol,
            timeout=_HOLDERS_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("blockscout_x402: get_token_holders_x402 failed (%s)", exc)
        return []
    if result.status != "ok" or not result.body:
        return []
    return _parse_holders(result.body)


# Anti-infinite-loop guard on pagination -- independent of ``target_count``
# (a token with millions of holders should never trigger dozens of payments
# on a single unexpected call).
_MAX_PAGES_PER_EXTRACTION = 10


async def get_token_holders_x402_paginated(
    contract: str, *, chain: str = "base", target_count: int = 50, token_symbol: str = "",
) -> list[dict]:
    """Like ``get_token_holders_x402`` but goes beyond the first page
    (50 holders) via the ``next_page_params`` cursor -- ONE PAYMENT PER PAGE
    ($0.002 each). Stops as soon as ``target_count`` is reached, there's no
    next page, or a page fails (budget exhausted, /stop active, network
    outage) -- then returns whatever's already been paid for and obtained,
    never an exception, never losing the work already done.

    Capped at ``_MAX_PAGES_PER_EXTRACTION`` pages regardless of
    ``target_count`` -- anti-infinite-loop protection, independent of the
    shared weekly budget (``x402_budget.py``, already fail-closed on its
    own side)."""
    addr = (contract or "").strip()
    chain_id = _CHAIN_IDS.get(chain)
    if not addr or not chain_id or target_count <= 0:
        return []

    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    all_holders: list[dict] = []
    next_page_params: dict | None = None
    pages_fetched = 0

    while len(all_holders) < target_count and pages_fetched < _MAX_PAGES_PER_EXTRACTION:
        url = _HOLDERS_URL.format(chain_id=chain_id, contract=addr)
        if next_page_params:
            url = f"{url}?{urlencode(next_page_params)}"
        try:
            result = await x402_executor.fetch_paid_resource(
                url,
                resource="token-holders",
                provider="blockscout",
                balance_fn=usdc_balance_usd,
                pay_fn=build_x402_payment_header,
                contract=addr,
                token_symbol=token_symbol,
                timeout=_HOLDERS_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "blockscout_x402: page %s failed for %s (%s)", pages_fetched + 1, addr, exc,
            )
            break
        if result.status != "ok" or not result.body:
            break
        page_holders, next_page_params = _parse_holders_page(result.body)
        if not page_holders:
            break
        all_holders.extend(page_holders)
        pages_fetched += 1
        if next_page_params is None:
            break

    return all_holders[:target_count]
