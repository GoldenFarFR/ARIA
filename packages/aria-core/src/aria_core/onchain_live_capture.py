"""Persist LIVE swap events into the same raw table the backfill writes.

Why (02/09, operator-validated): the replay engine reads
``onchain_replay_raw``, which today only the historical backfill fills. So
``build_trajectory`` works on any token we reconstructed and on none that ARIA
is watching right now. Feeding the live WebSocket into the SAME table with the
SAME schema means one analytical engine for past and present -- rather than a
second implementation that would drift and give two answers to one question.

**Five constraints the operator set, each enforced here rather than promised:**

1. *Write-only.* Nothing in this module reads a threshold, touches a filter, or
   returns anything a decision could branch on. It appends rows.
2. *Same format as the backfill.* Identical columns, identical raw payload
   (``topics_json`` / ``data_hex`` verbatim). A live row and a backfilled row
   must be indistinguishable except for their ``source``.
3. *No computation here.* Decoding stays in ``onchain_trajectory``; this stores
   the bytes. A price computed at capture time could never be recomputed if the
   decoding turns out wrong.
4. *Causality is preserved for free*, because a live row can only carry a block
   that already happened -- and ``build_trajectory`` filters on
   ``block_timestamp <= t`` regardless of who wrote the row.
5. *Explicit provenance.* ``source='live'`` vs ``source='backfill'``, so an
   audit can always tell which path produced a row even though the format is
   identical.

**Never raises into the feed.** ``evm_swap_ws`` is production plumbing for the
trading pockets; a failure here must cost a stored row, never a dropped price
update. Every call is wrapped and swallowed, same posture as
``momentum_signal_observation.capture_observation``.

**Timestamps are the one thing we cannot get for free.** A log notification
carries a block NUMBER, not its time. Resolving each one would mean an RPC call
per event on a feed that already bills 1 RU per push. So rows land with
``block_timestamp = NULL`` and a separate pass fills them in bulk -- which the
backfill's ``ON CONFLICT DO UPDATE`` already supports, since it was built so a
cheap first pass could be completed by a richer second one.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.onchain_replay_backfill import (
    DB_PATH,
    EVENT_MODIFY_LIQUIDITY,
    EVENT_SWAP,
    TABLE,
    _TOPIC_TO_EVENT,
)

logger = logging.getLogger(__name__)

SOURCE_LIVE = "live"
SOURCE_BACKFILL = "backfill"

# Rows are buffered and flushed in batches: a busy pool emits dozens of swaps
# per second (Money Mushroom: 13,333 events in 20 minutes), and one INSERT per
# event would put a SQLite write on the WebSocket's own event loop.
_FLUSH_EVERY = 50
_FLUSH_SECONDS = 5.0

_buffer: list[tuple] = []
_last_flush: float = 0.0
_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_source_column() -> None:
    """Add ``source`` to the raw table if an older schema predates it.

    Additive migration, run once at wiring time: existing rows default to
    ``backfill``, which is what they are -- the column exists to tell the two
    paths apart, and every row written before it was introduced came from the
    historical path by definition.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"PRAGMA table_info({TABLE})")
        cols = {r[1] for r in await cur.fetchall()}
        if not cols:
            return  # table not created yet; the backfill's DDL will include it
        if "source" not in cols:
            await db.execute(
                f"ALTER TABLE {TABLE} ADD COLUMN source TEXT NOT NULL DEFAULT '{SOURCE_BACKFILL}'"
            )
            await db.commit()
            logger.info("onchain_live_capture: added `source` column to %s", TABLE)


def record_event(
    *,
    chain: str,
    token: str,
    pool_id: str,
    block_number: int,
    tx_hash: str,
    log_index: int,
    topics: list[str],
    data_hex: str,
    provider: str = "chainstack",
) -> None:
    """Buffer one live event. Cheap, synchronous, never raises.

    Called from the WebSocket handler, so it must not await and must not fail:
    a swallowed exception costs one stored row, a raised one could kill the feed
    that the trading pockets depend on.
    """
    try:
        topic0 = (topics[0] if topics else "").lower()
        event_type = _TOPIC_TO_EVENT.get(topic0)
        if event_type is None:
            return
        _buffer.append((
            chain, (token or "").lower(), pool_id, int(block_number), None,
            tx_hash, int(log_index), event_type,
            json.dumps(list(topics)), data_hex or "", None, provider,
            _now_iso(), SOURCE_LIVE,
        ))
    except Exception as exc:  # noqa: BLE001 -- never bubbles into the feed
        logger.info("onchain_live_capture: buffering failed (%s)", type(exc).__name__)


def pending() -> int:
    """How many events are buffered but not yet written -- for diagnostics."""
    return len(_buffer)


async def flush(force: bool = False) -> int:
    """Write the buffer. Returns how many rows were persisted.

    ``ON CONFLICT DO UPDATE`` rather than ``OR IGNORE`` so a row the backfill
    already wrote is completed rather than skipped, and vice versa -- the two
    paths can cover the same block without either erasing the other's work.
    """
    global _last_flush
    if not _buffer:
        return 0
    now = asyncio.get_event_loop().time()
    if not force and len(_buffer) < _FLUSH_EVERY and (now - _last_flush) < _FLUSH_SECONDS:
        return 0

    async with _lock:
        rows, _buffer[:] = list(_buffer), []
    if not rows:
        return 0
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executemany(
                f"INSERT INTO {TABLE} "
                f"(chain, token, pool_id, block_number, block_timestamp, tx_hash, "
                f"log_index, event_type, topics_json, data_hex, tx_sender, "
                f"rpc_provider, fetched_at, source) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                f"ON CONFLICT(chain, tx_hash, log_index) DO UPDATE SET "
                f"block_timestamp=COALESCE(excluded.block_timestamp, block_timestamp), "
                f"tx_sender=COALESCE(excluded.tx_sender, tx_sender)",
                rows,
            )
            await db.commit()
        _last_flush = now
        return len(rows)
    except Exception as exc:  # noqa: BLE001 -- a failed write must not kill the feed
        logger.info("onchain_live_capture: flush failed (%s)", type(exc).__name__)
        return 0


async def backfill_missing_timestamps(chain: str, *, limit: int = 500) -> dict:
    """Fill ``block_timestamp`` for live rows that landed without one.

    Separate from capture on purpose: resolving a timestamp costs one RPC call
    per BLOCK, and doing it inline would add a call to every push on a feed
    already billed per notification. Batched here, one call per distinct block,
    so a busy pool with 50 events in one block costs one call rather than 50.
    """
    from aria_core.onchain_replay_backfill import _Rpc, rpc_url

    url = rpc_url(chain)
    if not url:
        return {"resolved": 0, "error": "no RPC configured"}

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"SELECT DISTINCT block_number FROM {TABLE} "
            f"WHERE chain=? AND source=? AND block_timestamp IS NULL "
            f"ORDER BY block_number DESC LIMIT ?",
            (chain, SOURCE_LIVE, limit),
        )
        blocks = [r[0] for r in await cur.fetchall()]
    if not blocks:
        return {"resolved": 0, "blocks": 0}

    resolved: dict[int, int] = {}
    async with _Rpc(chain, url) as rpc:
        for bn in blocks:
            blk = await rpc.call("eth_getBlockByNumber", [hex(int(bn)), False])
            if blk and blk.get("timestamp"):
                resolved[bn] = int(blk["timestamp"], 16)
        calls = rpc.calls

    if not resolved:
        return {"resolved": 0, "blocks": len(blocks), "rpc_calls": calls}
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            f"UPDATE {TABLE} SET block_timestamp=? "
            f"WHERE chain=? AND block_number=? AND block_timestamp IS NULL",
            [(ts, chain, bn) for bn, ts in resolved.items()],
        )
        await db.commit()
    return {"resolved": len(resolved), "blocks": len(blocks), "rpc_calls": calls}


async def live_stats(chain: str) -> dict:
    """What the live path has actually stored -- read from storage, never memory."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT COUNT(*) n, COUNT(DISTINCT pool_id) pools, "
            f"SUM(block_timestamp IS NULL) missing_ts, MAX(block_number) last_block "
            f"FROM {TABLE} WHERE chain=? AND source=?",
            (chain, SOURCE_LIVE),
        )
        row = await cur.fetchone()
    return dict(row) if row else {}
