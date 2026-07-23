"""General technical indicators (facts-only, deterministic) — EMA, MACD, Bollinger, ATR.

Complements `entry_signals.rsi_series` (Wilder RSI) and `ta_levels` (levels/trend).
`CLAUDE.md` has long announced a "TA Engine (RSI/MACD/EMA/fibo/divergences)" —
MACD and EMA were in fact never computed anywhere before this module (a gap
discovered on 10/07 while checking the real code before writing anything).
Bollinger Bands, meanwhile, were never announced but were missing to cover
the 10/07 operator request (RSI + Bollinger + volumes + candles as inputs
to a future backtest engine — see `docs/architecture-extensibilite.md`).
ATR (Average True Range, 19/07) answers the same gap as EMA/MACD originally:
announced nowhere but absent from the codebase (confirmed by exhaustive
grep) until a Gemini cross-review flagged that a fixed-percentage trailing
stop ignores a token's real volatility.

Everything is derived from the provided close series (or, for ATR, the full
high/low/close candles) — same inputs -> same result. No made-up value: an
insufficient warm-up period -> ``None`` at those positions, never an estimate.
"""
from __future__ import annotations

import math

from aria_core.skills.ta_levels import Candle

_EMA_FAST = 12
_EMA_SLOW = 26
_MACD_SIGNAL = 9
_BOLLINGER_PERIOD = 20
_BOLLINGER_NUM_STD = 2.0
_ATR_PERIOD = 14


def ema_series(closes: list[float], period: int) -> list[float | None]:
    """EMA aligned on ``closes``. Seeded by the SMA of the first ``period``
    closes (standard convention), then EMA recursion. ``None`` during the
    warm-up period."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out

    k = 2.0 / (period + 1)
    sma = sum(closes[:period]) / period
    out[period - 1] = sma
    prev = sma
    for i in range(period, n):
        prev = closes[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd_series(
    closes: list[float],
    *,
    fast: int = _EMA_FAST,
    slow: int = _EMA_SLOW,
    signal: int = _MACD_SIGNAL,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Standard MACD (line, signal, histogram), aligned on ``closes``.

    MACD line = fast EMA - slow EMA. Signal = EMA of the MACD line. Histogram
    = MACD - signal. ``None`` until the slow EMA (the longest warm-up period)
    is available.
    """
    n = len(closes)
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)

    macd_line: list[float | None] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # Signal EMA applied only on the defined segment of the MACD line
    # (otherwise the leading Nones would throw off ema_series' SMA seeding).
    first_defined = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n
    if first_defined is not None:
        defined_macd = [v for v in macd_line[first_defined:]]  # all non-None from here on
        signal_on_defined = ema_series(defined_macd, signal)  # type: ignore[arg-type]
        for offset, value in enumerate(signal_on_defined):
            if value is None:
                continue
            idx = first_defined + offset
            signal_line[idx] = value
            histogram[idx] = macd_line[idx] - value

    return macd_line, signal_line, histogram


def bollinger_bands(
    closes: list[float],
    *,
    period: int = _BOLLINGER_PERIOD,
    num_std: float = _BOLLINGER_NUM_STD,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Bollinger Bands (middle = SMA, upper/lower = SMA ± ``num_std`` population
    standard deviations over the same window). ``None`` during the warm-up period.

    Standard convention: POPULATION standard deviation (``period`` divisor, not
    ``period - 1``) over the sliding window — not the sample standard deviation.
    """
    n = len(closes)
    middle: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return middle, upper, lower

    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        middle[i] = mean
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return middle, upper, lower


def atr_series(candles: list[Candle], *, period: int = _ATR_PERIOD) -> list[float | None]:
    """Wilder's Average True Range (ATR), aligned on ``candles`` — measures an
    asset's RAW VOLATILITY (normal "breathing" amplitude), without indicating
    direction (19/07, Gemini cross-review: replaces a fixed-percentage
    trailing stop with a width that adapts to each token).

    True Range of a candle = max(high-low, |high - previous close|,
    |low - previous close|) — also captures gaps, not just intra-candle
    amplitude. The very first candle has no previous close, uses high-low
    alone (standard convention, no made-up data).

    Seeded by a simple average of the first ``period`` True Ranges, then
    Wilder smoothing (``atr = (previous_atr * (period-1) + tr) / period`` —
    alpha = 1/period, deliberately NOT the 2/(period+1) of a classic EMA:
    that's the historical ATR convention, different from ``ema_series``
    above). ``None`` during the warm-up period, never a made-up value."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out

    true_ranges: list[float] = [0.0] * n
    for i, c in enumerate(candles):
        high_low = c.high - c.low
        if i == 0:
            true_ranges[i] = high_low
        else:
            prev_close = candles[i - 1].close
            true_ranges[i] = max(high_low, abs(c.high - prev_close), abs(c.low - prev_close))

    atr = sum(true_ranges[:period]) / period
    out[period - 1] = atr
    for i in range(period, n):
        atr = (atr * (period - 1) + true_ranges[i]) / period
        out[i] = atr
    return out
