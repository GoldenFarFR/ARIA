"""Dip-recovery shadow tracker (13/08, operator-proposed entry signal:
"j'hesite a ouvrir une nouvelle poche et tester d'acheter que des tokens qui
ont minimum -30% sur 24h et mettre un stop loss a -5%").

Same anti-overfitting doctrine as the paper-trading winrate audit that
preceded this build (13/08): a naive backtest on `candle_history` found the
deduplicated sample (one trade per continuous dip episode, matching this
module's own doctrine below) too small (12 trades) to draw any conclusion --
50% win rate, high variance. Building a live paper pocket directly on an
unvalidated signal would repeat the exact 10/08 wick-gate mistake
(`docs/HANDOFF_PIPELINE_MOMENTUM.md`). This module instead SHADOW-LOGS the
signal going forward (same pattern as `wick_filter_shadow.py`/
`v8_rsi_reversal_shadow.py`): purely observational, never touches the real
$1M paper portfolio, never places a real or even a real-simulated trade.

Zero extra network call: reads candles already collected by
`run_candle_history_watchlist_cycle` (`candle_history` table, "standard"
mode) rather than fetching a dedicated series -- the dedicated
`dip_recovery_shadow_cycle` (momentum_entry.py) only does local DB reads.

Episode deduplication (the real bug found in the manual backtest, 13/08): a
naive per-candle check would open a "new" signal on every hourly candle a
token stays under -30%/24h, wildly overlapping trades from the same
continuous dip. A dedicated per-(contract,chain) episode-state table tracks
whether the token is CURRENTLY inside a qualifying dip -- a new shadow
position only opens on the transition into the episode (state False->True),
and the state only resets to False once the 24h variation genuinely recovers
above the threshold, independent of whether a stop already closed the
position. Matches v9's own "one buy per synchronized episode" doctrine.

Stop-loss is a FIXED -5% from entry (not trailing) -- matches the operator's
own words verbatim ("mettre un stop loss a -5%"), no invented trailing
mechanic. A timeout safety net (MAX_HOLD_HOURS) exists purely to keep a
position from drifting open forever absent any exit signal -- the operator
never asked for a take-profit, and adding one would be inventing a rule not
requested."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Candidate thresholds under shadow evaluation -- verbatim from the
# operator's own proposal (13/08), not independently tuned. Shadow mode
# exists precisely so these can be validated (or not) against real forward
# data before ever considering a live paper pocket.
DIP_THRESHOLD_PCT = -30.0
STOP_LOSS_PCT = -5.0

# Safety net only, never an invented take-profit -- see module docstring.
MAX_HOLD_HOURS = 168.0  # 7 days

LOOKBACK_CANDLES = 24  # 24 hourly candles = 24h lookback on the 1H series
MIN_CANDLES_1H = LOOKBACK_CANDLES + 1  # +1 for the current candle itself

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_tables() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dip_recovery_shadow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                entry_price REAL NOT NULL,
                entry_var_24h_pct REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                exit_price REAL,
                close_reason TEXT,
                pnl_pct REAL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dip_recovery_shadow_lookup "
            "ON dip_recovery_shadow (contract, chain, status)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dip_recovery_shadow_episode_state (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                in_episode INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()
    _ensured_db_paths.add(path)


def _hours_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


async def record_evaluation(
    contract: str, chain: str, *, symbol: str | None, candles_1h: list[Candle],
) -> None:
    """Called once per due watchlist token by ``dip_recovery_shadow_cycle``.
    Best-effort: never raises into the caller (same contract as every other
    shadow module on this project)."""
    try:
        if not contract or len(candles_1h) < MIN_CANDLES_1H:
            return
        closes = [c.close for c in candles_1h]
        last_close = closes[-1]
        ref_close = closes[-1 - LOOKBACK_CANDLES]
        if not ref_close:
            return
        var_24h_pct = (last_close / ref_close - 1.0) * 100.0

        await _ensure_tables()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            await _advance_open_position(db, contract, chain, last_close)
            await _advance_episode_state(
                db, contract, chain, symbol, var_24h_pct=var_24h_pct, last_close=last_close,
            )
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real cycle
        logger.info("dip_recovery_shadow: record_evaluation failed (%s)", exc)


async def _advance_open_position(
    db: aiosqlite.Connection, contract: str, chain: str, last_close: float,
) -> None:
    cur = await db.execute(
        "SELECT * FROM dip_recovery_shadow WHERE contract = ? AND chain = ? "
        "AND status = 'open' LIMIT 1",
        (contract, chain or "base"),
    )
    row = await cur.fetchone()
    if row is None:
        return
    row = dict(row)
    entry_price = row["entry_price"]
    if not entry_price:
        return
    pnl_pct = (last_close / entry_price - 1.0) * 100.0
    age_hours = _hours_since(row["opened_at"]) or 0.0
    close_reason: str | None = None
    if pnl_pct <= STOP_LOSS_PCT:
        close_reason = "stop_loss_5pct"
    elif age_hours >= MAX_HOLD_HOURS:
        close_reason = "timeout_max_hold"
    if close_reason is None:
        return
    await db.execute(
        """
        UPDATE dip_recovery_shadow SET status = 'closed', closed_at = ?,
            exit_price = ?, close_reason = ?, pnl_pct = ? WHERE id = ?
        """,
        (datetime.now(timezone.utc).isoformat(), last_close, close_reason, pnl_pct, row["id"]),
    )
    await db.commit()


async def _advance_episode_state(
    db: aiosqlite.Connection, contract: str, chain: str, symbol: str | None,
    *, var_24h_pct: float, last_close: float,
) -> None:
    cur = await db.execute(
        "SELECT in_episode FROM dip_recovery_shadow_episode_state WHERE contract = ? AND chain = ?",
        (contract, chain or "base"),
    )
    row = await cur.fetchone()
    was_in_episode = bool(row["in_episode"]) if row is not None else False

    if var_24h_pct > DIP_THRESHOLD_PCT:
        if was_in_episode:
            await db.execute(
                "UPDATE dip_recovery_shadow_episode_state SET in_episode = 0 "
                "WHERE contract = ? AND chain = ?",
                (contract, chain or "base"),
            )
            await db.commit()
        return

    if not was_in_episode:
        # Fresh transition into a qualifying dip -- open exactly one shadow
        # position for this episode (never re-opened mid-episode even if a
        # stop already closed a prior attempt this same episode).
        await db.execute(
            """
            INSERT INTO dip_recovery_shadow (
                contract, chain, symbol, status, entry_price, entry_var_24h_pct, opened_at
            ) VALUES (?, ?, ?, 'open', ?, ?, ?)
            """,
            (
                contract, chain or "base", symbol, last_close, var_24h_pct,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    await db.execute(
        "INSERT INTO dip_recovery_shadow_episode_state (contract, chain, in_episode) "
        "VALUES (?, ?, 1) ON CONFLICT (contract, chain) DO UPDATE SET in_episode = 1",
        (contract, chain or "base"),
    )
    await db.commit()


async def summary() -> dict:
    """Aggregate read for session/monitoring use -- never called from a real
    trading path."""
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT status, contract, pnl_pct FROM dip_recovery_shadow")
        rows = [dict(r) for r in await cur.fetchall()]
    closed = [r for r in rows if r["status"] == "closed"]
    wins = sum(1 for r in closed if (r["pnl_pct"] or 0) > 0)
    return {
        "open": sum(1 for r in rows if r["status"] == "open"),
        "closed": len(closed),
        "wins": wins,
        "distinct_tokens": len({r["contract"] for r in rows}),
        "avg_pnl_pct": (sum(r["pnl_pct"] or 0 for r in closed) / len(closed)) if closed else None,
    }
