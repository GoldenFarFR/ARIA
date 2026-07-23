"""Cumulative persistence of the momentum rejection funnel (19/07) -- a direct
answer to a proposal ARIA herself made in a Telegram conversation: "let's log
the per-step counter for 48h -- how many rejected at the honeypot check, how
many at R/R, how many at liquidity. Then we'll know whether it's the market
or whether my R/R threshold is calibrated too tight. Proof before opinion."

The funnel itself (counting by ``hold_reason``) already exists and is NOT
duplicated here -- ``paper_trader.run_paper_cycle`` computes it every cycle
(mandate #192, 16/07) but ONLY logs it (``logger.info``) and then loses it: no
other caller reads ``actions["momentum_funnel"]`` (verified by grep before
writing this module). A single cycle (5-20 candidates) isn't a large enough
sample anyway to judge "too strict vs a flat market" -- it's the CUMULATIVE
total over time that makes the signal usable. This module only adds
persistence; no change to the decision logic (``momentum_entry.py``) or to
the funnel computation itself.

Append-only in practice (same doctrine as ``momentum_blacklist.py``/
``agent_wallet_log.py``): each cycle adds one row per ``reason_code``, never
an UPDATE/DELETE. Reads aggregate over a sliding time window."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS momentum_funnel_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                count INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_momentum_funnel_log_recorded_at "
            "ON momentum_funnel_log (recorded_at)"
        )
        await db.commit()


async def record_funnel(funnel: dict[str, int]) -> None:
    """Persists ONE funnel cycle (called from ``paper_trader.run_paper_cycle``,
    right after the already-existing computation). Does nothing if the funnel
    is empty (no candidate rejected this cycle -- nothing to record, not an
    anomaly)."""
    if not funnel:
        return
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO momentum_funnel_log (recorded_at, reason_code, count) VALUES (?, ?, ?)",
            [(now, str(reason), int(count)) for reason, count in funnel.items()],
        )
        await db.commit()


async def summarize_since(hours: float = 48.0) -> dict[str, int]:
    """Aggregates all entries from the last ``hours`` hours -- ``{reason_code: total}``,
    unsorted here (display sorting happens in ``format_funnel_summary``, this
    function stays a reusable raw read)."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT reason_code, SUM(count) FROM momentum_funnel_log "
            "WHERE recorded_at >= ? GROUP BY reason_code",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    return {reason: int(total) for reason, total in rows}


def format_funnel_summary(summary: dict[str, int], *, hours: float = 48.0) -> str:
    """Telegram rendering -- ranked by descending frequency (the dominant
    rejection cause first, that's the signal being sought: flat market vs an
    overly strict filter)."""
    header = f"📊 Funnel de rejet momentum -- {hours:.0f}h glissantes"
    if not summary:
        return f"{header}\n\nAucun rejet enregistré sur cette période."

    total = sum(summary.values())
    ranked = sorted(summary.items(), key=lambda kv: kv[1], reverse=True)
    lines = [header, f"Total : {total} candidats rejetés/HOLD", ""]
    for reason, count in ranked:
        pct = (count / total * 100.0) if total else 0.0
        lines.append(f"- {reason} : {count} ({pct:.0f}%)")
    return "\n".join(lines)
