"""Persisted candle history, per (chain, pool_address, timeframe) -- 11/08.

Real gap this closes: before this module, `_fetch_candles` (momentum_entry.py)
never kept anything beyond a 60-second in-memory cache -- every scan re-fetched
the network from scratch, and no series ever accumulated enough depth for a
real trend read or a reproducible backtest. Operator-designed (11/08 session,
long back-and-forth, see docs/HANDOFF_PIPELINE_MOMENTUM.md's own entry for the
full design trail): a FIFO series per (chain, pool_address, timeframe), fed
mainly by a dedicated cycle over `services/goplus_watchlist.py` ("the
watchlist" -- see feedback_watchlist_means_goplus_watchlist.md, the one and
only list this bare term refers to on this project), plus this module's own
passive hook (free, zero extra network call: records whatever the real
trading pipeline already fetches for its own decisions).

Deliberately NOT purged by staleness/age (explicit operator decision, 11/08:
"normalement elle doit tenir le registre des bougies pour tous a l'identique
quelle y trade ou non") -- a token evaluated once and never retraded keeps its
few candles forever, on purpose: it's exactly this majority-rejected class
(measured live this session: ~92% of tokens ever scanned never generate a
trade) that has the most value for a future "was this rejection justified?"
backtest. Only a per-key FIFO cap bounds an individual series' depth --
unbounded in the NUMBER of distinct keys, a deliberate tradeoff the operator
weighed against the storage cost (measured this session: ~1945 distinct
tokens/13 days, order of magnitude a few GB/year even without any purge).

Schema deliberately minimal (audit finding, same session): no `provider`/
`degraded`/`symbol`/`inserted_at` columns -- none has a real Phase-1 consumer,
`ALTER TABLE ADD COLUMN` is free later in SQLite when one actually needs them.

Timeframe is INFERRED from the candles' own median interval (never trusted
from the caller's `mode` string) -- `mode` encodes WHO asked (which pocket),
`timeframe` encodes WHAT was actually served, and the two diverge whenever
the cascade degrades (e.g. a "standard" request landing on 4H instead of 1D
because the token is too young). Mixing 15M and 1D candles in the same FIFO
series under a shared "standard"/"scalping" key would silently corrupt any
future trend/backtest read -- each REAL granularity gets its own independent
series. The 5-minute granularity (scalping_v9's default) is explicitly
EXCLUDED from persistence (operator decision, 11/08: "deja le 5 minutes on
peut lenlever sa economisera des bougie") -- v9's own trading behavior is
entirely unaffected, only this module's persistence skips that granularity.
"""
from __future__ import annotations

import logging

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Nominal interval (seconds) per real timeframe label, as actually reported
# by the fetch ladders in services/ohlcv.py (_FETCH_LADDER/_SCALPING_FETCH_
# LADDER/_V9_SINGLE_TF_LADDERS) -- "1D"/"4H"/"1H"/"30M"/"15M"/"5M". Used to
# INFER the real timeframe from the candles' own median spacing, since
# neither `_try_gecko_stage` nor `_fetch_candles` currently expose the
# ladder's chosen rung to the caller (only the requested `mode`).
_TIMEFRAME_INTERVAL_SECONDS: dict[str, int] = {
    "1D": 86400,
    "4H": 14400,
    "1H": 3600,
    "30M": 1800,
    "15M": 900,
    "5M": 300,
}

# Tolerance band around each nominal interval (handles normal jitter: a
# provider's "daily" candle isn't always EXACTLY 86400s apart, weekends/gaps
# on thin pools, etc.) -- wide enough to absorb real-world jitter, narrow
# enough that two adjacent timeframes (e.g. 1H=3600s vs 4H=14400s) never
# collide.
_TIMEFRAME_MATCH_TOLERANCE = 0.25

# 11/08, explicit operator decision -- 5M (scalping_v9's default granularity)
# is the heaviest to persist (up to 288 new candles/day/key) and was singled
# out to exclude: "deja le 5 minutes on peut lenlever sa economisera des
# bougie". v9's own trading behavior never reads this module, so nothing
# about its real cadence changes -- only persistence skips this granularity.
_EXCLUDED_TIMEFRAMES = frozenset({"5M"})

# Per-timeframe FIFO cap (11/08, operator-reviewed grid) -- NOT a uniform
# "1000 everywhere" default: the goal is a comparable USEFUL DEPTH per
# timeframe, not an identical candle count (1000 candles is ~2.7 years at
# 1D but only ~10 days at 15M). 30M is a rare standard-ladder/scalping
# fallback rung (never the primary source for any pocket), given a smaller
# cap since ~10 days of context there is already enough.
FIFO_CAP_BY_TIMEFRAME: dict[str, int] = {
    "1D": 1000,
    "4H": 1000,
    "1H": 1000,
    "15M": 1000,
    "30M": 500,
}

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # candle_staleness_shadow.py/wick_filter_shadow.py.
    return DB_PATH


def infer_timeframe(median_interval_seconds: float | None) -> str | None:
    """Maps a candle series' own median spacing to the real timeframe label
    it was served at, or ``None`` if it matches none within tolerance (too
    few candles, an irregular series, or a granularity this module doesn't
    know about) -- never a guess, an honest \"can't tell\" that skips
    persistence for that fetch rather than risk mislabeling a series."""
    if not median_interval_seconds or median_interval_seconds <= 0:
        return None
    for label, nominal in _TIMEFRAME_INTERVAL_SECONDS.items():
        low = nominal * (1 - _TIMEFRAME_MATCH_TOLERANCE)
        high = nominal * (1 + _TIMEFRAME_MATCH_TOLERANCE)
        if low <= median_interval_seconds <= high:
            return label
    return None


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS candle_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL DEFAULT 'base',
                pool_address TEXT NOT NULL,
                contract TEXT NOT NULL DEFAULT '',
                timeframe TEXT NOT NULL,
                mode TEXT NOT NULL,
                ts INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0
            )
            """
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_candle_history_key "
            "ON candle_history (chain, pool_address, timeframe, ts)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_candle_history_contract "
            "ON candle_history (contract, chain, timeframe)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_candles(
    chain: str,
    pool_address: str,
    *,
    mode: str,
    candles: list[Candle],
    median_interval_seconds: float | None,
    contract: str = "",
) -> None:
    """Persists ``candles`` into the FIFO series for their INFERRED real
    timeframe -- best-effort, NEVER raises into the caller's real fetch path
    (same contract as every other shadow/history module on this project).
    Silently skips (no-op) when: fewer than 2 candles (nothing to infer
    spacing from), the inferred timeframe is unknown, or it's explicitly
    excluded (5M). Deduplicates via the unique index (``INSERT OR IGNORE``)
    -- re-persisting an already-known candle is a no-op, not an error."""
    if not pool_address or not candles:
        return
    timeframe = infer_timeframe(median_interval_seconds)
    if timeframe is None or timeframe in _EXCLUDED_TIMEFRAMES:
        return
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.executemany(
                """
                INSERT OR IGNORE INTO candle_history (
                    chain, pool_address, contract, timeframe, mode,
                    ts, open, high, low, close, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chain or "base", pool_address, contract or "", timeframe, mode,
                        c.ts, c.open, c.high, c.low, c.close, c.volume,
                    )
                    for c in candles
                ],
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- persistence must never break a real fetch
        logger.info("candle_history: record failed for %s/%s (%s)", chain, pool_address[:10], exc)
        return
    # Unconditional: a cheap no-op DELETE (index lookup only) when already
    # under cap -- simpler and safer than trying to detect "was anything
    # actually new" (INSERT OR IGNORE's own change count is unreliable to
    # read back mid-transaction, and the cost of purging when nothing
    # changed is negligible).
    try:
        await _purge_fifo(chain, pool_address, timeframe)
    except Exception as exc:  # noqa: BLE001 -- same best-effort contract
        logger.info("candle_history: FIFO purge failed for %s/%s/%s (%s)", chain, pool_address[:10], timeframe, exc)


async def _purge_fifo(chain: str, pool_address: str, timeframe: str) -> None:
    """Keeps only the ``cap`` most recent rows for this exact key, oldest
    first evicted -- runs unconditionally on every ``record_candles`` call
    for that key (cheap: a no-op DELETE when already under cap, SQLite's
    own index lookup handles the ordering)."""
    cap = FIFO_CAP_BY_TIMEFRAME.get(timeframe)
    if cap is None:
        return
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            DELETE FROM candle_history
            WHERE chain = ? AND pool_address = ? AND timeframe = ?
            AND id NOT IN (
                SELECT id FROM candle_history
                WHERE chain = ? AND pool_address = ? AND timeframe = ?
                ORDER BY ts DESC LIMIT ?
            )
            """,
            (chain, pool_address, timeframe, chain, pool_address, timeframe, cap),
        )
        await db.commit()


async def get_history(
    chain: str, pool_address: str, timeframe: str, *, limit: int | None = None,
) -> list[dict]:
    """Full (or capped) series for one key, oldest first -- the read side
    for any future consumer (trend, backtest, dedup for wallet-scoring...),
    none wired yet (Phase 1 is storage-only, see the module docstring)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        query = (
            "SELECT * FROM candle_history WHERE chain = ? AND pool_address = ? AND timeframe = ? "
            "ORDER BY ts ASC"
        )
        params: list = [chain, pool_address, timeframe]
        if limit is not None:
            query = (
                "SELECT * FROM (SELECT * FROM candle_history WHERE chain = ? AND pool_address = ? "
                "AND timeframe = ? ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC"
            )
            params = [chain, pool_address, timeframe, limit]
        cur = await db.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]
