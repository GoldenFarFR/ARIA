"""Doppler protocol (Bankr token launches, Uniswap v4 dynamic-auction bonding
curve) -- direct on-chain price reads via Base RPC (07/24, "build in-house"
follow-up: no HTTP API or public subgraph exists for Doppler, confirmed via
real diligence -- only a TypeScript SDK that reads the exact same on-chain
state this module reads directly, no Python equivalent).

Real diligence (07/24, never guessed): a Doppler-launched token on Base is a
genuine Uniswap v4 pool. The hook only rebalances LIQUIDITY (an anti-snipe
fee schedule that dutch-auctions from a high starting fee down to a stable
floor over ~14 seconds post-launch -- confirmed on a real transaction,
``FeeScheduleSet`` event, startFee 800000 -> endFee 5000 over 14s). It does
NOT change how the CURRENT price is read -- a Doppler pool's price is read
exactly like any standard Uniswap v4 pool: ``sqrtPriceX96`` via the official
StateView contract, same as a plain vanilla v4 pool.

The one Doppler-specific piece -- finding WHICH pool belongs to a given
token -- has no API either. Confirmed via a real transaction (CLOWNS token,
07/18 mint): the PoolManager's own ``Initialize`` event (both ``currency0``
and ``currency1`` indexed) is emitted in the SAME transaction as the token's
mint. This module queries that event directly by ``eth_getLogs``, filtered
on the token address in EITHER indexed position (never assumes the token is
currency0 or currency1 -- both orders are possible).

Conversion formula cross-checked against a real on-chain data point (never
trusted on formula alone): CLOWNS's initialization event carried both
``sqrtPriceX96`` (10764248344314596577690877662916079) and ``tick`` (236400)
in the SAME log -- ``(sqrtPriceX96/2**96)**2`` and ``1.0001**tick`` matched to
within floating-point rounding (ratio 1.0000000000026), confirming the
formula below is correct, not just textbook-plausible.

Canonical Base addresses cross-checked TWICE (07/24): once via a real
Blockscout-tagged ("Uniswap V4: Pool Manager", Open Labels Initiative)
transaction, once via Uniswap's own official deployments page -- identical
address both times.
"""
from __future__ import annotations

import logging
import os

import httpx

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore

logger = logging.getLogger(__name__)

_DEFAULT_RPC_URL = "https://mainnet.base.org"

# Marker used by ``paper_trader._default_pair_lookup`` to route a position's
# price refresh here instead of DexScreener -- same pattern as
# ``bonding_entry.CHAIN_MARKER`` ("virtuals-bonding"), never a real chain id.
CHAIN_MARKER = "doppler"

# Uniswap v4 canonical contracts on Base (chain id 8453).
POOL_MANAGER_ADDRESS = "0x498581ff718922c3f8e6a244956af099b2652b2b"
STATE_VIEW_ADDRESS = "0xa3c0c9b65bad0b08107aa264b0f3db444b867a71"

WETH_ADDRESS = "0x4200000000000000000000000000000000000006"
_ETH_COINGECKO_ID = "ethereum"

_Q96 = 2 ** 96

_INITIALIZE_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "id", "type": "bytes32"},
        {"indexed": True, "name": "currency0", "type": "address"},
        {"indexed": True, "name": "currency1", "type": "address"},
        {"indexed": False, "name": "fee", "type": "uint24"},
        {"indexed": False, "name": "tickSpacing", "type": "int24"},
        {"indexed": False, "name": "hooks", "type": "address"},
        {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
        {"indexed": False, "name": "tick", "type": "int24"},
    ],
    "name": "Initialize",
    "type": "event",
}
_POOL_MANAGER_ABI = [_INITIALIZE_EVENT_ABI]

_STATE_VIEW_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "poolId", "type": "bytes32"}],
        "name": "getSlot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint24", "name": "protocolFee", "type": "uint24"},
            {"internalType": "uint24", "name": "lpFee", "type": "uint24"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


def _rpc_url() -> str:
    return (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


# 14/08 -- discovery scanner (discover_recent_pools below): persistent
# round-robin cursor (last block scanned), same SingleRowStore pattern as
# holder_concentration_outage_bypass.py/goplus_quota_suspension.py.
_SCAN_CURSOR_TABLE = "doppler_pool_scan_cursor"


def _scan_cursor_store() -> SingleRowStore:
    return SingleRowStore(
        str(aria_db_path()), _SCAN_CURSOR_TABLE,
        [("last_scanned_block", "INTEGER NOT NULL DEFAULT 0", 0)],
    )


def _client(*, w3=None):
    if w3 is not None:
        return w3
    from web3 import Web3

    return Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 10}))


def find_pool(token_address: str, *, w3=None, from_block: int = 0, to_block: str | int = "latest") -> dict | None:
    """Finds the Uniswap v4 pool a Doppler-launched token trades on, by
    scanning PoolManager ``Initialize`` events for either indexed currency
    position -- never assumes the token is currency0 or currency1 (both
    orders observed in practice: WETH is currency0 against most memecoins
    since it sorts lower numerically, but never assumed here).

    Returns ``{"pool_id": bytes32, "currency0": addr, "currency1": addr,
    "hooks": addr}``, or ``None`` if no pool was ever initialized for this
    token or the RPC call fails -- never a fabricated pool.

    ``from_block``/``to_block`` let a caller narrow the scan (e.g. once a
    token's approximate launch block is known) -- a full-history scan on a
    public RPC may be slow or rejected for a very wide range."""
    try:
        client = _client(w3=w3)
        checksum_token = client.to_checksum_address(token_address)
        pool_manager = client.eth.contract(
            address=client.to_checksum_address(POOL_MANAGER_ADDRESS), abi=_POOL_MANAGER_ABI
        )
        event = pool_manager.events.Initialize()
        for indexed_arg in ("currency0", "currency1"):
            logs = event.get_logs(
                from_block=from_block, to_block=to_block,
                argument_filters={indexed_arg: checksum_token},
            )
            if logs:
                log = logs[0]
                return {
                    "pool_id": log["args"]["id"],
                    "currency0": log["args"]["currency0"],
                    "currency1": log["args"]["currency1"],
                    "hooks": log["args"]["hooks"],
                }
        return None
    except Exception as exc:  # noqa: BLE001
        logger.info("doppler.find_pool: RPC read failed for %s (%s)", token_address, exc)
        return None


def read_slot0(pool_id: bytes, *, w3=None) -> tuple[int, int] | None:
    """Returns ``(sqrtPriceX96, tick)`` for a pool, or ``None`` on any RPC
    failure -- never a fabricated/stale price."""
    try:
        client = _client(w3=w3)
        state_view = client.eth.contract(
            address=client.to_checksum_address(STATE_VIEW_ADDRESS), abi=_STATE_VIEW_ABI
        )
        sqrt_price_x96, tick, _protocol_fee, _lp_fee = state_view.functions.getSlot0(pool_id).call()
        return sqrt_price_x96, tick
    except Exception as exc:  # noqa: BLE001
        logger.info("doppler.read_slot0: RPC read failed (%s)", exc)
        return None


def price_from_sqrt_price_x96(
    sqrt_price_x96: int, *, token_is_currency0: bool, decimals0: int = 18, decimals1: int = 18,
) -> float:
    """Converts a raw ``sqrtPriceX96`` into "other currency per token", decimal-
    adjusted. ``sqrtPriceX96`` natively encodes ``sqrt(currency1_wei /
    currency0_wei) * 2**96`` -- cross-checked against a real on-chain
    ``tick`` from the same event (see module docstring): squaring the ratio
    and comparing to ``1.0001**tick`` matched to within floating-point
    rounding.

    ``decimals0``/``decimals1`` default to 18 (the overwhelmingly common
    case -- WETH is always 18, and the vast majority of ERC-20 memecoins
    launched via Doppler/Bankr use the same OpenZeppelin-standard 18) but are
    NEVER assumed silently correct for a token with a different decimals
    count -- pass the token's real ``decimals()`` when known."""
    raw_ratio = (sqrt_price_x96 / _Q96) ** 2  # currency1_wei per currency0_wei
    human_ratio = raw_ratio * (10 ** (decimals0 - decimals1))  # currency1 units per currency0 units
    # human_ratio is ALWAYS currency1/currency0. If the token IS currency0,
    # "other currency per token" is currency1/currency0 == human_ratio as-is.
    # If the token is currency1, "other currency per token" is the INVERSE
    # (currency0/currency1) -- confirmed against the real CLOWNS bug (07/24):
    # the first version of this line had the two branches swapped, producing
    # a nonsensical $31 trillion price for a token priced at fractions of a
    # cent.
    return human_ratio if token_is_currency0 else (1.0 / human_ratio)


async def eth_usd_rate() -> float | None:
    """Current ETH/USD rate (WETH == ETH for valuation purposes) -- ``None``
    on any CoinGecko failure, fail-open, same pattern as
    ``services.virtuals.virtual_usd_rate``. Callers must never fabricate a
    USD price from a WETH-denominated one when this returns ``None``."""
    from aria_core.services.coingecko import coingecko_client

    try:
        result = await coingecko_client.get_simple_price([_ETH_COINGECKO_ID], vs_currencies=["usd"])
    except Exception as exc:  # noqa: BLE001
        logger.info("doppler.eth_usd_rate: CoinGecko failed (%s)", exc)
        return None
    if not result.available:
        return None
    rate = result.prices.get(_ETH_COINGECKO_ID, {}).get("usd")
    if not rate or rate <= 0:
        return None
    return rate


_BLOCKSCOUT_BASE_URL = "https://base.blockscout.com/api/v2"
_LAUNCH_BLOCK_MAX_PAGES = 20  # sanity cap, same doctrine as blockscout.get_transactions_bounded
_LAUNCH_BLOCK_SEARCH_MARGIN = 20  # blocks either side of the mint -- Initialize is emitted in the SAME tx


async def _find_launch_block_via_blockscout(token_address: str) -> int | None:
    """Finds the block a token was minted at, via Blockscout's token-transfers
    endpoint (free, no API key) -- ``eth_getLogs`` on a public RPC rejects any
    wide block range (413 Payload Too Large, confirmed empirically: even
    5000-20000 blocks on Base's PoolManager overflow the response size, since
    it's shared by ~90% of Base's DEX activity per Doppler's own stats) so a
    blind full-history scan is never practical. Blockscout paginates
    newest-first -- the mint is the LAST transfer once the full (bounded)
    history is walked. ``None`` if the token has no transfers, the token
    address is invalid, or every page attempt fails -- never a guessed block."""
    oldest_block: int | None = None
    params: dict = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for _ in range(_LAUNCH_BLOCK_MAX_PAGES):
            try:
                resp = await client.get(
                    f"{_BLOCKSCOUT_BASE_URL}/tokens/{token_address}/transfers", params=params,
                )
            except httpx.HTTPError as exc:
                logger.info("doppler._find_launch_block_via_blockscout: request failed (%s)", exc)
                break
            if resp.status_code != 200:
                break
            try:
                data = resp.json()
            except ValueError:
                break
            items = data.get("items") or []
            if items:
                oldest_block = items[-1].get("block_number")
            next_page = data.get("next_page_params")
            if not next_page:
                break
            params = next_page
    return oldest_block


# >90% of new Base pools route through Doppler's Uniswap v4 PoolManager
# (Pantera/The Block sourced, 14/08) -- a raw block-range scan of every
# Initialize event (no argument_filters, unlike find_pool's targeted lookup)
# is the highest-leverage discovery source ARIA doesn't have yet. Kept well
# under the empirically-confirmed 413 threshold (module docstring above:
# even 5000-20000 blocks overflow on this heavily-used contract).
_DISCOVERY_WINDOW_BLOCKS = 500  # ~15-20min of Base blocks (~2s/block)


def discover_new_pools(from_block: int, to_block: int, *, w3=None) -> list[dict] | None:
    """Raw scan of every PoolManager ``Initialize`` event in
    ``[from_block, to_block]`` -- unlike ``find_pool``, no ``argument_filters``,
    so this surfaces EVERY pool created in the window, not just one known
    token. Returns a list of ``{"pool_id", "currency0", "currency1", "hooks"}``
    dicts (possibly empty if the window genuinely had no launches), or
    ``None`` on RPC failure -- never a fabricated empty result standing in
    for a real failure (callers must be able to tell the two apart to avoid
    silently skipping a window)."""
    try:
        client = _client(w3=w3)
        pool_manager = client.eth.contract(
            address=client.to_checksum_address(POOL_MANAGER_ADDRESS), abi=_POOL_MANAGER_ABI
        )
        logs = pool_manager.events.Initialize().get_logs(from_block=from_block, to_block=to_block)
        return [
            {
                "pool_id": log["args"]["id"],
                "currency0": log["args"]["currency0"],
                "currency1": log["args"]["currency1"],
                "hooks": log["args"]["hooks"],
            }
            for log in logs
        ]
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "doppler.discover_new_pools: RPC read failed for blocks %s-%s (%s)", from_block, to_block, exc,
        )
        return None


async def discover_recent_pools(*, window_size: int = _DISCOVERY_WINDOW_BLOCKS, w3=None, current_block: int | None = None) -> list[dict]:
    """Persistent-cursor wrapper around ``discover_new_pools`` -- advances a
    ``SingleRowStore``-backed ``last_scanned_block`` by at most
    ``window_size`` blocks per call, so repeated calls (the heartbeat's
    ``watchlist_refill_cycle``, every 15min) progressively cover the chain
    without ever re-scanning the same blocks twice or attempting a blind
    full-history scan.

    First-ever call starts near ``current_block - window_size`` (never from
    block 0 -- a full-history scan is empirically rejected by the public RPC,
    see module docstring). On RPC failure the cursor is NOT advanced, so the
    same window is retried on the next call rather than silently skipped.
    Returns ``[]`` (never ``None``) on failure or when already caught up to
    the chain tip -- this is the discovery-source contract callers rely on."""
    try:
        client = _client(w3=w3)
        tip = current_block if current_block is not None else client.eth.block_number
    except Exception as exc:  # noqa: BLE001
        logger.info("doppler.discover_recent_pools: could not read chain tip (%s)", exc)
        return []

    store = _scan_cursor_store()
    await store.ensure_table()
    row = await store.read("last_scanned_block")
    last_scanned = row[0] if row else 0

    if last_scanned <= 0:
        from_block = max(0, tip - window_size)
    else:
        from_block = last_scanned + 1

    if from_block > tip:
        return []

    to_block = min(from_block + window_size - 1, tip)

    pools = discover_new_pools(from_block, to_block, w3=w3)
    if pools is None:
        return []

    await store.write({"last_scanned_block": to_block})
    return pools


async def get_token_price_usd(token_address: str, *, token_decimals: int = 18, w3=None) -> float | None:
    """Full pipeline: find the token's Doppler/Uniswap-v4 pool, read its
    CURRENT price, convert to USD via the real WETH/USD rate. ``None`` at any
    stage (no pool found, RPC read failed, rate unavailable) -- never a
    fabricated price. Assumes WETH as the numeraire (the observed case for
    every Doppler/Bankr launch checked so far) -- a pool paired against a
    different numeraire (e.g. USDC directly) is out of scope for this first
    version, returns ``None`` rather than a wrong conversion.

    Locates the token's launch block via Blockscout FIRST (see
    ``_find_launch_block_via_blockscout``) so ``find_pool`` only ever scans a
    tiny, RPC-safe window around it -- a blind ``from_block=0`` scan is
    empirically rejected by the public RPC (413) for this heavily-used
    PoolManager. ``None`` (never a fallback full scan) if the launch block
    can't be found -- a slow/likely-to-fail scan is worse than an honest
    "unavailable"."""
    launch_block = await _find_launch_block_via_blockscout(token_address)
    if launch_block is None:
        logger.info("doppler.get_token_price_usd: launch block not found for %s", token_address)
        return None

    pool = find_pool(
        token_address, w3=w3,
        from_block=max(0, launch_block - _LAUNCH_BLOCK_SEARCH_MARGIN),
        to_block=launch_block + _LAUNCH_BLOCK_SEARCH_MARGIN,
    )
    if pool is None:
        return None

    currency0 = pool["currency0"].lower()
    currency1 = pool["currency1"].lower()
    token_lower = token_address.lower()
    weth_lower = WETH_ADDRESS.lower()
    token_is_currency0 = token_lower == currency0
    other_currency = currency1 if token_is_currency0 else currency0
    if other_currency != weth_lower:
        logger.info(
            "doppler.get_token_price_usd: pool numeraire %s is not WETH -- out of scope for now",
            other_currency,
        )
        return None

    slot0 = read_slot0(pool["pool_id"], w3=w3)
    if slot0 is None:
        return None
    sqrt_price_x96, _tick = slot0

    weth_decimals = 18
    decimals0 = token_decimals if token_is_currency0 else weth_decimals
    decimals1 = weth_decimals if token_is_currency0 else token_decimals
    price_in_weth = price_from_sqrt_price_x96(
        sqrt_price_x96, token_is_currency0=token_is_currency0, decimals0=decimals0, decimals1=decimals1,
    )

    rate = await eth_usd_rate()
    if rate is None:
        return None
    return price_in_weth * rate
