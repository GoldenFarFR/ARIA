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
module falls back to ONE bounded, ponctual call to
``dexpaprika.get_pool_reserve_usd`` (already cached negatively, already
throttled) the moment a pool's first real tick arrives -- never polling
before that, never repeated more than once per ``_RECHECK_INTERVAL_SECONDS``
within the observation window. This keeps the actual latency win (detection
at creation, not at the next 120s scan) while reusing the one liquidity
source this dome already trusts, rather than inventing a second one.

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

from aria_core.services import chainstack_ru_budget, dexpaprika
from aria_core.services.evm_swap_ws import EVMSwapWebSocketFeed
from aria_core.services.geckoterminal import TrendingPool

logger = logging.getLogger(__name__)

# How long a candidate is watched for its first liquidity-crossing tick
# before being dropped -- bounded, never tracked forever (see module
# docstring's "no silent cap" doctrine).
_OBSERVATION_WINDOW_SECONDS = 600.0

# Minimum spacing between two ``get_pool_reserve_usd`` calls for the SAME
# candidate -- dexpaprika.py's own negative cache already absorbs a
# confirmed 404, but a pool that exists yet isn't indexed YET would
# otherwise be re-queried on every single tick.
_RECHECK_INTERVAL_SECONDS = 20.0

_RECONNECT_MIN_SECONDS = 1.0
_RECONNECT_MAX_SECONDS = 30.0


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
            _POOL_CREATED_AERODROME_TOPIC, _INITIALIZE_V4_TOPIC,
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
        from aria_core.services.evm_swap_ws import _KNOWN_USD_STABLES, _WETH_ADDRESSES

        quote_candidates = _KNOWN_USD_STABLES | _WETH_ADDRESSES
        token0_is_quote = token0 in quote_candidates
        token1_is_quote = token1 in quote_candidates
        if token0_is_quote == token1_is_quote:
            # Neither side is a known quote (unpriceable), or both are
            # (stable/stable or WETH/WETH -- not a memecoin pool) -- honestly
            # uncovered, never guessed.
            return
        tracked_token = token1 if token0_is_quote else token0
        if pool_key in self._candidates:
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
        for key, cand in self._candidates.items():
            if now - cand.discovered_at >= _OBSERVATION_WINDOW_SECONDS:
                expired_keys.append(key)
                continue
            snapshot = self._ws_feed.get_snapshot(key)
            if not snapshot.available:
                continue
            reserve_usd = snapshot.reserve_usd
            price_usd = None
            if snapshot.quote_is_weth:
                from aria_core.services.doppler import eth_usd_rate
                rate = await eth_usd_rate()
                if rate is not None and snapshot.price_quote is not None:
                    price_usd = snapshot.price_quote * rate
            elif reserve_usd is not None:
                price_usd = snapshot.price_quote
            if reserve_usd is None:
                if cand.last_checked_at is not None and (
                    now - cand.last_checked_at < _RECHECK_INTERVAL_SECONDS
                ):
                    continue
                cand.last_checked_at = now
                reserve_usd = await dexpaprika.get_pool_reserve_usd(key, network=self.chain)
                if price_usd is None and reserve_usd is not None:
                    # Last resort for price when neither a stable quote nor a
                    # WETH conversion is available -- never fabricated, only
                    # used if DexPaprika itself already has an indexed price.
                    detail, _ = await dexpaprika._get_json(  # noqa: SLF001 -- internal, same module family
                        f"/networks/{self.chain}/pools/{key}", params={},
                    )
                    if isinstance(detail, dict):
                        raw_price = detail.get("price_usd")
                        if isinstance(raw_price, (int, float)):
                            price_usd = float(raw_price)
            if reserve_usd is None or reserve_usd < min_liquidity_usd or price_usd is None:
                continue
            qualified.append(TrendingPool(
                pool_address=key, token_address=cand.token_address, symbol=None,
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
        return qualified

    @property
    def pending_count(self) -> int:
        return len(self._candidates)

    @property
    def dropped_count(self) -> int:
        return self._dropped_count
