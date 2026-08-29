"""On-chain pool-creation discovery (25/08, specs/006-onchain-dayzero-entry).

**Why this exists**: 005-discovery-budget measured that a direct
``eth_subscribe("logs")`` on Base/Robinhood's factories detects a new pool
the instant its block lands (7.2k/11.1k RU/day projected, well under the
calibrated 25k/400k daily caps) -- far faster than DexPaprika's own 120s
polling cadence plus its own indexing lag. That measurement stopped short of
building anything because the pipeline AT THE TIME gated entry on a 5-minute
price surge, which a freshly-created pool cannot have. Operator-directed
(25/08): replace the entry criterion itself -- "day-zero" (a liquidity floor
at creation) instead of "m5-surge" (wait for the pool to already be moving).

**Architecture, reusing what already exists rather than duplicating it**:
this module owns ONLY the discovery half (decode PairCreated/PoolCreated/
Initialize into a pool key + dex_id + tokens). The moment a pool is decoded,
it is hand off to the chain's own shared ``EVMSwapWebSocketFeed`` (already
built, already tracks price/reserve via Sync/Swap events) via ``add_pool``
-- never a second WS connection per pool, never a duplicate price decoder.

**Liquidity check, honestly scoped**: ``EVMSwapWebSocketFeed`` only computes
an exact ``reserve_usd`` for a v2 pool quoted in a known USD stable (see its
own docstring) -- a WETH-quoted v2 pool, and EVERY v3/v4 pool (concentrated
liquidity has no single "total reserve"), leave ``reserve_usd=None`` by
design. Rather than reimplement USD liquidity math for every family (a real,
separate chantier -- concentrated-liquidity depth in particular), this
module falls back to ONE bounded, ponctual targeted on-chain read
(``EVMSwapWebSocketFeed.resolve_cold``, a direct ``eth_call`` reusing the
SAME price/reserve formula the live event decoder already applies -- see
that method's own docstring) the moment a pool's first real tick still
hasn't landed -- never polling before that, never repeated more than once
per ``_RECHECK_INTERVAL_SECONDS`` within the observation window. This keeps
the actual latency win (detection at creation, not at the next 120s scan)
while staying 100% Chainstack-sourced (28/08, specs/015-robinhood-
chainstack-only -- this used to fall back to ``dexpaprika.get_pool_reserve_
usd`` here, which is the confirmed, direct cause of every day-zero candidate
without an observed Sync/Swap event getting stuck at ``qualified_this_
cycle=0`` once that provider started failing, see that feature's research.md
for the full trace).

**Never program-wide**: the subscription filter is a FIXED, small list of
factory addresses (Uniswap v2/v3/v4 per chain, plus Aerodrome Slipstream and
PancakeSwap V3 on Base -- the DEXes 005 confirmed carry Base's real volume
via DefiLlama) -- never "every contract creation on the chain". This is
exactly the shape Solana's own 21/08 incident (``programSubscribe`` at
74GB/day) warns against skipping.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aria_core import discovery_liquidity_observation
from aria_core.services import chainstack_ru_budget
from aria_core.services.evm_swap_ws import EVMSwapWebSocketFeed
from aria_core.services.geckoterminal import TrendingPool

logger = logging.getLogger(__name__)

# How long a candidate is watched for its first liquidity-crossing tick
# before being dropped -- bounded, never tracked forever (see module
# docstring's "no silent cap" doctrine).
_OBSERVATION_WINDOW_SECONDS = 600.0

# Minimum spacing between two ``resolve_cold`` calls for the SAME candidate
# -- a pool that exists yet has produced no Sync/Swap event YET would
# otherwise be re-queried on every single tick.
_RECHECK_INTERVAL_SECONDS = 20.0

_RECONNECT_MIN_SECONDS = 1.0
_RECONNECT_MAX_SECONDS = 30.0

# 28/08, specs/015-robinhood-chainstack-only T020 -- an INITIAL guardrail,
# not a tuned optimum: research.md measured ~66 concurrently-tracked
# candidates under the DexPaprika 402 outage (qualification was failing at
# the time, so real post-fix demand may be higher). 150 gives ~2.3x headroom
# over that baseline. Recalibrate once qualification is genuinely restored
# and real demand is measured (T031, a separate follow-up decision, never
# bundled into this feature).
MAX_CONCURRENT_TRACKED_POOLS = 150


def _topic0(signature: str) -> str:
    from web3 import Web3

    return "0x" + Web3.keccak(text=signature).hex()


_PAIR_CREATED_TOPIC = _topic0("PairCreated(address,address,address,uint256)")
# Uniswap v3 AND PancakeSwap V3 share the exact same signature text (verified
# live against both official interfaces, 25/08) -- same topic0, one decoder.
_POOL_CREATED_V3_TOPIC = _topic0("PoolCreated(address,address,uint24,int24,address)")
# Aerodrome Slipstream's CLFactory -- verified live against
# aerodrome-finance/slipstream's ICLFactory.sol, 25/08: a DIFFERENT
# signature (tickSpacing indexed instead of fee), so a distinct topic0.
_POOL_CREATED_AERODROME_TOPIC = _topic0("PoolCreated(address,address,int24,address)")
# Aerodrome's ORIGINAL "Classic" factory (stable/volatile pools, Solidly
# fork) -- a THIRD, distinct signature from both V3-style topics above
# (`bool indexed stable` where V3/Slipstream have `fee`/`tickSpacing`).
# 26/08, found missing during specs/011's Base sourcing-silence diagnostic:
# this pocket only ever listened for Slipstream (the concentrated-liquidity
# fork), never Classic -- yet Classic is the dominant AMM for fresh memecoin
# pools on Base (lower fees, simpler deploy). Signature verified against
# aerodrome-finance/contracts' IPoolFactory.sol (26/08): `event PoolCreated(
# address indexed token0, address indexed token1, bool indexed stable,
# address pool, uint256)` -- `pool` is the FIRST word of the non-indexed
# data (unlike the V3-style topics above, where it's the LAST), same layout
# as `_PAIR_CREATED_TOPIC`'s own pair address.
_POOL_CREATED_AERODROME_CLASSIC_TOPIC = _topic0(
    "PoolCreated(address,address,bool,address,uint256)"
)
_INITIALIZE_V4_TOPIC = _topic0(
    "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
)

# Per-chain factory addresses -- verified live against official docs/repos in
# 005-discovery-budget's T002 measurement (confirmed by actually receiving
# real events on each), never guessed. dex_id matches DexPaprika/GeckoTerminal
# naming so a discovered pool is directly usable by evm_swap_ws.dex_family().
_FACTORIES: dict[str, dict[str, str]] = {
    "base": {
        "0x8909dc15e40173ff4699343b6eb8132c65e18ec6": "uniswap_v2",
        "0x33128a8fc17869897dce68ed026d694621f6fdfd": "uniswap_v3",
        "0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a": "aerodrome_slipstream_3",
        # Aerodrome Classic PoolFactory -- verified live on BaseScan, 26/08
        # (contract "Aerodrome: Pool Factory", source verified). dex_id
        # "aerodrome" matches evm_swap_ws._DEX_FAMILY's existing "v2" entry
        # (already wired for pricing, only discovery was missing).
        "0x420dd381b31aef6683db6b902084cb0ffece40da": "aerodrome",
        "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865": "pancakeswap_v3",
    },
    "robinhood": {
        "0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f": "uniswap_v2",
        "0x1f7d7550b1b028f7571e69a784071f0205fd2efa": "uniswap_v3",
    },
}
# v4 has no per-pool factory -- every chain's v4 pools are created through
# its own PoolManager singleton (see evm_swap_ws._POOL_MANAGER_BY_CHAIN,
# reused here rather than restated).
_V4_DEX_ID = "uniswap_v4"


@dataclass
class _Candidate:
    pool_key: str  # pool address (v2/v3-style) or poolId hex (v4)
    dex_id: str
    token_address: str
    chain: str
    discovered_at: float = field(default_factory=time.monotonic)
    pool_created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_checked_at: float | None = None


class OnChainPoolDiscoveryFeed:
    """One instance per chain -- a FIXED subscription to that chain's known
    factories, never modified after ``start()`` (unlike
    ``EVMSwapWebSocketFeed``'s per-pool dynamic filter)."""

    def __init__(self, *, chain: str, ws_url: str, ws_feed: EVMSwapWebSocketFeed) -> None:
        self.chain = chain
        self._ws_url = ws_url
        self._ws_feed = ws_feed
        self._factories = _FACTORIES.get(chain, {})
        self._pool_manager = ws_feed._pool_manager_address()
        self._candidates: dict[str, _Candidate] = {}
        self._dropped_count = 0
        self._w3 = None
        self._task = None
        self._stopped = False
        self._connected = False
        # 27/08, real incident: Base went from ~28 candidates/hour (25/08) to
        # ZERO for 36h+ straight (crash fixed same day, see check_candidates'
        # own comment) -- and even after the fix, zero new candidates AND
        # zero add_pool attempts over a full clean 48min observation window,
        # with the WS reporting "subscribed on 6 factories" throughout. No
        # existing counter could tell "no raw notifications reaching this
        # feed at all" apart from "notifications arrive but every one is
        # rejected by the quote-token filter" -- both look identical from
        # the outside (silence). Diagnostics-only, never changes behaviour.
        self.raw_notifications_seen = 0
        self.rejected_not_priceable_count = 0
        # 28/08, specs/015-robinhood-chainstack-only T019 -- distinct from
        # `rejected_not_priceable_count` above (a PERMANENT rejection at
        # registration time: neither pool side is a known quote token).
        # This one counts a TEMPORARY state: a candidate whose websocket
        # snapshot and cold on-chain read both came back unresolved THIS
        # cycle -- it stays in `_candidates`, retried on a later pass, never
        # dropped and never treated as qualified on a partial read (operator's
        # explicit two-state requirement: not_yet_priceable vs. qualified,
        # nothing in between).
        self.not_yet_priceable_count = 0
        # T020 -- initial guardrail (research.md: measured ~66 concurrent
        # candidates under the 402 outage, 2.3x headroom), NOT a tuned
        # optimum. Recalibrate once qualification is genuinely restored and
        # real post-fix demand is measured (T031, separate follow-up).
        self._cap_dropped_count = 0

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        import asyncio

        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:  # noqa: BLE001 -- best-effort shutdown
                pass
            self._task = None
        self._connected = False

    async def _run(self) -> None:
        import asyncio

        from web3 import AsyncWeb3
        from web3.providers.persistent import WebSocketProvider

        backoff = _RECONNECT_MIN_SECONDS
        addresses = list(self._factories.keys()) + [self._pool_manager]
        topics = [
            _PAIR_CREATED_TOPIC, _POOL_CREATED_V3_TOPIC,
            _POOL_CREATED_AERODROME_TOPIC, _POOL_CREATED_AERODROME_CLASSIC_TOPIC,
            _INITIALIZE_V4_TOPIC,
        ]
        while not self._stopped:
            try:
                async with AsyncWeb3(WebSocketProvider(self._ws_url)) as w3:
                    self._w3 = w3
                    self._connected = True
                    backoff = _RECONNECT_MIN_SECONDS
                    await w3.eth.subscribe("logs", {"address": addresses, "topics": [topics]})
                    logger.info("onchain_pool_discovery[%s]: subscribed on %d factories",
                                self.chain, len(addresses))
                    async for payload in w3.socket.process_subscriptions():
                        if self._stopped:
                            break
                        self._handle_notification(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- reconnect, never crash the feed
                logger.info("onchain_pool_discovery[%s]: connection lost (%s), retrying in %.1fs",
                            self.chain, exc, backoff)
            finally:
                self._connected = False
                self._w3 = None
            if self._stopped:
                break
            import asyncio as _asyncio
            await _asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)

    def _handle_notification(self, payload) -> None:
        try:
            result = payload.get("result") if isinstance(payload, dict) else None
            if not result:
                return
            self.raw_notifications_seen += 1
            chainstack_ru_budget.record_usage_fast(self.chain, 1)
            topics = result.get("topics") or []
            if not topics:
                return
            topic0 = topics[0].hex() if hasattr(topics[0], "hex") else str(topics[0])
            if not topic0.startswith("0x"):
                topic0 = "0x" + topic0
            address = (result.get("address") or "").lower()
            data = result.get("data")
            raw = bytes.fromhex(data[2:]) if isinstance(data, str) else bytes(data)

            if topic0 == _PAIR_CREATED_TOPIC:
                self._on_v2_pair_created(address, topics, raw)
            elif topic0 in (_POOL_CREATED_V3_TOPIC, _POOL_CREATED_AERODROME_TOPIC):
                self._on_v3_pool_created(address, topics, raw)
            elif topic0 == _POOL_CREATED_AERODROME_CLASSIC_TOPIC:
                self._on_aerodrome_classic_pool_created(address, topics, raw)
            elif topic0 == _INITIALIZE_V4_TOPIC:
                self._on_v4_initialize(topics, raw)
        except Exception as exc:  # noqa: BLE001 -- one bad notification never kills the feed
            logger.info("onchain_pool_discovery[%s]: notification decode failed (%s)", self.chain, exc)

    @staticmethod
    def _addr_from_topic(topic) -> str:
        hex_str = topic.hex() if hasattr(topic, "hex") else str(topic)
        return ("0x" + hex_str[-40:]).lower()

    def _on_v2_pair_created(self, factory_address: str, topics, raw: bytes) -> None:
        dex_id = self._factories.get(factory_address)
        if dex_id != "uniswap_v2" or len(topics) < 3:
            return
        token0 = self._addr_from_topic(topics[1])
        token1 = self._addr_from_topic(topics[2])
        pair_address = ("0x" + raw[12:32].hex()).lower()
        self._register_candidate(pair_address, dex_id, token0, token1)

    def _on_v3_pool_created(self, factory_address: str, topics, raw: bytes) -> None:
        dex_id = self._factories.get(factory_address)
        if dex_id is None or len(topics) < 3:
            return
        token0 = self._addr_from_topic(topics[1])
        token1 = self._addr_from_topic(topics[2])
        # v3/PancakeSwap: data = tickSpacing(32) + pool(32) -- pool in the LAST word.
        # Aerodrome: data = pool(32) only -- pool in the ONLY word.
        pool_address = ("0x" + raw[-20:].hex()).lower()
        self._register_candidate(pool_address, dex_id, token0, token1)

    def _on_aerodrome_classic_pool_created(self, factory_address: str, topics, raw: bytes) -> None:
        """26/08 -- `event PoolCreated(address indexed token0, address indexed
        token1, bool indexed stable, address pool, uint256)` (verified against
        aerodrome-finance/contracts' IPoolFactory.sol). `stable` (topics[3])
        is intentionally not read here: `add_pool`/`_add_pool_v2v3` already
        refuses a stable pool on its own (unsupported pricing curve, see
        evm_swap_ws._DEX_FAMILY's "aerodrome" comment) -- duplicating that
        check here would be a second place to keep in sync for no benefit.
        `pool` is the FIRST word of the non-indexed data, same layout as
        `_PAIR_CREATED_TOPIC`'s own pair address."""
        dex_id = self._factories.get(factory_address)
        if dex_id != "aerodrome" or len(topics) < 3:
            return
        token0 = self._addr_from_topic(topics[1])
        token1 = self._addr_from_topic(topics[2])
        pool_address = ("0x" + raw[12:32].hex()).lower()
        self._register_candidate(pool_address, dex_id, token0, token1)

    def _on_v4_initialize(self, topics, raw: bytes) -> None:
        if len(topics) < 2:
            return
        pool_id_hex = topics[1].hex() if hasattr(topics[1], "hex") else str(topics[1])
        if not pool_id_hex.startswith("0x"):
            pool_id_hex = "0x" + pool_id_hex
        # data = currency0(32) currency1(32) fee(32) tickSpacing(32) hooks(32) sqrtPriceX96(32) tick(32)
        currency0 = ("0x" + raw[12:32].hex()).lower()
        currency1 = ("0x" + raw[44:64].hex()).lower()
        self._register_candidate(pool_id_hex, _V4_DEX_ID, currency0, currency1)

    def _register_candidate(self, pool_key: str, dex_id: str, token0: str, token1: str) -> None:
        from aria_core.services.evm_swap_ws import _CBBTC_ADDRESSES, _KNOWN_USD_STABLES, _WETH_ADDRESSES

        quote_candidates = _KNOWN_USD_STABLES | _WETH_ADDRESSES | _CBBTC_ADDRESSES
        token0_is_quote = token0 in quote_candidates
        token1_is_quote = token1 in quote_candidates
        if token0_is_quote == token1_is_quote:
            # Neither side is a known quote (unpriceable), or both are
            # (stable/stable or WETH/WETH -- not a memecoin pool) -- honestly
            # uncovered, never guessed.
            self.rejected_not_priceable_count += 1
            return
        tracked_token = token1 if token0_is_quote else token0
        if pool_key in self._candidates:
            return
        if len(self._candidates) >= MAX_CONCURRENT_TRACKED_POOLS:
            # T020 -- skip/defer, never an unbounded queue: a pool dropped
            # here because the cap is full is simply never subscribed this
            # notification; if it re-notifies (another Sync/Swap-adjacent
            # factory event) once headroom frees up, it gets picked up then,
            # same as any other candidate arriving between cycles.
            self._cap_dropped_count += 1
            logger.info(
                "onchain_pool_discovery[%s]: MAX_CONCURRENT_TRACKED_POOLS=%d reached, "
                "dropping candidate %s (cap_dropped_total=%d)",
                self.chain, MAX_CONCURRENT_TRACKED_POOLS, pool_key, self._cap_dropped_count,
            )
            return
        self._candidates[pool_key] = _Candidate(
            pool_key=pool_key, dex_id=dex_id, token_address=tracked_token, chain=self.chain,
        )
        # v4's add_pool has a DIFFERENT contract from v2/v3: no separate pool
        # contract to introspect, so it trusts the caller's `token_address`
        # argument to literally BE the string "currency0"/"currency1" (see
        # EVMSwapWebSocketFeed._add_pool_v4's own docstring) -- never a real
        # address for that family. v2/v3 want the real token address.
        add_pool_token_arg = (
            ("currency0" if not token0_is_quote else "currency1") if dex_id == _V4_DEX_ID
            else tracked_token
        )
        import asyncio

        asyncio.create_task(self._ws_feed.add_pool(
            pool_key, dex_id=dex_id, token_address=add_pool_token_arg,
        ))

    async def check_candidates(self, *, min_liquidity_usd: float) -> list[TrendingPool]:
        """Checks every pending candidate's latest tick against
        ``min_liquidity_usd``. Returns qualified pools as ``TrendingPool``
        objects ready for ``record_signals(..., entry_mode="day_zero")`` --
        never mutates the caller's own discovery format, just adapts to it.
        Drops (and counts, never silently) any candidate past the
        observation window without ever crossing the floor."""
        qualified: list[TrendingPool] = []
        now = time.monotonic()
        expired_keys = []
        # 27/08, real incident: this loop awaits repeatedly per candidate
        # (cold on-chain reads, symbol resolution), and `_register_candidate`
        # -- called synchronously from the WS notification handler -- can
        # insert a new key into `self._candidates` during any of those
        # awaits. Iterating the live dict then raises "dictionary changed
        # size during iteration" on the next `next()`. Confirmed live on
        # base_discovery_loop (186 consecutive failures since 26/08 02:02,
        # zero new candidates for ~36h): Base's WS throughput is high enough
        # to reliably land a new pool mid-cycle; Robinhood's is not, so the
        # same bug never surfaced there despite sharing this exact code
        # path. A snapshot list sidesteps it: anything registered mid-cycle
        # is simply picked up on the next check_candidates() pass, same as
        # today's behavior for any candidate that arrives between cycles.
        for key, cand in list(self._candidates.items()):
            if now - cand.discovered_at >= _OBSERVATION_WINDOW_SECONDS:
                expired_keys.append(key)
                continue
            snapshot = self._ws_feed.get_snapshot(key)
            if not snapshot.available:
                # 26/08 -- real incident: `add_pool` verification (a live
                # `token0()`/`token1()` eth_call against a contract that may
                # have landed on-chain only moments ago) genuinely fails on
                # a fresh day-zero pool when the RPC node hasn't finished
                # indexing that exact block yet ("Could not decode contract
                # function call to token0() with return data: b''" -- an
                # empty return is the classic signature of calling code that
                # isn't visible at the queried state yet). `_register_candidate`
                # only ever calls `add_pool` ONCE, at discovery -- a
                # permanent RPC-vs-WS race left every such candidate stuck at
                # `available=False` for its entire observation window,
                # `continue`-ing straight past the REST fallback below on
                # every single check_candidates() pass, which is exactly why
                # this fell through to zero real day-zero candidates on Base
                # despite the 26/08 Aerodrome factory fix landing correctly.
                # Retry add_pool here (best-effort, fire-and-forget, same
                # cooldown as the REST fallback so a persistently-broken pool
                # doesn't spam eth_call every tick) -- the RPC has usually
                # caught up within a few cycles, letting the WS take over
                # for free on a later pass. Falling through to the REST
                # fallback below (rather than `continue`-ing) means this
                # candidate is never worse off than a v3/v4 pool that never
                # gets a WS reserve_usd in the first place.
                if cand.last_checked_at is None or (
                    now - cand.last_checked_at >= _RECHECK_INTERVAL_SECONDS
                ):
                    import asyncio
                    asyncio.create_task(self._ws_feed.add_pool(
                        key, dex_id=cand.dex_id, token_address=cand.token_address,
                    ))
            reserve_usd = snapshot.reserve_usd
            price_usd = None
            # 28/08, operator-directed post-deploy observation ask -- lets
            # the qualification log line below distinguish a real decoded
            # Sync/Swap event from a cold eth_call read, without a DB schema
            # change (SC-002's own tx_hash/block_number provenance already
            # covers the live-event case at the EVMSwapSnapshot level; this
            # is a lighter, log-only signal for the immediate post-deploy
            # observation window, not a replacement for that).
            source = "event"
            if snapshot.quote_is_weth:
                from aria_core.services.doppler import eth_usd_rate
                rate = await eth_usd_rate()
                if rate is not None and snapshot.price_quote is not None:
                    price_usd = snapshot.price_quote * rate
                # 28/08, specs/015 -- same real reason `qualified_this_cycle`
                # stayed at 0 even after DexPaprika/Gecko were removed:
                # reserve_usd was NEVER converted for a WETH-quoted pool
                # (only price_usd was), so every such pool -- the vast
                # majority sampled on Robinhood Chain, T023/T024's finding --
                # permanently failed the liquidity floor below regardless of
                # its real depth. Reuses the SAME `rate` fetched above, never
                # a second doppler call.
                if rate is not None and reserve_usd is None and snapshot.quote_reserve_raw is not None:
                    reserve_usd = 2.0 * snapshot.quote_reserve_raw * rate
            elif reserve_usd is not None:
                price_usd = snapshot.price_quote
            # specs/015-robinhood-chainstack-only -- was a `dexpaprika.
            # get_pool_reserve_usd`/`_get_json` fallback (the confirmed,
            # direct cause of `qualified_this_cycle=0` under the 402 outage,
            # see research.md's "real call chain" finding): a fresh pool with
            # no Sync/Swap event observed yet ALWAYS had `reserve_usd is
            # None` here, and every such candidate routed straight into the
            # now-broken provider. Replaced by a targeted cold on-chain read
            # -- same all-or-nothing contract as the websocket path: either
            # BOTH reserve and price resolve together, or the candidate
            # stays `not_yet_priceable` and is retried on a later cycle
            # (never a partial reserve-without-price or vice versa treated
            # as good enough, per the operator's own explicit vigilance).
            if reserve_usd is None or price_usd is None:
                if cand.last_checked_at is not None and (
                    now - cand.last_checked_at < _RECHECK_INTERVAL_SECONDS
                ):
                    continue
                cand.last_checked_at = now
                cold = await self._ws_feed.resolve_cold(
                    key, dex_id=cand.dex_id, token_address=cand.token_address,
                )
                if not cold.available:
                    # T019 -- explicit, counted `not_yet_priceable` state:
                    # neither the websocket nor the cold on-chain read could
                    # resolve BOTH reserve and price this cycle. The
                    # candidate stays in `_candidates`, retried on a later
                    # pass (never dropped here, never treated as qualified
                    # on this partial/absent read).
                    self.not_yet_priceable_count += 1
                    # 29/08, operator-directed -- log-only observation, see
                    # discovery_liquidity_observation.py's own docstring.
                    # Never gates anything, never a new network call.
                    await discovery_liquidity_observation.record_observation(
                        chain=self.chain, pool_address=key, token_address=cand.token_address,
                        reserve_usd=None, price_usd=None, min_liquidity_usd=min_liquidity_usd,
                        source=None, qualified=False,
                    )
                    continue
                source = "cold_read"
                reserve_usd = cold.reserve_usd
                price_usd = cold.price_usd
                if cold.quote_is_weth and (price_usd is None or (reserve_usd is None and cold.quote_reserve_raw is not None)):
                    from aria_core.services.doppler import eth_usd_rate
                    rate = await eth_usd_rate()
                    if rate is not None:
                        if price_usd is None and cold.price_quote is not None:
                            price_usd = cold.price_quote * rate
                        # 28/08, specs/015 -- same WETH reserve_usd
                        # conversion as the live-snapshot branch above,
                        # mirrored here for the cold-read path.
                        if reserve_usd is None and cold.quote_reserve_raw is not None:
                            reserve_usd = 2.0 * cold.quote_reserve_raw * rate
            if reserve_usd is None or reserve_usd < min_liquidity_usd or price_usd is None:
                # 29/08, operator-directed -- log-only observation of the
                # FULL population reaching this verdict (not just rejects),
                # see discovery_liquidity_observation.py's own docstring.
                # Never gates anything, never a new network call: reserve_usd/
                # price_usd/source are exactly the values already computed
                # above.
                await discovery_liquidity_observation.record_observation(
                    chain=self.chain, pool_address=key, token_address=cand.token_address,
                    reserve_usd=reserve_usd, price_usd=price_usd, min_liquidity_usd=min_liquidity_usd,
                    source=source, qualified=False,
                )
                continue
            # 26/08 -- a raw on-chain PairCreated/Initialize event carries no
            # ERC-20 symbol metadata, so every day-zero candidate used to log
            # with symbol=None ("?" in Telegram notifications, confirmed live
            # on base_momentum_shadow.py). Resolving it here (bounded: only
            # QUALIFIED candidates reach this line, funnel doctrine) --
            # cosmetic only, never gates qualification (specs/015: replaced
            # DexPaprika's `_resolve_base_token` with a direct `symbol()`
            # eth_call, never fabricated, None on any resolution failure).
            symbol = await self._ws_feed.resolve_token_symbol(cand.token_address)
            # 29/08, same observation point as the rejects above -- the
            # qualified branch of the same verdict, so the eventual analysis
            # has a real denominator (qualified + rejected) rather than a
            # rejects-only sample.
            await discovery_liquidity_observation.record_observation(
                chain=self.chain, pool_address=key, token_address=cand.token_address,
                reserve_usd=reserve_usd, price_usd=price_usd, min_liquidity_usd=min_liquidity_usd,
                source=source, qualified=True,
            )
            logger.info(
                "onchain_pool_discovery[%s]: qualified %s via %s (price_usd=%s reserve_usd=%s)",
                self.chain, key, source, price_usd, reserve_usd,
            )
            qualified.append(TrendingPool(
                pool_address=key, token_address=cand.token_address, symbol=symbol,
                price_usd=price_usd, price_change_pct={}, transactions_m15=None,
                volume_usd_m15=None, reserve_usd=reserve_usd,
                pool_created_at=cand.pool_created_at, dex_id=cand.dex_id,
            ))
            expired_keys.append(key)
        for key in expired_keys:
            self._candidates.pop(key, None)
        self._dropped_count += sum(
            1 for k in expired_keys if k not in {q.pool_address for q in qualified}
        )
        # 27/08, real incident: Base's candidate rate dropped from ~28/hour
        # to zero for 36h+ (crash, fixed) then STAYED at zero afterwards with
        # no error logged anywhere -- silence looked identical whether the
        # WS simply received nothing or received plenty that the quote-token
        # filter rejected. One line per cycle (same cadence as "SOLANA regime
        # sensor:") makes the two cases distinguishable without guessing.
        logger.info(
            "onchain_pool_discovery[%s]: raw_notifications_seen=%d "
            "rejected_not_priceable=%d not_yet_priceable_total=%d cap_dropped_total=%d "
            "pending=%d qualified_this_cycle=%d",
            self.chain, self.raw_notifications_seen,
            self.rejected_not_priceable_count, self.not_yet_priceable_count,
            self._cap_dropped_count, len(self._candidates), len(qualified),
        )
        return qualified

    @property
    def pending_count(self) -> int:
        return len(self._candidates)

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def cap_dropped_count(self) -> int:
        return self._cap_dropped_count
