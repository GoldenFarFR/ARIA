"""Item #193 (28/07) -- exhaustive per-TOKEN scan log for the momentum
discovery pipeline. Answers a concrete operator need: before calibrating a
rejection-TTL cache in ``discover_momentum_candidates`` (never re-scanning a
structurally-ineligible token every 15min cycle), get a real "how many
DISTINCT tokens does ARIA actually scan" baseline -- neither
``momentum_funnel_log`` (exhaustive counts, but no contract column, so no
way to deduplicate) nor ``counterfactual_tracker`` (has a contract column,
but deliberately EXCLUDES the most frequent rejection reasons -- see its own
``_EXCLUDED_REASONS`` -- so it undercounts by design) can answer this alone.

Deliberately keyed on the TOKEN contract address (``paper_trader.run_paper_
cycle``'s own ``contract`` loop variable), NEVER a pair/pool address --
operator-explicit requirement (28/07): a token with several pools (e.g.
TOKEN/WETH and TOKEN/USDC) must count ONCE, not once per pool, or the
"distinct tokens scanned" figure would be inflated by how many pools a
token happens to have, not by how many tokens ARIA actually evaluated.

Append-only (same doctrine as ``momentum_funnel_log.py``/``momentum_
blacklist.py``): one row per evaluation, no UPDATE/DELETE -- lets
``count_distinct_scanned`` answer over ANY sliding window (the baseline
measurement here, but also any future comparison after Item #193's cache
lands). ``hold_reason`` is recorded EXHAUSTIVELY (unlike counterfactual_
tracker) -- ``None`` marks a BUY, any string marks the HOLD reason,
including reasons excluded elsewhere (no_entry_signal/ohlcv_unavailable/
blacklisted/etc.) -- this table's whole purpose is to never have a blind
spot.

The SAME row (last one per contract) also becomes the natural lookup for
the future rejection-TTL cache (Item #193's next step, not built here) --
``last_scan_for`` exposes it, though nothing yet consumes it to skip a
re-scan.

Extra columns (``symbol``/``price``/``mode``/``wallet``, all free -- already
in ``sig``/the caller's own locals, no extra network call) requested by the
operator for future analyses (exhaustive contrafactual by price, per-mode/
per-pocket scan volume). Operator-explicit requirement (28/07): ``symbol``
is PURELY informational (display only, e.g. formatting a report without
re-resolving the address) -- every lookup/dedup/decision in this module
keys EXCLUSIVELY on (``contract``, ``chain``), never on ``symbol`` (a ticker
is never unique across tokens, unlike a contract address)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS momentum_scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                hold_reason TEXT,
                symbol TEXT,
                price REAL,
                mode TEXT,
                wallet TEXT,
                scanned_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_momentum_scan_log_scanned_at "
            "ON momentum_scan_log (scanned_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_momentum_scan_log_contract "
            "ON momentum_scan_log (contract, chain)"
        )
        await db.commit()


async def record_scan(
    contract: str, chain: str, hold_reason: str | None, *,
    symbol: str | None = None, price: float | None = None,
    mode: str | None = None, wallet: str | None = None,
) -> None:
    """Records ONE evaluation -- ``hold_reason=None`` for a BUY, any string
    for a HOLD (never excluded, unlike ``counterfactual_tracker``). Best-
    effort, same doctrine as every other passive log in this codebase: a
    telemetry write failure must never break a real trading cycle.

    ``symbol``/``price``/``mode``/``wallet`` (28/07, operator request): free
    extras already in the caller's hand (``sig``/its own locals) -- never
    used for lookup/dedup/decision, see module docstring (``symbol`` in
    particular is display-only, a ticker is never a reliable identifier)."""
    if not contract:
        return
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO momentum_scan_log "
                "(contract, chain, hold_reason, symbol, price, mode, wallet, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (contract.lower(), chain or "base", hold_reason, symbol, price, mode, wallet, _now()),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- best-effort telemetry, never blocking
        pass


async def count_distinct_scanned(hours: float = 24.0) -> int:
    """Distinct (contract, chain) pairs scanned over the last ``hours`` --
    the real "how many different tokens" baseline, unlike
    ``momentum_funnel_log.summarize_since`` (exhaustive count, but counts
    EVALUATIONS, i.e. the same token re-scanned across several cycles counts
    once per cycle there)."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(DISTINCT contract || ':' || chain) FROM momentum_scan_log "
            "WHERE scanned_at >= ?",
            (cutoff,),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def total_scans(hours: float = 24.0) -> int:
    """Total evaluation rows (not deduplicated) over the last ``hours`` --
    cross-checked against ``momentum_funnel_log.summarize_since``'s own sum,
    should match closely (same underlying evaluations, this table just adds
    the contract identity)."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM momentum_scan_log WHERE scanned_at >= ?", (cutoff,),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def last_scan_for(contract: str, chain: str) -> dict | None:
    """Most recent scan for this (contract, chain) -- not yet consumed by
    any caller (Item #193's next step, the rejection-TTL cache itself, will
    read this to decide whether to skip a re-scan), exposed now so the
    lookup already exists once that cache is built."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT contract, chain, hold_reason, symbol, price, mode, wallet, scanned_at "
            "FROM momentum_scan_log WHERE contract = ? AND chain = ? "
            "ORDER BY scanned_at DESC LIMIT 1",
            (contract.lower(), chain or "base"),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None
