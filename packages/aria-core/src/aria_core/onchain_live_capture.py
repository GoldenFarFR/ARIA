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

# THREE clocks, never conflated (operator, 02/09). They answer different
# questions and mixing them would make both unanswerable:
#   event_time     -- when it happened ON CHAIN (block_timestamp). The ONLY one
#                     causality may use; a feature computed from anything else
#                     would leak our own latency into the analysis.
#   observed_time  -- when the WebSocket handed it to us. observed - event = how
#                     far behind reality ARIA sees the market.
#   persisted_time -- when SQLite accepted it. persisted - observed = our own
#                     pipeline's cost, distinct from the network's.
# A system that captures every event but two minutes late is complete AND
# useless for the fast regime -- these two deltas are what tells them apart.

# Completeness counters. "received" vs "persisted" is the whole point: a gap
# between them must be impossible to ignore, because a silently lossy capture
# produces a dataset that looks complete and is not.
_stats = {
    "received": 0,        # events handed to record_event
    "buffered": 0,        # accepted into the buffer (known topic)
    "ignored_topic": 0,   # real events we do not track -- not a loss
    "persisted": 0,       # rows actually written
    "failed": 0,          # writes that raised
    "gaps_detected": 0,   # block-number discontinuities seen on the feed
}
# Highest block seen per chain, to detect discontinuities in the live stream.
_last_block: dict[str, int] = {}
_gaps: list[tuple[str, int, int]] = []   # (chain, after_block, before_block)


def stats() -> dict:
    """Capture counters. ``received`` vs ``persisted`` is the completeness check.

    Read from the live process, so it reflects THIS process's view -- the
    durable count always comes from the table itself (``live_stats``).
    """
    out = dict(_stats)
    out["pending"] = len(_buffer)
    out["recent_gaps"] = list(_gaps[-20:])
    return out

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
        for col, ddl in (
            ("source", f"TEXT NOT NULL DEFAULT '{SOURCE_BACKFILL}'"),
            # observed_at: when the WebSocket handed us the event. Kept apart
            # from fetched_at (which the backfill uses as its own write stamp)
            # so observed - event measures network latency and nothing else.
            ("observed_at", "TEXT"),
        ):
            if col not in cols:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col} {ddl}")
                logger.info("onchain_live_capture: added `%s` column to %s", col, TABLE)
        await db.commit()


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
        _stats["received"] += 1
        topic0 = (topics[0] if topics else "").lower()
        event_type = _TOPIC_TO_EVENT.get(topic0)
        if event_type is None:
            # Not a loss: a real event we deliberately do not track. Counted
            # separately so it can never be mistaken for a dropped one.
            _stats["ignored_topic"] += 1
            return

        # Block-number discontinuity on the feed. Not proof of loss -- a chain
        # can simply have no tracked event in a block -- but a gap we never
        # noticed is a gap we can never check against eth_getLogs later.
        bn = int(block_number)
        prev = _last_block.get(chain)
        if prev is not None and bn > prev + 1:
            _gaps.append((chain, prev, bn))
            _stats["gaps_detected"] += 1
        if prev is None or bn > prev:
            _last_block[chain] = bn

        _stats["buffered"] += 1
        _buffer.append((
            chain, (token or "").lower(), pool_id, int(block_number), None,
            tx_hash, int(log_index), event_type,
            json.dumps(list(topics)), data_hex or "", None, provider,
            _now_iso(), SOURCE_LIVE, _now_iso(),
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
        # The table may not exist yet: in production the live feed can start
        # before any backfill has ever run, and a flush into a missing table
        # would drop the buffer silently. Found by the acceptance test, which
        # is exactly the scenario it was written to catch.
        from aria_core.onchain_replay_backfill import _ensure_tables

        await _ensure_tables()
        await ensure_source_column()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executemany(
                f"INSERT INTO {TABLE} "
                f"(chain, token, pool_id, block_number, block_timestamp, tx_hash, "
                f"log_index, event_type, topics_json, data_hex, tx_sender, "
                f"rpc_provider, fetched_at, source, observed_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                f"ON CONFLICT(chain, tx_hash, log_index) DO UPDATE SET "
                f"block_timestamp=COALESCE(excluded.block_timestamp, block_timestamp), "
                f"tx_sender=COALESCE(excluded.tx_sender, tx_sender)",
                rows,
            )
            await db.commit()
        _last_flush = now
        _stats["persisted"] += len(rows)
        return len(rows)
    except Exception as exc:  # noqa: BLE001 -- a failed write must not kill the feed
        _stats["failed"] += len(rows)
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


async def completeness_report(chain: str) -> dict:
    """Is the capture actually complete, and how far behind is it?

    Answers the two questions the acceptance test could NOT (operator, 02/09):
    that test proved the engine reconstructs the same state from the same
    events; this measures whether the live path receives all the events it
    should, and how late.

    ``received`` vs ``persisted`` is the loss check. The latency percentiles
    are the delay check -- a capture that misses nothing but runs two minutes
    behind is complete and useless for the fast regime, and only these two
    numbers together tell them apart.
    """
    # Readers guarantee the schema too: only flush() used to run the
    # migration, so any read before the first write hit "no such column:
    # source". Found on the live deployment, one minute after wiring.
    await ensure_source_column()
    counters = stats()
    lost = counters["buffered"] - counters["persisted"] - counters["pending"]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT COUNT(*) rows, COUNT(DISTINCT pool_id) pools, "
            f"SUM(block_timestamp IS NULL) missing_ts, "
            f"MIN(block_number) first_block, MAX(block_number) last_block "
            f"FROM {TABLE} WHERE chain=? AND source=?",
            (chain, SOURCE_LIVE),
        )
        stored = dict(await cur.fetchone() or {})
        # observed - event, on rows where both clocks exist. Computed in SQL so
        # it reflects what was STORED, never a value held in this process.
        cur = await db.execute(
            f"SELECT (strftime('%s', observed_at) - block_timestamp) lag "
            f"FROM {TABLE} WHERE chain=? AND source=? "
            f"AND observed_at IS NOT NULL AND block_timestamp IS NOT NULL "
            f"ORDER BY id DESC LIMIT 500",
            (chain, SOURCE_LIVE),
        )
        lags = sorted(r["lag"] for r in await cur.fetchall() if r["lag"] is not None)
    latency = None
    if lags:
        latency = {
            "n": len(lags),
            "p50_s": lags[len(lags) // 2],
            "p95_s": lags[int(len(lags) * 0.95)],
            "max_s": lags[-1],
        }
    return {
        "counters": counters,
        "stored": stored,
        # Non-zero means events entered the buffer and never reached storage.
        # Surfaced as its own field precisely so it cannot hide inside a ratio.
        "lost_events": max(0, lost),
        "capture_latency": latency,
        "latency_note": None if latency else "no row carries both clocks yet",
    }


async def verify_against_chain(chain: str, pool_id: str, from_block: int, to_block: int) -> dict:
    """Sample check: does the live capture hold what ``eth_getLogs`` reports?

    The chain is the oracle -- our own feed cannot validate its own
    completeness (a system's own data never validates that system, per the
    22/08 rule). Deliberately a SAMPLED check over a bounded window, not a
    permanent parallel: running it continuously would double the cost of the
    thing it audits.
    """
    from aria_core.onchain_replay_backfill import (
        _Rpc, rpc_url, pool_manager_for, MAX_BLOCK_RANGE,
        _V4_SWAP_TOPIC, _V4_MODIFY_LIQUIDITY_TOPIC,
    )

    url = rpc_url(chain)
    if not url:
        return {"error": "no RPC configured"}
    if to_block - from_block > MAX_BLOCK_RANGE:
        return {"error": f"window too wide (max {MAX_BLOCK_RANGE} blocks)"}

    async with _Rpc(chain, url) as rpc:
        logs = await rpc.call("eth_getLogs", [{
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
            "address": pool_manager_for(chain),
            "topics": [[_V4_SWAP_TOPIC, _V4_MODIFY_LIQUIDITY_TOPIC], [pool_id]],
        }]) or []
        calls = rpc.calls
    on_chain = {(l["transactionHash"].lower(), int(l["logIndex"], 16)) for l in logs}

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"SELECT LOWER(tx_hash), log_index FROM {TABLE} "
            f"WHERE chain=? AND pool_id=? AND block_number BETWEEN ? AND ?",
            (chain, pool_id, from_block, to_block),
        )
        captured = {(r[0], r[1]) for r in await cur.fetchall()}

    missing = on_chain - captured
    return {
        "on_chain": len(on_chain),
        "captured": len(captured),
        "missing": len(missing),
        # Complete only when nothing the chain reports is absent from storage.
        # Extra rows are not a failure (the backfill may have covered more).
        "complete": not missing,
        "sample_missing": sorted(missing)[:5],
        "rpc_calls": calls,
    }


async def live_stats(chain: str) -> dict:
    """What the live path has actually stored -- read from storage, never memory."""
    # Readers guarantee the schema too: only flush() used to run the
    # migration, so any read before the first write hit "no such column:
    # source". Found on the live deployment, one minute after wiring.
    await ensure_source_column()
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
