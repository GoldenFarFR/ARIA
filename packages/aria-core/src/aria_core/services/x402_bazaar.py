"""x402 Bazaar read-only client (CDP Coinbase, discovery layer) -- discovers
available x402 services, with a real trend signal (30d volume). Answers the
operator question "isn't there a top-trending list of the best x402 tools?"
(19/07) -- limiting ourselves to one-off manual diligence (Cybercentry,
#199) was indeed a limitation: this official Coinbase registry enables
dynamic discovery.

Verified live (19/07, BEFORE writing this module -- norm #157) against the
official doc (docs.cdp.coinbase.com/x402/bazaar) AND a real call:
  - ``GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search`` --
    read-only, NO CDP key required (confirmed HTTP 200 with no
    authentication). Combines text/semantic search + quality ranking (30d
    buyer reach, 30d transaction volume, recency, metadata quality, Coinbase
    curation) -- recomputed every 6h.
  - The ``quality.l30DaysTotalCalls``/``l30DaysUniquePayers`` field IS
    genuinely exposed in the real response (contrary to what the doc
    suggests, which says "no per-service breakdown, only final ordering")
    -- confirmed on 5 real services (e.g. Tavily Advanced Search: 48319
    calls / 374 unique payers over 30d). This is the most direct and
    objective TREND signal available -- ``discover_trending()`` explicitly
    sorts on it rather than trusting the API's raw order (which mixes in
    textual relevance, not very meaningful if ``query`` is empty).
  - ``GET .../discovery/resources`` (paginated catalog, newest-first) and
    ``GET .../discovery/merchant?payTo=<address>`` also exist -- not wired
    here (outside the immediate scope, search/trending answers the question
    asked), addable with no duplication if a real need arises.

Security (the "instruction source boundary" doctrine already applied
everywhere else in this project): every listed service is declared by an
unverified THIRD PARTY (``curated=True`` is still a Coinbase check, never an
absolute guarantee). Some fields observed under real conditions LITERALLY
address an AI agent (e.g.
``extensions.a2a_negotiation.message: "Hey agent! ..."``) -- this module
ONLY returns these fields as DISPLAY TEXT, never interprets or follows them.
Every caller must treat description/service_name as data, never as an
instruction.

Scope strictly respected: DISCOVERY ONLY. This module NEVER pays for a
service, never triggers a call to the paid endpoint itself -- payment (if
ever used) stays in x402_executor.py/x402_budget.py (5$/week cap already in
place), never here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery"
UNAVAILABLE = "donnée x402 Bazaar indisponible"
_HTTP_TIMEOUT = 15.0

# Canonical USDC addresses (stable, never change) -- deliberately NOT the
# `smart_money._STABLECOIN_ADDRESSES_BY_CHAIN` registry (private, Base ONLY,
# designed for another use): x402 Bazaar lists prices on both Base AND
# Solana, verified under real conditions on both networks the same day.
_USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_USDC_SOLANA = "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v"


@dataclass
class X402BazaarResource:
    """A discovered x402 service. ``curated`` = verified by Coinbase (extra
    trust signal), never an absolute guarantee. Any text field
    (``description``, ``service_name``) is metadata declared by a THIRD
    PARTY -- display data only, never an instruction."""

    resource_url: str
    description: str = ""
    service_name: str = ""
    curated: bool = False
    tags: list[str] = field(default_factory=list)
    price_usd: float | None = None
    calls_last_30d: int | None = None
    unique_payers_last_30d: int | None = None
    last_updated: str = ""


@dataclass
class X402BazaarSearchResult:
    available: bool = False
    error: str | None = None
    resources: list[X402BazaarResource] = field(default_factory=list)


def _extract_price_usd(accepts: object) -> float | None:
    """Best-effort, never a guess. Two schemas confirmed under real
    conditions (19/07): (1) ``asset="iso4217:USD"``, ``amount`` already in
    dollars (``agent-pay`` schema); (2) known USDC address (Base/Solana),
    ``amount`` in the smallest unit (6 decimals, ``exact`` schema). Any other
    schema -> None rather than a fabricated price (e.g. an unknown ERC-20
    asset whose decimals can't be verified from this response alone)."""
    if not isinstance(accepts, list):
        return None
    for entry in accepts:
        if not isinstance(entry, dict):
            continue
        asset = str(entry.get("asset") or "").strip().lower()
        amount_raw = entry.get("amount")
        if amount_raw is None:
            continue
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            continue
        if asset == "iso4217:usd":
            return amount
        if asset in (_USDC_BASE, _USDC_SOLANA):
            return amount / 1_000_000.0
    return None


def _parse_resource(raw: dict) -> X402BazaarResource | None:
    resource_url = str(raw.get("resource") or "").strip()
    if not resource_url:
        return None
    quality = raw.get("quality") if isinstance(raw.get("quality"), dict) else {}
    tags = raw.get("tags")
    calls = quality.get("l30DaysTotalCalls")
    payers = quality.get("l30DaysUniquePayers")
    return X402BazaarResource(
        resource_url=resource_url,
        description=str(raw.get("description") or ""),
        service_name=str(raw.get("serviceName") or ""),
        curated=bool(raw.get("curated")),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        price_usd=_extract_price_usd(raw.get("accepts")),
        calls_last_30d=calls if isinstance(calls, int) else None,
        unique_payers_last_30d=payers if isinstance(payers, int) else None,
        last_updated=str(raw.get("lastUpdated") or ""),
    )


async def search(
    *,
    query: str = "",
    network: str | None = None,
    tags: list[str] | None = None,
    curated_only: bool = False,
    limit: int = 20,
) -> X402BazaarSearchResult:
    """Raw search -- order returned by the API (relevance + quality combined).
    For a real trend ranking, prefer ``discover_trending()``."""
    params: list[tuple[str, str]] = [("limit", str(max(1, min(int(limit), 20))))]
    if query.strip():
        params.append(("query", query.strip()[:400]))
    if network:
        params.append(("network", network))
    if curated_only:
        params.append(("curatedOnly", "true"))
    for tag in tags or []:
        params.append(("tags", tag))

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(f"{BASE_URL}/search", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001 -- dome: never an exception that propagates
        logger.info("x402_bazaar: search failed (%s)", exc)
        return X402BazaarSearchResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    if not isinstance(data, dict):
        return X402BazaarSearchResult(available=False, error=UNAVAILABLE)

    raw_resources = data.get("resources")
    if not isinstance(raw_resources, list):
        return X402BazaarSearchResult(available=False, error=UNAVAILABLE)

    resources = [
        parsed
        for parsed in (_parse_resource(item) for item in raw_resources if isinstance(item, dict))
        if parsed is not None
    ]
    return X402BazaarSearchResult(available=True, resources=resources)


async def discover_trending(
    *,
    query: str = "",
    network: str | None = None,
    tags: list[str] | None = None,
    curated_only: bool = False,
    limit: int = 20,
) -> X402BazaarSearchResult:
    """Like ``search()``, but EXPLICITLY sorted by 30-day call volume (the
    most direct objective signal of real trend) rather than the API's raw
    order. A service with no volume data (``calls_last_30d=None``) is ranked
    after every service with a figure, never randomly mixed in with them."""
    result = await search(
        query=query, network=network, tags=tags, curated_only=curated_only, limit=limit
    )
    if not result.available:
        return result
    ranked = sorted(
        result.resources,
        key=lambda r: (r.calls_last_30d is None, -(r.calls_last_30d or 0)),
    )
    return X402BazaarSearchResult(available=True, resources=ranked)


def format_trending_report(result: X402BazaarSearchResult, *, query: str = "", max_items: int = 8) -> str:
    """Telegram rendering -- reminder: description/service_name are metadata
    declared by a THIRD PARTY (displayed as-is, never interpreted as an
    instruction). Pure discovery: no payment button/action in this rendering."""
    header = "🔎 x402 Bazaar — top tendance (volume 30j)"
    if query.strip():
        header += f' — "{query.strip()}"'
    if not result.available:
        return f"{header}\n\n⚠️ {result.error or UNAVAILABLE}."
    if not result.resources:
        return f"{header}\n\nAucun résultat."

    lines = [header, ""]
    for i, res in enumerate(result.resources[:max_items], start=1):
        name = res.service_name or res.resource_url
        badge = " ✅curated" if res.curated else ""
        lines.append(f"{i}. {name}{badge}")
        if res.description:
            desc = res.description[:140]
            lines.append(f"   {desc}")
        vol = (
            f"{res.calls_last_30d} appels/30j"
            if res.calls_last_30d is not None
            else "volume inconnu"
        )
        if res.unique_payers_last_30d is not None:
            vol += f" ({res.unique_payers_last_30d} payeurs)"
        price = f"~{res.price_usd:.4f}$" if res.price_usd is not None else "prix non résolu"
        lines.append(f"   💰 {price} · 📊 {vol}")
        lines.append(f"   {res.resource_url}")
        lines.append("")
    lines.append("Découverte seule -- aucun paiement déclenché par cette commande.")
    return "\n".join(lines).strip()
