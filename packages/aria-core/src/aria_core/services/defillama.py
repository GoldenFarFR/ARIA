"""DefiLlama client (read-only, public, keyless).

Two established uses, one shared HTTP policy:
- ``fetch_chain_tvl_ranking`` (#157, 07/14): TVL ranking of EVM chains for the
  ``/walletscore`` dynamic scan. Caching lives in ``smart_money.py``
  (``wallet_scoring_chain_ranking`` table), which orchestrates the network
  call + the DB write -- this module knows nothing about SQLite.
- Chain/protocol market-data grains (25/08, docs/HANDOFF_DEFILLAMA.md), added
  for the shadow pockets' market-regime work -- three grains matched to three
  trading horizons, not one generic feed bent to fit all three:

  - CHAIN grain (``get_chain_tvl_history`` / ``get_chain_dex_volume``) --
    REGIME/TIMING. Scalping/shadow's only real use: "is there a
    capital/activity inflow RIGHT NOW" to modulate entry FREQUENCY, never to
    pick a token. One point per DAY on both endpoints (confirmed live 25/08:
    Base's latest point was already 11h+ stale, no intraday granularity
    anywhere) -- unsuitable for anything faster than a multi-day regime read,
    exactly the role assigned here.
  - PROTOCOL grain (``get_protocol_growth``) -- SELECTION. Swing's real use:
    is this token's TVL/fees genuinely growing, or is the price pumping on
    nothing. DefiLlama's protocol catalogue is CURATED (established projects
    with real TVL, ~8100 entries verified live 25/08), not automatic
    per-token -- resolving an address that isn't listed returns
    ``available=False``, the expected honest outcome for most early
    candidates, never a bug. Resolution is address -> slug via
    ``resolve_protocol_slug``, built from the one ``/protocols`` listing (one
    call, cached), never a slug guessed or hand-maintained.
  - VC's own multi-protocol/ecosystem grain is NOT built yet: no verified
    need beyond what PROTOCOL already gives for the minority of VC candidates
    mature enough to be listed. Left as an explicit gap, not invented ahead
    of a real requirement.

  Known duplication, not touched here (25/08): ``narrative_signal_shadow.py``
  already runs its OWN DefiLlama protocol+fees fetch (``_refresh_defillama``,
  its own ``/protocols``+``/summary/fees`` calls) for an unrelated signal
  (``defillama_revenue``). A real candidate for later unification onto
  ``resolve_protocol_slug``/``get_protocol_growth`` below, deliberately left
  alone here -- it works, is in prod, and merging it is a separate,
  deliberate change, not a side effect of adding these grains.

  Three more fields surfaced in ``/protocols`` while verifying the above,
  banked in ``docs/HANDOFF_DEFILLAMA.md`` rather than silently dropped, none
  wired into anything yet: ``genuineSpikes`` (a per-protocol editorial log of
  DefiLlama-flagged anomalous dates, present on 37.2% of protocols -- NOT an
  automatic healthy/toxic score), ``misrepresentedTokens`` (bool scam-token
  flag, True on 872/8113), ``audits`` (audit count, non-empty on 2913/8113).

"Guardrail" doctrine (identical to blockscout.py/geckoterminal.py/
dexscreener.py/coinmarketcap.py):
- 429: exponential backoff, 3 attempts max, then abandon without blocking the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation (``None`` / ``available=False``).
- No missing data is ever replaced by a guess.

Rate limit: no current limit is documented for the free tier (verified 25/08
against docs.llama.fi/pro-api -- only the $300/mo Pro tier states a number,
1000 req/min). A 2023 official tweet (@DefiLlama) mentioned throttling ONE
abusive caller past 500 req/min, not a general documented ceiling -- treated
as a historical data point, not a current spec. Call volume from the grains
below is a handful of calls per regime-check cycle, never per-trade, so no
proactive numeric throttle is fabricated (CLAUDE.md doctrine: undocumented
capacity -> reactive 429/5xx backoff only, never an invented precise figure).

Filtering ``fetch_chain_tvl_ranking`` to confirmed chains happens via
``blockscout.CHAIN_IDS`` -- the SOLE source of truth, never a duplicated
registry here (a second copy could have silently diverged, like the "bnb"
forgotten in ``DEFAULT_SCAN_CHAINS`` before its fix that same evening)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import asyncio
import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée DefiLlama indisponible"

BASE_URL = "https://api.llama.fi"

# `/protocols`: one call, ~8100 entries (~8.6MB), reused for every
# address->slug lookup rather than re-fetched per candidate. Long TTL: the
# catalogue itself moves slowly (protocols get added/listed over weeks, not
# minutes).
_PROTOCOL_INDEX_CACHE_TTL_SECONDS = 6 * 3600
_protocol_index_cache: tuple[float, dict[str, str]] | None = None


async def _get_json(path: str) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same policy as the other clients
    in this folder."""
    url = f"{BASE_URL}{path}"
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("defillama: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout DefiLlama)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("defillama: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit DefiLlama)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("defillama: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur DefiLlama {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("defillama: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def fetch_chain_tvl_ranking() -> list[tuple[str, float]] | None:
    """TVL ranking of confirmed ARIA chains, sorted descending.

    GET ``/v2/chains`` (public, keyless), filters by numeric ``chainId``
    (never by ``name`` -- DefiLlama's labels don't always follow ARIA's
    vocabulary, e.g. "ZKsync Era" vs our "zksync") against
    ``blockscout.CHAIN_IDS``, the sole source of truth for confirmed
    queryable chains (Blockscout x GeckoTerminal, established 07/14).

    Returns ``None`` on any network failure or unexpected response shape --
    never an empty list silently confused with "zero TVL everywhere"."""
    from aria_core.services.blockscout import CHAIN_IDS

    data, error = await _get_json("/v2/chains")
    if error is not None:
        logger.warning("defillama: TVL ranking unavailable -> %s", error)
        return None
    if not isinstance(data, list):
        logger.warning("defillama: /v2/chains response has unexpected shape")
        return None

    chain_id_to_name = {cid: name for name, cid in CHAIN_IDS.items()}
    ranked: dict[str, float] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        chain_id = entry.get("chainId")
        name = chain_id_to_name.get(chain_id)
        if name is None:
            continue
        try:
            tvl = float(entry.get("tvl") or 0.0)
        except (TypeError, ValueError):
            tvl = 0.0
        ranked[name] = tvl

    return sorted(ranked.items(), key=lambda item: item[1], reverse=True)


@dataclass
class ChainTvlSeries:
    """Daily TVL history for one chain -- REGIME grain (see module docstring).

    25/08: chain slug is CAPITALIZED on this endpoint ("Base", "Robinhood",
    "Solana" all verified live)."""

    chain: str
    points: list[tuple[int, float]] = field(default_factory=list)  # (unix_ts, tvl_usd)
    available: bool = False
    error: str | None = None


@dataclass
class ChainDexVolumeSeries:
    """Daily DEX volume history for one chain -- REGIME grain, same caveats.

    Raw, NOT wash-trading-filtered (confirmed 25/08: the wash-trading-excluded
    "Normalized Volume" metric is Pro-only AND perps-only -- would not even
    solve the concern). Treat as a RELATIVE signal against its own history,
    never an absolute "clean" figure. 25/08: chain slug is lowercase on this
    endpoint ("base", "robinhood", "solana"), distinct casing from
    ChainTvlSeries -- not a typo."""

    chain: str
    points: list[tuple[int, float]] = field(default_factory=list)  # (unix_ts, volume_usd)
    total_24h: float | None = None
    total_7d: float | None = None
    available: bool = False
    error: str | None = None


@dataclass
class ProtocolGrowthSeries:
    """Daily TVL + DEX-volume + fees history for ONE protocol -- SELECTION grain.

    available=False for an unlisted token is the expected outcome for most
    early candidates (curated catalogue, see module docstring), never a
    failure to alarm on. Missing volume/fees alone (not every protocol has a
    DEX/fee adapter) degrades to an empty list for that series only -- never
    a reason to fail the whole result when TVL itself is real."""

    slug: str
    tvl_points: list[tuple[int, float]] = field(default_factory=list)
    volume_points: list[tuple[int, float]] = field(default_factory=list)
    fee_points: list[tuple[int, float]] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)
    available: bool = False
    error: str | None = None


async def get_chain_tvl_history(chain: str) -> ChainTvlSeries:
    data, error = await _get_json(f"/v2/historicalChainTvl/{chain}")
    if error is not None or not isinstance(data, list):
        return ChainTvlSeries(chain=chain, available=False, error=error or UNAVAILABLE)
    points = [
        (int(p["date"]), float(p["tvl"]))
        for p in data if isinstance(p, dict) and "date" in p and "tvl" in p
    ]
    return ChainTvlSeries(chain=chain, points=points, available=True)


async def get_chain_dex_volume(chain: str) -> ChainDexVolumeSeries:
    data, error = await _get_json(f"/overview/dexs/{chain.lower()}")
    if error is not None or not isinstance(data, dict):
        return ChainDexVolumeSeries(chain=chain, available=False, error=error or UNAVAILABLE)
    chart = data.get("totalDataChart") or []
    points = [(int(p[0]), float(p[1])) for p in chart if isinstance(p, list) and len(p) == 2]
    return ChainDexVolumeSeries(
        chain=chain, points=points,
        total_24h=data.get("total24h"), total_7d=data.get("total7d"),
        available=True,
    )


async def _protocol_index() -> dict[str, str] | None:
    global _protocol_index_cache
    now = time.monotonic()
    if _protocol_index_cache is not None:
        cached_at, cached_index = _protocol_index_cache
        if (now - cached_at) < _PROTOCOL_INDEX_CACHE_TTL_SECONDS:
            return cached_index

    data, error = await _get_json("/protocols")
    if error is not None or not isinstance(data, list):
        logger.info("defillama: protocol index unavailable -- %s", error)
        # A stale cache beats none if this refresh failed.
        return _protocol_index_cache[1] if _protocol_index_cache else None

    index: dict[str, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        address = entry.get("address")
        if not slug or not address or ":" not in address:
            continue
        chain_prefix, _, addr = address.partition(":")
        index[f"{chain_prefix.lower()}:{addr.lower()}"] = slug
    _protocol_index_cache = (now, index)
    return index


async def resolve_protocol_slug(chain: str, address: str) -> str | None:
    """Address -> DefiLlama protocol slug, via the one ``/protocols`` listing
    (cached, never re-fetched per lookup). Returns ``None`` for most
    early-stage candidates -- the catalogue is curated, not automatic
    per-token (see module docstring); that is the expected, honest outcome,
    never a lookup failure to alarm on."""
    index = await _protocol_index()
    if index is None:
        return None
    return index.get(f"{chain.lower()}:{address.lower()}")


async def get_protocol_growth(slug: str) -> ProtocolGrowthSeries:
    tvl_data, tvl_error = await _get_json(f"/protocol/{slug}")
    if tvl_error is not None or not isinstance(tvl_data, dict):
        return ProtocolGrowthSeries(slug=slug, available=False, error=tvl_error or UNAVAILABLE)

    tvl_raw = tvl_data.get("tvl") or []
    tvl_points = [
        (int(p["date"]), float(p["totalLiquidityUSD"]))
        for p in tvl_raw if isinstance(p, dict) and "date" in p and "totalLiquidityUSD" in p
    ]
    chains = tvl_data.get("chains") or list((tvl_data.get("chainTvls") or {}).keys())

    volume_points: list[tuple[int, float]] = []
    vol_data, _ = await _get_json(f"/summary/dexs/{slug}")
    if isinstance(vol_data, dict):
        chart = vol_data.get("totalDataChart") or []
        volume_points = [(int(p[0]), float(p[1])) for p in chart if isinstance(p, list) and len(p) == 2]

    fee_points: list[tuple[int, float]] = []
    fee_data, _ = await _get_json(f"/summary/fees/{slug}")
    if isinstance(fee_data, dict):
        chart = fee_data.get("totalDataChart") or []
        fee_points = [(int(p[0]), float(p[1])) for p in chart if isinstance(p, list) and len(p) == 2]

    return ProtocolGrowthSeries(
        slug=slug, tvl_points=tvl_points, volume_points=volume_points,
        fee_points=fee_points, chains=list(chains), available=True,
    )
