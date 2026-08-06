"""Indicator-combination shadow log (06/08) -- logs, NEVER blocks, NEVER fetches.

Operator guidance that motivates this module: RSI alone was already tried and
doesn't work ("on na deja tester avec le rsi seul sa marche pas"); the right
method is to draft a list of indicator COMBINATIONS and observe them on real
forward candles before forcing any single one into scalping_v8's live gate.

Reuses candles ALREADY fetched by v8's real evaluation cycle
(``evaluate_v8_wick_reversal`` in ``skills/scalping_variants.py``) -- zero
additional network calls, avoiding the exact GeckoTerminal-uncoordinated-
throughput incident a standalone backtest script triggered earlier the same
day (docs/api-rate-limit-calibration.md, 21/07 precedent). Each combo is
computed on the SAME candle window v8 itself sees, at the SAME moment v8
evaluates a real candidate -- true apples-to-apples forward data, not a
separate replay.

Same design as ``wick_filter_shadow.py``/``chasing_filter_shadow.py`` (the
established shadow-filter pattern this deliberately mirrors): dedicated
append-only table, best-effort writes that NEVER raise into a real trading
path, per-DB-path ensure cache.

Six candidates, chosen to cover distinct indicator families (never two
redundant on the same underlying signal) so the eventual forward comparison
actually discriminates:
  1. RSI divergence + MFI oversold (<=20)              -- momentum + volume-weighted momentum
  2. Stochastic %K oversold (<=15) + RSI divergence     -- range position + momentum
  3. Bollinger %B < 0 + wick confirmation (>=0.30)      -- mean-reversion band + price action
  4. VWAP z-score <=-2.5 + RSI divergence               -- volume-weighted deviation + momentum
  5. MACD histogram turning up + wick confirmation      -- trend momentum shift + price action
  6. Triple: RSI divergence + MFI oversold + wick       -- closest to v8's current logic, MFI added

Each column is 1 (combo fires), 0 (combo evaluated, does not fire), or NULL
(an input indicator was still in warmup on this candle window -- never
fabricated as False).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.skills.entry_signals import bullish_rsi_divergence
from aria_core.skills.indicators import (
    bollinger_percent_b,
    hammer_wick_ratio,
    macd_series,
    mfi_series,
    stochastic_k_series,
    vwap_zscore_series,
)
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

MFI_OVERSOLD = 20.0
STOCHASTIC_OVERSOLD = 15.0
VWAP_ZSCORE_OVERSOLD = -2.5
WICK_MIN_RATIO = 0.30

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # wick_filter_shadow.py.
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS combo_signal_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                wallet TEXT,
                combo1_rsi_div_mfi INTEGER,
                combo2_stoch_rsi_div INTEGER,
                combo3_boll_wick INTEGER,
                combo4_vwap_rsi_div INTEGER,
                combo5_macd_wick INTEGER,
                combo6_triple INTEGER,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_combo_signal_shadow_recorded_at "
            "ON combo_signal_shadow_log (recorded_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


def _and3(*values: bool | None) -> int | None:
    """AND over optional booleans: any unknown input -> unknown result (NULL),
    never silently treated as False -- same doctrine as every indicator's own
    ``None`` warmup convention."""
    if any(v is None for v in values):
        return None
    return 1 if all(values) else 0


def compute_combos(candles: list[Candle]) -> dict[str, int | None]:
    """Pure computation, no I/O -- kept separate from ``record_evaluation`` so
    it can be unit-tested without a DB."""
    closes = [c.close for c in candles]
    rsi_div_present, _ = bullish_rsi_divergence(candles)
    mfi_last = mfi_series(candles)[-1] if candles else None
    stoch_last = stochastic_k_series(candles)[-1] if candles else None
    boll_pb_last = bollinger_percent_b(closes)[-1] if closes else None
    wick_last = hammer_wick_ratio(candles[-1]) if candles else None
    vwap_z_last = vwap_zscore_series(candles)[-1] if candles else None
    _, _, histogram = macd_series(closes)
    hist_last = histogram[-1] if histogram else None
    hist_prev = histogram[-2] if len(histogram) >= 2 else None

    mfi_oversold = None if mfi_last is None else mfi_last <= MFI_OVERSOLD
    stoch_oversold = None if stoch_last is None else stoch_last <= STOCHASTIC_OVERSOLD
    boll_below_lower = None if boll_pb_last is None else boll_pb_last < 0
    wick_confirmed = None if wick_last is None else wick_last >= WICK_MIN_RATIO
    vwap_oversold = None if vwap_z_last is None else vwap_z_last <= VWAP_ZSCORE_OVERSOLD
    macd_turning_up = (
        None if hist_last is None or hist_prev is None else hist_last > hist_prev
    )

    return {
        "combo1_rsi_div_mfi": _and3(rsi_div_present, mfi_oversold),
        "combo2_stoch_rsi_div": _and3(stoch_oversold, rsi_div_present),
        "combo3_boll_wick": _and3(boll_below_lower, wick_confirmed),
        "combo4_vwap_rsi_div": _and3(vwap_oversold, rsi_div_present),
        "combo5_macd_wick": _and3(macd_turning_up, wick_confirmed),
        "combo6_triple": _and3(rsi_div_present, mfi_oversold, wick_confirmed),
    }


async def record_evaluation(
    contract: str, chain: str, *, wallet: str, candles: list[Candle], symbol: str | None = None,
) -> None:
    """Logs one shadow observation on candles already fetched by a real v8
    cycle. Best-effort: NEVER raises into the caller's trading path (same
    contract as ``wick_filter_shadow.record_trigger``)."""
    if not contract or not candles:
        return
    combos = compute_combos(candles)
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                """
                INSERT INTO combo_signal_shadow_log (
                    contract, chain, symbol, wallet,
                    combo1_rsi_div_mfi, combo2_stoch_rsi_div, combo3_boll_wick,
                    combo4_vwap_rsi_div, combo5_macd_wick, combo6_triple,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract, chain or "base", symbol, wallet,
                    combos["combo1_rsi_div_mfi"], combos["combo2_stoch_rsi_div"],
                    combos["combo3_boll_wick"], combos["combo4_vwap_rsi_div"],
                    combos["combo5_macd_wick"], combos["combo6_triple"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real cycle
        logger.info("combo_signal_shadow: record failed (%s)", exc)


async def list_recent(limit: int = 200) -> list[dict]:
    """Recent shadow observations, newest first -- for the future forward-
    validation pass (join against real trade outcomes once enough accumulate)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM combo_signal_shadow_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]
