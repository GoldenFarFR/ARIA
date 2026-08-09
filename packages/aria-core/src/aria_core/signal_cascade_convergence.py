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

Falsifiability test (operator design, added 09/08): "compare forward
returns of items Claude VALIDATED vs items Claude REJECTED. If validated
ones outperform, the criterion has value and can be transferred; if they
don't, the judgment is no better than chance and must NOT be given to
ARIA." ``record_triage_decision`` now captures the token's price AT the
decision (best-effort, never blocking the decision itself on a price-
lookup failure); ``refresh_forward_prices``/``falsifiability_report`` fill
+24h/+7d forward prices lazily (same doctrine as
``narrative_signal_shadow.py``: no dedicated poller, a return is captured
the first time it's looked up after the window elapses) and report the
average forward return per decision bucket -- 'validated' vs 'rejected',
never claiming significance below a minimum sample size per side.

Never a trigger, never a veto, never touches the momentum/paper-trading
pipeline -- purely a research triage aid for a Claude Code session.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Below this many RESOLVED samples on ONE SIDE (validated or rejected) for a
# given window, the comparison is not honestly meaningful -- never claimed
# as a verdict, same doctrine as the wallet-scoring anti-luck threshold.
_MIN_SAMPLES_PER_SIDE = 5

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


_QUEUE_ADDED_COLUMNS = (
    ("price_at_decision", "REAL"),
    ("price_after_24h", "REAL"),
    ("price_after_7d", "REAL"),
)


async def _ensure_tables() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CONVERGENCE_DDL)
        await db.execute(_QUEUE_DDL)
        # Hot migration (09/08): falsifiability-test columns added after
        # this table's first deployment -- same pattern as paper_trader.py's
        # _ADDED_COLUMNS.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(signal_cascade_triage_queue)")).fetchall()
        }
        for name, ddl in _QUEUE_ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE signal_cascade_triage_queue ADD COLUMN {name} {ddl}")
        await db.commit()
    _table_ready = True


async def _current_price_usd(contract: str, chain: str) -> float | None:
    """Best-effort spot price -- never a guess, ``None`` if unavailable
    (same degradation as every other pair lookup in this codebase)."""
    try:
        from aria_core.services.dexscreener import fetch_token_pairs

        pairs = await fetch_token_pairs(contract, chain=chain)
        if not pairs:
            return None
        best = max(pairs, key=lambda p: p.liquidity_usd or 0.0)
        return best.price_usd if best.price_usd and best.price_usd > 0 else None
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocking
        logger.info("signal_cascade_convergence: price lookup failed for %s (%s)", contract[:10], exc)
        return None


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
    # Best-effort: a failed price lookup never blocks recording the
    # decision itself -- it just means this item can't feed the
    # falsifiability comparison later (price_at_decision stays NULL).
    price_at_decision = await _current_price_usd(contract, chain)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE signal_cascade_triage_queue SET status = ?, decision_reasoning = ?, decided_at = ?, "
            "price_at_decision = ? WHERE contract = ? AND chain = ? AND status = 'pending'",
            (
                decision, reasoning.strip(), datetime.now(timezone.utc).isoformat(),
                price_at_decision, contract, chain,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0


async def refresh_forward_prices() -> int:
    """Lazily fills +24h/+7d forward prices for every DECIDED item whose
    window has elapsed and isn't captured yet -- same doctrine as
    ``narrative_signal_shadow.py``: no dedicated poller, a return is
    captured the first time someone looks after the window (via this
    function, called by ``falsifiability_report`` below or directly).
    Returns the number of rows updated. Best-effort: never raises."""
    await _ensure_tables()
    updated = 0
    now = datetime.now(timezone.utc)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT id, contract, chain, decided_at, price_after_24h, price_after_7d "
                "FROM signal_cascade_triage_queue "
                "WHERE status IN ('validated', 'rejected') AND price_at_decision IS NOT NULL "
                "AND decided_at IS NOT NULL AND (price_after_24h IS NULL OR price_after_7d IS NULL)"
            )
            rows = await cursor.fetchall()

        for row_id, contract, chain, decided_at, has_24h, has_7d in rows:
            try:
                decided_dt = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            due_24h = has_24h is None and (now - decided_dt) >= timedelta(hours=24)
            due_7d = has_7d is None and (now - decided_dt) >= timedelta(days=7)
            if not due_24h and not due_7d:
                continue
            price = await _current_price_usd(contract, chain)
            if price is None:
                continue
            sets, params = [], []
            if due_24h:
                sets.append("price_after_24h = ?")
                params.append(price)
            if due_7d:
                sets.append("price_after_7d = ?")
                params.append(price)
            params.append(row_id)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    f"UPDATE signal_cascade_triage_queue SET {', '.join(sets)} WHERE id = ?", params,
                )
                await db.commit()
            updated += 1
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocking
        logger.info("signal_cascade_convergence: forward-price refresh failed (%s)", exc)
    return updated


async def falsifiability_report() -> dict:
    """The operator's own test: does copying Claude's triage decisions
    (validated vs rejected) actually produce a different forward outcome?
    Refreshes due forward prices first (lazy, see ``refresh_forward_prices``),
    then reports the average return per decision bucket, per window --
    NEVER a verdict below ``_MIN_SAMPLES_PER_SIDE`` resolved samples on
    both sides for that window (returns an honest 'not enough data yet'
    instead)."""
    await refresh_forward_prices()
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, price_at_decision, price_after_24h, price_after_7d "
            "FROM signal_cascade_triage_queue WHERE status IN ('validated', 'rejected') "
            "AND price_at_decision IS NOT NULL"
        )
        rows = await cursor.fetchall()

    def _bucket(window: str) -> dict:
        by_status: dict[str, list[float]] = {"validated": [], "rejected": []}
        for status, entry, after_24h, after_7d in rows:
            after = after_24h if window == "24h" else after_7d
            if after is None or not entry or entry <= 0:
                continue
            by_status[status].append((after / entry - 1.0) * 100.0)
        n_validated, n_rejected = len(by_status["validated"]), len(by_status["rejected"])
        enough = n_validated >= _MIN_SAMPLES_PER_SIDE and n_rejected >= _MIN_SAMPLES_PER_SIDE
        avg_validated = sum(by_status["validated"]) / n_validated if n_validated else None
        avg_rejected = sum(by_status["rejected"]) / n_rejected if n_rejected else None
        return {
            "n_validated": n_validated, "n_rejected": n_rejected,
            "avg_return_validated_pct": round(avg_validated, 2) if avg_validated is not None else None,
            "avg_return_rejected_pct": round(avg_rejected, 2) if avg_rejected is not None else None,
            "enough_data": enough,
            "verdict": (
                (
                    "critère utile -- les validés surperforment"
                    if avg_validated > avg_rejected else
                    "critère sans valeur -- pas mieux que le hasard, NE PAS transmettre à ARIA"
                )
                if enough and avg_validated is not None and avg_rejected is not None
                else f"pas assez de données (min {_MIN_SAMPLES_PER_SIDE}/côté requis)"
            ),
        }

    return {"window_24h": _bucket("24h"), "window_7d": _bucket("7d")}
