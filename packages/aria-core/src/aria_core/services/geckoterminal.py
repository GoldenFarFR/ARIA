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
#
# 26/07 -- real incident found (operator report "aria ne trade pas en
# scalping"): 0 HTTP 429 on this endpoint in the 6h before Ethereum was added
# to the momentum pipeline's DEFAULT_CHAINS, 13+ right after -- confirmed via
# real prod logs, not assumed. The per-call throttle itself never changed;
# what changed is that Base+Ethereum together keep this client PERMANENTLY
# busy (no more idle gaps between scans for the shared budget to recover),
# which is what actually exposed a real sustained cap lower than the 30
# req/min documented figure (documented != empirically measured under
# continuous load -- same doctrine as the GoPlus/Tavily incidents in
# CLAUDE.md). Widened the safety margin 90% -> 70% of documented (30*0.7=21
# req/min = 2.857s) as an immediate, reversible precaution -- a real
# burst-controlled empirical measurement of the true sustained cap under this
# NEW continuous two-chain load still needs to happen separately, this is not
# a substitute for it.
#
# 08/01 -- briefly halved (15:46-17:3x UTC) as a temporary de-escalation
# signal during a sustained Cloudflare 429 block -- restored to nominal once
# the real root cause was found and fixed (a 479-contract manual-candidate
# backlog, injected in one burst, dominating every discovery cycle's fetch
# quota -- see manual_candidates.py's own history). The throttle itself was
# never the actual cause.
#
# 08/02 -- widened again, operator decision, after live prod logs still showed
# a sustained 429 rate (29/15min, ~54/57 of all 429s across services) even
# with the limit_orders.py watch-check cap (08/01 entry) and the per-call
# throttle both already in place -- confirms the real sustained cap is lower
# than 21 req/min under Base+Ethereum's continuous two-chain load. 20 req/min
# = 3.0s, a small further cut pending an actual burst-controlled measurement
# of the true sustained cap (still not done, see the 26/07 comment above).
#
# 08/02 (later same day) -- widened AGAIN, operator decision, after a live
# post-deploy sample at 20 req/min still showed a near-total failure rate
# (28/29 GeckoTerminal calls -> 429 in the 20 minutes right after a redeploy,
# 32 in the following 20-min window too) -- 20 req/min was still above the
# real sustained cap under current load. 15 req/min = 4.0s. Still not the
# burst-controlled empirical measurement called for above -- this remains an
# incremental de-escalation, not a calibrated final value.
#
# 04/08 -- widened AGAIN (5x, 15 -> 75 req/min), explicit operator decision,
# on a re-examined premise: DEFAULT_CHAINS has been Base-only since 27/07
# (the "Base+Ethereum two-chain load" that drove every widening above no
# longer applies to current traffic -- verified live, 100% of GeckoTerminal
# calls in the prior 2h were networks/base/pools, zero Ethereum), AND the
# OHLCV fallback cascade (_fetch_candles: DexPaprika/CoinMarketCap/Mobula/
# Codex.io/DexScreener/Dune) was confirmed absorbing a GeckoTerminal 429
# cleanly -- two live 429s minutes before this change both got real 15min
# candles from Mobula within ~300ms, zero pipeline impact.
#
# 04/08, minutes later -- REVERTED. The adaptive per-provider circuit
# breaker (_PROVIDER_FAIL_THRESHOLD=3, see momentum_entry.py) tripped within
# ~10s of deploy and stayed open (repeated "paused" log lines) for the
# entire observation window -- a SUSTAINED, not isolated, 429 rate, exactly
# the rollback trigger this comment named. Operator also flagged a real,
# already-lived risk this change didn't weigh enough: GeckoTerminal sits
# behind Cloudflare, and a sustained aggressive rate risks a Cloudflare-level
# IP block (the 08/01 incident above), which is far worse and longer-lived
# than an API 429 -- not a risk to re-test casually. Back to the
# already-stable 15 req/min. The burst-controlled empirical measurement
# (task #41) remains the right way to find the real ceiling -- a bounded,
# monitored test script, not a permanent throttle change applied live.
_AUTHENTICATED_MIN_INTERVAL = 4.0


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
    # 16/08 -- Solana added for solana_pump_shadow.py's shadow-only "take the
    # train" observation (never a discovery source, momentum_entry.DEFAULT_CHAINS
    # stays Base-only). ARIA's own vocabulary already matches GeckoTerminal's
    # real slug here (VERIFIED LIVE, GET /api/v2/networks page 1 -- {"id":
    # "solana","attributes":{"name":"Solana"}}), listed explicitly anyway
    # rather than relying on the fallback ``GECKO_NETWORK_SLUGS.get(network,
    # network)`` silent passthrough -- same "never assume a coincidence"
    # doctrine as the 26/07 Ethereum bug this dict's own docstring above
    # documents.
    "solana": "solana",
    # 16/08 -- Robinhood Chain added for robinhood_pump_shadow.py's shadow-only
    # "take the train" observation (same doctrine as Solana above, never a
    # discovery source). VERIFIED LIVE (GET /api/v2/networks/robinhood/
    # trending_pools?duration=5m -> HTTP 200, real pool data e.g. "CASHCAT /
    # WETH 0.3%", ids prefixed "robinhood_0x...") -- ARIA's own chain name
    # already matches GeckoTerminal's real slug here too, listed explicitly
    # anyway rather than relying on the fallback passthrough (same reasoning
    # as the Solana entry above).
    "robinhood": "robinhood",
}

# 21/07 -- calibrated to 90% of 30 req/min (CLAUDE.md "90% calibrated
# throughput" doctrine): 27 req/min = 2.222s. The vanguard/backend client now
# shares this same throttle (wait_for_shared_rate_limit), no more need to keep
# the two aligned manually.
# 26/07 -- widened alongside _AUTHENTICATED_MIN_INTERVAL (same real incident,
# see its comment) to keep the "authenticated is never slower than keyless"
# invariant intact -- this path isn't the one active in prod today
# (authenticated mode is), but kept consistent rather than left stale.
#
# 08/01 -- briefly halved then restored, same episode as
# _AUTHENTICATED_MIN_INTERVAL above -- see its comment for the real root
# cause found (manual-candidate backlog, not the throttle itself).
#
# 08/02 -- widened alongside _AUTHENTICATED_MIN_INTERVAL (same real incident,
# see its comment) to keep the "authenticated is never slower than keyless"
# invariant intact -- this path isn't the one active in prod today
# (authenticated mode is), kept consistent rather than left stale.
#
# 08/02 (later same day) -- widened again alongside _AUTHENTICATED_MIN_INTERVAL,
# same reason, same invariant.
#
# 04/08 -- widened, then REVERTED minutes later alongside
# _AUTHENTICATED_MIN_INTERVAL (same operator decision/incident, see its
# comment: circuit breaker tripped sustained, Cloudflare-block risk flagged
# by the operator), same invariant kept intact.
_MIN_INTERVAL = 4.0

# 10/08 -- adaptive throttle, replaces the manual edit-commit-deploy cycle
# this constant went through 7 times between 21/07 and 04/08 (each widening
# above is a real incident that needed a human to notice, diagnose, edit
# the code and redeploy). The client now tightens its OWN pace in reaction
# to a real 429 and loosens it back on its own -- but DELIBERATELY
# ASYMMETRIC, mirroring the operator's own explicit 04/08 warning: a 429 is
# recoverable, a sustained aggressive rate risking a Cloudflare-level IP
# block is not, "not a risk to re-test casually". So: tighten FAST (every
# single 429 widens the interval immediately), loosen SLOW (only after many
# consecutive successes, in small steps) -- and NEVER below the operator's
# own last hand-calibrated ``_MIN_INTERVAL``/``_AUTHENTICATED_MIN_INTERVAL``
# value above, which stays the hard floor. Speeding up past that floor is
# still a decision only a human makes deliberately, same doctrine as the
# 04/08 revert.
_RATE_LIMIT_BACKOFF_FACTOR = 1.5
_MAX_INTERVAL_MULTIPLIER = 3.0
_CONSECUTIVE_SUCCESSES_BEFORE_EASING = 30
_EASE_STEP_FACTOR = 0.9

# Reserve/volume plausibility threshold for `resolve_primary_pool` (14/07 fix,
# cf. its docstring) -- calibrated on real data (direct GeckoTerminal query,
# WETH token on Base, 20 pools): the legitimate pools in the list had a
# reserve/volume ratio in ~[0.01, 5] (e.g. WETH/USDC real 0.3% ~1.4x), while
# the corrupted pool excluded by this fix showed a ratio of ~204,000x -- a
# margin of several orders of magnitude, threshold chosen well below that to
# stay robust without risking excluding a borderline legitimate pool.
_PLAUSIBILITY_RATIO_MAX = 1000.0

# 14/08, #181 -- no provider checked (DexScreener, GeckoTerminal, DexPaprika,
# CoinGecko onchain, Birdeye) exposes a ready-made all-time-high price field
# for a DEX pool, even DexScreener's own web UI (which visibly shows one)
# never surfaces it through its documented API. The only real source is
# GeckoTerminal's own daily OHLCV history, paginated back to the pool's
# creation. Verified live (CoinGecko onchain docs, which serve the same
# GeckoTerminal data): "Each API call can only retrieve data for a maximum
# range of 6 months. To fetch older data, use the before_timestamp
# parameter" -- 180 days/page (~6 months), paginated backward via
# ``before_timestamp``. Capped at 20 pages (~10 years) -- far beyond any
# real token's age on this pipeline (micro-caps, recently launched), pure
# anti-infinite-loop guard, same doctrine as blockscout_x402.py's
# ``_MAX_PAGES_PER_EXTRACTION``.
_ATH_CANDLES_PER_PAGE = 180
_ATH_MAX_PAGES = 20


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


@dataclass(frozen=True)
class PoolTrade:
    tx_from_address: str
    kind: str  # "buy" or "sell", as GeckoTerminal names it (NOT "type")
    volume_usd: float
    block_timestamp: str


@dataclass
class PoolTradesResult:
    trades: list[PoolTrade] = field(default_factory=list)
    available: bool = True
    error: str | None = None


@dataclass
class AthResult:
    ath_price: float | None = None
    ath_at: datetime | None = None
    available: bool = True
    error: str | None = None


@dataclass(frozen=True)
class TrendingPool:
    """One pool from ``/networks/{network}/trending_pools`` -- 16/08, first
    consumer is ``solana_pump_shadow.py`` (shadow-only, never a real trigger).
    Every numeric field is ``None`` when the provider omitted or gave an
    unparseable value -- never fabricated (same dome doctrine as every other
    dataclass in this module)."""

    pool_address: str
    token_address: str | None
    symbol: str | None
    price_usd: float | None
    price_change_pct: dict[str, float]  # keys among m5/m15/m30/h1/h6/h24
    transactions_m15: dict[str, int] | None  # buys/sells/buyers/sellers
    volume_usd_m15: float | None
    reserve_usd: float | None
    # 16/08, second pass -- already present in the raw response (`attributes.
    # pool_created_at`), no extra call needed. First consumer: the shadow's
    # MAX_POOL_AGE_MINUTES protection against a pool that already rug-pulled
    # its liquidity well before the shadow ever saw it.
    pool_created_at: datetime | None = None
    # 18/08, operator-directed exhaustive-capture pass (future trades should
    # carry no analysis blind spots): these 4 fields are already present in
    # DexPaprika's ``/pools/search`` response item (confirmed live via a
    # real curl test 18/08) but were being parsed away before this change --
    # zero extra network cost, purely additive.
    # ``None`` for GeckoTerminal's own ``trending_pools`` endpoint (this
    # dataclass is shared between both providers; only DexPaprika's parser
    # populates these for now).
    dex_id: str | None = None
    dex_name: str | None = None
    volume_usd_24h: float | None = None
    transactions_24h: int | None = None


@dataclass
class TrendingPoolsResult:
    pools: list[TrendingPool] = field(default_factory=list)
    available: bool = True
    error: str | None = None


@dataclass
class PoolSnapshot:
    """A pool's CURRENT price/liquidity -- 16/08, first consumer is
    ``solana_pump_shadow.py``'s forward-outcome measurement pass (re-checks a
    previously-logged signal's real price N minutes/hours later). Deliberately
    a NEW, separate dataclass rather than extending ``PoolMetadata`` (which
    ``get_pool_created_at`` already returns and several production call sites
    already depend on) -- avoids any risk of regressing an existing caller."""

    pool_address: str
    price_usd: float | None = None
    reserve_usd: float | None = None
    available: bool = True
    error: str | None = None
    # 17/08 -- the SAME response already carries per-window price changes,
    # transaction counts and volumes; they were simply parsed away. Captured
    # here at ZERO extra network cost (this endpoint is already called every
    # exit-tracking cycle) because a data audit found 5 signals never
    # collected at all -- m15/m30 price change, buyers/sellers, volume --
    # purely because discovery runs on DexPaprika, whose search endpoint
    # exposes none of them. Optional with None defaults so no existing
    # caller is affected.
    price_change_pct: dict[str, float] = field(default_factory=dict)
    transactions: dict[str, dict] = field(default_factory=dict)
    volume_usd: dict[str, float] = field(default_factory=dict)
    # 17/08 -- lets a caller recognize a PumpSwap pool (pump.fun's native
    # graduated-pool AMM): both DexScreener and GeckoTerminal report its
    # reserve as near-zero regardless of real liquidity (confirmed live,
    # EYE pool: $837k real 24h volume, both providers read <$0.01 reserve).
    # Root cause per pump.fun's own public docs (pump-fun/pump-public-docs):
    # a PumpSwap pool's real tradable depth is
    # `pool_quote_token_account.amount + Pool.virtual_quote_reserves`, a
    # formula neither indexer implements for this pool type -- not a parsing
    # bug on our side, a genuine upstream gap. None when the source (e.g. the
    # GeckoTerminal fallback path) doesn't report a dex id at all.
    dex_id: str | None = None


@dataclass
class OHLCVResult:
    candles: list[Candle] = field(default_factory=list)
    available: bool = True
    error: str | None = None
    # 27/07 -- Item #126: True only for a REAL network/rate-limit/server error
    # (429, timeout, 5xx) as opposed to a clean response with too few/no
    # candles. Default False keeps every existing caller's behavior
    # unchanged (they never read this field). Lets a multi-timeframe caller
    # (e.g. momentum_entry's Mobula 15m/30m scalping loop) stop escalating
    # immediately on a real error instead of retrying a doomed request at
    # the next timeframe -- same "stop, don't compound" principle already
    # applied inside OHLCVClient.get_ohlcv's own 1D/4H/1H ladder (Item #121).
    network_error: bool = False


def _as_float(value: object) -> float | None:
    """Never fabricates -- ``None`` on missing/unparseable, same doctrine as
    every other numeric parse in this module."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_trending_pool_item(
    item: object, included_by_id: dict[str, dict], network: str,
) -> TrendingPool | None:
    """Parses one ``data[]`` entry of ``get_trending_pools`` -- returns
    ``None`` (never raises) on a malformed row, same "skip, don't crash on one
    bad row" doctrine as ``get_pool_trades``'s own parsing loop."""
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes")
    if not isinstance(attrs, dict):
        return None
    pool_address = attrs.get("address")
    if not isinstance(pool_address, str) or not pool_address:
        return None

    price_usd = _as_float(attrs.get("base_token_price_usd"))

    raw_pct = attrs.get("price_change_percentage")
    price_change_pct: dict[str, float] = {}
    if isinstance(raw_pct, dict):
        for window, raw_value in raw_pct.items():
            parsed = _as_float(raw_value)
            if parsed is not None:
                price_change_pct[str(window)] = parsed

    transactions_m15: dict[str, int] | None = None
    raw_tx = attrs.get("transactions")
    if isinstance(raw_tx, dict) and isinstance(raw_tx.get("m15"), dict):
        transactions_m15 = {}
        for key, raw_value in raw_tx["m15"].items():
            try:
                transactions_m15[str(key)] = int(raw_value)
            except (TypeError, ValueError):
                continue
        if not transactions_m15:
            transactions_m15 = None

    volume_usd_m15 = None
    raw_volume = attrs.get("volume_usd")
    if isinstance(raw_volume, dict):
        volume_usd_m15 = _as_float(raw_volume.get("m15"))

    reserve_usd = _as_float(attrs.get("reserve_in_usd"))

    pool_created_at: datetime | None = None
    raw_created = attrs.get("pool_created_at")
    if isinstance(raw_created, str) and raw_created:
        try:
            pool_created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
        except ValueError:
            pool_created_at = None  # never fabricate -- an unparseable date stays None

    token_address: str | None = None
    symbol: str | None = None
    relationships = item.get("relationships")
    if isinstance(relationships, dict):
        base_token = relationships.get("base_token")
        if isinstance(base_token, dict):
            token_data = (base_token.get("data") or {}) if isinstance(base_token.get("data"), dict) else {}
            token_id = token_data.get("id")
            if isinstance(token_id, str):
                prefix = f"{network}_"
                token_address = token_id[len(prefix):] if token_id.startswith(prefix) else token_id
                included = included_by_id.get(token_id)
                if isinstance(included, dict):
                    included_attrs = included.get("attributes")
                    if isinstance(included_attrs, dict) and isinstance(included_attrs.get("symbol"), str):
                        symbol = included_attrs["symbol"]

    return TrendingPool(
        pool_address=pool_address,
        token_address=token_address,
        symbol=symbol,
        price_usd=price_usd,
        price_change_pct=price_change_pct,
        transactions_m15=transactions_m15,
        volume_usd_m15=volume_usd_m15,
        reserve_usd=reserve_usd,
        pool_created_at=pool_created_at,
    )


class GeckoTerminalClient:
    """Async HTTP client, read-only, conservative throttle (free public API).
    10/08 -- the throttle is now adaptive (see the module-level constants
    above ``_MIN_INTERVAL`` for the full doctrine): ``_min_interval`` stays
    the hard floor (never sped past automatically), ``_current_interval`` is
    the live pace, tightened fast on a real 429, eased back slowly on
    sustained success."""

    def __init__(
        self, base_url: str = BASE_URL, *, min_interval: float = _MIN_INTERVAL,
        outage_suspension_db_path: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        # 17/08, real incident -- default None keeps every existing call site's
        # exact prior behavior (shared prod suspension state). Only the
        # standalone shadow process's dedicated client passes its own
        # shadow_db_path() here so a shadow-only 429 streak can never arm a
        # suspension that also blocks prod's GeckoTerminal calls -- see
        # geckoterminal_outage_suspension.py's module docstring.
        self._outage_suspension_db_path = outage_suspension_db_path
        self._current_interval = min_interval
        self._consecutive_successes = 0
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._current_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def _record_rate_limit(self) -> None:
        """A real 429 -- tighten immediately (never wait for a threshold of
        consecutive failures, unlike the outage-bypass mechanisms elsewhere
        in this codebase: here the cost of tightening a bit too eagerly is
        just a slower scan, while the cost of NOT tightening fast enough is
        a sustained saturation risking a Cloudflare-level block)."""
        self._consecutive_successes = 0
        cap = self._min_interval * _MAX_INTERVAL_MULTIPLIER
        new_interval = min(self._current_interval * _RATE_LIMIT_BACKOFF_FACTOR, cap)
        if new_interval > self._current_interval:
            logger.warning(
                "geckoterminal: adaptive throttle tightened %.2fs -> %.2fs after a real 429 "
                "(floor %.2fs, cap %.2fs)",
                self._current_interval, new_interval, self._min_interval, cap,
            )
        self._current_interval = new_interval

    def _record_success(self) -> None:
        """Eases the pace back toward the floor, but only after a SUSTAINED
        run of successes and in small steps -- never below the floor, never
        a fast return to nominal (see the module-level doctrine comment)."""
        if self._current_interval <= self._min_interval:
            self._consecutive_successes = 0
            return
        self._consecutive_successes += 1
        if self._consecutive_successes < _CONSECUTIVE_SUCCESSES_BEFORE_EASING:
            return
        self._consecutive_successes = 0
        eased = max(self._current_interval * _EASE_STEP_FACTOR, self._min_interval)
        logger.info(
            "geckoterminal: adaptive throttle eased %.2fs -> %.2fs after %s consecutive successes",
            self._current_interval, eased, _CONSECUTIVE_SUCCESSES_BEFORE_EASING,
        )
        self._current_interval = eased

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
        """GET with retry on 5xx/timeout, but NO retry on 429 (08/08, real
        incident: prod logs showed near-continuous 429s across every
        GeckoTerminal call site, aggregate demand from every pocket running
        simultaneously exceeding the account's real ceiling -- a SUSTAINED
        saturation, not the isolated burst the #157/14/07 retry-once policy
        was designed for. Under sustained saturation a 429 retry almost never
        succeeds (the service is still busy) while still spending a real
        request against an already-exhausted quota -- pure amplification.
        Gives up on the FIRST 429 instead of the previous 3 attempts."""
        from aria_core import geckoterminal_outage_suspension

        if await geckoterminal_outage_suspension.is_suspended(self._outage_suspension_db_path):
            return None, f"{UNAVAILABLE} (suspension automatique GeckoTerminal, rate-limit sustained)"

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
                logger.warning("geckoterminal: HTTP 429 on %s after %s attempt(s)", url, attempt_429)
                self._record_rate_limit()
                await geckoterminal_outage_suspension.record_rate_limit_failure(self._outage_suspension_db_path)
                return None, f"{UNAVAILABLE} (rate limit GeckoTerminal)"

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

            self._record_success()
            await geckoterminal_outage_suspension.record_success(self._outage_suspension_db_path)
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

    async def get_pool_snapshot(self, pool_address: str, *, network: str = NETWORK) -> PoolSnapshot:
        """Current ``base_token_price_usd``/``reserve_in_usd`` for a pool --
        16/08, built for ``solana_pump_shadow.py``'s forward-outcome
        measurement (was the ``+25%/15min`` signal actually followed by a real
        gain N minutes/hours later). Same ``/networks/{network}/pools/{pool}``
        endpoint as ``get_pool_created_at`` (CONFIRMED LIVE 16/08 to also
        carry ``base_token_price_usd``/``reserve_in_usd`` on the SAME response
        -- verified against a real Base pool, not assumed), but a dedicated
        method/dataclass rather than widening ``get_pool_created_at`` itself
        (that one is on a live production hot path -- never touch it for an
        unrelated shadow-only need)."""
        data, error = await self._get_json(f"/networks/{network}/pools/{pool_address}")
        if error is not None:
            return PoolSnapshot(pool_address=pool_address, available=False, error=error)
        if not isinstance(data, dict):
            return PoolSnapshot(pool_address=pool_address, available=False, error=UNAVAILABLE)
        attrs = (data.get("data") or {}).get("attributes")
        if not isinstance(attrs, dict):
            return PoolSnapshot(pool_address=pool_address, available=False, error=UNAVAILABLE)
        price_usd = _as_float(attrs.get("base_token_price_usd"))
        if price_usd is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="prix indisponible")
        raw_change = attrs.get("price_change_percentage")
        raw_tx = attrs.get("transactions")
        raw_vol = attrs.get("volume_usd")
        return PoolSnapshot(
            pool_address=pool_address, price_usd=price_usd,
            reserve_usd=_as_float(attrs.get("reserve_in_usd")), available=True, error=None,
            price_change_pct={
                k: v for k, v in (
                    (k, _as_float(raw_change.get(k))) for k in ("m5", "m15", "m30", "h1", "h6", "h24")
                ) if v is not None
            } if isinstance(raw_change, dict) else {},
            transactions=(
                {k: v for k, v in raw_tx.items() if isinstance(v, dict)}
                if isinstance(raw_tx, dict) else {}
            ),
            volume_usd={
                k: v for k, v in (
                    (k, _as_float(raw_vol.get(k))) for k in ("m5", "m15", "m30", "h1", "h6", "h24")
                ) if v is not None
            } if isinstance(raw_vol, dict) else {},
        )

    async def get_all_time_high(self, pool_address: str, *, network: str = NETWORK) -> AthResult:
        """The real all-time-high price, scanned from the pool's FULL daily
        OHLCV history (never a guess, never limited to whatever window a
        caller happened to already fetch) -- see the module-level comment
        above ``_ATH_CANDLES_PER_PAGE`` for why this has to be built rather
        than consumed from a ready-made provider field. Paginates backward
        via ``before_timestamp`` until the page's oldest candle reaches (or
        passes) the pool's own creation date, or a page comes back thin
        (fewer candles than requested -- signals the true beginning of the
        series, same "less than requested = last page" signal every other
        paginated client in this codebase uses).

        A page that errors OUT (429, timeout, 5xx) after at least one
        successful page stops the scan and returns the ATH computed from
        history ALREADY gathered, rather than discarding real partial
        coverage -- an ATH from a partial-but-real history is still a
        genuine (if possibly understated) floor, never a fabricated number.
        Only fails closed (``available=False``) if not even the first page
        could be read."""
        created = await self.get_pool_created_at(pool_address, network=network)
        if not created.available or created.created_at is None:
            return AthResult(available=False, error=created.error or UNAVAILABLE)
        created_ts = int(created.created_at.timestamp())

        from aria_core.services.ohlcv import _parse_candles

        highest_price: float | None = None
        highest_at: datetime | None = None
        before_ts: int | None = None
        pages_scanned = 0
        scanned_until_ts: int | None = None

        for _page in range(_ATH_MAX_PAGES):
            params: dict[str, object] = {"aggregate": 1, "limit": _ATH_CANDLES_PER_PAGE}
            if before_ts is not None:
                params["before_timestamp"] = before_ts
            data, error = await self._get_json(f"/networks/{network}/pools/{pool_address}/ohlcv/day", params=params)
            if error is not None:
                if highest_price is None:
                    return AthResult(available=False, error=error)
                break

            candles = _parse_candles(data)
            if not candles:
                break
            pages_scanned += 1

            for c in candles:
                if highest_price is None or c.high > highest_price:
                    highest_price = c.high
                    highest_at = datetime.fromtimestamp(c.ts, tz=timezone.utc)

            oldest_ts = min(c.ts for c in candles)
            scanned_until_ts = oldest_ts
            if oldest_ts <= created_ts or len(candles) < _ATH_CANDLES_PER_PAGE:
                break
            before_ts = oldest_ts

        if highest_price is None:
            return AthResult(available=False, error=UNAVAILABLE)

        try:
            from aria_core import ath_shadow

            await ath_shadow.record_scan(
                pool_address, network,
                ath_price=highest_price, ath_at=highest_at,
                scanned_until_ts=scanned_until_ts, pages_scanned=pages_scanned,
            )
        except Exception as exc:  # noqa: BLE001 -- shadow logging must never affect a real scan
            logger.info("get_all_time_high: ath_shadow record failed (%s)", exc)

        return AthResult(ath_price=highest_price, ath_at=highest_at, available=True, error=None)

    async def get_pool_trades(
        self, pool_address: str, *, network: str = NETWORK, min_volume_usd: float | None = None,
    ) -> PoolTradesResult:
        """Latest trades (up to 300, past 24h) for a pool -- `kind` ("buy"/
        "sell"), `tx_from_address` (the actual wallet), `volume_usd`. Verified
        LIVE 13/08 against a real Base pool (this project's own throttled
        client, not a raw call) -- the real field is `kind`, not `type` as
        third-party docs claimed. Same dome doctrine as every other method
        here: any failure -> `available=False`, never an exception."""
        params: dict = {}
        if min_volume_usd is not None:
            params["trade_volume_in_usd_greater_than"] = min_volume_usd
        data, error = await self._get_json(f"/networks/{network}/pools/{pool_address}/trades", params=params)
        if error is not None:
            return PoolTradesResult(available=False, error=error)
        if not isinstance(data, dict):
            return PoolTradesResult(available=False, error=UNAVAILABLE)

        trades = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") or {}
            from_address = attrs.get("tx_from_address")
            kind = attrs.get("kind")
            if not isinstance(from_address, str) or not from_address:
                continue
            if not isinstance(kind, str) or not kind:
                continue
            try:
                volume = float(attrs.get("volume_in_usd") or 0.0)
            except (TypeError, ValueError):
                volume = 0.0
            trades.append(
                PoolTrade(
                    tx_from_address=from_address, kind=kind, volume_usd=volume,
                    block_timestamp=str(attrs.get("block_timestamp") or ""),
                )
            )
        return PoolTradesResult(trades=trades, available=True, error=None)

    async def get_trending_pools(
        self, *, network: str = NETWORK, duration: str = "5m",
    ) -> TrendingPoolsResult:
        """Pools GeckoTerminal itself ranks as trending on ``network`` -- 16/08,
        first consumer is ``solana_pump_shadow.py``'s out-of-sample "take the
        train" validation (shadow-only, read+log, never a trigger -- see that
        module's own docstring). Uses ``self._get_json`` like every other
        method here, so the SAME shared adaptive throttle/dome applies (16/08
        norm re-verified: this is the endpoint a NEW GeckoTerminal consumer
        must always go through, never a second independent client).

        ``duration`` picks which window GeckoTerminal itself sorts the
        trending list by -- **verified live 16/08 against the real API**
        (``GET .../base/trending_pools?duration=15m`` -> HTTP 400 `"Invalid
        duration. Allowed values: 5m, 1h, 6h, 24h"`): only those 4 values are
        valid, ``"15m"``/``"30m"`` are NOT (despite an earlier, unverified
        assumption that ``15m`` had already been confirmed working -- it had
        not; re-verifying against the real API rather than trusting that
        claim is exactly the "verify before asserting" doctrine this project
        already commits to). This does not limit which price-change window can
        be READ: every returned pool's ``attributes.price_change_percentage``
        always carries all 6 windows (m5/m15/m30/h1/h6/h24) regardless of
        ``duration`` -- confirmed live in the same test, ``duration`` only
        changes which pools GeckoTerminal considers "trending enough" to
        return, not the shape of each pool's own data. Default ``"5m"`` (the
        shortest valid value, closest to a fast-momentum use case).

        Response shape (CONFIRMED LIVE 16/08, ``GET
        /api/v2/networks/base/trending_pools?duration=5m&include=base_token``,
        HTTP 200, 20 pools + 12 included tokens -- schema also cross-checked
        against CoinGecko's own published Pro-API-mirror docs, which serve the
        identical GeckoTerminal onchain data): each pool's ``attributes``
        holds ``address`` (the POOL, not the token), ``base_token_price_usd``,
        ``price_change_percentage``/``transactions``/``volume_usd`` (each a
        dict keyed by window), ``reserve_in_usd``. The token's own
        ``symbol``/``address`` are NOT inline on the pool -- they live in the
        top-level ``included`` array (``include=base_token`` query param),
        matched by ``relationships.base_token.data.id`` (``"<network>_<addr>"``
        -- the network prefix is stripped using the REQUESTED network, never a
        naive first-underscore split, since some GeckoTerminal slugs
        themselves contain an underscore, e.g. ``polygon_pos``)."""
        params: dict[str, object] = {"duration": duration, "include": "base_token"}
        data, error = await self._get_json(f"/networks/{network}/trending_pools", params=params)
        if error is not None:
            return TrendingPoolsResult(available=False, error=error)
        if not isinstance(data, dict):
            return TrendingPoolsResult(available=False, error=UNAVAILABLE)

        included_by_id: dict[str, dict] = {}
        for inc in data.get("included") or []:
            if isinstance(inc, dict) and isinstance(inc.get("id"), str):
                included_by_id[inc["id"]] = inc

        pools: list[TrendingPool] = []
        for item in data.get("data") or []:
            parsed = _parse_trending_pool_item(item, included_by_id, network)
            if parsed is not None:
                pools.append(parsed)
        return TrendingPoolsResult(pools=pools, available=True, error=None)

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
        mode: str = "standard",
        skip_daily: bool = False,
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
        ``mode`` (Item #101, 26/07): ``"scalping"`` is passed through to reach
        ``services/ohlcv.py``'s dedicated 15min/30min sub-hour ladder --
        default ``"standard"`` is the original day/4h/1h ladder, unchanged
        behavior for every existing caller. ``skip_daily`` (#157, revived
        08/02) is passed through to ``services/ohlcv.py`` -- ``False`` by
        default, unchanged behavior for every existing caller. ``**_kwargs``
        absorbs any inherited period/aggregate/limit (no caller in production
        currently passes them) without raising.

        26/07 -- real gap found while adding Ethereum to the momentum
        pipeline's ``DEFAULT_CHAINS``: ``GECKO_NETWORK_SLUGS`` existed since
        14/07 (#157) but was NEVER actually applied anywhere in the codebase
        (grepped, zero consumers) -- every caller silently relied on the ARIA
        chain name already matching GeckoTerminal's own slug, true only for
        ``"base"`` by coincidence. GeckoTerminal's real slug for Ethereum is
        ``"eth"``, not ``"ethereum"`` (verified live, GET
        ``/api/v2/networks``) -- without this translation, every Ethereum
        OHLCV lookup would have hit a nonexistent network path and silently
        starved every candidate on ``ohlcv_unavailable``. Applied HERE (the
        one place every caller funnels through), never at each call site."""
        from aria_core.services.ohlcv import ohlcv_client as _wide_ohlcv_client

        extra: dict[str, object] = {}
        if min_useful_candles is not None:
            extra["min_useful_candles"] = min_useful_candles

        gecko_network = GECKO_NETWORK_SLUGS.get(network, network)
        wide = await _wide_ohlcv_client.get_ohlcv(
            pool_address, network=gecko_network, mode=mode, skip_daily=skip_daily, **extra
        )
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
