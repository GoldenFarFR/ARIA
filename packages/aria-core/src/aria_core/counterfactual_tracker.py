"""#176 (20/07), learning track b -- counterfactual tracker for candidates REJECTED
by a hard gate of the momentum pipeline. Response to the plan agreed with the
operator ("what about learning?"): weekly reset (#173) -> Formula B sizing (#174) ->
simulated slippage (#175) -> learning (regime, #176a + counterfactual, here).

Records EVERY rejection WORTH a counterfactual (contract/chain/reason/price at the
moment of rejection), then a dedicated heartbeat cycle (gated OFF, ``ARIA_COUNTERFACTUAL_TRACKER_
ENABLED``) revisits after a fixed delay and records the price evolution -- a simple
BEFORE/AFTER comparison, never a re-simulation of the entry pipeline (thresholds
may have changed since then, re-simulating would be misleading AND would cost a full
scan per candidate). Goal: find out objectively whether the hard thresholds cost
real missed gains -- never an automatic judgment, just raw numbers so a future
session can judge with facts.

Reasons DELIBERATELY excluded from recording (``_EXCLUDED_REASONS``) -- no useful
counterfactual: the token simply had no usable signal/data
(``no_entry_signal``/``ohlcv_unavailable``), or the rejection is a CONFIRMED threat
where an on-paper price gain would be misleading (``blacklisted``/any ``honeypot_*``
code -- you can never sell a real honeypot, no matter what the displayed price does
afterwards). Any OTHER ``hold_reason`` (present today or added tomorrow by a future
guardrail) is included by default -- fail-open on inclusion, never the reverse: an
extra recording is free (just one SQLite row), a missed recording would be a silent
blind spot.

The RECORDING itself is NOT gated (same doctrine as ``momentum_funnel_log.py``
-- a passive by-product of the evaluation already underway, no extra network call,
strictly additive). Only the REVISIT CYCLE (a real network call per due candidate)
is gated."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


def counterfactual_tracker_enabled() -> bool:
    """Additive gate -- ``run_revisit_cycle()`` (the only part that costs a real
    network call per due candidate) is only called from the heartbeat if this flag
    is active (OFF by default, same pattern as the other heartbeat tasks). RECORDING
    rejections (``record_rejection``, called from ``paper_trader.run_paper_cycle``)
    remains unconditional -- same doctrine as ``momentum_funnel_log.py``, no network
    call, nothing to gate."""
    return os.environ.get("ARIA_COUNTERFACTUAL_TRACKER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

# Delay before a rejection becomes "due" for a revisit -- long enough for a real
# price move to have had time to form, short enough to stay usable
# (not years of market drift unrelated to the original decision).
REVISIT_AFTER_DAYS = 7.0

_EXCLUDED_REASONS = frozenset({
    "no_entry_signal", "ohlcv_unavailable", "blacklisted",
    "honeypot_rejected", "honeypot_unavailable", "chain_not_covered",
})


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS counterfactual_rejection (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT NOT NULL DEFAULT '',
                reject_reason TEXT NOT NULL,
                price_at_rejection REAL NOT NULL,
                rejected_at TEXT NOT NULL,
                revisited_at TEXT,
                price_at_revisit REAL,
                price_change_pct REAL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_counterfactual_rejection_rejected_at "
            "ON counterfactual_rejection (rejected_at)"
        )
        await db.commit()


def is_trackable_reason(hold_reason: str | None) -> bool:
    """``True`` if this rejection deserves a counterfactual -- a real discretionary
    threshold blocked a candidate with a known price, NOT a lack of data/signal nor
    a confirmed threat (see module docstring)."""
    return bool(hold_reason) and hold_reason not in _EXCLUDED_REASONS


async def record_rejection(
    contract: str, chain: str, symbol: str, hold_reason: str | None, price: float | None,
) -> None:
    """Records a rejection -- silent no-op if ``hold_reason`` isn't trackable or
    if ``price`` is absent/invalid (no starting point for a counterfactual).
    Never an exception that would bubble up to the caller (``paper_trader.run_paper_cycle``)
    -- a telemetry write failure must never break a real trading cycle."""
    if not is_trackable_reason(hold_reason) or not price or price <= 0:
        return
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO counterfactual_rejection
                  (contract, chain, symbol, reject_reason, price_at_rejection, rejected_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (contract, chain or "base", symbol or "", hold_reason, price, _now()),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — best-effort telemetry, never blocking
        logger.info("counterfactual_tracker: recording failed for %s", contract, exc_info=True)


async def list_due_for_revisit(*, older_than_days: float = REVISIT_AFTER_DAYS, limit: int = 20) -> list[dict]:
    """Rejections never revisited, older than ``older_than_days`` -- oldest first
    (FIFO, never an arbitrary order that would leave some candidates waiting
    forever if volume exceeds ``limit`` per cycle)."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM counterfactual_rejection "
            "WHERE revisited_at IS NULL AND rejected_at <= ? "
            "ORDER BY rejected_at ASC LIMIT ?",
            (cutoff, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def record_revisit(row_id: int, price_at_revisit: float | None) -> None:
    """Records the outcome of a revisit -- ``price_at_revisit=None`` (price not
    found at revisit time, e.g. token illiquid/rugged since) still marks the row
    as revisited (never retried in a loop), but leaves ``price_change_pct`` at
    ``NULL`` -- never an invented 0% that would be indistinguishable from a real
    stable price."""
    await _ensure_table()
    now = _now()
    change_pct = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT price_at_rejection FROM counterfactual_rejection WHERE id = ?", (row_id,),
        )
        row = await cursor.fetchone()
        if row and price_at_revisit and price_at_revisit > 0 and row["price_at_rejection"]:
            change_pct = (price_at_revisit / row["price_at_rejection"] - 1.0) * 100.0
        await db.execute(
            "UPDATE counterfactual_rejection "
            "SET revisited_at = ?, price_at_revisit = ?, price_change_pct = ? WHERE id = ?",
            (now, price_at_revisit, change_pct, row_id),
        )
        await db.commit()


async def run_revisit_cycle(*, limit: int = 20) -> dict:
    """One revisit pass: for each due rejection, refetch the REAL current price
    (same client as the rest of the momentum pipeline, ``paper_trader._default_pair_lookup``
    -- never a second duplicated client) and record the evolution. Gated by the
    caller (``heartbeat.py``, ``ARIA_COUNTERFACTUAL_TRACKER_ENABLED``) -- this
    function doesn't check the gate itself, same pattern as the other cycles
    (``bonding_discovery_cycle``, etc.)."""
    from aria_core import paper_trader

    due = await list_due_for_revisit(limit=limit)
    revisited = 0
    price_unavailable = 0
    for row in due:
        price = None
        try:
            pair = await paper_trader._default_pair_lookup(row["contract"], chain=row["chain"] or "base")
            price = pair.price_usd if pair is not None else None
        except Exception:  # noqa: BLE001 — a network failure on THIS candidate doesn't block the others
            price = None
        if not price or price <= 0:
            price_unavailable += 1
        await record_revisit(row["id"], price)
        revisited += 1
    return {"due": len(due), "revisited": revisited, "price_unavailable": price_unavailable}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def summarize_revisited(*, limit: int = 500) -> dict:
    """Aggregates the already-resolved (revisited) counterfactuals -- by rejection
    reason: how many, average/median price evolution, how many would have
    "significantly" risen (>= +50%, a reading threshold, not a judgment -- see
    format_counterfactual_summary for the sample-size warning)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM counterfactual_rejection "
            "WHERE revisited_at IS NOT NULL AND price_change_pct IS NOT NULL "
            "ORDER BY revisited_at DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

    buckets: dict[str, list[float]] = {}
    for r in rows:
        buckets.setdefault(r["reject_reason"], []).append(r["price_change_pct"])

    by_reason: dict[str, dict] = {}
    for reason, changes in buckets.items():
        changes_sorted = sorted(changes)
        n = len(changes_sorted)
        median = changes_sorted[n // 2] if n % 2 == 1 else (changes_sorted[n // 2 - 1] + changes_sorted[n // 2]) / 2.0
        by_reason[reason] = {
            "count": n,
            "avg_price_change_pct": sum(changes) / n,
            "median_price_change_pct": median,
            "would_have_gained_50pct_or_more": sum(1 for c in changes if c >= 50.0),
        }
    return {"resolved_total": len(rows), "by_reason": by_reason}


def format_counterfactual_summary(summary: dict) -> str:
    header = "🔍 Contrefactuel des candidats rejetés (seuils durs momentum)"
    by_reason = summary.get("by_reason") or {}
    if not by_reason:
        return f"{header}\n\nAucun contrefactuel résolu pour l'instant (rien à revisiter, ou cycle pas encore activé)."

    lines = [header, f"{summary.get('resolved_total', 0)} rejet(s) revisité(s) au total", ""]
    ranked = sorted(by_reason.items(), key=lambda kv: kv[1]["count"], reverse=True)
    for reason, stats in ranked:
        lines.append(
            f"- {reason} : {stats['count']} · évolution moyenne {stats['avg_price_change_pct']:+.1f}%"
            f" (médiane {stats['median_price_change_pct']:+.1f}%)"
            f" · {stats['would_have_gained_50pct_or_more']} auraient pris ≥+50%"
        )
    lines.append("")
    lines.append(
        "Lecture prudente : un petit nombre de résolutions par case ne prouve rien -- "
        "ne pas ajuster un seuil dur sur la base de quelques contrefactuels seulement."
    )
    return "\n".join(lines)
