"""High-quality entry signals (facts-only, deterministic) — the hunter's scope.

Encodes a proven entry setup: **price in the deep Fibonacci zone** (golden
pocket 0.618-0.786, the "red support") **+ bullish RSI divergence**, formed
within a **<= 25 candle** window. When both coincide, it's historically one
of the best entry points for the risk/reward ratio (tight invalidation below
support, target = return to the top of the range -> generous R/R).

Everything is derived from the real OHLCV series (same candles -> same
result). No invented value: without a setup, ``present=False`` (the report
simply omits the signal). This is a **hypothesis** (operator intuition) that
the track record validates — never a dogma.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aria_core.skills.ta_levels import Candle

_FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)
_DEFAULT_LOOKBACK = 25
_RSI_PERIOD = 14

# 25/07, operator-found gap: a real buy (ZEN) had "RSI remonte (39 -> 40)" as
# its divergence -- accepted by the old criterion (a PURELY relative check:
# price lower low + RSI higher low, no floor or ceiling on the RSI value
# itself), even though 39/40 sits well outside a real oversold zone. Operator
# explicit requirement, timeframe-independent (same reading rule on 15min,
# 1h, 4h, or day candles -- only the input candles change, never this logic):
# the RECENT RSI value at the divergence point must itself sit in [20, 40] --
# a real oversold-recovering zone, not just any two points where the second
# is marginally higher than the first.
RSI_DIVERGENCE_MIN = 20.0
RSI_DIVERGENCE_MAX = 40.0

# Item #101 (26/07): dedicated RSI period for the scalping mode (15-30min
# candles). Workflow research (3-agent pipeline, operator-requested): generic
# forex-scalping advice (period 5-9) is calibrated for a CALMER underlying --
# Base microcaps are already noisy even on a swing timeframe, so a period
# that short would amplify false signals. Multiple sources converge on 9-11
# specifically paired with a 15min chart -- 10 picked as the middle of that
# range, not backtested on real Base memecoins yet (flagged open risk).
SCALPING_RSI_PERIOD = 10


@dataclass(frozen=True)
class EntrySignal:
    """A detected entry point (or its absence), with its factual basis and R/R."""

    present: bool
    reasons: list[str] = field(default_factory=list)
    in_golden_pocket: bool = False
    rsi_divergence: bool = False
    entry: float | None = None
    invalidation: float | None = None
    target: float | None = None
    rr: float | None = None
    lookback_used: int = 0
    # Item #101 (26/07), operator request ("aria doit pouvoir connaitre en
    # temps reel toute les valeurs de son golden pocket d'entree et de
    # sortie"): the golden pocket's own bounds (0.618/0.786 retracement) --
    # previously computed internally by fibonacci_zone() but never returned,
    # so nothing downstream (thesis text, relay conversation) could cite the
    # actual entry ZONE, only the derived invalidation/target levels.
    gp_low: float | None = None
    gp_high: float | None = None
    # Item #182 (28/07), golden-pocket liberation: the window's swing-high --
    # same value ``detect_entry`` uses as ``target`` once a setup IS
    # confirmed -- exposed even when ``present=False`` (as long as a
    # Fibonacci zone is geometrically computable), so a caller can build a
    # hypothetical entry/target/invalidation for a "not there yet" setup
    # (limit-order watch-and-wait) without re-deriving fibonacci_zone() itself.
    range_high: float | None = None
    # Item #182 (28/07), same rationale as range_high -- the window's
    # swing-low, needed to compute a retracement RATIO (how far price has
    # already pulled back from the high toward the zone) rather than a bare
    # above/below check, which can't distinguish "just starting a fresh
    # uptrend, still far from any pullback" from "already retracing toward
    # the zone" -- both look identical as a plain "price > gp_high" test.
    range_low: float | None = None
    # Item #183 (28/07), watch-RSI-divergence: RsiDivergenceDetail's own
    # gap/span, exposed here so a caller can judge the CONVICTION of a
    # confirmed divergence (netteté/brièveté, operator's own trading
    # intuition) -- filled whenever ``rsi_divergence`` is True, independent
    # of whether ``in_golden_pocket``/``present`` also hold (a divergence can
    # be confirmed before the price has even reached the zone, see
    # ``_golden_pocket_watch_candidate``'s counterpart).
    rsi_gap: float | None = None
    rsi_span: int | None = None


def rsi_series(closes: list[float], period: int = _RSI_PERIOD) -> list[float | None]:
    """Wilder RSI aligned on ``closes`` (None during the warm-up period)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    def _val(ag: float, al: float) -> float:
        if al == 0:
            return 100.0 if ag > 0 else 50.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _val(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _val(avg_gain, avg_loss)
    return out


def _fib_levels_from_range(lo: float, hi: float) -> dict | None:
    """Pure ratio math shared by ``fibonacci_zone`` (naive window max/min) and
    ``detect_swing_leg`` (ATR-confirmed pivot leg, 04/08) -- both ultimately
    describe the same "golden pocket between hi and lo" geometry, only the
    METHOD used to pick hi/lo differs. Returns None if the leg is flat."""
    if hi <= lo:
        return None
    diff = hi - lo
    levels = {r: hi - diff * r for r in _FIB_RATIOS}
    return {
        "high": hi,
        "low": lo,
        "levels": levels,
        "gp_high": levels[0.618],  # zone's upper bound (shallower retracement)
        "gp_low": levels[0.786],   # lower bound (deeper retracement)
    }


def fibonacci_zone(candles: list[Candle]) -> dict | None:
    """Golden pocket (0.618-0.786) + levels, from the window's low to its high.

    Measures the swing-low -> swing-high leg; retracements are SUPPORTS below
    the high. Returns None if the window is flat/too short.

    04/08, operator-found gap (real ZRO order, gp_high computed 0.7645 while
    the operator's own hand-drawn Fibonacci on the full visible chart gave a
    completely different zone): this function is a NAIVE max/min over
    whatever window it's given -- it does not detect a real swing, it just
    measures the extremes of the candles handed to it. ``detect_entry`` no
    longer calls this directly on a truncated window (see
    ``detect_swing_leg`` below, which finds the actual swing leg first) --
    kept as a pure, unchanged utility for existing callers/tests that
    genuinely want "the extremes of this exact candle list" (e.g. a caller
    that already trimmed the window itself)."""
    if len(candles) < 2:
        return None
    hi = max(c.high for c in candles)
    lo = min(c.low for c in candles)
    return _fib_levels_from_range(lo, hi)


# 04/08, operator + Fable 5 (relayed second opinion) -- root cause of the ZRO
# gap above: fibonacci_zone()'s naive max/min, combined with detect_entry's
# truncation to the last _DEFAULT_LOOKBACK (25) candles, measured a random
# recent sub-move instead of the token's real swing. Standard practice for an
# automated Fibonacci (verified via web research on ZigZag/Auto-Fib
# indicators, 04/08) is a ZigZag-style pivot detector: a reversal must
# exceed a threshold -- here ATR x multiplier, so it self-calibrates to each
# token's real volatility instead of an arbitrary fixed candle count -- before
# a new pivot is confirmed. Separate multiplier per mode (scalping's noisier
# 15-30min candles need a wider multiplier to avoid parasitic pivots than
# swing's calmer 1h-4h-1j candles): starting points from published ZigZag/ATR
# guidance (day-trading ~2.0xATR, swing default ~2.0-2.5xATR), taking the
# upper end of each range because Base micro-caps are noisier than the
# markets these defaults were calibrated on -- explicitly a starting point to
# recalibrate against paper_position_archive (gp_low/gp_high stored since
# 26/07), never a settled truth.
ZIGZAG_ATR_MULTIPLIER = 2.5
ZIGZAG_ATR_MULTIPLIER_SCALPING = 1.5


def _zigzag_pivots(
    candles: list[Candle], *, atr_mult: float, atr_period: int = 14
) -> list[tuple[int, float, str]]:
    """ATR-threshold ZigZag: confirms a pivot (index, price, "low"/"high")
    once price reverses by ``atr_mult`` x the ATR-at-that-point from the
    running candidate extreme since the last confirmed pivot. Alternates
    low/high by construction. Returns [] if there isn't enough data for ATR
    to warm up -- never a fabricated pivot on too-short a series."""
    from aria_core.skills.indicators import atr_series

    n = len(candles)
    atrs = atr_series(candles, period=atr_period)
    start = next((i for i in range(n) if atrs[i] is not None), None)
    if start is None or start >= n - 1:
        return []

    pivots: list[tuple[int, float, str]] = []
    cand_high, cand_high_idx = candles[start].high, start
    cand_low, cand_low_idx = candles[start].low, start
    trend: str | None = None  # None (undecided) / "up" (tracking a high) / "down" (tracking a low)

    for i in range(start + 1, n):
        atr = atrs[i]
        if atr is None:
            continue
        threshold = atr_mult * atr
        c = candles[i]

        if trend is None:
            if c.high > cand_high:
                cand_high, cand_high_idx = c.high, i
            if c.low < cand_low:
                cand_low, cand_low_idx = c.low, i
            if cand_high - c.low >= threshold:
                pivots.append((cand_high_idx, cand_high, "high"))
                trend, cand_low, cand_low_idx = "down", c.low, i
            elif c.high - cand_low >= threshold:
                pivots.append((cand_low_idx, cand_low, "low"))
                trend, cand_high, cand_high_idx = "up", c.high, i
        elif trend == "up":
            if c.high > cand_high:
                cand_high, cand_high_idx = c.high, i
            elif cand_high - c.low >= threshold:
                pivots.append((cand_high_idx, cand_high, "high"))
                trend, cand_low, cand_low_idx = "down", c.low, i
        else:  # trend == "down"
            if c.low < cand_low:
                cand_low, cand_low_idx = c.low, i
            elif c.high - cand_low >= threshold:
                pivots.append((cand_low_idx, cand_low, "low"))
                trend, cand_high, cand_high_idx = "up", c.high, i

    return pivots


def detect_swing_leg(candles: list[Candle], *, mode: str | None = None) -> dict | None:
    """Finds the LAST COMPLETE low -> high leg via ATR-confirmed ZigZag
    pivots, searched over the FULL candle series provided (never truncated
    to a fixed count -- the old bug was exactly this truncation). Returns
    None (fail-closed, per Fable 5's explicit spec, 04/08) when no valid
    bullish leg is currently confirmed:
    - not enough data for ATR to warm up,
    - fewer than 2 confirmed pivots,
    - the last two confirmed pivots are NOT a low followed by a high (e.g.
      price is currently in a fresh downswing) -- never silently falls back
      to a naive max/min, which is exactly the geometry bug this replaces."""
    atr_mult = ZIGZAG_ATR_MULTIPLIER_SCALPING if mode == "scalping" else ZIGZAG_ATR_MULTIPLIER
    pivots = _zigzag_pivots(candles, atr_mult=atr_mult)
    if len(pivots) < 2:
        return None
    (low_idx, low_price, low_kind), (high_idx, high_price, high_kind) = pivots[-2], pivots[-1]
    if low_kind != "low" or high_kind != "high":
        return None
    return {"low": low_price, "high": high_price, "low_idx": low_idx, "high_idx": high_idx}


@dataclass(frozen=True)
class RsiDivergenceDetail:
    """Item #183 (28/07), watch-RSI-divergence chantier -- operator's own
    trading intuition ("plus [la divergence] est nette et courte plus le
    retournement peut être fort"): beyond the plain present/absent boolean,
    exposes the two facts that measure how CONVINCING a confirmed divergence
    is, never invented -- both derived from the exact same pivot comparison
    ``bullish_rsi_divergence`` already performs.

    ``gap`` -- RSI netteté: how far the recent pivot's RSI rose above the
    earlier pivot's (``r2 - r1``). A bigger gap means a stronger momentum
    shift, not just a marginal uptick.
    ``span`` -- brièveté: how many candles separate the two pivots compared.
    A short span means the divergence formed quickly (a sharp reversal), a
    long span means it formed slowly across most of the lookback window.
    Both ``None`` when no divergence is present -- never a fabricated value."""

    present: bool
    reason: str
    gap: float | None = None
    span: int | None = None


def _bullish_rsi_divergence_detail(
    candles: list[Candle], *, lookback: int = _DEFAULT_LOOKBACK, period: int = _RSI_PERIOD
) -> RsiDivergenceDetail:
    """The real implementation behind ``bullish_rsi_divergence`` -- same exact
    detection logic (see that function's docstring for the full rationale),
    additionally returning the gap/span quality metrics (Item #183, 28/07)."""
    # RSI computed on the FULL series (warmed up before the window), then we
    # only look for lows within the last `lookback` candles. This way a
    # recent setup has a defined RSI even if the window is short.
    closes_all = [c.close for c in candles]
    rsis = rsi_series(closes_all, period)
    start = max(1, len(candles) - lookback) if lookback else 1
    pivots: list[tuple[int, float, float]] = []
    for i in range(start, len(candles) - 1):
        r = rsis[i]
        if r is None:
            continue
        if candles[i].low <= candles[i - 1].low and candles[i].low <= candles[i + 1].low:
            pivots.append((i, candles[i].low, r))
    if len(pivots) < 2:
        return RsiDivergenceDetail(False, "")
    i2, l2, r2 = pivots[-1]
    # 25/07, operator explicit requirement: the RECENT RSI value itself must
    # sit in a real oversold-recovering zone [20, 40] -- same rule regardless
    # of the candles' timeframe (15min/1h/4h/day), never just "any two points
    # where the second is marginally higher". A relative-only check let
    # RSI 39->40 (no real oversold reading at all) pass as a "divergence".
    if not (RSI_DIVERGENCE_MIN <= r2 <= RSI_DIVERGENCE_MAX):
        return RsiDivergenceDetail(False, "")
    for i1, l1, r1 in reversed(pivots[:-1]):
        if l2 < l1 and r2 > r1:
            return RsiDivergenceDetail(
                True,
                f"plus-bas prix {l2:.6g} < {l1:.6g} mais RSI remonte ({r1:.0f} → {r2:.0f})",
                gap=r2 - r1,
                span=i2 - i1,
            )
    return RsiDivergenceDetail(False, "")


def bullish_rsi_divergence(
    candles: list[Candle], *, lookback: int = _DEFAULT_LOOKBACK, period: int = _RSI_PERIOD
) -> tuple[bool, str]:
    """Bullish divergence: price makes a LOWER low, RSI makes a HIGHER low.

    Compares the window's LAST low (local minimum) against every EARLIER low,
    starting from the most recent -- not just the immediately preceding one
    (07/19, fixed after empirical investigation on real momentum pipeline
    candidates: 0 divergence detected on 8 candidates with usable data,
    against 4 golden pockets reached alone -- the comparison only examined
    the immediately adjacent pair of lows, missing any divergence formed over
    a wider leg of the same window). Same strict signal DEFINITION (lower
    price + higher RSI) as before -- only the SCOPE of the search is widened,
    not the criterion. Classic sign of a downtrend running out of steam.
    Returns (present, factual basis).

    Item #183 (28/07): thin wrapper over ``_bullish_rsi_divergence_detail``
    (same detection, plus gap/span quality metrics) -- kept byte-for-byte
    compatible with every existing caller/test, never changes this
    signature."""
    detail = _bullish_rsi_divergence_detail(candles, lookback=lookback, period=period)
    return detail.present, detail.reason


# Item #105 (26/07): exit-side mirror of RSI_DIVERGENCE_MIN/MAX above -- the
# recent pivot's RSI must sit in a real overbought-weakening zone [60, 80],
# same timeframe-independent doctrine (only the input candles change, never
# this range). Used as a scalping-mode SELL signal, not an entry gate.
RSI_EXIT_DIVERGENCE_MIN = 60.0
RSI_EXIT_DIVERGENCE_MAX = 80.0


def bearish_rsi_divergence(
    candles: list[Candle], *, lookback: int = _DEFAULT_LOOKBACK, period: int = _RSI_PERIOD
) -> tuple[bool, str]:
    """Bearish divergence: price makes a HIGHER high, RSI makes a LOWER high --
    the exact mirror of ``bullish_rsi_divergence`` (entry), used here as a
    scalping-mode EXIT signal (Item #105, 26/07). Same non-adjacent-pivot
    search (widest recent high vs any earlier high, not just the immediately
    preceding one) and same absolute-range requirement on the recent pivot's
    RSI (``RSI_EXIT_DIVERGENCE_MIN``/``MAX``) as the entry-side function --
    never a purely relative check. Classic sign of an uptrend running out of
    steam. Returns (present, factual basis)."""
    closes_all = [c.close for c in candles]
    rsis = rsi_series(closes_all, period)
    start = max(1, len(candles) - lookback) if lookback else 1
    pivots: list[tuple[int, float, float]] = []
    for i in range(start, len(candles) - 1):
        r = rsis[i]
        if r is None:
            continue
        if candles[i].high >= candles[i - 1].high and candles[i].high >= candles[i + 1].high:
            pivots.append((i, candles[i].high, r))
    if len(pivots) < 2:
        return False, ""
    _, h2, r2 = pivots[-1]
    if not (RSI_EXIT_DIVERGENCE_MIN <= r2 <= RSI_EXIT_DIVERGENCE_MAX):
        return False, ""
    for _, h1, r1 in reversed(pivots[:-1]):
        if h2 > h1 and r2 < r1:
            return True, f"plus-haut prix {h2:.6g} > {h1:.6g} mais RSI faiblit ({r1:.0f} → {r2:.0f})"
    return False, ""


# 30/07 -- real bug found live, 3 separate cases in one morning (CFI, TIBBIR,
# FOLKS-on-swing): ``invalidation = gp_low * (1 - 0.02)`` is a FIXED 2% below
# the zone's own low bound -- but the entry test above tolerates the price
# sitting up to ``tolerance`` (3%) BELOW ``gp_low`` and still calling it
# "in the golden pocket". When the entry price is already 2-3% under gp_low,
# the invalidation (still anchored 2% under gp_low, not under the entry
# price) can end up a fraction of a percent away from entry -- an
# R/R in the hundreds that has nothing to do with the trade's real risk.
# Confirmed structurally on 30/07: the SAME token (FOLKS) at the SAME spot
# price produced a sane R/R=1.6 on scalping (short candles -> gp_low
# naturally further from spot) and R/R=132.4 on swing (long candles -> gp_low
# landed almost exactly at spot) from the exact same formula.
#
# Fix: the invalidation can never sit closer to the ENTRY price than a floor
# derived from the token's own REAL volatility (ATR on these same candles,
# already the exact doctrine used for the trailing stop on open positions --
# see paper_trader.py's ATR_TRAIL_MULTIPLIER/MIN_ATR_TRAIL_PCT/MAX_ATR_TRAIL_
# PCT). Deliberately reuses the SAME numeric values here, independently
# defined (no cross-module import, same autonomy doctrine already applied
# elsewhere in this codebase, e.g. risk_guard.PRICE_IMPACT_MIN_RR) -- a
# stop-loss-shaped floor and a trailing-stop width are the same concept
# (a sane distance given this token's real volatility), so there's no reason
# for the two to diverge.
ATR_INVALIDATION_MULTIPLIER = 2.5
MIN_ATR_INVALIDATION_PCT = 0.05
MAX_ATR_INVALIDATION_PCT = 0.40

# 04/08 -- real bug found live (operator diligence, cross-checked against
# entry_signals): this floor was NEVER scoped by mode, exactly the same gap
# as the trailing stop before its 03/08 fix (see paper_trader.py's
# ATR_TRAIL_MULTIPLIER_SCALPING/MIN_ATR_TRAIL_PCT_SCALPING/MAX_ATR_TRAIL_PCT_
# SCALPING -- same bounds, same doctrine, mirrored here). Confirmed
# empirically on 3 real scalping_v6/v7 limit orders (CLANKER/MPP/a third),
# all pinned to exactly -5.0% invalidation -- the 5% swing-calibrated floor
# dominated every single one on scalping's much tighter ATR (15-30min
# candles). This is the mechanism that determines the R/R shown to the
# operator and used to calibrate risk_guard's conviction thresholds --
# leaving it unscoped silently biased that entire distribution.
ATR_INVALIDATION_MULTIPLIER_SCALPING = 2.5
MIN_ATR_INVALIDATION_PCT_SCALPING = 0.015
MAX_ATR_INVALIDATION_PCT_SCALPING = 0.10


def _invalidation_floor_pct_from_ratio(atr_ratio: float, *, mode: str | None = None) -> float:
    """Pure clamp/multiplier logic -- no candles required. Split out (04/08)
    from ``_invalidation_floor_pct`` specifically so an OFFLINE/retroactive
    recomputation (e.g. a one-off script recalculating historical R/R from
    persisted ``entry_atr_pct``, never real candles) can import this EXACT
    function instead of reimplementing the formula -- a duplicated copy would
    silently diverge from this one the day either changes (operator-mandated
    process guardrail, diligence #9/#6)."""
    if mode == "scalping":
        return max(
            MIN_ATR_INVALIDATION_PCT_SCALPING,
            min(MAX_ATR_INVALIDATION_PCT_SCALPING, ATR_INVALIDATION_MULTIPLIER_SCALPING * atr_ratio),
        )
    return max(
        MIN_ATR_INVALIDATION_PCT,
        min(MAX_ATR_INVALIDATION_PCT, ATR_INVALIDATION_MULTIPLIER * atr_ratio),
    )


def _invalidation_floor_pct(candles: list[Candle], *, mode: str | None = None) -> float | None:
    """ATR-derived floor on how close the invalidation may sit to the entry
    price, as a fraction. ``None`` if ATR isn't computable yet (insufficient
    candles) -- caller falls back to the pre-existing fixed-2%-below-gp_low
    behavior, never a fabricated distance.

    ``mode`` (04/08): scalping uses its own dedicated bounds/multiplier (see
    the module-level constants above) instead of the swing-calibrated
    defaults -- same ``mode == "scalping"`` switch already used by
    ``paper_trader._effective_trail_pct``. The actual clamp/multiplier logic
    lives in ``_invalidation_floor_pct_from_ratio`` (see its own docstring)."""
    from aria_core.skills.indicators import atr_series

    atr_values = atr_series(candles)
    last_atr = atr_values[-1] if atr_values else None
    if last_atr is None or last_atr <= 0:
        return None
    close = candles[-1].close
    if not close or close <= 0:
        return None
    return _invalidation_floor_pct_from_ratio(last_atr / close, mode=mode)


# 04/08 -- the fixed 3% tolerance (below) was found disproportionate on a
# narrow ZigZag-detected leg (operator/Fable 5): on ZRO's real ~4.2% leg, 3%
# widened the entry band by nearly as much as the golden pocket itself is
# wide. ``tolerance`` is now proportional to the zone's own width (a fraction
# of gp_high-gp_low), capped at the previous 3% absolute -- a CAP, never a
# floor, so a wide/healthy swing is never given MORE slack than before.
# 0.275 = midpoint of Fable 5's recommended 25-30% range, itself a starting
# point (not backtested) like the ZigZag multipliers above.
ZONE_TOLERANCE_FRACTION = 0.275
ZONE_TOLERANCE_MAX = 0.03


def detect_entry(
    candles: list[Candle],
    *,
    lookback: int = _DEFAULT_LOOKBACK,
    tolerance: float | None = None,
    execution_price: float | None = None,
    period: int = _RSI_PERIOD,
    mode: str | None = None,
) -> EntrySignal:
    """Detects the "golden pocket + RSI divergence" setup.

    ``lookback`` (04/08 -- narrowed scope): used ONLY to bound the RSI
    divergence pivot search (``_bullish_rsi_divergence_detail``), same as
    before. The golden pocket itself is no longer computed on a
    ``candles[-lookback:]`` slice -- ``detect_swing_leg`` searches the FULL
    candle series for the actual swing leg (see its docstring; this was the
    root cause of a real geometry bug found 04/08 on a live ZRO order).

    ``tolerance`` (04/08): ``None`` (default) computes a proportional
    tolerance from the detected zone's own width, capped at
    ``ZONE_TOLERANCE_MAX`` -- see the constant's comment above. An explicit
    float still overrides it completely (e.g. a test that wants a fixed
    value), unchanged behavior for that case.

    ``period`` (Item #101, 26/07): the RSI period passed through to
    ``bullish_rsi_divergence`` -- default ``_RSI_PERIOD`` (14, swing/standard
    mode), unchanged behavior for every existing caller. The scalping mode
    passes ``SCALPING_RSI_PERIOD`` (10) instead.

    ``mode`` (04/08): forwarded to ``_invalidation_floor_pct`` so scalping
    gets its own dedicated ATR floor bounds instead of the swing-calibrated
    defaults -- unchanged behavior for every existing caller that doesn't
    pass it (``None`` keeps the original swing bounds).

    ``present`` only if the current price is in (or very close to) the deep
    Fibonacci zone AND a bullish RSI divergence is present. Then provides
    entry/invalidation/target derived from the real levels + the R/R.

    ``execution_price`` (07/19, optional -- unchanged behavior without it,
    e.g. ``acp_onchain_scan.py``/``/vc`` where there's no imminent execution
    at a precise price): a real finding while checking a trade's legitimacy
    (GITLAWB) at the operator's request -- the displayed R/R (149.1) came
    from the last OHLCV candle's ``close`` (one source), while the price
    ACTUALLY executed comes from ANOTHER source (real-time DexScreener,
    ``momentum_entry.py``) which can diverge by a few % at the same nominal
    instant (not just time drift -- two different providers). Result: the
    displayed R/R can significantly over/underestimate that of the trade
    ACTUALLY taken. When provided (and consistent --
    ``execution_price > invalidation``), replaces the ``close`` as the entry
    reference for R/R (AND the returned ``entry`` field) --
    ``invalidation``/``target`` stay derived from the real Fibonacci/RSI
    levels, unchanged (they describe the setup's STRUCTURE, not a fill price)."""
    if len(candles) < period + 2:
        return EntrySignal(present=False, reasons=["série trop courte pour un signal fiable"])

    leg = detect_swing_leg(candles, mode=mode)
    fib = _fib_levels_from_range(leg["low"], leg["high"]) if leg is not None else None
    div_detail = _bullish_rsi_divergence_detail(candles, lookback=lookback, period=period)
    div, div_base = div_detail.present, div_detail.reason
    close = candles[-1].close
    reasons: list[str] = []

    in_gp = False
    if fib is not None:
        gp_low, gp_high = fib["gp_low"], fib["gp_high"]  # gp_low < gp_high
        effective_tolerance = tolerance
        if effective_tolerance is None:
            zone_width_pct = (gp_high - gp_low) / gp_high if gp_high else 0.0
            effective_tolerance = min(ZONE_TOLERANCE_MAX, ZONE_TOLERANCE_FRACTION * zone_width_pct)
        if gp_low * (1 - effective_tolerance) <= close <= gp_high * (1 + effective_tolerance):
            in_gp = True
            # Item #101 (26/07): the exact zone bounds are now cited in the
            # thesis text itself, not just the principle -- operator request
            # ("le parametre d'entree et de sortie doit etre 100% dans la
            # these").
            reasons.append(
                f"prix {close:.6g} dans la zone Fibonacci 0,618–0,786 "
                f"({gp_low:.6g}–{gp_high:.6g}, support profond)"
            )
    if div:
        reasons.append("divergence haussière RSI : " + div_base)

    if not (in_gp and div and fib is not None):
        return EntrySignal(
            present=False, reasons=reasons or ["setup non réuni"],
            in_golden_pocket=in_gp, rsi_divergence=div,
            lookback_used=(leg["high_idx"] - leg["low_idx"] + 1) if leg is not None else 0,
            # Item #182 (28/07): the zone itself is a fact derived from the
            # real candles (fibonacci_zone), independent of whether RSI has
            # confirmed a divergence yet -- exposing it here invents nothing,
            # it only surfaces a value already computed above. None when no
            # zone is geometrically computable (``fib is None``, flat/too-short
            # window), never a fabricated level.
            gp_low=fib["gp_low"] if fib is not None else None,
            gp_high=fib["gp_high"] if fib is not None else None,
            range_high=fib["high"] if fib is not None else None,
            range_low=fib["low"] if fib is not None else None,
            # Item #183 (28/07): a divergence can be confirmed BEFORE the
            # golden pocket zone is reached (in_gp=False) -- exposed
            # regardless of whether the overall setup is "present", same
            # doctrine as gp_low/gp_high above (a fact already computed,
            # never gated behind the final present/absent verdict).
            rsi_gap=div_detail.gap,
            rsi_span=div_detail.span,
        )

    # Zone derived from the real levels: invalidation below the deep support,
    # target = return to the top of the range (swing-high retest) -> generous
    # R/R by construction.
    # 07/19 -- ``execution_price`` (if provided and consistent) replaces the
    # close as the entry reference for R/R -- the R/R must reflect the trade
    # ACTUALLY taken, not an estimate based on another price source (see docstring).
    entry = close
    if execution_price is not None and execution_price > 0:
        entry = execution_price
    # Structural level: independent of ``entry``, describes the setup itself,
    # never a fill price. This is what gates whether ``entry`` is even
    # consistent with the setup (below -- see the ``rr`` guard below).
    structural_invalidation = fib["gp_low"] * (1 - 0.02)
    invalidation = structural_invalidation
    # 30/07 -- ATR floor (see this module's own comment above): never let the
    # invalidation sit closer to ENTRY than this token's real volatility
    # allows, regardless of how close gp_low happens to be to the entry
    # price. Takes the LOWER of the two -- a naturally wide Fibonacci
    # structure is never tightened, only a dangerously narrow one is widened.
    atr_floor_pct = _invalidation_floor_pct(candles, mode=mode)
    if atr_floor_pct is not None and entry > 0:
        invalidation = min(invalidation, entry * (1 - atr_floor_pct))
    target = fib["high"]
    rr = None
    # Consistency check against the STRUCTURAL level, never the ATR-widened
    # ``invalidation`` -- the ATR floor is anchored ON entry (entry * (1 - x),
    # x > 0), so it is ALWAYS strictly below entry by construction and could
    # never itself catch an execution_price that has broken clean through the
    # real support (structural_invalidation) -- it would silently "follow"
    # entry down and still produce a number. This guard is what a corrupted/
    # aberrant execution_price actually needs to be caught by.
    if entry > structural_invalidation and target > entry:
        rr = round((target - entry) / (entry - invalidation), 1)
    return EntrySignal(
        present=True,
        reasons=reasons,
        in_golden_pocket=True,
        rsi_divergence=True,
        entry=entry,
        invalidation=invalidation,
        target=target,
        rr=rr,
        lookback_used=leg["high_idx"] - leg["low_idx"] + 1,
        gp_low=gp_low,
        gp_high=gp_high,
        range_high=target,
        range_low=fib["low"],
        rsi_gap=div_detail.gap,
        rsi_span=div_detail.span,
    )
