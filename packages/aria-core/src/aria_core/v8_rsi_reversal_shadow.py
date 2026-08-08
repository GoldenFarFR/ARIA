"""V8 RSI-reversal shadow (08/08, Claude's own pocket, carte blanche --
operator: "il n'y a pas de session dedie a v8 c'est ton agent tu le code en
temps reel").

Backtest basis (08/08, 35 real v8 tokens -- the same 35 unique contracts
across v8's own 43 real closed trades -- 500 real CoinMarketCap candles each,
~5-21 days of history depending on timeframe): a plain RSI oversold-exit/
overbought-exit round trip on the 60-MINUTE timeframe clearly beat v8's real
wick+divergence+ATR-trail system (which backtested NEGATIVE avg return on
ALL 3 tested timeframes -- 15/30/60min -- consistent with its real 0-winner/
43-trade prod history, see _V8_ENTRY_PAUSED's own comment in
scalping_variants.py).

Two settings both showed broad CROSS-TOKEN robustness on the backtest (not
concentrated on 1-2 outlier tokens -- operator's own explicit check before
authorizing this build):
- RSI(21), thresholds 30/70: 16/35 tokens touched at least once, ZERO losing
  tokens, 17 trades total, +7.43% avg return, 100% win rate.
- RSI(14), thresholds 25/75: 17/35 tokens touched, 14/17 winning tokens
  (BNKR/LFI/cbXRP the 3 losers), 20 trades total, +4.58% avg return, 80% WR.

Both tracked here in SHADOW mode, same doctrine as v8_limit_shadow.py/
combo_signal_shadow.py: forward-validated on real candidates before ever
routing a real paper trade through either. Purely observational -- never
touches the real $1M paper portfolio, never places a real or even a
real-simulated trade, a completely separate table read by nothing else in
the pipeline. Best-effort throughout: a failure here must never block a
real v8 evaluation.

Resamples v8's OWN already-fetched 15/30min candles up to 60min instead of
fetching a dedicated series -- zero extra network call. Detects the real
spacing of the candles it's handed (mode="scalping" can silently fall back
15m->30m per candidate, see _gates_and_candles's own docstring) and
aggregates accordingly (factor 4 for 15min sources, 2 for 30min)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.skills.entry_signals import rsi_series
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

VARIANTS: dict[str, dict[str, float]] = {
    "rsi14": {"period": 14, "low": 25.0, "high": 75.0},
    "rsi21": {"period": 21, "low": 30.0, "high": 70.0},
}

# Backtest window topped out at ~21 days of real 60min history -- a position
# that never sees RSI cross back down within 3 days is no longer comparable
# to what was actually backtested, close it out as an explicit timeout
# rather than let it drift indefinitely.
MAX_HOLD_HOURS = 72.0

_MIN_CANDLES_60M = 25  # RSI(21) needs period+1 to produce its first value

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS v8_rsi_reversal_shadow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                variant TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                entry_price REAL NOT NULL,
                entry_rsi REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                exit_price REAL,
                exit_rsi REAL,
                close_reason TEXT,
                pnl_pct REAL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_v8_rsi_reversal_shadow_lookup "
            "ON v8_rsi_reversal_shadow (contract, chain, variant, status)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


def _resample_to_60m(candles: list[Candle]) -> list[Candle]:
    """Aggregates already-fetched 15/30min candles up to 60min, keeping only
    complete groups anchored on the MOST RECENT candle (older leftover
    candles that don't fill a full group are dropped, never a partial/
    in-progress final bar)."""
    if len(candles) < 2:
        return []
    spacing = candles[-1].ts - candles[-2].ts
    if spacing <= 0:
        return []
    factor = max(1, round(3600 / spacing))
    if factor == 1:
        return candles
    n_complete = len(candles) // factor
    if n_complete == 0:
        return []
    trimmed = candles[-(n_complete * factor):]
    out: list[Candle] = []
    for i in range(0, len(trimmed), factor):
        group = trimmed[i:i + factor]
        out.append(
            Candle(
                ts=group[-1].ts,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return out


async def record_evaluation(
    contract: str, chain: str, *, symbol: str | None, candles: list[Candle],
) -> None:
    """Called on every real v8 evaluation that reaches valid candles (same
    doctrine as combo_signal_shadow.record_evaluation) -- advances any open
    shadow position and opens a new one on a fresh oversold-exit crossover,
    for BOTH tracked RSI variants independently."""
    try:
        candles_60m = _resample_to_60m(candles)
        if len(candles_60m) < _MIN_CANDLES_60M:
            return
        closes = [c.close for c in candles_60m]
        last_close = closes[-1]
        await _ensure_table()
        for variant_name, cfg in VARIANTS.items():
            series = rsi_series(closes, period=int(cfg["period"]))
            if len(series) < 2 or series[-1] is None or series[-2] is None:
                continue
            await _advance_or_open(
                contract, chain, symbol, variant_name, cfg,
                prev=series[-2], cur=series[-1], last_close=last_close,
            )
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real evaluation
        logger.info("v8_rsi_reversal_shadow: record_evaluation failed (%s)", exc)


def _hours_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


async def _advance_or_open(
    contract: str, chain: str, symbol: str | None, variant_name: str, cfg: dict[str, float],
    *, prev: float, cur: float, last_close: float,
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur_db = await db.execute(
            "SELECT * FROM v8_rsi_reversal_shadow WHERE contract = ? AND chain = ? "
            "AND variant = ? AND status = 'open' LIMIT 1",
            (contract, chain or "base", variant_name),
        )
        row = await cur_db.fetchone()
        row = dict(row) if row else None

        if row is None:
            crossed_up = prev < cfg["low"] <= cur
            if not crossed_up:
                return
            await db.execute(
                """
                INSERT INTO v8_rsi_reversal_shadow (
                    contract, chain, symbol, variant, status, entry_price,
                    entry_rsi, opened_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (
                    contract, chain or "base", symbol, variant_name, last_close, cur,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
            return

        crossed_down = prev > cfg["high"] >= cur
        age_hours = _hours_since(row["opened_at"]) or 0.0
        close_reason: str | None = None
        if crossed_down:
            close_reason = "rsi_exit_overbought"
        elif age_hours >= MAX_HOLD_HOURS:
            close_reason = "timeout_max_hold"
        if close_reason is None:
            return

        entry_price = row["entry_price"]
        pnl_pct = (last_close / entry_price - 1.0) * 100.0 if entry_price else 0.0
        await db.execute(
            """
            UPDATE v8_rsi_reversal_shadow SET status = 'closed', closed_at = ?,
                exit_price = ?, exit_rsi = ?, close_reason = ?, pnl_pct = ? WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), last_close, cur, close_reason, pnl_pct, row["id"]),
        )
        await db.commit()


async def summary() -> dict:
    """Aggregate read for session/monitoring use -- never called from a real
    trading path."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT variant, status, contract, pnl_pct FROM v8_rsi_reversal_shadow")
        rows = [dict(r) for r in await cur.fetchall()]
    out: dict[str, dict] = {}
    for variant_name in VARIANTS:
        vrows = [r for r in rows if r["variant"] == variant_name]
        closed = [r for r in vrows if r["status"] == "closed"]
        wins = sum(1 for r in closed if (r["pnl_pct"] or 0) > 0)
        out[variant_name] = {
            "open": sum(1 for r in vrows if r["status"] == "open"),
            "closed": len(closed),
            "wins": wins,
            "distinct_tokens": len({r["contract"] for r in vrows}),
            "avg_pnl_pct": (sum(r["pnl_pct"] or 0 for r in closed) / len(closed)) if closed else None,
        }
    return out
