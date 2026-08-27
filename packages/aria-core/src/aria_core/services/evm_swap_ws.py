"""Generic EVM swap price feed via native websocket push (24/08, operator-
directed: "tu me construit quelque chose de solide qui peut bombarder en
req/s et sans erreur 429" -- after GeckoTerminal's shared throttle got
tightened live by a real 429 the same session, and after this dome's own
"nominal vs realistic" incident showed a rug's own peak/entry price can be
up to 36s stale through an aggregator's own indexing delay).

**What this replaces**: GeckoTerminal/DexPaprika/DexScreener REST polling
for the exit-tracking leg of the Base/Robinhood shadow pockets
(``advance_exit_simulation``'s ``get_pool_snapshot``/``get_ohlcv`` calls).
Those read an AGGREGATOR's own indexed copy of the chain, always at least
one indexing cycle behind. This module instead subscribes directly to the
pool CONTRACT's own ``Sync``/``Swap`` event via ``eth_subscribe("logs")`` --
the price the chain itself just settled, pushed the moment the block lands,
no aggregator in between. Same architectural move already proven on Solana
(``services/pumpswap_ws.py``, 413ms median vs 60s REST polling, 19/08) --
this is the EVM-side counterpart, generic across any EVM chain (chain_id
verified per network, e.g. Base=8453/Robinhood=4663), NOT chain-specific
(operator-directed 24/08: "c'est un websocket evm partage par robinhood et
base et d'autre peut etre plus tard").

**One shared connection per chain, dynamic subscription** (never one
connection per pool, same doctrine as pumpswap_ws.py): ``add_pool``/
``remove_pool`` update a single ``eth_subscribe("logs", ...)`` filter's
address list live. A v2/v3-style pool is its own contract (subscribed by
its own ``pool_address``); a v4-style pool is NOT a separate contract --
Uniswap v4 pools all live inside one shared ``PoolManager`` singleton
(``POOL_MANAGER_ADDRESS``, imported from ``doppler.py`` rather than
reimplemented -- same address already proven live there) and are
distinguished only by an indexed ``poolId`` topic, so v4 subscription
filters on the PoolManager's fixed address + a growing list of poolId
topics instead.

**Coverage, measured empirically before writing a single decoder** (24/08,
Base's own 25-pool trending sample via DexPaprika, never assumed): dex_id
split uniswap_v4=8, uniswap_v2=5, uniswap_v3=4, aerodrome=4,
aerodrome_v3=3, aerodrome_slipstream_3=1 -- no single family dominates.
This module covers uniswap_v2 (``Sync`` event) and uniswap_v3/uniswap_v4
(``Swap`` event, ``sqrtPriceX96`` via ``doppler.price_from_sqrt_price_x96``,
reused not duplicated) -- 68% of that sample. Aerodrome variants are
Solidly/Uniswap-v3 FORKS, not verified live to share the exact same event
signature/decimals convention -- deliberately left unmapped
(``get_snapshot`` returns ``available=False``) rather than assumed
compatible; a caller always falls back to the existing REST cascade for
those, exactly as pumpswap_ws.py's callers already do for non-PumpSwap
pools. Extend ``_DEX_FAMILY`` once verified, never guess a new mapping in.

**USD pricing, honestly scoped** (same doctrine as pumpswap_ws.py's
WSOL-only USD coverage): a raw on-chain price is always "quote units per
token", decimal-adjusted -- never USD by itself. This module resolves USD
only when the quote leg is WETH (via ``doppler.eth_usd_rate()``, already
proven, never reimplemented) or a known USD stablecoin (USDC/USDbC, 1:1).
Any other quote leg leaves ``price_usd=None`` -- ``price_quote`` (the raw
ratio) is still populated, but the caller must treat ``price_usd is None``
as "no USD read available here", never fabricate one.

**Auto-verification, never trust a decode blindly**: every subscribed pool
is checked once (fetching ``token0()``/``token1()`` for v2/v3, or the
``Initialize`` event's ``currency0``/``currency1`` for v4 -- same call
``doppler.find_pool`` already makes) before being marked coverable, so a
mis-ordered currency0/currency1 assumption never silently produces a wrong
price (same class of bug as doppler.py's own CLOWNS incident, 07/24).

**Fail-open by design**: a decode failure, an unmapped dex_id, or a
disconnected websocket all resolve to ``available=False`` -- this module
never blocks a shadow pocket's cycle and never fabricates a price. Callers
keep their existing REST fallback (DexPaprika/GeckoTerminal) unconditionally;
this is a latency upgrade layered on top, never a single point of failure.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from aria_core.services import chainstack_ru_budget
from aria_core.services.doppler import POOL_MANAGER_ADDRESS, price_from_sqrt_price_x96

logger = logging.getLogger(__name__)

# How long a pool's price-tick history is kept for window-high/window-low
# reconstruction (mirrors the 5min scalping candle window advance_exit_
# simulation already reads from GeckoTerminal -- widened slightly to 10min
# so a caller asking for a wider window than 5min is never starved).
_TICK_HISTORY_SECONDS = 600.0

# Reconnect backoff -- same shape as every other long-running feed in this
# dome (doubling, capped), never a tight retry loop against a real RPC.
_RECONNECT_MIN_SECONDS = 1.0
_RECONNECT_MAX_SECONDS = 30.0

# 24/08 -- mid-day circuit breaker cadence. add_pool()'s own can_spend()
# check only stops the leak from GROWING (refuses new pools); it never
# touches pools already subscribed before the cap was hit, because a push
# already arriving is already billed by the time _handle_notification runs
# (see its own comment) -- there is no "skip this one" lever on the hot
# path. This separate, cheap ticker is the real stop: it unsubscribes every
# already-tracked pool the moment the daily cap is reached, and re-subscribes
# them automatically once the next calendar day resets the budget. 30s, not
# on every notification: can_spend() already carries its own 5s read cache
# (chainstack_ru_budget.py), so this costs nothing extra to poll this
# often, and 30s is fast enough that a breached cap is caught within a
# single digit number of pushes' worth of overrun, never a whole day.
_BREAKER_CHECK_INTERVAL_SECONDS = 30.0

# 25/08 -- proactive counterpart to the budget breaker: closes the newHeads
# keepalive once _pools has sat empty this long, rather than waiting for the
# daily RU cap to already be blown. Short enough to matter (on Robinhood's
# ~100ms blocks, even 2 minutes idle is ~72k RU that this now avoids
# entirely), long enough that a position closing and a new one opening
# moments later doesn't flap the keepalive open/closed for no real saving.
_IDLE_NEWHEADS_CLOSE_SECONDS = 120.0

# 27/08, real gap found live: both sets were Base-only, silently applied to
# Robinhood notifications too (this class and onchain_pool_discovery.py both
# reuse the same global constants for every chain, `self.chain` never
# consulted here) -- confirmed via a 24h prod sample that this made
# onchain_pool_discovery reject 100% of Robinhood's own accountNotification
# traffic (1095/1095 in one clean window), never a partial miss like Base's.
# Robinhood addresses verified live against robinhoodchain.blockscout.com's
# own token listing (never guessed): WETH is the dominant pairing token by a
# wide margin (505,469 holders, decimals=18, exchange_rate ~2498$ matching
# real ETH price), USDG (Global Dollar) the dominant stablecoin (104,054
# holders, decimals=6) -- both orders of magnitude ahead of the next
# candidates (USDE at 4,804 holders). Combining both chains into these same
# two global sets (rather than a per-chain lookup) is intentional and safe:
# addresses are globally unique strings, no collision risk between chains.
_KNOWN_USD_STABLES = frozenset({
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (Base)
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC (Base, bridged)
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG / Global Dollar (Robinhood Chain)
})
_WETH_ADDRESSES = frozenset({
    "0x4200000000000000000000000000000000000006",  # WETH (Base, canonical)
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH (Robinhood Chain, canonical)
})
# 27/08 -- confirmed real, active quote token on Base via GeckoTerminal's live
# top-pools listing (genuine third-party tokens quoted directly against it,
# not just widely held) -- see doppler.btc_usd_rate()'s own docstring for the
# full verification. Kept SEPARATE from _WETH_ADDRESSES on purpose: a BTC-
# quoted pool needs doppler.btc_usd_rate(), never eth_usd_rate() -- merging
# the sets would silently apply the wrong asset's price.
_CBBTC_ADDRESSES = frozenset({
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",  # cbBTC (Base, Coinbase-issued canonical)
})

# Event topic0 hashes, computed live (never hand-typed/guessed) so a typo
# can never silently produce a filter that matches nothing.
def _topic0(signature: str) -> str:
    from web3 import Web3

    return "0x" + Web3.keccak(text=signature).hex()


_SYNC_TOPIC = _topic0("Sync(uint112,uint112)")
# Aerodrome's classic (volatile) Pool.sol emits Sync with uint256 reserves,
# not Uniswap V2's uint112 -- a DIFFERENT topic0 (signature text differs),
# but the SAME binary layout (both ABI-encode to 32 bytes per value), so it
# is routed to the identical _handle_sync decoder, never a separate one.
# Verified live against aerodrome-finance/contracts IPool.sol (24/08),
# never assumed compatible with Uniswap V2's signature.
_SYNC_TOPIC_AERODROME = _topic0("Sync(uint256,uint256)")
_V3_SWAP_TOPIC = _topic0("Swap(address,address,int256,int256,uint160,uint128,int24)")
# Aerodrome Slipstream's CLPool emits the EXACT SAME Swap shape as Uniswap
# V3 (verified live against aerodrome-finance/slipstream's ICLPoolEvents.sol,
# 24/08: sender/recipient indexed, same non-indexed field order/types) --
# same topic0, no separate constant needed, routed through _V3_SWAP_TOPIC.
# v4's PoolManager emits a DIFFERENT Swap shape (id is indexed, sender is
# indexed) -- verified against the same Uniswap v4 core ABI doppler.py's
# own Initialize-event handling already relies on.
_V4_SWAP_TOPIC = _topic0(
    "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"
)

# 25/08, real bug found live: this module always subscribed to Base's own
# PoolManager (``POOL_MANAGER_ADDRESS``, imported from doppler.py, itself
# explicitly documented as "Canonical Base addresses" -- never intended for
# any other chain) regardless of ``self.chain``. On Robinhood this silently
# pointed every v4 subscription at the WRONG contract -- no v4 Swap event was
# ever received for a Robinhood v4 pool, degrading (fail-open, no crash) to
# the REST fallback for every such position. Robinhood's own PoolManager
# address below was verified live against the official Uniswap developer
# docs and confirmed by actually receiving real Initialize/Swap events on it
# (specs/005-discovery-budget T002's measurement, 25/08).
_POOL_MANAGER_BY_CHAIN: dict[str, str] = {
    "base": POOL_MANAGER_ADDRESS,
    "robinhood": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
}

# dex_id (as DexPaprika/GeckoTerminal report it) -> decode family. Anything
# not listed here is honestly uncovered, never guessed into a family.
_DEX_FAMILY: dict[str, str] = {
    "uniswap_v2": "v2",
    "uniswap_v3": "v3",
    "uniswap_v4": "v4",
    # Aerodrome (Base's largest DEX by volume, $596M/24h vs Uniswap V3's
    # $283M -- verified live via DefiLlama's /overview/dexs/base, 24/08).
    # Slipstream (CLPool) verified live to emit the EXACT SAME Swap event
    # as Uniswap V3 (aerodrome-finance/slipstream's ICLPoolEvents.sol) --
    # routed through the same "v3" family, zero new decoder needed.
    "aerodrome_slipstream_3": "v3",
    "aerodrome_v3": "v3",
    # Classic (volatile-pool) Aerodrome routes through "v2" -- same binary
    # layout as Uniswap V2's Sync despite a different topic0 (uint256 vs
    # uint112 reserves, see _SYNC_TOPIC_AERODROME above). ONLY covers
    # volatile pools (stable=false) -- Aerodrome's "stable" pools use a
    # different (stableswap curve) pricing formula this decoder does NOT
    # implement; add_pool refuses a stable pool rather than compute a wrong
    # price (see _add_pool_v2v3's aerodrome stable check).
    "aerodrome": "v2",
}


@dataclass
class EVMSwapSnapshot:
    """Read-only view of a tracked pool's latest known state. ``available``
    is the caller's unambiguous fallback signal -- mirrors
    ``PumpSwapLiveSnapshot`` on the Solana side, deliberately the same
    shape so a caller injecting either feed follows one contract."""

    available: bool
    price_quote: float | None = None  # quote-currency units per token
    price_usd: float | None = None  # None unless the quote leg resolves to USD
    window_high_quote: float | None = None
    window_low_quote: float | None = None
    last_update_at: float | None = None  # time.monotonic() of the last tick
    stale_seconds: float | None = None
    # 24/08, operator-directed ("uniswap devrait te la donner si tu extrait
    # le ca") -- both fields below were ALREADY present in every decoded
    # event, just discarded until now, zero extra RPC call:
    # - reserve_usd: exact, from v2's own Sync(reserve0, reserve1) -- only
    #   populated when the quote leg resolves to USD (WETH via
    #   doppler.eth_usd_rate(), or a known stablecoin 1:1), same honesty
    #   rule as price_usd. None for v3/v4 (concentrated liquidity has no
    #   single "total reserve" figure).
    # - raw_liquidity: v3/v4's own `L` param from the Swap event itself
    #   (uint128, offset 96:128) -- an abstract, non-USD unit (active
    #   liquidity at the current tick), but directly comparable POOL TO
    #   POOL as a depth proxy without any further RPC call. None for v2
    #   (use reserve_usd there instead, which IS exact).
    reserve_usd: float | None = None
    raw_liquidity: float | None = None
    # 24/08, operator-directed ("tu aurai du pensai a capturer tous se qui
    # est utile") -- also already in every decoded event, zero extra RPC:
    swap_count: int = 0  # real per-event count (undercounted by any 2s-
    # polling caller between reads, but exact from the feed's own side)
    cumulative_volume_quote: float = 0.0  # sum of |amount_quote| since add_pool()
    distinct_traders_count: int = 0  # v3/v4 only, 0 for v2 (see _TrackedPool)
    # 24/08 -- lets a caller resolve price_usd itself when the quote leg is
    # WETH (price_usd stays None here per the honesty rule above, this flag
    # is the caller's cue to multiply price_quote by doppler.eth_usd_rate()
    # rather than silently treating an unresolved WETH-quoted pool the same
    # as a genuinely un-priceable one).
    quote_is_weth: bool = False
    # 27/08 -- same cue as quote_is_weth above, but for a cbBTC-quoted pool:
    # the caller must multiply price_quote by doppler.btc_usd_rate(), never
    # eth_usd_rate() (BTC and ETH trade at very different USD levels).
    quote_is_btc: bool = False


@dataclass
class _TrackedPool:
    dex_id: str
    family: str
    token_is_currency0: bool
    decimals0: int
    decimals1: int
    quote_is_weth: bool
    quote_is_stable: bool
    quote_is_btc: bool = False
    ticks: deque = field(default_factory=deque)  # [(monotonic_time, price_quote)]
    pool_id_hex: str | None = None  # v4 only, the topic-filtered poolId
    last_reserve_usd: float | None = None  # v2 only, exact when quote_is_stable
    last_raw_liquidity: float | None = None  # v3/v4 only, abstract depth proxy
    # 24/08, operator-directed ("tu aurai du pensai a capturer tous se qui
    # est utile") -- also already in every decoded event, zero extra RPC:
    swap_count: int = 0  # real per-event count, distinct from _log_tick's
    # 2s-polled snapshot count (which undercounts bursts between polls)
    cumulative_volume_quote: float = 0.0  # sum of |amount_quote| per swap
    distinct_traders: set = field(default_factory=set)  # v3/v4 only, from
    # the Swap event's own indexed `sender` -- V2's Sync event carries no
    # sender (that is a SEPARATE Swap event this module does not decode),
    # so V2 pools never populate this, honestly left empty rather than
    # approximated.


class EVMSwapWebSocketFeed:
    """One instance per chain -- construct once, share across every shadow
    pocket on that chain (operator-directed: one shared websocket, not one
    per pocket/pool)."""

    def __init__(self, *, chain: str, ws_url: str, chain_id: int) -> None:
        self.chain = chain
        self._ws_url = ws_url
        self._chain_id = chain_id
        self._pools: dict[str, _TrackedPool] = {}  # key: lowercase pool_address (v4: poolId hex)
        self._w3 = None  # AsyncWeb3, connected lazily by _run
        self._task: asyncio.Task | None = None
        self._breaker_task: asyncio.Task | None = None
        self._stopped = False
        self._connected = False
        # 24/08 -- mid-day circuit breaker state. When the daily RU budget is
        # exhausted, every currently-tracked pool moves from _pools into
        # here (unsubscribed, in-memory state kept so restoring it later
        # needs no fresh on-chain verification/RPC calls -- these pools were
        # already verified once). Moves back the moment the budget resets.
        self._breaker_open = False
        self._evicted_pools: dict[str, _TrackedPool] = {}
        # 25/08 -- proactive twin of the budget breaker above. That one only
        # ever reacts AFTER the daily cap is already blown; this tracks how
        # long _pools has sat empty so the newHeads keepalive can be closed
        # BEFORE it ever costs anything, regardless of the budget. Confirmed
        # safe to close mid-flight (see _run()'s own comment): web3.py's
        # process_subscriptions() only exits early if there is nothing to
        # listen to the MOMENT it starts iterating, never because a
        # subscription active at that time is later torn down -- so this
        # never risks the reconnect-storm the keepalive was built to avoid.
        self._pools_empty_since: float | None = time.monotonic()
        # 24/08 real incident: _resubscribe() created a fresh "logs"
        # subscription on every add_pool()/remove_pool() WITHOUT ever closing
        # the previous one (the docstring claimed "unsubscribes and
        # resubscribes fresh" -- never actually implemented). Adding ~100
        # pools one at a time, as the early-discovery experiment did, left
        # ~100 overlapping subscriptions alive on the same connection, each
        # separately re-delivering every matching event -- a combinatorial
        # multiplier on top of the v4 fan-out bug below. Track the active
        # subscription ids so a resubscribe can close them first.
        self._active_sub_ids: list[str] = []
        # 24/08 -- separate from _active_sub_ids (logs subscriptions, closed
        # and reissued together on every pool-set change). newHeads is a
        # keepalive ONLY: see _resubscribe()'s docstring for why it must be
        # open exactly while _active_sub_ids is empty, never otherwise.
        self._newheads_sub_id: str | None = None

    # -- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run())
        self._breaker_task = asyncio.create_task(self._breaker_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 -- best-effort shutdown
                pass
            self._task = None
        if self._breaker_task is not None:
            self._breaker_task.cancel()
            try:
                await self._breaker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 -- best-effort shutdown
                pass
            self._breaker_task = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def breaker_open(self) -> bool:
        return self._breaker_open

    async def _breaker_loop(self) -> None:
        while not self._stopped:
            try:
                await self._check_budget_circuit_breaker()
            except Exception as exc:  # noqa: BLE001 -- never let this ticker die the feed
                logger.info("evm_swap_ws[%s]: breaker check failed (%s)", self.chain, exc)
            try:
                await self._check_idle_newheads()
            except Exception as exc:  # noqa: BLE001 -- never let this ticker die the feed
                logger.info("evm_swap_ws[%s]: idle newHeads check failed (%s)", self.chain, exc)
            await asyncio.sleep(_BREAKER_CHECK_INTERVAL_SECONDS)

    async def _check_idle_newheads(self) -> None:
        """25/08 -- _resubscribe() only ever re-evaluates idle_too_long when
        add_pool()/remove_pool() calls it; with nothing happening at all
        (the exact idle state this exists for), nothing would ever trigger
        that re-evaluation on its own. This periodic check is what actually
        closes the keepalive once the idle window elapses, proactively,
        never waiting for the daily budget to blow first."""
        if self._newheads_sub_id is None or self._pools_empty_since is None:
            return  # nothing open to close, or not idle at all right now
        if time.monotonic() - self._pools_empty_since >= _IDLE_NEWHEADS_CLOSE_SECONDS:
            await self._resubscribe()

    async def _check_budget_circuit_breaker(self) -> None:
        """25/08, real bug found live (a 295k/200k daily overshoot on
        Robinhood the breaker never once caught -- ``CIRCUIT BREAKER OPEN``
        never appeared in production logs despite ``can_spend`` reading
        False for hours): the original ``and self._pools`` guard meant the
        breaker only ever fired while at least one pool was actively
        tracked. The newHeads keepalive (see ``_resubscribe()``'s own
        docstring) runs precisely when ``_pools`` is EMPTY -- exactly the
        state this guard skipped -- so on a fast chain like Robinhood
        (~100ms blocks, ~36k RU/hour for the keepalive alone) the single
        biggest cost was never something this breaker could touch. Now opens
        on ``not spendable`` alone, evicting whatever pools exist (zero, one,
        or many) and letting ``_resubscribe()`` -- itself updated to respect
        ``_breaker_open`` -- close the keepalive too instead of reopening it."""
        spendable = await chainstack_ru_budget.can_spend(self.chain)
        if not spendable and not self._breaker_open:
            self._breaker_open = True
            self._evicted_pools = self._pools
            self._pools = {}
            await self._resubscribe()
            logger.warning(
                "evm_swap_ws[%s]: CIRCUIT BREAKER OPEN -- daily RU budget exhausted, "
                "unsubscribed %d pool(s) and the newHeads keepalive, REST fallback "
                "takes over until the daily reset",
                self.chain, len(self._evicted_pools),
            )
        elif spendable and self._breaker_open:
            restored = len(self._evicted_pools)
            self._pools = self._evicted_pools
            self._evicted_pools = {}
            self._breaker_open = False
            await self._resubscribe()
            logger.warning(
                "evm_swap_ws[%s]: CIRCUIT BREAKER CLOSED -- budget reset, restored %d pool(s)",
                self.chain, restored,
            )

    # -- subscription management ---------------------------------------

    def dex_family(self, dex_id: str | None) -> str | None:
        return _DEX_FAMILY.get(dex_id or "")

    def _pool_manager_address(self) -> str:
        """Per-chain PoolManager -- see ``_POOL_MANAGER_BY_CHAIN``'s own
        docstring for the real bug this replaces. Falls back to Base's
        address for an unrecognized chain, the historical (buggy but at
        least consistent) behaviour, rather than raising."""
        return _POOL_MANAGER_BY_CHAIN.get(self.chain, POOL_MANAGER_ADDRESS)

    async def add_pool(
        self, pool_address: str, *, dex_id: str, token_address: str,
        decimals0: int | None = None, decimals1: int | None = None,
    ) -> bool:
        """Registers a pool for live tracking. Returns ``False`` (no error
        raised) when the dex_id is not covered or the on-chain verification
        fails -- the caller's cue to stay on its REST fallback, never a
        reason to interrupt its own cycle.

        ``decimals0``/``decimals1`` are optional overrides -- 24/08, real gap
        found while wiring this into base_momentum_shadow.py: the previous
        18/18 default silently mispriced any pool where the tracked token
        (a fresh, unpredictable meme token) does NOT use the near-universal
        18-decimal convention, by a power-of-10 factor. Left unset, both
        sides are now fetched on-chain (this module's own "never trust a
        decode blindly" doctrine, already applied to token0/token1
        ordering) -- pass an explicit value only to skip that RPC round-trip
        when the caller already knows it for certain."""
        family = self.dex_family(dex_id)
        if family is None:
            return False
        if self._w3 is None:
            return False  # not connected yet -- caller retries next cycle
        # 24/08 -- refuses to grow the subscription further once this
        # chain's daily RU budget is spent (chainstack_ru_budget.py). Does
        # NOT unsubscribe already-tracked pools -- a push already arriving
        # is already billed regardless (see _handle_notification's own
        # comment) -- this only stops the leak from getting worse. The
        # caller's REST fallback still prices this pool normally.
        if not await chainstack_ru_budget.can_spend(self.chain):
            return False
        try:
            if family == "v4":
                return await self._add_pool_v4(pool_address, token_address, dex_id, decimals0, decimals1)
            return await self._add_pool_v2v3(
                pool_address, token_address, dex_id, family, decimals0, decimals1,
            )
        except Exception as exc:  # noqa: BLE001 -- verification failure = uncovered, not a crash
            logger.info("evm_swap_ws[%s]: add_pool verify failed for %s (%s)", self.chain, pool_address, exc)
            return False

    async def _fetch_decimals(self, token_address: str) -> int:
        """Real ERC20 decimals() call -- falls back to 18 (the near-universal
        convention) only on failure, never blocks add_pool over it."""
        try:
            checksum = self._w3.to_checksum_address(token_address)
            contract = self._w3.eth.contract(address=checksum, abi=_MINIMAL_DECIMALS_ABI)
            return int(await contract.functions.decimals().call())
        except Exception as exc:  # noqa: BLE001 -- best-effort, 18 is the safe fallback
            logger.info("evm_swap_ws[%s]: decimals() failed for %s, defaulting to 18 (%s)",
                        self.chain, token_address, exc)
            return 18

    async def _add_pool_v2v3(
        self, pool_address: str, token_address: str, dex_id: str, family: str,
        decimals0: int | None, decimals1: int | None,
    ) -> bool:
        key = pool_address.lower()
        if key in self._pools:
            return True
        checksum = self._w3.to_checksum_address(pool_address)
        contract = self._w3.eth.contract(address=checksum, abi=_MINIMAL_TOKEN01_ABI)
        token0 = (await contract.functions.token0().call()).lower()
        token1 = (await contract.functions.token1().call()).lower()
        tracked_token = token_address.lower()
        if tracked_token not in (token0, token1):
            logger.info(
                "evm_swap_ws[%s]: %s tracked token %s matches neither token0 %s nor token1 %s -- refused",
                self.chain, pool_address, tracked_token, token0, token1,
            )
            return False
        token_is_currency0 = tracked_token == token0
        quote = token1 if token_is_currency0 else token0
        if decimals0 is None:
            decimals0 = await self._fetch_decimals(token0)
        if decimals1 is None:
            decimals1 = await self._fetch_decimals(token1)
        self._pools[key] = _TrackedPool(
            dex_id=dex_id, family=family, token_is_currency0=token_is_currency0,
            decimals0=decimals0, decimals1=decimals1,
            quote_is_weth=quote in _WETH_ADDRESSES, quote_is_stable=quote in _KNOWN_USD_STABLES,
            quote_is_btc=quote in _CBBTC_ADDRESSES,
        )
        await self._resubscribe()
        return True

    async def _add_pool_v4(
        self, pool_id_hex: str, token_address: str, dex_id: str,
        decimals0: int | None, decimals1: int | None,
    ) -> bool:
        # v4 pools carry no separate contract to introspect token0/token1
        # from -- the caller (which already resolved this pool via
        # doppler.find_pool or an equivalent Initialize-event lookup) is
        # trusted to pass the correct token_is_currency0 read from that same
        # event, never re-derived here.
        key = pool_id_hex.lower()
        if key in self._pools:
            return True
        # v4 has no shared PoolManager contract to introspect either side
        # from -- unlike v2/v3, decimals are NOT auto-fetched here, same
        # trust-the-caller posture this function already applies to
        # token_is_currency0 (see comment above). Falls back to 18/18 when
        # unset, same as before this module's v2/v3 auto-fetch was added.
        self._pools[key] = _TrackedPool(
            dex_id=dex_id, family="v4",
            token_is_currency0=token_address == "currency0",
            decimals0=decimals0 if decimals0 is not None else 18,
            decimals1=decimals1 if decimals1 is not None else 18,
            quote_is_weth=False, quote_is_stable=False, pool_id_hex=pool_id_hex,
        )
        await self._resubscribe()
        return True

    async def remove_pool(self, pool_address_or_id: str) -> None:
        key = pool_address_or_id.lower()
        removed_active = self._pools.pop(key, None) is not None
        # 24/08 -- also drop it from the breaker's evicted set: a position
        # that closes while the circuit breaker is open must not sit around
        # to be pointlessly re-subscribed once the budget resets.
        self._evicted_pools.pop(key, None)
        if removed_active:
            await self._resubscribe()

    # -- snapshot --------------------------------------------------------

    def get_snapshot(self, pool_address_or_id: str, *, window_seconds: float = 300.0) -> EVMSwapSnapshot:
        pool = self._pools.get(pool_address_or_id.lower())
        if pool is None or not pool.ticks or not self._connected:
            return EVMSwapSnapshot(available=False)
        now = time.monotonic()
        cutoff = now - window_seconds
        window = [p for t, p in pool.ticks if t >= cutoff]
        if not window:
            return EVMSwapSnapshot(available=False)
        last_t, last_price = pool.ticks[-1]
        price_usd = None
        if pool.quote_is_stable:
            price_usd = last_price
        elif pool.quote_is_weth:
            price_usd = None  # resolved by the caller via doppler.eth_usd_rate() -- no network I/O here
        elif pool.quote_is_btc:
            price_usd = None  # resolved by the caller via doppler.btc_usd_rate() -- no network I/O here
        return EVMSwapSnapshot(
            available=True, price_quote=last_price, price_usd=price_usd,
            window_high_quote=max(window), window_low_quote=min(window),
            last_update_at=last_t, stale_seconds=now - last_t,
            reserve_usd=pool.last_reserve_usd, raw_liquidity=pool.last_raw_liquidity,
            swap_count=pool.swap_count, cumulative_volume_quote=pool.cumulative_volume_quote,
            distinct_traders_count=len(pool.distinct_traders),
            quote_is_weth=pool.quote_is_weth, quote_is_btc=pool.quote_is_btc,
        )

    # -- websocket loop ----------------------------------------------------

    async def _run(self) -> None:
        from web3 import AsyncWeb3
        from web3.providers.persistent import WebSocketProvider

        backoff = _RECONNECT_MIN_SECONDS
        while not self._stopped:
            try:
                async with AsyncWeb3(WebSocketProvider(self._ws_url)) as w3:
                    self._w3 = w3
                    self._connected = True
                    # A fresh connection invalidates every subscription id
                    # from the previous one -- _resubscribe()'s unsubscribe
                    # pass would otherwise try (harmlessly, but pointlessly)
                    # to close ids that no longer exist on this new socket.
                    self._active_sub_ids = []
                    self._newheads_sub_id = None
                    backoff = _RECONNECT_MIN_SECONDS
                    logger.info("evm_swap_ws[%s]: connected", self.chain)
                    # Real bug found live 24/08: web3.py's process_subscriptions()
                    # generator exits immediately if there is no active
                    # subscription the moment it starts iterating -- add_pool()
                    # racing in from the outside to create the real logs
                    # subscription arrives too late, the socket has already
                    # closed. _resubscribe() itself opens a newHeads keepalive
                    # for exactly this empty-pools window (see its docstring),
                    # so a single call here covers the cold start too.
                    await self._resubscribe()
                    async for payload in w3.socket.process_subscriptions():
                        if self._stopped:
                            break
                        self._handle_notification(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- reconnect, never crash the pocket
                logger.info("evm_swap_ws[%s]: connection lost (%s), retrying in %.1fs", self.chain, exc, backoff)
            finally:
                self._connected = False
                self._w3 = None
            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)

    async def _resubscribe(self) -> str | None:
        """Re-issues the logs subscription(s) with the current pool set. A
        real RPC ``eth_subscribe`` filter is static once created -- there is
        no "add address to an existing filter" call, so a pool-set change
        closes every previous subscription first (24/08 fix -- see
        ``_active_sub_ids``'s own docstring for the incident this closes).

        Also owns the ``newHeads`` keepalive (24/08 fix): open only while
        ``_active_sub_ids`` is empty (a real logs subscription keeps
        ``process_subscriptions()`` alive on its own, so the keepalive would
        be pure waste on top of it), closed the moment a real pool is
        tracked, reopened if the pool set ever empties out again.

        **25/08 addition**: also closed (and never reopened) while
        ``_breaker_open`` -- the circuit breaker's whole point is to stop
        spending RU once the daily budget is exhausted, so reopening the
        keepalive the moment ``_pools`` empties out (which is exactly what
        eviction does) would silently undo it. See
        ``_check_budget_circuit_breaker``'s own docstring for the real
        incident this closes.

        v2/v3 and v4 are issued as TWO SEPARATE subscriptions, never merged
        into one filter (24/08 fix, real incident): v4 pools all share the
        one PoolManager contract address, so an address-only filter cannot
        distinguish "this pocket's tracked pools" from "every v4 pool on the
        whole chain" -- v4 is Base's single busiest DEX by pool-creation
        rate (see this module's own docstring), so that filter received
        every swap on every v4 pool on Base, not just the tracked ones,
        discarded only AFTER being received and billed. The eth_subscribe
        filter's ``topics`` list is positional (topics[1] applies to every
        address in the filter alike), so v4's poolId restriction cannot
        share a filter with v2/v3's addresses without also breaking their
        matching -- hence two subscriptions."""
        if self._w3 is None:
            return None
        for sub_id in self._active_sub_ids:
            try:
                await self._w3.eth.unsubscribe(sub_id)
            except Exception as exc:  # noqa: BLE001 -- best-effort, a dead socket cannot leak
                logger.info("evm_swap_ws[%s]: unsubscribe(%s) failed (%s)", self.chain, sub_id, exc)
        self._active_sub_ids = []

        v4_pool_ids = [p.pool_id_hex for p in self._pools.values() if p.family == "v4" and p.pool_id_hex]
        v2v3_addresses = [addr for addr, p in self._pools.items() if p.family in ("v2", "v3")]

        if v2v3_addresses:
            sub_id = await self._w3.eth.subscribe(
                "logs",
                {"address": v2v3_addresses, "topics": [[_SYNC_TOPIC, _SYNC_TOPIC_AERODROME, _V3_SWAP_TOPIC]]},
            )
            self._active_sub_ids.append(sub_id)
        if v4_pool_ids:
            sub_id = await self._w3.eth.subscribe(
                "logs",
                {"address": [self._pool_manager_address()], "topics": [[_V4_SWAP_TOPIC], v4_pool_ids]},
            )
            self._active_sub_ids.append(sub_id)

        # 25/08 -- tracks how long _pools has sat empty (see
        # _pools_empty_since's own docstring) -- feeds the proactive idle
        # check below, independent of whether the daily budget is blown.
        if self._pools:
            self._pools_empty_since = None
        elif self._pools_empty_since is None:
            self._pools_empty_since = time.monotonic()
        idle_too_long = (
            self._pools_empty_since is not None
            and time.monotonic() - self._pools_empty_since >= _IDLE_NEWHEADS_CLOSE_SECONDS
        )

        # 24/08 -- newHeads is billed 1 RU per push, same as any other
        # subscription (docs.chainstack.com/docs/request-units, confirmed
        # live), so it must be open ONLY while no real logs subscription
        # exists to keep process_subscriptions() alive on its own. Left
        # permanently open (the pre-fix behaviour), a fast chain costs real
        # money for nothing: Robinhood Chain's 100ms block time alone would
        # be ~864k RU/day just for this keepalive, on top of every real Sync/
        # Swap event this module actually wants. 25/08 -- also closed (and
        # never reopened) once idle_too_long, proactively, rather than
        # waiting for the daily cap to already be blown (see
        # _IDLE_NEWHEADS_CLOSE_SECONDS's own docstring).
        if self._active_sub_ids or self._breaker_open or idle_too_long:
            if self._newheads_sub_id is not None:
                try:
                    await self._w3.eth.unsubscribe(self._newheads_sub_id)
                except Exception as exc:  # noqa: BLE001 -- best-effort, a dead socket cannot leak
                    logger.info("evm_swap_ws[%s]: unsubscribe(newHeads) failed (%s)", self.chain, exc)
                self._newheads_sub_id = None
        elif self._newheads_sub_id is None:
            self._newheads_sub_id = await self._w3.eth.subscribe("newHeads")

        return self._active_sub_ids[-1] if self._active_sub_ids else self._newheads_sub_id

    def _handle_notification(self, payload) -> None:
        try:
            result = payload.get("result") if isinstance(payload, dict) else None
            if not result:
                return
            # 24/08 -- every push is billed 1 RU regardless of content
            # (confirmed live, see evm_swap_ws.py's own newHeads-keepalive
            # fix), including newHeads itself -- counted here, before the
            # topics filter below, so the keepalive's real cost is tracked
            # too. Visibility only for now (chainstack_ru_budget.py's own
            # docstring): unlike Solana's poll_due(), a push already arrived
            # and was already billed by the time this runs, there is no
            # "skip this one" lever here.
            chainstack_ru_budget.record_usage_fast(self.chain, 1)
            topics = result.get("topics") or []
            if not topics:
                return
            # Real bug found live 24/08: HexBytes.hex() (web3.py's topic type)
            # returns the hash WITHOUT the "0x" prefix, unlike this module's own
            # _topic0() constants which always include it -- an unnormalized
            # comparison silently matched nothing, ever, on every real event
            # received. Always normalize to the "0x"-prefixed form before
            # comparing.
            topic0 = topics[0].hex() if hasattr(topics[0], "hex") else str(topics[0])
            if not topic0.startswith("0x"):
                topic0 = "0x" + topic0
            address = (result.get("address") or "").lower()
            if topic0 in (_SYNC_TOPIC, _SYNC_TOPIC_AERODROME):
                self._handle_sync(address, result)
            elif topic0 == _V3_SWAP_TOPIC:
                self._handle_v3_swap(address, topics, result)
            elif topic0 == _V4_SWAP_TOPIC:
                self._handle_v4_swap(topics, result)
        except Exception as exc:  # noqa: BLE001 -- one bad notification never kills the feed
            logger.info("evm_swap_ws[%s]: notification decode failed (%s)", self.chain, exc)

    def _handle_sync(self, address: str, result: dict) -> None:
        # 27/08, real defect found via cross-session review with aria-94
        # (obv-ao-screener, a sibling project) -- `Sync(uint112,uint112)` is
        # emitted on EVERY reserve change, not just a swap: `Mint`/`Burn`
        # (add/remove liquidity) emit it too, in the same transaction. This
        # module only subscribes to Sync/V3-Swap (see the `topics` filter in
        # start()), never Mint/Burn, so a Mint/Burn's own Sync is silently
        # treated the same as a real swap's.
        #
        # Verified this does NOT corrupt `price` -- a standard V2 mint/burn
        # preserves the reserve RATIO by design (both legs move together),
        # so the ratio computed below stays correct either way. The one real
        # casualty is `pool.swap_count += 1` a few lines down: it counts a
        # Mint/Burn as a swap. Grepped every caller in this repo (27/08):
        # `swap_count` is exposed in `get_snapshot()` but consumed by NO
        # other module today -- zero real impact right now.
        #
        # Correct fix (not applied here, deliberately) -- CORRECTED 27/08,
        # aria-94 retracted her first proposal below: transactionHash
        # correlation does NOT work, because Sync is always emitted FIRST in
        # the transaction, before Mint/Burn/Swap -- there is no way to know
        # at Sync-receipt-time whether a Mint/Burn will follow, so nothing to
        # correlate against yet. Her corrected, simpler fix: never derive
        # `swap_count` (or volume) from Sync at all -- move that tracking to
        # the V2 `Swap` event instead (Mint/Burn never emit Swap, only Sync).
        # This module doesn't currently subscribe to/decode V2 Swap at all
        # (only Sync for V2, Swap for V3/V4) -- adding a `_handle_v2_swap`
        # would be the real fix. Not built: `swap_count` is unused today
        # (confirmed via grep, no other module reads it). Revisit if
        # `swap_count` is ever wired into a real decision -- fix it THEN.
        pool = self._pools.get(address)
        if pool is None or pool.family != "v2":
            return
        data = result.get("data")
        raw = bytes.fromhex(data[2:]) if isinstance(data, str) else bytes(data)
        reserve0 = int.from_bytes(raw[0:32], "big")
        reserve1 = int.from_bytes(raw[32:64], "big")
        if reserve0 == 0 or reserve1 == 0:
            return  # never divide by a zero reserve
        ratio = (reserve1 / (10 ** pool.decimals1)) / (reserve0 / (10 ** pool.decimals0))
        price = ratio if pool.token_is_currency0 else (1.0 / ratio)
        # Exact reserve_usd, already in this same event -- only when the
        # quote leg is a known USD stable (2x its reserve == total pool
        # value at equilibrium); WETH-quoted pools need doppler.eth_usd_
        # rate() to convert, left to the caller per this module's no-
        # network-I/O-in-decoder rule.
        if pool.quote_is_stable:
            quote_reserve = (reserve0 if not pool.token_is_currency0 else reserve1) / (
                10 ** (pool.decimals0 if not pool.token_is_currency0 else pool.decimals1)
            )
            pool.last_reserve_usd = 2.0 * quote_reserve
        pool.swap_count += 1
        self._record_tick(pool, price)

    @staticmethod
    def _to_signed256(raw_int: int) -> int:
        return raw_int - (1 << 256) if raw_int >= (1 << 255) else raw_int

    def _record_swap_amount(self, pool: _TrackedPool, raw: bytes, sender_topic) -> None:
        """Shared by v3/v4 -- amount0/amount1 and sender are ALREADY in the
        same event this module already decodes for price/liquidity, zero
        extra RPC call (operator-directed 24/08: "tu aurai du pensai a
        capturer tous se qui est utile"). The quote-leg amount (whichever
        side is NOT the tracked token) is this swap's real traded size,
        decimal-adjusted, in quote units -- not USD (same honesty rule as
        price_quote/reserve_usd elsewhere in this module)."""
        amount0 = self._to_signed256(int.from_bytes(raw[0:32], "big"))
        amount1 = self._to_signed256(int.from_bytes(raw[32:64], "big"))
        quote_raw = amount1 if pool.token_is_currency0 else amount0
        quote_decimals = pool.decimals1 if pool.token_is_currency0 else pool.decimals0
        pool.cumulative_volume_quote += abs(quote_raw) / (10 ** quote_decimals)
        pool.swap_count += 1
        if sender_topic is not None:
            sender_hex = sender_topic.hex() if hasattr(sender_topic, "hex") else str(sender_topic)
            # An indexed `address` topic is always right-aligned in 32 bytes
            # (12 zero bytes then the 20-byte address) -- keep only the real
            # address, never the padded topic, so distinct_traders holds
            # genuine EVM addresses comparable to any other source.
            pool.distinct_traders.add(("0x" + sender_hex[-40:]).lower())

    def _handle_v3_swap(self, address: str, topics, result: dict) -> None:
        pool = self._pools.get(address)
        if pool is None or pool.family != "v3":
            return
        data = result.get("data")
        raw = bytes.fromhex(data[2:]) if isinstance(data, str) else bytes(data)
        # amount0(32) amount1(32) sqrtPriceX96(32) liquidity(32) tick(32)
        sqrt_price_x96 = int.from_bytes(raw[64:96], "big")
        if sqrt_price_x96 == 0:
            return
        price = price_from_sqrt_price_x96(
            sqrt_price_x96, token_is_currency0=pool.token_is_currency0,
            decimals0=pool.decimals0, decimals1=pool.decimals1,
        )
        # Already in this same event, zero extra RPC call (operator-directed
        # 24/08: "uniswap devrait te la donner si tu extrait le ca") --
        # abstract active-liquidity-at-current-tick unit, not USD, but a
        # valid pool-to-pool depth proxy on its own.
        pool.last_raw_liquidity = float(int.from_bytes(raw[96:128], "big"))
        # topics: [topic0, sender, recipient] -- sender is topics[1]
        sender_topic = topics[1] if len(topics) > 1 else None
        self._record_swap_amount(pool, raw, sender_topic)
        self._record_tick(pool, price)

    def _handle_v4_swap(self, topics, result: dict) -> None:
        # Same "0x" prefix normalization as _handle_notification's topic0 --
        # must match whatever prefix the caller's add_pool(pool_id_hex=...)
        # used as the dict key.
        pool_id_hex = (topics[1].hex() if hasattr(topics[1], "hex") else str(topics[1])).lower()
        if not pool_id_hex.startswith("0x"):
            pool_id_hex = "0x" + pool_id_hex
        pool = self._pools.get(pool_id_hex)
        if pool is None or pool.family != "v4":
            return
        data = result.get("data")
        raw = bytes.fromhex(data[2:]) if isinstance(data, str) else bytes(data)
        # amount0(32) amount1(32) sqrtPriceX96(32) liquidity(32) tick(32) fee(32)
        sqrt_price_x96 = int.from_bytes(raw[64:96], "big")
        if sqrt_price_x96 == 0:
            return
        price = price_from_sqrt_price_x96(
            sqrt_price_x96, token_is_currency0=pool.token_is_currency0,
            decimals0=pool.decimals0, decimals1=pool.decimals1,
        )
        pool.last_raw_liquidity = float(int.from_bytes(raw[96:128], "big"))
        # topics: [topic0, id, sender] -- sender is topics[2]
        sender_topic = topics[2] if len(topics) > 2 else None
        self._record_swap_amount(pool, raw, sender_topic)
        self._record_tick(pool, price)

    def _record_tick(self, pool: _TrackedPool, price: float) -> None:
        now = time.monotonic()
        pool.ticks.append((now, price))
        cutoff = now - _TICK_HISTORY_SECONDS
        while pool.ticks and pool.ticks[0][0] < cutoff:
            pool.ticks.popleft()


_MINIMAL_TOKEN01_ABI = [
    {"constant": True, "inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "type": "function"},
]

_MINIMAL_DECIMALS_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]
