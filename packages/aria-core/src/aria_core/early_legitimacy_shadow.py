"""Early on-chain legitimacy shadow observer (15/08, operator research thread
on "how do we tell a real team from noise at token creation" -- logs, NEVER
blocks, NEVER touches a real/paper trade).

Real gap this closes: the existing legitimacy engine (``skills/dev_wallet.py``,
``skills/safety_screen.py``, ``skills/acp_onchain_scan.py``) depends entirely
on ``services/blockscout.py``, which structurally lags for brand-new tokens
(confirmed live, 15/08: real fresh Base tokens sat on ``honeypot_pending``/
``holder_concentration_unverifiable`` for the first several minutes-to-hours
of their life). That engine is correct for the VC pocket's already-days-old
candidates, but useless at the moment a token is born.

This module computes 2 signals via DIRECT RPC only, zero Blockscout/GoPlus
dependency, verified against the real codebase (workflow audit, 15/08)
before being built:

1. ``owner_renounced`` -- a best-effort ``owner()`` view call (OpenZeppelin
   convention only; most memecoins skip ownership entirely, in which case
   this stays ``None`` -- never a bad mark for an absent function).
2. ``lp_locked_or_burned_pct`` -- reconstructs current LP-token balances by
   scanning ``Transfer`` events on the DEX pair contract (Uniswap-v2-style
   pairs on Base double as their own LP-token ERC20) since the pool's own
   creation, chunked in <=500-block windows -- ``services/doppler.py``'s own
   docstring documents the public Base RPC (``mainnet.base.org``) REJECTING
   wide ``eth_getLogs`` ranges with 413 Payload Too Large (confirmed
   empirically, even 5000-20000 blocks on a heavily-used contract), so this
   reuses that exact empirically-safe window rather than a guessed one.
   Uniswap-v4 (Doppler) pools have no fungible LP token to scan -- this
   signal stays ``None`` for those, an honest partial-coverage gap, same
   doctrine as ``base_onchain.py``'s own documented partial coverage.

Explicitly OUT of scope for this first version (found by the same audit):
the deployer wallet's funding source and cross-token history -- both need a
real indexer to trace, no honest RPC-only shortcut exists. Those stay on the
existing (slower, Blockscout-backed) ``dev_wallet.py`` path, available once
indexing catches up, same as today.

No proactive numeric RPC throttle: ``mainnet.base.org``'s real per-second
capacity is undocumented and unverified (same "unknown capacity" case as
CLAUDE.md's calibration doctrine already covers for other providers) -- this
module relies on the existing reactive dome (best-effort try/except, never
raises, retried next cycle) plus a small per-passage batch size, rather than
inventing a false-precision number.

Same shadow design as ``candle_staleness_shadow.py`` (deliberately mirrored):
dedicated table, one row per (contract, chain),
best-effort writes that NEVER raise into a real fetch path, per-DB-path
ensure cache. Pure observation -- no gate anywhere reads this table yet.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

_DEFAULT_RPC_URL = "https://mainnet.base.org"

# Known burn/dead addresses -- LP tokens sent here are permanently
# unrecoverable by the deployer, the strongest available "won't rug the
# pool" signal. Deliberately NOT attempting to enumerate every known LP
# locker contract (Unicrypt, Team Finance, etc.) in this first version --
# burn-to-dead is the single most common and unambiguous pattern; a locker
# registry is a future extension, not a blocker for shadow observation.
_BURN_ADDRESSES = {
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000000",
}

# services/doppler.py's own docstring: eth_getLogs on mainnet.base.org
# empirically rejects (413) wide ranges, even 5000-20000 blocks on a
# heavily-used contract -- 500 is the same window already validated safe in
# prod there (discover_new_pools/discover_recent_pools).
_LP_SCAN_CHUNK_BLOCKS = 500
# Bounds a single token's worst-case scan cost: 4 chunks * 500 blocks =
# ~2000 blocks =~ 1.1h at Base's ~2s/block (documented alongside this same
# constant in doppler.py) -- comfortably covers the "early legitimacy"
# window this module cares about without an unbounded scan.
_LP_SCAN_MAX_CHUNKS = 4
_BASE_SECONDS_PER_BLOCK = 2.0

# Tokens older than this at evaluation time are skipped (not re-attempted
# forever) -- past this point they're no longer "early legitimacy" data
# points, and the LP-scan window above wouldn't reach back to their real
# creation anyway. Bounds the pending backlog to a rolling window instead of
# growing forever on tokens the collector never got to in time.
MAX_TOKEN_AGE_HOURS = 6.0

_TRANSFER_EVENT_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    }
]

_OWNER_ABI = [
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # every other shadow module in this codebase.
    return DB_PATH


def _rpc_url() -> str:
    return (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _client(*, w3=None):
    if w3 is not None:
        return w3
    from web3 import Web3

    return Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 10}))


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS early_legitimacy_shadow_log (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                owner_renounced INTEGER,
                lp_pair_address TEXT,
                lp_locked_or_burned_pct REAL,
                lp_scan_blocks_covered INTEGER,
                lp_scan_complete INTEGER,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_early_legitimacy_shadow_computed_at "
            "ON early_legitimacy_shadow_log (computed_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


def owner_renounced(token_address: str, *, w3=None) -> bool | None:
    """Best-effort ``owner()`` read. ``None`` when the contract has no such
    function (most memecoins), the RPC call fails, or the address is
    invalid -- never a fabricated verdict, never a bad mark for a token that
    simply doesn't implement OpenZeppelin's ``Ownable``."""
    if not token_address:
        return None
    try:
        client = _client(w3=w3)
        contract = client.eth.contract(
            address=client.to_checksum_address(token_address), abi=_OWNER_ABI
        )
        owner = contract.functions.owner().call()
        return owner.lower() == "0x0000000000000000000000000000000000000000"
    except Exception as exc:  # noqa: BLE001 -- absent/reverting owner() is the common case, not an error
        logger.info(
            "early_legitimacy_shadow.owner_renounced: no owner() or RPC failed for %s (%s)",
            token_address, exc,
        )
        return None


def lp_lock_snapshot(
    lp_pair_address: str, *, w3=None, pair_age_seconds: float | None = None,
    chunk_blocks: int = _LP_SCAN_CHUNK_BLOCKS, max_chunks: int = _LP_SCAN_MAX_CHUNKS,
) -> dict | None:
    """Reconstructs current LP-token balances by scanning ``Transfer`` events
    on the pair contract (a Uniswap-v2-style pair IS its own LP-token ERC20
    on Base) since roughly the pool's creation, chunked in <=``chunk_blocks``
    windows (doppler.py's empirically-safe limit). ``pair_age_seconds`` (when
    known, e.g. DexScreener's ``pairCreatedAt``) bounds the scan to the
    token's REAL age instead of always spending the full ``max_chunks``
    budget on a token that's only minutes old.

    Returns ``{"locked_or_burned_pct": float | None, "blocks_covered": int,
    "complete": bool}`` on a successful (possibly partial) scan, or ``None``
    on total RPC failure (bad address, non-ERC20 contract, e.g. a Uniswap-v4
    pool with no fungible LP token). ``complete=False`` means a chunk failed
    mid-scan (partial coverage, never silently treated as a full scan) or
    the token is older than the total window this function is willing to
    cover -- honest partial coverage, never a fabricated 100%."""
    if not lp_pair_address:
        return None
    try:
        client = _client(w3=w3)
        tip = client.eth.block_number
    except Exception as exc:  # noqa: BLE001
        logger.info("early_legitimacy_shadow.lp_lock_snapshot: could not read chain tip (%s)", exc)
        return None

    max_window_blocks = chunk_blocks * max_chunks
    if pair_age_seconds is not None and pair_age_seconds >= 0:
        wanted_blocks = int(pair_age_seconds / _BASE_SECONDS_PER_BLOCK) + chunk_blocks
        window_blocks = min(wanted_blocks, max_window_blocks)
    else:
        window_blocks = max_window_blocks
    from_block = max(0, tip - window_blocks)

    try:
        contract = client.eth.contract(
            address=client.to_checksum_address(lp_pair_address), abi=_TRANSFER_EVENT_ABI
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("early_legitimacy_shadow.lp_lock_snapshot: bad pair address %s (%s)", lp_pair_address, exc)
        return None

    balances: dict[str, int] = {}
    blocks_covered = 0
    complete = True
    chunk_start = from_block
    while chunk_start <= tip:
        chunk_end = min(chunk_start + chunk_blocks - 1, tip)
        try:
            logs = contract.events.Transfer().get_logs(from_block=chunk_start, to_block=chunk_end)
        except Exception as exc:  # noqa: BLE001 -- stop, keep what was already gathered
            logger.info(
                "early_legitimacy_shadow.lp_lock_snapshot: chunk %s-%s failed for %s (%s)",
                chunk_start, chunk_end, lp_pair_address, exc,
            )
            complete = False
            break
        for log in logs:
            frm = log["args"]["from"].lower()
            to = log["args"]["to"].lower()
            value = int(log["args"]["value"])
            balances[frm] = balances.get(frm, 0) - value
            balances[to] = balances.get(to, 0) + value
        blocks_covered += (chunk_end - chunk_start + 1)
        chunk_start = chunk_end + 1

    if pair_age_seconds is not None and window_blocks < int(pair_age_seconds / _BASE_SECONDS_PER_BLOCK):
        # The pool is older than the max scan budget could reach -- this
        # snapshot never saw the real earliest transfers, honestly flagged.
        complete = False

    total = sum(v for v in balances.values() if v > 0)
    if total <= 0:
        return {"locked_or_burned_pct": None, "blocks_covered": blocks_covered, "complete": complete}
    burned = sum(v for addr, v in balances.items() if addr in _BURN_ADDRESSES and v > 0)
    pct = (burned / total) * 100.0
    return {"locked_or_burned_pct": pct, "blocks_covered": blocks_covered, "complete": complete}


async def record_observation(
    contract: str,
    chain: str,
    *,
    symbol: str | None = None,
    lp_pair_address: str | None = None,
    pair_age_seconds: float | None = None,
    w3=None,
) -> None:
    """Computes both signals and logs one row (upserted on re-evaluation).
    Best-effort throughout: NEVER raises into the caller's cycle (same
    contract as every other shadow module in this codebase)."""
    if not contract:
        return
    owner_r = owner_renounced(contract, w3=w3)
    lp_result = lp_lock_snapshot(lp_pair_address, w3=w3, pair_age_seconds=pair_age_seconds) if lp_pair_address else None
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                """
                INSERT INTO early_legitimacy_shadow_log (
                    contract, chain, symbol, owner_renounced, lp_pair_address,
                    lp_locked_or_burned_pct, lp_scan_blocks_covered, lp_scan_complete,
                    computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (contract, chain) DO UPDATE SET
                    symbol = excluded.symbol,
                    owner_renounced = excluded.owner_renounced,
                    lp_pair_address = excluded.lp_pair_address,
                    lp_locked_or_burned_pct = excluded.lp_locked_or_burned_pct,
                    lp_scan_blocks_covered = excluded.lp_scan_blocks_covered,
                    lp_scan_complete = excluded.lp_scan_complete,
                    computed_at = excluded.computed_at
                """,
                (
                    contract, chain or "base", symbol,
                    None if owner_r is None else int(owner_r),
                    lp_pair_address,
                    lp_result.get("locked_or_burned_pct") if lp_result else None,
                    lp_result.get("blocks_covered") if lp_result else None,
                    None if not lp_result else int(lp_result.get("complete", False)),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the real collector
        logger.info("early_legitimacy_shadow: record failed for %s/%s (%s)", chain, contract[:10], exc)


async def already_computed(contract: str, chain: str) -> bool:
    """True once a row exists for this (contract, chain) -- the collector
    cycle's own "already done" check, so a token is evaluated exactly once."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            "SELECT 1 FROM early_legitimacy_shadow_log WHERE contract = ? AND chain = ?",
            (contract, chain),
        )
        return (await cur.fetchone()) is not None


async def list_recent(limit: int = 200) -> list[dict]:
    """Recent shadow observations, newest first -- for the future
    forward-price-correlation pass (does a high lock percentage / renounced
    ownership actually predict anything, once enough observations
    accumulate)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM early_legitimacy_shadow_log ORDER BY computed_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


# Forward price tracking deliberately does NOT introduce a second write path
# -- it reads whatever candle_history_watchlist_cycle already collected for
# this token (same doctrine as dip_recovery_shadow_cycle: zero extra network
# call). Read-only helpers, computed on demand rather than stored, so a
# later change to the horizon set (e.g. adding +3d) never requires a
# migration or a backfill.
_PRICE_LOOKUP_TOLERANCE_HOURS = 2.0
FORWARD_HORIZONS_HOURS = {"1h": 1.0, "6h": 6.0, "24h": 24.0, "7d": 168.0}


async def price_at_horizon(
    contract: str, chain: str, from_iso: str, hours: float,
    *, tolerance_hours: float = _PRICE_LOOKUP_TOLERANCE_HOURS,
) -> float | None:
    """Closest ``candle_history`` close price to (``from_iso`` + ``hours``),
    across whichever timeframe(s) happen to be recorded for this token
    (timeframe is inferred per-series by candle_history.py itself, never
    assumed here). ``None`` if nothing falls within ``tolerance_hours`` of
    the target -- never a fabricated/misleading match across a real data
    gap (e.g. the collector hadn't reached this token yet at that time)."""
    try:
        from_dt = datetime.fromisoformat(from_iso)
    except ValueError:
        return None
    target_ts = int(from_dt.timestamp() + hours * 3600)
    tolerance_seconds = tolerance_hours * 3600
    await _ensure_table()  # harmless if already ensured; keeps this fn standalone-callable
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            "SELECT ts, close FROM candle_history WHERE contract = ? AND chain = ? "
            "ORDER BY ABS(ts - ?) ASC LIMIT 1",
            (contract, chain, target_ts),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    ts, close = row
    if abs(ts - target_ts) > tolerance_seconds:
        return None
    return close


async def forward_price_deltas_pct(contract: str, chain: str, computed_at_iso: str) -> dict[str, float | None]:
    """Percent price change from the shadow-evaluation moment to each of
    ``FORWARD_HORIZONS_HOURS`` -- ``None`` per horizon when either the entry
    anchor or that horizon's candle isn't available within tolerance (never
    a fabricated data point). This is what a future correlation pass (does
    a high LP-lock percentage / renounced ownership actually predict
    anything) would read, once enough shadow observations accumulate --
    same anti-overfitting doctrine as every other shadow module in this
    codebase: observe first, never gate on an unvalidated signal."""
    entry = await price_at_horizon(contract, chain, computed_at_iso, 0.0, tolerance_hours=1.0)
    deltas: dict[str, float | None] = {}
    for label, hours in FORWARD_HORIZONS_HOURS.items():
        later = await price_at_horizon(contract, chain, computed_at_iso, hours)
        if entry is None or later is None or entry <= 0:
            deltas[label] = None
        else:
            deltas[label] = (later / entry - 1.0) * 100.0
    return deltas
