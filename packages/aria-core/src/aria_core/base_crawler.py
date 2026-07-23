"""Base crawler — discovers tokens and feeds them through the absorber.

"Scan everything": continuously pull Base pools (new + trending via
GeckoTerminal), extract the token contracts, and pass them through
``token_absorber.absorb`` -> kept in the proprietary database or rejected
forever (unless resurrected). A token already known (active/rejected) is
short-circuited by the absorber, so re-crawling costs nothing.

Network discovery (GeckoTerminal) is **injectable** -> testable offline. In
prod, the default hits the public API (runs on the VPS, network allowed).
Read-only, no signing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_GT_BASE = "https://api.geckoterminal.com/api/v2"
_DISCOVERY_PATHS = (
    "/networks/base/new_pools",
    "/networks/base/trending_pools",
)
# Top pools: the hunting ground for ESTABLISHED tokens (verified, with real
# depth) — not the bin of fresh launches. This is where the real 85% VC
# builders are found. ``sort=h24_volume_usd_desc`` is EXPLICIT (following
# audit #77: the GeckoTerminal default for this endpoint is
# ``h24_tx_count_desc`` — 24h transaction count, not depth/volume — biased
# toward raw activity (bots/snipers on just-launched tokens) rather than
# genuinely established pools. The previous comment claimed a volume/liquidity
# sort that the code never actually enforced — fixed here with an explicit
# query parameter).
_TOP_POOLS_PATH = "/networks/base/pools?sort=h24_volume_usd_desc"


def _extract_token_contracts(payload: object) -> list[str]:
    """Extracts token addresses (base_token) from a GeckoTerminal pools response.

    ``data[].relationships.base_token.data.id`` = ``"base_0x..."`` -> strip the
    network prefix. Silently ignores any malformed entry (never raises).
    """
    out: list[str] = []
    if not isinstance(payload, dict):
        return out
    for item in payload.get("data", []) or []:
        try:
            tid = item["relationships"]["base_token"]["data"]["id"]
        except (KeyError, TypeError):
            continue
        if isinstance(tid, str):
            # "base_0xabc..." -> "0xabc..."
            addr = tid.split("_", 1)[1] if "_" in tid else tid
            if addr.startswith("0x") and len(addr) == 42:
                out.append(addr.lower())
    return out


async def _fetch_gt(path: str) -> object | None:
    """GET GeckoTerminal (graceful degradation: None on any error, never blocking)."""
    url = f"{_GT_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:  # noqa: BLE001 — never blocking
        logger.info("base_crawler: fetch %s failed (%s)", path, exc)
        return None


async def discover_base_tokens(*, fetch=None, limit: int = 100) -> list[str]:
    """Base token contracts to consider (new + trending), deduplicated."""
    fetch = fetch or _fetch_gt
    seen: dict[str, None] = {}
    for path in _DISCOVERY_PATHS:
        payload = await fetch(path)
        for addr in _extract_token_contracts(payload):
            if addr not in seen:
                seen[addr] = None
            if len(seen) >= limit:
                break
        if len(seen) >= limit:
            break
    return list(seen.keys())


def _pool_age_days(created_at: object) -> float | None:
    """Pool age in days from ``attributes.pool_created_at`` (ISO 8601, GeckoTerminal).

    ``None`` if the field is missing or couldn't be parsed (never raises — an
    unknown age isn't an error, just missing data for the caller to handle,
    see ``discover_top_pools``).
    """
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - created).total_seconds() / 86_400.0


def _extract_tokens_with_liquidity(payload: object) -> list[tuple[str, float, float | None]]:
    """(address, USD reserve, age in days or None) from a GeckoTerminal pools response.

    ``attributes.reserve_in_usd`` = pool liquidity, ``attributes.pool_created_at``
    = creation date (ISO 8601). Enables filtering AT DISCOVERY TIME: no point
    scanning a pool below the liquidity floor or too young (it will rarely pass/
    mature past the safety filter) — zero extra cost, both fields are already in
    the same GeckoTerminal response, no additional network call.
    """
    out: list[tuple[str, float, float | None]] = []
    if not isinstance(payload, dict):
        return out
    for item in payload.get("data", []) or []:
        try:
            tid = item["relationships"]["base_token"]["data"]["id"]
            attrs = item.get("attributes") or {}
            reserve = attrs.get("reserve_in_usd")
        except (KeyError, TypeError, AttributeError):
            continue
        if not isinstance(tid, str):
            continue
        addr = tid.split("_", 1)[1] if "_" in tid else tid
        if not (addr.startswith("0x") and len(addr) == 42):
            continue
        try:
            r = float(reserve) if reserve is not None else 0.0
        except (TypeError, ValueError):
            r = 0.0
        age_days = _pool_age_days(attrs.get("pool_created_at"))
        out.append((addr.lower(), r, age_days))
    return out


async def discover_top_pools(
    *,
    fetch=None,
    limit: int = 100,
    min_liquidity_usd: float = 45_000.0,
    min_age_days: float | None = None,
) -> list[str]:
    """Tokens from TOP Base pools (established, liquid), filtered by a liquidity floor.

    The real hunting ground for the 85% VC pocket: tokens with real depth, not
    illiquid fresh launches. Only returns what can PASS the filter.

    ``min_liquidity_usd`` (default $45,000, raised from $30,000 on 07/12 —
    following audit #77 diversification): this floor checks ``reserve_in_usd``
    via GeckoTerminal, while the real gate in ``safety_screen`` checks
    liquidity via DexScreener (``scan_base_token``) — two providers that are
    NOT guaranteed to agree on the same pool. Empirical safety margin (07/12
    sample: candidates at $30k+ in `reserve_in_usd` scanned at $0 on the
    DexScreener side), not a new safety criterion — the real threshold ($30k)
    in ``safety_screen.py`` stays unchanged, this margin just reduces the
    noise sent to ``absorb()``.

    ``min_age_days`` (optional, default ``None`` = no filter, unchanged
    behavior): excludes pools younger than this threshold. An unknown age
    (missing/unparsable ``pool_created_at`` field) is treated as too young as
    soon as ``min_age_days`` is provided — fail-closed, consistent with the
    rest of the pipeline (see ``safety_screen``), without ever touching its
    safety gates.
    """
    fetch = fetch or _fetch_gt
    payload = await fetch(_TOP_POOLS_PATH)
    seen: dict[str, None] = {}
    for addr, reserve, age_days in _extract_tokens_with_liquidity(payload):
        if reserve < min_liquidity_usd:
            continue
        if min_age_days is not None and (age_days is None or age_days < min_age_days):
            continue
        if addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def discover_virtuals_tokens(*, client=None, limit: int = 50) -> list[str]:
    """Virtuals tokens in bonding (the 15% niche) — real AI-agent builders.

    WARNING: these tokens are on a bonding curve (thin liquidity, often not
    verified in the Blockscout sense) -> they do NOT enter the standard
    absorber (they would fail wrongly). Reserved for the future dedicated
    bonding pipeline (adapted analysis mode). Exposed here for that pipeline,
    not for the standard VC crawl.
    """
    if client is None:
        from aria_core.services.virtuals import virtuals_client as client
    try:
        protos = await client.fetch_prototypes()
    except Exception as exc:  # noqa: BLE001 — never blocking
        logger.info("base_crawler: Virtuals discovery failed (%s)", exc)
        return []
    seen: dict[str, None] = {}
    for vt in protos or []:
        addr = (getattr(vt, "token_address", None) or "").lower()
        if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def discover_virtuals_graduated_tokens(*, client=None, limit: int = 50) -> list[str]:
    """Recently-graduated Virtuals tokens — real DEX liquidity, STANDARD pipeline.

    Unlike ``discover_virtuals_tokens`` (bonding, 15% niche), these tokens have
    a real DEX pair post-graduation: they join the generic absorber
    (``token_absorber.absorb``, 85% VC pool) like any other Base token, with no
    special handling. Exposed for a faster pickup than waiting for them to
    show up in ``discover_top_pools`` (liquidity threshold, can still be thin
    right after graduation).
    """
    if client is None:
        from aria_core.services.virtuals import virtuals_client as client
    try:
        tokens = await client.fetch_graduated()
    except Exception as exc:  # noqa: BLE001 — never blocking
        logger.info("base_crawler: graduated Virtuals discovery failed (%s)", exc)
        return []
    seen: dict[str, None] = {}
    for vt in tokens or []:
        addr = (getattr(vt, "token_address", None) or "").lower()
        if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


async def crawl_and_absorb(
    *, discover=None, absorber=None, limit: int = 50, max_age_days: int | None = None
) -> dict:
    """Discovers Base tokens and absorbs them. Returns the count per verdict.

    ``discover()`` -> list of contracts (default: GeckoTerminal). ``absorber(contract)``
    -> 'kept'/'rejected'/'skip_*' (default: token_absorber.absorb). The absorber
    already short-circuits known tokens, so no waste. ``max_age_days``
    (optional): forwarded to the absorber, out of scope ('skip_too_old') beyond it.
    """
    # Default: the "top pools" hunting ground (established, liquid) — not the
    # bin of fresh launches. This is where the real 85% VC builders live.
    disc = discover or discover_top_pools
    if absorber is None:
        from aria_core.token_absorber import absorb as absorber

    tokens = await disc() if callable(disc) else disc
    counts: dict[str, int] = {}
    for contract in list(tokens)[:limit]:
        try:
            if max_age_days is not None:
                verdict = await absorber(contract, max_age_days=max_age_days)
            else:
                verdict = await absorber(contract)
        except Exception as exc:  # noqa: BLE001 — a failing token doesn't stop the crawl
            logger.info("base_crawler: absorb %s failed (%s)", contract, exc)
            verdict = "error"
        counts[verdict] = counts.get(verdict, 0) + 1
    logger.info("base_crawler: %s tokens processed %s", sum(counts.values()), counts)
    return counts


async def retry_stale_pending(
    *,
    limit: int = 20,
    older_than_hours: int = 24,
    max_retries: int = 5,
    max_age_days: int = 7,
    lister=None,
    absorber=None,
    abandon_checker=None,
) -> dict:
    """Deliberately retries ``pending`` candidates (soft failure) left behind.

    ``crawl_and_absorb`` only revisits a ``pending`` candidate if it happens
    to reappear in a later discovery (``token_absorber.absorb`` doesn't
    short-circuit 'pending' already, see
    ``test_soft_fail_pending_is_still_rescanned_next_cycle``) — but nothing
    deliberately goes back to fetch it if the market doesn't put it back in
    front of the crawl. Measured result (audit #77): the ``active`` pool stays
    at 0 despite a correct discovery flow, because candidates that are "not
    yet mature" (contract not yet verified, holders not yet readable,
    liquidity still rising) are never revisited once their data may have
    matured.

    Duplicates no filter: ``lister`` (default ``screened_pool.list_stale_pending``)
    just finds WHO to retry, ``absorber`` (default ``token_absorber.absorb``) is
    the SAME filtering code as the normal crawl — a still-immature candidate
    stays 'pending' (retried next pass), a candidate now confirmed malicious
    becomes 'rejected', a candidate that has matured finally becomes 'active'.

    Anti-infinite-loop cap (following audit #77/#105: 41/50 ``rejected`` found
    with no hard signal, leftovers from a stricter filter version, never
    retried since — without a cap, a candidate that never matures would be
    retried every 24h forever). If a candidate stays ``skip_incomplete``
    (still soft-failing) after this new pass, ``abandon_checker`` (default
    ``screened_pool.abandon_stale_pending``) checks ``max_retries``/``max_age_days``
    and flips it to ``rejected`` (explicit reason) if exceeded. Again NO new
    safety criterion — just a limit on the number of passes, applied only
    after ``absorber`` has already decided it's neither mature ('kept') nor
    confirmed malicious ('rejected').
    """
    if lister is None:
        from aria_core import screened_pool

        async def lister():
            return await screened_pool.list_stale_pending(
                older_than_hours=older_than_hours, limit=limit
            )

    stale = await lister()

    # ``known_age_days`` (Track C, 07/12 -- same-day fix: computed
    # UNCONDITIONALLY, NOT only when ``absorber is None``. In prod,
    # ``heartbeat.py`` ALWAYS injects its own ``absorber`` (Track A wrapper that
    # tags ``source``) -- an earlier version of this computation, confined to
    # the "default absorber" branch, therefore never actually ran (found by
    # checking prod, not by assuming it worked). Derived from
    # ``first_screened_at`` -- a conservative bound on the real on-chain age
    # (often older than ARIA's first detection of it, never younger for
    # ``top_pools``/``bonding_direct`` candidates). Forwarded to ANY ``absorber``
    # (default or injected) via a kwarg -- the Track A wrappers
    # (``_absorb_top_pools``/``_absorb_radar`` in ``heartbeat.py``) already
    # forward it as-is via their ``**kw``, no change needed on the
    # ``heartbeat.py`` side.
    _age_by_contract: dict[str, float] = {}
    for row in stale:
        if not isinstance(row, dict):
            continue
        contract_key = row.get("contract")
        first_screened = row.get("first_screened_at")
        if not (contract_key and first_screened):
            continue
        try:
            dt = datetime.fromisoformat(str(first_screened).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            _age_by_contract[contract_key] = (
                datetime.now(timezone.utc) - dt
            ).total_seconds() / 86_400.0
        except (ValueError, TypeError):
            continue

    if absorber is None:
        from aria_core.token_absorber import absorb as absorber

    if abandon_checker is None:
        from aria_core import screened_pool as _screened_pool

        async def abandon_checker(contract):
            return await _screened_pool.abandon_stale_pending(
                contract, max_retries=max_retries, max_age_days=max_age_days
            )

    counts: dict[str, int] = {}
    for row in stale:
        contract = row["contract"] if isinstance(row, dict) else row
        try:
            verdict = await absorber(contract, known_age_days=_age_by_contract.get(contract))
            # 'skip_prefiltered' (Track C) is also a soft-failure variant -- a
            # structurally blocked candidate must eventually be abandoned, like
            # 'skip_incomplete', not retried forever every 24h.
            if verdict in ("skip_incomplete", "skip_prefiltered") and await abandon_checker(contract):
                verdict = "abandoned"
        except Exception as exc:  # noqa: BLE001 — a failing candidate doesn't stop the others
            logger.info("base_crawler: retry %s failed (%s)", contract, exc)
            verdict = "error"
        counts[verdict] = counts.get(verdict, 0) + 1
    logger.info("base_crawler: retry pending -> %s tokens revisited %s", sum(counts.values()), counts)
    return counts
