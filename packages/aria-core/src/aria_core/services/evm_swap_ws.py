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

_KNOWN_USD_STABLES = frozenset({
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (Base)
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC (Base, bridged)
})
_WETH_ADDRESSES = frozenset({
    "0x4200000000000000000000000000000000000006",  # WETH (Base, canonical)
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


@dataclass
class _TrackedPool:
    dex_id: str
    family: str
    token_is_currency0: bool
    decimals0: int
    decimals1: int
    quote_is_weth: bool
    quote_is_stable: bool
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
        self._stopped = False
        self._connected = False

    # -- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 -- best-effort shutdown
                pass
            self._task = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # -- subscription management ---------------------------------------

    def dex_family(self, dex_id: str | None) -> str | None:
        return _DEX_FAMILY.get(dex_id or "")

    async def add_pool(
        self, pool_address: str, *, dex_id: str, token_address: str,
        decimals0: int = 18, decimals1: int = 18,
    ) -> bool:
        """Registers a pool for live tracking. Returns ``False`` (no error
        raised) when the dex_id is not covered or the on-chain verification
        fails -- the caller's cue to stay on its REST fallback, never a
        reason to interrupt its own cycle."""
        family = self.dex_family(dex_id)
        if family is None:
            return False
        if self._w3 is None:
            return False  # not connected yet -- caller retries next cycle
        try:
            if family == "v4":
                return await self._add_pool_v4(pool_address, token_address, dex_id, decimals0, decimals1)
            return await self._add_pool_v2v3(
                pool_address, token_address, dex_id, family, decimals0, decimals1,
            )
        except Exception as exc:  # noqa: BLE001 -- verification failure = uncovered, not a crash
            logger.info("evm_swap_ws[%s]: add_pool verify failed for %s (%s)", self.chain, pool_address, exc)
            return False

    async def _add_pool_v2v3(
        self, pool_address: str, token_address: str, dex_id: str, family: str,
        decimals0: int, decimals1: int,
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
        self._pools[key] = _TrackedPool(
            dex_id=dex_id, family=family, token_is_currency0=token_is_currency0,
            decimals0=decimals0, decimals1=decimals1,
            quote_is_weth=quote in _WETH_ADDRESSES, quote_is_stable=quote in _KNOWN_USD_STABLES,
        )
        await self._resubscribe()
        return True

    async def _add_pool_v4(
        self, pool_id_hex: str, token_address: str, dex_id: str, decimals0: int, decimals1: int,
    ) -> bool:
        # v4 pools carry no separate contract to introspect token0/token1
        # from -- the caller (which already resolved this pool via
        # doppler.find_pool or an equivalent Initialize-event lookup) is
        # trusted to pass the correct token_is_currency0 read from that same
        # event, never re-derived here.
        key = pool_id_hex.lower()
        if key in self._pools:
            return True
        self._pools[key] = _TrackedPool(
            dex_id=dex_id, family="v4",
            token_is_currency0=token_address == "currency0",
            decimals0=decimals0, decimals1=decimals1,
            quote_is_weth=False, quote_is_stable=False, pool_id_hex=pool_id_hex,
        )
        await self._resubscribe()
        return True

    async def remove_pool(self, pool_address_or_id: str) -> None:
        key = pool_address_or_id.lower()
        if self._pools.pop(key, None) is not None:
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
        return EVMSwapSnapshot(
            available=True, price_quote=last_price, price_usd=price_usd,
            window_high_quote=max(window), window_low_quote=min(window),
            last_update_at=last_t, stale_seconds=now - last_t,
            reserve_usd=pool.last_reserve_usd, raw_liquidity=pool.last_raw_liquidity,
            swap_count=pool.swap_count, cumulative_volume_quote=pool.cumulative_volume_quote,
            distinct_traders_count=len(pool.distinct_traders),
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
                    backoff = _RECONNECT_MIN_SECONDS
                    logger.info("evm_swap_ws[%s]: connected", self.chain)
                    # Real bug found live 24/08: web3.py's process_subscriptions()
                    # generator exits immediately if there is no active
                    # subscription the moment it starts iterating -- add_pool()
                    # racing in from the outside to create the real logs
                    # subscription arrives too late, the socket has already
                    # closed. A permanent newHeads subscription (ignored by
                    # _handle_notification, which only acts on log topics) keeps
                    # the generator alive across pool add/remove churn, including
                    # the empty-pools startup window.
                    await w3.eth.subscribe("newHeads")
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
        """Re-issues the logs subscription with the current pool set. A real
        RPC ``eth_subscribe`` filter is static once created -- there is no
        "add address to an existing filter" call, so a pool-set change
        unsubscribes and resubscribes fresh. Cheap and infrequent (pool
        open/close events, not a per-tick cost)."""
        if self._w3 is None:
            return None
        v4_pool_ids = [p.pool_id_hex for p in self._pools.values() if p.family == "v4" and p.pool_id_hex]
        v2v3_addresses = [addr for addr, p in self._pools.items() if p.family in ("v2", "v3")]
        topics_filter = [[_SYNC_TOPIC, _SYNC_TOPIC_AERODROME, _V3_SWAP_TOPIC, _V4_SWAP_TOPIC]]
        addresses = list(v2v3_addresses)
        if v4_pool_ids:
            addresses.append(POOL_MANAGER_ADDRESS)
        if not addresses:
            return None
        return await self._w3.eth.subscribe("logs", {"address": addresses, "topics": topics_filter})

    def _handle_notification(self, payload) -> None:
        try:
            result = payload.get("result") if isinstance(payload, dict) else None
            if not result:
                return
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
