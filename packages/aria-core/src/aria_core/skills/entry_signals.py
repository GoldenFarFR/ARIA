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


def fibonacci_zone(candles: list[Candle]) -> dict | None:
    """Golden pocket (0.618-0.786) + levels, from the window's low to its high.

    Measures the swing-low -> swing-high leg; retracements are SUPPORTS below
    the high. Returns None if the window is flat/too short.
    """
    if len(candles) < 2:
        return None
    hi = max(c.high for c in candles)
    lo = min(c.low for c in candles)
    if hi <= lo:
        return None
    diff = hi - lo
    levels = {r: hi - diff * r for r in _FIB_RATIOS}
    # Golden pocket: between the 0.618 and 0.786 retracement (the deep "red" zone).
    return {
        "high": hi,
        "low": lo,
        "levels": levels,
        "gp_high": levels[0.618],  # zone's upper bound (shallower retracement)
        "gp_low": levels[0.786],   # lower bound (deeper retracement)
    }


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
    """
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
        return False, ""
    _, l2, r2 = pivots[-1]
    for _, l1, r1 in reversed(pivots[:-1]):
        if l2 < l1 and r2 > r1:
            return True, f"plus-bas prix {l2:.6g} < {l1:.6g} mais RSI remonte ({r1:.0f} → {r2:.0f})"
    return False, ""


def detect_entry(
    candles: list[Candle],
    *,
    lookback: int = _DEFAULT_LOOKBACK,
    tolerance: float = 0.03,
    execution_price: float | None = None,
) -> EntrySignal:
    """Detects the "golden pocket + RSI divergence" setup over <= ``lookback`` candles.

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
    if len(candles) < _RSI_PERIOD + 2:
        return EntrySignal(present=False, reasons=["série trop courte pour un signal fiable"])

    window = candles[-lookback:]
    fib = fibonacci_zone(window)
    div, div_base = bullish_rsi_divergence(candles, lookback=lookback)
    close = candles[-1].close
    reasons: list[str] = []

    in_gp = False
    if fib is not None:
        gp_low, gp_high = fib["gp_low"], fib["gp_high"]  # gp_low < gp_high
        if gp_low * (1 - tolerance) <= close <= gp_high * (1 + tolerance):
            in_gp = True
            reasons.append(f"prix {close:.6g} dans la zone Fibonacci 0,618–0,786 (support profond)")
    if div:
        reasons.append("divergence haussière RSI : " + div_base)

    if not (in_gp and div and fib is not None):
        return EntrySignal(
            present=False, reasons=reasons or ["setup non réuni"],
            in_golden_pocket=in_gp, rsi_divergence=div, lookback_used=len(window),
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
    invalidation = fib["gp_low"] * (1 - 0.02)
    target = fib["high"]
    rr = None
    if entry > invalidation and target > entry:
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
        lookback_used=len(window),
    )
