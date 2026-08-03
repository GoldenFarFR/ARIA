"""Anti-chasing filter -- SHADOW MODE ONLY (Item #65, 08/03).

Real incident (Fren Pet, scalping_v3, id=102): the entire creux->pic move
happened inside a SINGLE 15min candle, so the %K confirmation could only
fire after the bounce was already spent -- entry_price ended up equal to
high_water_price (the position never went up again). Backtested on all 75
closed scalping trades: a 5% distance-to-recent-low cutoff would have
avoided the 3 worst losses on scalping_v3 (the only variant whose stop is
literally anchored on a recent candle low) while keeping its one winner.

Adversarial workflow review (2 agents, 08/03) found the plan wasn't ready to
go bloquant: the 5% threshold was backtested against ``invalidation_price``
(a DIFFERENT, narrower proxy -- previous CANDLE low, not the min over N
candles) rather than the ``recent_low`` metric this module actually computes
-- min(N candles) <= min(1 candle) always, so the real distance will always
read >= the backtested one, and the single surviving winner in that
backtest (distance 4.75%, just under 5%) would likely flip over the
threshold once measured correctly. No threshold is validated on the REAL
metric yet -- that's exactly what this shadow phase exists to produce.

This module NEVER blocks a trade. It only logs, for every real BUY (both
execution paths -- direct and limit-order trigger), what several CANDIDATE
thresholds would have decided, so a later query can compare the PnL of
"would have kept" vs "would have rejected" trades before ever making this
filter bloquant. See docs/HANDOFF_PIPELINE_MOMENTUM.md (Item #65) for the
exit criteria this shadow phase is judged against.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Candidate thresholds logged side by side -- NOT a single "the" threshold.
# The 08/03 workflow review's explicit recommendation: log several at once
# rather than commit to one number derived from a mismatched proxy metric.
CANDIDATE_THRESHOLDS_PCT: tuple[float, ...] = (3.0, 5.0, 7.0, 10.0)

# recent_low window, per mechanism -- NOT a single N=14 uniform choice (the
# workflow found no reason to tie this to "whatever's already computed" for
# variants that don't use a 14-candle oscillator). Matches each variant's
# OWN lookback: Stochastique %K (V3/V4) = 14, Bollinger/VWAP (V1/V2/V5) = 20,
# golden-pocket lookback (V6 legacy + standard momentum/swing/megacap) = 25.
RECENT_LOW_WINDOW_STOCHASTIC = 14
RECENT_LOW_WINDOW_BOLLINGER_VWAP = 20
RECENT_LOW_WINDOW_GOLDEN_POCKET = 25


_ensured_db_paths: set[str] = set()


async def _ensure_table() -> None:
    # 08/03 -- Gemini second-opinion review (scripts/consult-gemini.sh):
    # re-issuing CREATE TABLE/INDEX IF NOT EXISTS on every real BUY was
    # redundant DDL on a hot path -- skipped once this process has confirmed
    # the table exists FOR THE CURRENT DB_PATH. Keyed by path (not a plain
    # bool) so a DB_PATH change (e.g. between tests, each monkeypatching a
    # distinct tmp_path) is never masked by a stale flag from a previous path.
    if DB_PATH in _ensured_db_paths:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chasing_filter_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                wallet TEXT,
                source TEXT NOT NULL,
                variant TEXT,
                recent_low REAL,
                recent_low_window INTEGER,
                execution_price REAL,
                distance_pct REAL,
                would_reject_3pct INTEGER,
                would_reject_5pct INTEGER,
                would_reject_7pct INTEGER,
                would_reject_10pct INTEGER,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chasing_filter_shadow_recorded_at "
            "ON chasing_filter_shadow_log (recorded_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chasing_filter_shadow_wallet "
            "ON chasing_filter_shadow_log (wallet)"
        )
        await db.commit()
    _ensured_db_paths.add(DB_PATH)


def _column_for_threshold(threshold_pct: float) -> str | None:
    # Only the 4 candidate thresholds have a dedicated column -- anything
    # else is silently not persisted as a would_reject flag (distance_pct
    # itself always is, so any threshold can still be re-derived later).
    mapping = {3.0: "would_reject_3pct", 5.0: "would_reject_5pct",
               7.0: "would_reject_7pct", 10.0: "would_reject_10pct"}
    return mapping.get(threshold_pct)


async def record_check(
    contract: str, chain: str, *, wallet: str, source: str,
    recent_low: float | None, recent_low_window: int | None,
    execution_price: float | None, symbol: str | None = None,
    variant: str | None = None,
) -> None:
    """Logs one shadow observation. Best-effort: NEVER raises into the
    caller's real trading cycle -- a failure here must never block or delay
    an actual buy. ``source`` distinguishes the two execution paths this is
    wired into (``"direct_buy"`` -- paper_trader._open_new_entries_for_wallet
    -- and ``"limit_order_trigger"`` -- limit_orders.check_rsi_divergence_
    watching_order) -- both matter, the second is the statistically dominant
    one per the 08/03 workflow review (most watching orders trigger through
    it, not a direct buy).

    ``distance_pct`` and the would_reject flags are ``None``/unset whenever
    ``recent_low``/``execution_price`` aren't both known -- never a
    fabricated distance."""
    if not contract:
        return
    distance_pct = None
    if recent_low and recent_low > 0 and execution_price and execution_price > 0:
        distance_pct = (execution_price - recent_low) / recent_low * 100.0
    flags: dict[str, int | None] = {
        "would_reject_3pct": None, "would_reject_5pct": None,
        "would_reject_7pct": None, "would_reject_10pct": None,
    }
    if distance_pct is not None:
        for threshold in CANDIDATE_THRESHOLDS_PCT:
            column = _column_for_threshold(threshold)
            if column:
                flags[column] = 1 if distance_pct > threshold else 0
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO chasing_filter_shadow_log (
                    contract, chain, symbol, wallet, source, variant,
                    recent_low, recent_low_window, execution_price, distance_pct,
                    would_reject_3pct, would_reject_5pct, would_reject_7pct, would_reject_10pct,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract, chain or "base", symbol, wallet, source, variant,
                    recent_low, recent_low_window, execution_price, distance_pct,
                    flags["would_reject_3pct"], flags["would_reject_5pct"],
                    flags["would_reject_7pct"], flags["would_reject_10pct"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real cycle
        logger.warning("chasing_filter_shadow: record_check failed (%s)", exc)


def recent_low_from_candles(candles: list, window: int) -> float | None:
    """``min(low)`` over the last ``window`` candles already in hand --
    zero network cost (same doctrine as entry_atr_pct, Item #253). ``None``
    if fewer candles are available than ``window`` (never a partial/
    misleading low computed on too short a history)."""
    if not candles or len(candles) < window:
        return None
    return min(c.low for c in candles[-window:])
