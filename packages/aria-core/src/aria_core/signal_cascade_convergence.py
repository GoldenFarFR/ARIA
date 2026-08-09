"""Multi-source signal cascade -- stages 3 (CONVERGENCE) + 4 (PERSISTENT
QUEUE). See docs/HANDOFF_PIPELINE_MOMENTUM.md's "multi-source signal
cascade" entry for the full 4-stage design and docs/HANDOFF_SIGNAL_CASCADE.md
for the per-column build log.

Stage 3 CONVERGENCE: one shared table across every source column (today:
GitHub only, per the operator's own build order -- GitHub/Farcaster are
free, web is budget-bounded, X is pay-per-use, built in that order).
``record_source_signal`` is the seam every future column calls the same
way. Concordance across sources is deliberately the free, most
discriminating filter (operator design) -- a token with 2+ independent
sources agreeing is a stronger signal than any single source's own score.

Stage 4 PERSISTENT QUEUE: a durable table, never a volatile notification --
Claude Code sessions are intermittent, so a candidate must survive between
sessions until triaged. ``record_triage_decision`` REQUIRES a reasoning
string (operator design: "capture the REASONING, not just the verdict" --
a bare yes/no transfers nothing toward the day this criterion might be
handed to ARIA). Whether Claude's validated picks actually outperform its
rejects (operator's own falsifiability test) is future analysis once
enough decisions accumulate -- this module only owns persistence, not that
comparison.

Never a trigger, never a veto, never touches the momentum/paper-trading
pipeline -- purely a research triage aid for a Claude Code session.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

_CONVERGENCE_DDL = """
CREATE TABLE IF NOT EXISTS signal_cascade_convergence (
    contract TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT 'base',
    symbol TEXT,
    source TEXT NOT NULL,
    signal TEXT NOT NULL,
    accelerating INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (contract, chain, source)
)
"""
_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS signal_cascade_triage_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT 'base',
    symbol TEXT,
    convergence_count INTEGER NOT NULL,
    sources_detail TEXT,
    queued_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decision_reasoning TEXT,
    decided_at TEXT,
    UNIQUE(contract, chain)
)
"""

_table_ready = False


async def _ensure_tables() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CONVERGENCE_DDL)
        await db.execute(_QUEUE_DDL)
        await db.commit()
    _table_ready = True


async def record_source_signal(
    contract: str, chain: str, source: str, signal: str, *,
    accelerating: bool = False, detail: str | None = None, symbol: str | None = None,
) -> None:
    """Stage 3. Called by a source column's own stage-2 refresh (today:
    ``signal_cascade_github.run_refresh_cycle``) whenever it produces a
    result -- every signal is recorded (not just "positive"), so a source
    that later downgrades a token correctly drops it out of the convergence
    count instead of leaving a stale "positive" row behind. Best-effort:
    never raises, never blocks the caller's own cycle."""
    try:
        await _ensure_tables()
        contract = (contract or "").strip().lower()
        chain = (chain or "base").strip().lower()
        if not contract or not source:
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO signal_cascade_convergence "
                "(contract, chain, symbol, source, signal, accelerating, detail, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(contract, chain, source) DO UPDATE SET "
                "symbol = excluded.symbol, signal = excluded.signal, "
                "accelerating = excluded.accelerating, detail = excluded.detail, "
                "recorded_at = excluded.recorded_at",
                (
                    contract, chain, symbol, source, signal, int(accelerating), detail,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()

        if signal == "positive":
            await _refresh_convergence_and_maybe_queue(contract, chain, symbol)
    except Exception as exc:  # noqa: BLE001 -- never blocking the source column's own cycle
        logger.info("signal_cascade_convergence: record failed for %s/%s (%s)", source, contract[:10], exc)


async def _refresh_convergence_and_maybe_queue(contract: str, chain: str, symbol: str | None) -> None:
    """Recomputes this token's convergence count across every source column
    with a 'positive' signal, and queues it for triage (stage 4) if not
    already queued. A STILL-PENDING row is kept live (convergence_count/
    sources_detail updated in place as more sources agree, real bug found
    and fixed 08/08 -- the count was previously frozen at its value on
    first insertion). An ALREADY-DECIDED row (validated/rejected) is never
    touched -- a human's decision is never silently reopened by a later
    source."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT source, detail, accelerating FROM signal_cascade_convergence "
            "WHERE contract = ? AND chain = ? AND signal = 'positive'",
            (contract, chain),
        )
        rows = await cursor.fetchall()
        if not rows:
            return
        sources_detail = [{"source": r[0], "detail": r[1], "accelerating": bool(r[2])} for r in rows]

        updated = await db.execute(
            "UPDATE signal_cascade_triage_queue SET convergence_count = ?, sources_detail = ?, symbol = ? "
            "WHERE contract = ? AND chain = ? AND status = 'pending'",
            (len(rows), json.dumps(sources_detail), symbol, contract, chain),
        )
        if updated.rowcount == 0:
            await db.execute(
                "INSERT INTO signal_cascade_triage_queue "
                "(contract, chain, symbol, convergence_count, sources_detail, queued_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(contract, chain) DO NOTHING",  # already decided -- never reopened
                (
                    contract, chain, symbol, len(rows), json.dumps(sources_detail),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        await db.commit()


async def list_pending_triage(limit: int = 20) -> list[dict]:
    """Stage 4 read side -- strongest convergence first, then oldest first.
    ``sources_detail`` is parsed back into a list of dicts for direct use
    (never a raw JSON string leaking into a caller that just wants to read
    it)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT contract, chain, symbol, convergence_count, sources_detail, queued_at "
            "FROM signal_cascade_triage_queue WHERE status = 'pending' "
            "ORDER BY convergence_count DESC, queued_at ASC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    out = []
    for contract, chain, symbol, count, sources_detail, queued_at in rows:
        try:
            sources = json.loads(sources_detail) if sources_detail else []
        except (ValueError, TypeError):
            sources = []
        out.append({
            "contract": contract, "chain": chain, "symbol": symbol,
            "convergence_count": count, "sources": sources, "queued_at": queued_at,
        })
    return out


async def record_triage_decision(contract: str, chain: str, decision: str, reasoning: str) -> bool:
    """Stage 4 write side -- a Claude Code session's own triage call.
    ``decision`` must be 'validated' or 'rejected'; ``reasoning`` is
    REQUIRED and must be non-empty (operator design: a bare verdict with no
    "why" transfers nothing toward a future handover to ARIA -- see module
    docstring). Returns ``False`` (no exception) on invalid input or no
    matching pending row, so a caller can report the real outcome instead
    of assuming success."""
    if decision not in ("validated", "rejected") or not (reasoning or "").strip():
        return False
    await _ensure_tables()
    contract = (contract or "").strip().lower()
    chain = (chain or "base").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE signal_cascade_triage_queue SET status = ?, decision_reasoning = ?, decided_at = ? "
            "WHERE contract = ? AND chain = ? AND status = 'pending'",
            (decision, reasoning.strip(), datetime.now(timezone.utc).isoformat(), contract, chain),
        )
        await db.commit()
        return cursor.rowcount > 0
