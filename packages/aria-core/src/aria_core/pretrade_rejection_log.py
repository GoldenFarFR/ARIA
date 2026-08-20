"""Persistent log of PRE-TRADE rejections (20/08, operator-directed).

**Why this table has to exist at all.** The pre-trade holder gate's whole
value is that a blocked candidate never becomes a position -- no row in the
pocket's own table, no entry fee, no loss. But that also means the rejection
leaves ZERO trace: without this log, every blocked candidate's top holder
concentration, the gate's real latency, and the price we would have paid are
lost the instant the coroutine returns. Nothing could then answer the only
question that matters -- "is the filter actually saving money, or is it
cutting the winners?".

This is the proactive-ingestion doctrine applied at the moment the mechanism
is built rather than after: the filter and its measurement ship together.

**What "avoided PnL" means here, honestly.** Storing the price we would have
entered at is not by itself a measurement. `advance_avoided_tracking()` polls
each rejected candidate's price afterwards, exactly as if a position had been
opened, so the counterfactual is measured on real subsequent prices rather
than assumed. Two deliberate honesty constraints:
  - The would-be entry price is the SLIPPAGE-ADJUSTED one (same
    ``_apply_price_impact_and_fee`` the real pockets use). Comparing a raw
    mid price against a real exit would flatter the filter.
  - Tracking is bounded (``TRACKING_WINDOW_MINUTES``) and rejections whose
    price can no longer be resolved are marked ``unresolvable`` rather than
    silently dropped -- a pool that went dark IS the outcome, and dropping
    those would bias the result toward whichever tokens survived.

ACCEPTED candidates are logged too, not just rejections. A filter can only be
judged against what it let through; a table of rejections alone cannot tell a
good cut from an indiscriminate one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from .paths import shadow_db_path

TABLE = "fresh_launch_pretrade_gate_log"

# How long a rejected candidate's counterfactual is tracked. Matched to the
# pockets' own MAX_HOLD_MINUTES (180) so the avoided PnL is measured over the
# same horizon a real position would have lived, never a longer one that would
# quietly credit the filter with a later collapse it never avoided.
TRACKING_WINDOW_MINUTES = 180.0

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return str(shadow_db_path())


@dataclass
class GateDecision:
    """One gate verdict, accepted or rejected."""

    pocket: str
    chain: str
    mint: str
    pool_address: str | None
    blocked: bool
    reason: str | None
    top_holder_pct: float | None
    gate_latency_ms: float | None
    would_be_entry_price: float | None
    would_be_reserve_usd: float | None
    realistic_would_be_entry_price: float | None
    # 20/08 -- the real concentration among actual wallets, pool excluded.
    # Defaulted so existing call sites keep working; see the gate's own
    # `top_holder_excluding_pool_pct` docstring for why the two are measured
    # side by side before either threshold is touched.
    top_holder_excluding_pool_pct: float | None = None


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pocket TEXT NOT NULL,
                chain TEXT NOT NULL,
                mint TEXT NOT NULL,
                pool_address TEXT,
                decided_at TEXT NOT NULL,
                blocked INTEGER NOT NULL,
                reason TEXT,
                top_holder_pct REAL,
                top_holder_excluding_pool_pct REAL,
                gate_latency_ms REAL,
                would_be_entry_price REAL,
                would_be_reserve_usd REAL,
                realistic_would_be_entry_price REAL,
                last_price REAL,
                last_checked_at TEXT,
                peak_price REAL,
                avoided_multiplier REAL,
                tracking_status TEXT
            )
            """
        )
        # Indexed on the two columns every read path filters by -- this table
        # grows by every gate decision, not just entries, so a scan would get
        # expensive fast.
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_decided ON {TABLE}(decided_at)")
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_status ON {TABLE}(tracking_status)")
        # Hot ALTER for a table that already exists in prod -- proactive-
        # ingestion doctrine: add the column and start accumulating now,
        # never wait for a rebuild. A PRAGMA guard keeps it idempotent.
        cur = await db.execute(f"PRAGMA table_info({TABLE})")
        columns = {row[1] for row in await cur.fetchall()}
        if "top_holder_excluding_pool_pct" not in columns:
            await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN top_holder_excluding_pool_pct REAL")
        await db.commit()
    _ensured_db_paths.add(path)


async def record_decision(decision: GateDecision, *, db_path: str | None = None) -> int | None:
    """Best-effort write. NEVER raises into the trading loop: losing a log row
    is bad, blocking or crashing an entry decision because logging failed
    would be worse."""
    try:
        path = db_path or _db_path()
        await _ensure_table(path)
        # Only a genuine reject with a real price is worth tracking forward --
        # a fail-closed "unavailable" has no holder data to validate, and an
        # accepted candidate becomes a real position tracked by the pocket
        # itself (double-tracking it here would double the API load).
        trackable = (
            decision.blocked
            and decision.reason is not None
            and decision.reason.startswith("blocked_holder_concentration")
            and decision.would_be_entry_price
        )
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                f"""
                INSERT INTO {TABLE}
                    (pocket, chain, mint, pool_address, decided_at, blocked, reason,
                     top_holder_pct, top_holder_excluding_pool_pct, gate_latency_ms,
                     would_be_entry_price, would_be_reserve_usd, realistic_would_be_entry_price,
                     peak_price, tracking_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.pocket, decision.chain, decision.mint, decision.pool_address,
                    datetime.now(timezone.utc).isoformat(), 1 if decision.blocked else 0,
                    decision.reason, decision.top_holder_pct,
                    decision.top_holder_excluding_pool_pct, decision.gate_latency_ms,
                    decision.would_be_entry_price, decision.would_be_reserve_usd,
                    decision.realistic_would_be_entry_price,
                    decision.would_be_entry_price if trackable else None,
                    "tracking" if trackable else "not_tracked",
                ),
            )
            await db.commit()
            return cur.lastrowid
    except Exception:  # noqa: BLE001 -- logging must never break a trade decision
        return None


async def advance_avoided_tracking(
    *, resolve_price_fn, max_rows: int = 40, db_path: str | None = None,
) -> dict:
    """Polls each still-tracked rejection's real current price and updates its
    counterfactual. ``resolve_price_fn(pool_address, mint, chain)`` must return
    ``(price_usd, reserve_usd)`` or ``(None, None)``.

    ``max_rows`` bounds the API cost per pass -- this table can grow much
    faster than the position tables it shadows, so an unbounded sweep would
    quietly become the dome's biggest consumer."""
    path = db_path or _db_path()
    await _ensure_table(path)
    stats = {"checked": 0, "updated": 0, "closed": 0, "unresolvable": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=TRACKING_WINDOW_MINUTES)).isoformat()

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {TABLE} WHERE tracking_status = 'tracking' ORDER BY decided_at LIMIT ?",
            (max_rows,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for row in rows:
        stats["checked"] += 1
        try:
            price_usd, _reserve_usd = await resolve_price_fn(row["pool_address"], row["mint"], row["chain"])
        except Exception:  # noqa: BLE001 -- a provider failure is not a verdict
            price_usd = None

        expired = row["decided_at"] < cutoff
        if price_usd is None:
            if expired:
                # Marked, never deleted: a pool that went dark IS the outcome.
                # Dropping these would bias the result toward survivors.
                await _set_status(path, row["id"], "unresolvable")
                stats["unresolvable"] += 1
            continue

        entry = row["realistic_would_be_entry_price"] or row["would_be_entry_price"]
        peak = max(row["peak_price"] or price_usd, price_usd)
        multiplier = (price_usd / entry) if entry else None
        status = "closed" if expired else "tracking"

        async with aiosqlite.connect(path) as db:
            await db.execute(
                f"""
                UPDATE {TABLE} SET last_price = ?, last_checked_at = ?, peak_price = ?,
                    avoided_multiplier = ?, tracking_status = ?
                WHERE id = ?
                """,
                (price_usd, datetime.now(timezone.utc).isoformat(), peak, multiplier, status, row["id"]),
            )
            await db.commit()
        stats["updated"] += 1
        if status == "closed":
            stats["closed"] += 1

    return stats


async def advance_avoided_tracking_cycle(*, max_rows: int = 40, db_path: str | None = None) -> dict:
    """Ready-to-call wrapper for the standalone shadow process: resolves prices
    through the SAME REST cascade the pockets themselves use, so the
    counterfactual is measured on the same data source as the real positions
    (a different source would make the comparison meaningless) and shares that
    cascade's own throttles and circuit breakers rather than adding a parallel,
    uncoordinated load on the same providers.

    Kept here, in the tracked repo and under test, precisely because its only
    caller lives OUTSIDE the repo (`shadow_persistent.py`) where nothing is
    covered by CI -- the call site there stays a single line."""
    from .solana_fresh_launch_ws_exit_shadow import _snapshot_with_fallback, geckoterminal_client

    async def _resolve(pool_address, mint, chain):
        snapshot = await _snapshot_with_fallback(geckoterminal_client, pool_address, mint, chain=chain)
        if not snapshot.available or snapshot.price_usd is None:
            return (None, None)
        return (snapshot.price_usd, snapshot.reserve_usd)

    return await advance_avoided_tracking(resolve_price_fn=_resolve, max_rows=max_rows, db_path=db_path)


async def _set_status(path: str, row_id: int, status: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(f"UPDATE {TABLE} SET tracking_status = ? WHERE id = ?", (status, row_id))
        await db.commit()


async def avoided_pnl_summary(*, pocket: str | None = None, db_path: str | None = None) -> dict:
    """What the filter actually saved (or cost), on real subsequent prices.

    A POSITIVE ``avoided_pnl_pct`` means the blocked candidates would have
    LOST that much on average -- the filter saved it. A negative one means the
    filter is cutting winners, which is exactly the failure mode this table
    exists to make visible rather than assumable."""
    path = db_path or _db_path()
    await _ensure_table(path)
    where = "WHERE avoided_multiplier IS NOT NULL"
    params: list = []
    if pocket:
        where += " AND pocket = ?"
        params.append(pocket)

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT avoided_multiplier AS m FROM {TABLE} {where}", params)
        multipliers = [r["m"] for r in await cur.fetchall() if r["m"] is not None]
        cur = await db.execute(
            f"SELECT tracking_status AS s, COUNT(*) AS n FROM {TABLE} GROUP BY tracking_status"
        )
        by_status = {r["s"]: r["n"] for r in await cur.fetchall()}
        cur = await db.execute(
            f"SELECT reason AS r, COUNT(*) AS n, AVG(gate_latency_ms) AS lat FROM {TABLE} "
            f"WHERE blocked = 1 GROUP BY reason"
        )
        by_reason = [
            {"reason": r["r"], "n": r["n"], "avg_latency_ms": round(r["lat"], 1) if r["lat"] else None}
            for r in await cur.fetchall()
        ]

    if not multipliers:
        return {
            "n_measured": 0, "avoided_pnl_pct": None, "would_have_won_pct": None,
            "by_status": by_status, "by_reason": by_reason,
        }

    avg = sum(multipliers) / len(multipliers)
    winners = sum(1 for m in multipliers if m > 1.0)
    return {
        "n_measured": len(multipliers),
        # Sign flipped on purpose: a blocked candidate that would have lost 30%
        # means the filter AVOIDED +30%.
        "avoided_pnl_pct": round(-(avg - 1.0) * 100.0, 2),
        "would_have_won_pct": round(100.0 * winners / len(multipliers), 1),
        "by_status": by_status,
        "by_reason": by_reason,
    }
