"""GeckoTerminal client (read-only, public, optional key) -- aria-core side (#157).

A GeckoTerminal client already exists on the ``vanguard/backend`` side (chart
data for the product), but aria-core (Telegram/CLI, also runs standalone
without the FastAPI backend) has NO dependency toward ``vanguard/backend`` and
must not create one -- would reverse the monorepo's dependency direction. This
module is therefore a separate, lightweight client, with its own dataclasses
(not the backend's Pydantic models), designed solely for the wallet
evaluator's needs (#157):
- ``get_pool_created_at``: a pool's creation timestamp (early entry).
- ``resolve_primary_pool``: resolves a token's real pool (plausible 24h
  volume, reserve as tiebreaker -- cf. its docstring for the 14/07 fix).
- ``get_ohlcv``: price history to value a trade (FIFO PnL) -- delegates to
  ``services/ohlcv.py`` (14/07 fix, cf. the method's docstring) rather than
  duplicating a second OHLCV client with a narrower window.

Network: Base by default (ARIA doctrine: Base only for everything EXCEPT
wallet-scoring #157, 14/07 -- the only multi-chain EVM capability to date, cf.
``services/blockscout.py`` for the same chain registry). Missing data is
never replaced by a guess -- ``available=False``/``error`` carry the absence
of data, same policy as ``blockscout.py``.

OPTIONAL authentication (18/07, #211): if ``COINGECKO_DEMO_API_KEY`` is
present in the environment (free CoinGecko "Demo" key, no cost --
https://www.coingecko.com/en/api/pricing), attached as the
``x-cg-demo-api-key`` header on every call. The header is still sent (can
legitimately unlock a larger MONTHLY quota and access to premium endpoints
even without speeding up the PER-MINUTE throughput), but the authenticated
throttle was realigned on 19/07 to the same pace as unauthenticated mode --
**a fix for a real bug**, not preventive hardening.

**19/07 incident**: the first version of this comment (18/07) claimed "raises
the cap ... to 100 req/min (verified via official CoinGecko docs)" -- this
figure was WRONG, confused with a different CoinGecko tier (probably the
general keyless API, not GeckoTerminal's ``/onchain`` endpoints which have
their own pricing grid). A real web search on 19/07
(apiguide.geckoterminal.com/faq, support.coingecko.com) confirms: free Public
API (with a Demo key) = **~30 req/min**, keyless with no key = ~10 req/min,
paid = up to 250 req/min (25x keyless). The 0.65s/call throttle (~92 req/min)
deployed on this false premise produced an HTTP 429 failure rate of ~79% in
production for over an hour (666 failures / 176 successes observed) --
explains a good part of the momentum pipeline's silence that evening. Reverted
to ``_MIN_INTERVAL`` (2.1s, the pace already proven in production before this
change) even in authenticated mode, until the REAL sustained cap under real
conditions is verified before attempting to speed up again.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée GeckoTerminal indisponible"

# 21/07 -- calibrated to 90% of the real documented limit (30 req/min Demo,
# CLAUDE.md "90% calibrated throughput" doctrine): 27 req/min = 2.222s.
# Replaces the 2.1s (95%, insufficient margin) set on 19/07 out of caution
# after the incident.
_AUTHENTICATED_MIN_INTERVAL = 2.222


def geckoterminal_authenticated() -> bool:
    """True if ``COINGECKO_DEMO_API_KEY`` is configured (free or paid CoinGecko
    Demo key) -- determines the throttle applied by the module-level client."""
    return bool(os.environ.get("COINGECKO_DEMO_API_KEY", "").strip())


def _resolve_min_interval() -> float:
    """Throttle for the module-level client -- a separate function (rather
    than inline at instantiation) to stay directly testable without reloading
    the module."""
    return _AUTHENTICATED_MIN_INTERVAL if geckoterminal_authenticated() else _MIN_INTERVAL


BASE_URL = "https://api.geckoterminal.com/api/v2"
NETWORK = "base"

# ARIA chain mapping (same vocabulary as blockscout.CHAIN_IDS) -> GeckoTerminal
# network identifier (#157, multi-chain wallet-scoring, 14/07). "bnb" removed
# (14/07) -- Blockscout doesn't serve BNB Smart Chain (cf. blockscout.CHAIN_IDS),
# no point keeping its GeckoTerminal slug alone. Extended (14/07) to the 11
# remaining chains from the dynamic TVL ranking (#157, services/defillama.py)
# -- slugs VERIFIED LIVE (GET https://api.geckoterminal.com/api/v2/networks),
# not assumed: GeckoTerminal's vocabulary doesn't always follow the chain's
# usual name ("gnosis" -> "xdai", "zksync era" -> "zksync" not "zksync_era").
GECKO_NETWORK_SLUGS: dict[str, str] = {
    "base": "base",
    "ethereum": "eth",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "polygon": "polygon_pos",
    "celo": "celo",
    "gnosis": "xdai",
    "scroll": "scroll",
    "zksync": "zksync",
    "rootstock": "rootstock",
    "unichain": "unichain",
    "soneium": "soneium",
    "mode": "mode",
}

# 21/07 -- calibrated to 90% of 30 req/min (CLAUDE.md "90% calibrated
# throughput" doctrine): 27 req/min = 2.222s. The vanguard/backend client now
# shares this same throttle (wait_for_shared_rate_limit), no more need to keep
# the two aligned manually.
_MIN_INTERVAL = 2.222

# Reserve/volume plausibility threshold for `resolve_primary_pool` (14/07 fix,
# cf. its docstring) -- calibrated on real data (direct GeckoTerminal query,
# WETH token on Base, 20 pools): the legitimate pools in the list had a
# reserve/volume ratio in ~[0.01, 5] (e.g. WETH/USDC real 0.3% ~1.4x), while
# the corrupted pool excluded by this fix showed a ratio of ~204,000x -- a
# margin of several orders of magnitude, threshold chosen well below that to
# stay robust without risking excluding a borderline legitimate pool.
_PLAUSIBILITY_RATIO_MAX = 1000.0


def _pool_is_plausible(reserve_usd: float, volume_h24_usd: float) -> bool:
    """A pool is deemed implausible if its declared reserve and its 24h volume
    diverge in statistically inconsistent proportions for a real pool -- in
    ONE direction (huge reserve, near-zero volume: signal of a corrupted/
    spoofed `reserve_in_usd`, real case confirmed on 14/07) OR THE OTHER
    (huge volume, near-zero reserve: classic wash-trading signal). A zero/
    negative reserve is always implausible (no real liquidity could have
    generated a swap). A zero volume is NOT in itself disqualifying (a
    legitimate token can simply have had no trade in the last 24h) -- only the
    extreme RATIO, when computable, disqualifies."""
    if reserve_usd <= 0:
        return False
    if volume_h24_usd <= 0:
        return True
    ratio = max(reserve_usd / volume_h24_usd, volume_h24_usd / reserve_usd)
    return ratio <= _PLAUSIBILITY_RATIO_MAX


@dataclass
class PoolMetadata:
    pool_address: str
    created_at: datetime | None = None
    reserve_usd: float | None = None  # 15/07 (anti-dust/scam-pool defense, #157) -- ``None``
    # = unknown (never built by a caller that doesn't provide it, e.g.
    # existing tests) and treated as "trust it" (fail-open), NOT as "zero
    # liquidity" -- only a value CONFIRMED below the floor should block OHLCV
    # valuation (cf. WEIGHTS.min_pool_liquidity_usd_for_pricing).
    available: bool = True
    error: str | None = None


@dataclass
class OHLCVResult:
    candles: list[Candle] = field(default_factory=list)
    available: bool = True
    error: str | None = None


class GeckoTerminalClient:
    """Async HTTP client, read-only, conservative throttle (free public API)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = _MIN_INTERVAL) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
        """GET with retry on 429/5xx/timeout -- same policy as blockscout.py
        (#157, 14/07 fix: this function used to never retry a rate limit,
        silently marking "unavailable" on the first 429 encountered, with no
        log -- impossible to diagnose. An active wallet (~20 tokens x 2 calls)
        can easily trigger an isolated 429 on the free tier; retrying once is
        enough in the vast majority of cases rather than giving up outright."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False

        headers = {"Accept": "application/json"}
        api_key = os.environ.get("COINGECKO_DEMO_API_KEY", "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.warning("geckoterminal: timeout on %s -> %s", url, exc)
                return None, f"{UNAVAILABLE} (timeout GeckoTerminal)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    logger.warning("geckoterminal: HTTP 429 on %s after %s attempts", url, attempt_429)
                    return None, f"{UNAVAILABLE} (rate limit GeckoTerminal)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.warning("geckoterminal: HTTP %s on %s", response.status_code, url)
                return None, f"{UNAVAILABLE} (erreur serveur GeckoTerminal)"

            if response.status_code in (400, 404):
                return None, f"{UNAVAILABLE} (HTTP {response.status_code})"
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning("geckoterminal: %s", exc)
                return None, f"{UNAVAILABLE} ({exc})"

            return response.json(), None

    async def get_pool_created_at(self, pool_address: str, *, network: str = NETWORK) -> PoolMetadata:
        data, error = await self._get_json(f"/networks/{network}/pools/{pool_address}")
        if error is not None:
            return PoolMetadata(pool_address=pool_address, available=False, error=error)
        if not isinstance(data, dict):
            return PoolMetadata(pool_address=pool_address, available=False, error=UNAVAILABLE)

        attrs = (data.get("data") or {}).get("attributes") or {}
        raw = attrs.get("pool_created_at")
        created_at = None
        if raw:
            try:
                created_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                created_at = None

        if created_at is None:
            return PoolMetadata(pool_address=pool_address, available=False, error="date de création du pool indisponible")
        return PoolMetadata(pool_address=pool_address, created_at=created_at, available=True, error=None)

    async def resolve_primary_pool(self, token_address: str, *, network: str = NETWORK) -> PoolMetadata:
        """Resolves a token's MAIN pool -- #157: `get_pool_created_at`/
        `get_ohlcv` expect a POOL address, not a TOKEN contract (two different
        things in an AMM). Fixes a latent bug: the calling code was passing
        the token contract address directly where a pool address was
        expected. Also serves as the basis for multi-token wash-trading
        exclusion (#157, 14/07 fix) -- each token's REAL pool, not a single
        static address. ``network`` (#157 multi-chain, 14/07): GeckoTerminal
        network identifier (cf. ``GECKO_NETWORK_SLUGS``), ``"base"`` by
        default -- unchanged historical behavior for any existing caller.

        **Pool selection fix (14/07 review, following #157)**: the historical
        criterion ("highest `reserve_in_usd`") produced a real confirmed case
        where a WETH pool advertising 7.6 BILLION dollars of reserve for
        $37,000 of 24h volume (ratio ~204,000x, `reserve_in_usd` visibly
        corrupted/spoofed on GeckoTerminal's side for this exotic pool) was
        chosen instead of the real WETH/USDC pool used in an actual
        transaction -- a ~8x price gap, never flagged as an error
        (`available=True`), hence worse than a simply unpriced leg. New
        criterion (cf. `_pool_is_plausible`): first filters out pools whose
        reserve/volume ratio is statistically implausible in either direction
        (inflated reserve with no real volume = corrupted-data signal;
        inflated volume with no real reserve = wash-trading signal), THEN
        sorts the survivors by 24h volume (reflects real usage, harder to
        durably fake than a declared reserve), with `reserve_in_usd` as a
        secondary tiebreaker. A SINGLE-POOL token (vast majority of cases
        outside wallet-scoring) is NEVER subjected to the filter -- that pool
        is always kept, strictly unchanged behavior for this case. A
        multi-pool token where NONE passes the filter fails honestly
        (`available=False`) rather than falling back to the worst available
        choice."""
        data, error = await self._get_json(f"/networks/{network}/tokens/{token_address}/pools")
        if error is not None:
            return PoolMetadata(pool_address=token_address, available=False, error=error)
        if not isinstance(data, dict):
            return PoolMetadata(pool_address=token_address, available=False, error=UNAVAILABLE)

        pools = data.get("data") or []
        candidates: list[tuple[dict, float, float]] = []
        for item in pools:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") or {}
            try:
                reserve = float(attrs.get("reserve_in_usd") or 0.0)
            except (TypeError, ValueError):
                reserve = 0.0
            volume_raw = (attrs.get("volume_usd") or {}).get("h24") if isinstance(attrs.get("volume_usd"), dict) else None
            try:
                volume = float(volume_raw or 0.0)
            except (TypeError, ValueError):
                volume = 0.0
            candidates.append((attrs, reserve, volume))

        if not candidates:
            return PoolMetadata(pool_address=token_address, available=False, error="aucun pool trouvé pour ce token")

        if len(candidates) == 1:
            # Single pool -- never subjected to the plausibility filter
            # (nothing to tiebreak), strictly unchanged behavior.
            best_attrs, best_reserve, _volume = candidates[0]
        else:
            plausible = [c for c in candidates if _pool_is_plausible(c[1], c[2])]
            if not plausible:
                return PoolMetadata(
                    pool_address=token_address,
                    available=False,
                    error="aucun pool plausible pour ce token (réserve/volume incohérents sur tous les pools trouvés)",
                )
            best_attrs, best_reserve, _best_volume = max(plausible, key=lambda c: (c[2], c[1]))

        if not best_attrs.get("address"):
            return PoolMetadata(pool_address=token_address, available=False, error="aucun pool trouvé pour ce token")

        pool_address = str(best_attrs["address"])
        raw_created = best_attrs.get("pool_created_at")
        created_at = None
        if raw_created:
            try:
                created_at = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
            except ValueError:
                created_at = None

        return PoolMetadata(
            pool_address=pool_address, created_at=created_at, reserve_usd=best_reserve, available=True, error=None,
        )

    async def get_ohlcv(
        self,
        pool_address: str,
        *,
        network: str = NETWORK,
        min_useful_candles: int | None = None,
        **_kwargs: object,
    ) -> OHLCVResult:
        """Delegates to ``services.ohlcv.ohlcv_client`` -- 14/07 fix (#157):
        this method used to reimplement a second GeckoTerminal client with its
        own fixed window (200 1h candles ~ 8 days), when a GeckoTerminal
        client already existed (``services/ohlcv.py``, day(120) -> 4h(180) ->
        1h(240) escalation, already proven in production by
        `vc_predictions`/`weekly_training`/`pump_dump_autopsy`) -- a violation
        of the "never duplicate an existing client" doctrine, and the REAL
        cause (confirmed by an operator re-test after the same day's
        retry/429 fix, identical result) of "no price" legs on a wallet whose
        trade history exceeds 8 days: the 1h window simply didn't reach far
        enough back, it wasn't a rate-limit problem. ``network`` (#157
        multi-chain, 14/07) is passed through to ``services/ohlcv.py`` (which
        already accepted this parameter, never used until now).
        ``min_useful_candles`` (#182, 15/07, wallet-scoring speed fix) is also
        passed through to ``services/ohlcv.py`` -- ``None`` by default (the
        corresponding parameter of ``ohlcv_client.get_ohlcv`` then keeps ITS
        own default, ``_MIN_USEFUL_CANDLES``, no change for existing callers).
        ``**_kwargs`` absorbs any inherited period/aggregate/limit (no caller
        in production currently passes them) without raising."""
        from aria_core.services.ohlcv import ohlcv_client as _wide_ohlcv_client

        extra: dict[str, object] = {}
        if min_useful_candles is not None:
            extra["min_useful_candles"] = min_useful_candles

        wide = await _wide_ohlcv_client.get_ohlcv(pool_address, network=network, **extra)
        if not wide.available or not wide.candles:
            return OHLCVResult(candles=[], available=False, error=wide.error or UNAVAILABLE)
        return OHLCVResult(candles=wide.candles, available=True, error=None)


def price_at(ohlcv: OHLCVResult, ts: int) -> float | None:
    """Price (close of the nearest candle at or before ``ts``) -- never an
    interpolation or a guess: ``None`` if no candle precedes ``ts``."""
    candidates = [c for c in ohlcv.candles if c.ts <= ts]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.ts).close


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


geckoterminal_client = GeckoTerminalClient(min_interval=_resolve_min_interval())


async def wait_for_shared_rate_limit() -> None:
    """Public entry point for a caller EXTERNAL to this module
    (``vanguard/backend``, the only one authorized -- aria-core never depends
    on vanguard, cf. module docstring) that needs to respect the SAME
    throughput toward GeckoTerminal without duplicating its own throttle lock.
    21/07: root cause of a sustained 55% 429 rate -- two independent
    GeckoTerminal clients (this one + `vanguard/backend/app/services/
    geckoterminal.py`) coexisted in the same container, each respecting its
    own 2.1s interval WITHOUT ever coordinating -- their combined throughput
    exceeded the account's real cap. This function makes both clients share
    the SAME lock/state (``geckoterminal_client._throttle``), without merging
    their fetch/parsing logic (deliberately distinct: this one serves
    wide-window FIFO pricing, the other serves precise-timeframe-granularity
    charts -- not the same need, not the same return format)."""
    await geckoterminal_client._throttle()
