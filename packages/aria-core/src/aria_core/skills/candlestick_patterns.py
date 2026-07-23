"""Japanese candlestick pattern detection (facts-only, deterministic).

Complements `indicators.py` (EMA/MACD/Bollinger) and `entry_signals.py`
(Fibonacci/RSI divergence) — the "candle arrangement" requested by the
operator on 10/07, designed as an input building block for a future backtest
engine (cf. `docs/architecture-extensibilite.md`) rather than as a standalone
signal.

Each detector is a pure function over real OHLC ratios (body/wicks), with
SIMPLE, DECLARED thresholds right here (no universal definition exists for
these patterns) — same doctrine as `btc_cycles` for its heuristics. Never a
trend judgment: these functions describe the SHAPE of a candle (or a pair),
not what happens next — interpretation stays with the backtest engine or the
LLM, anchored on real numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

from aria_core.skills.ta_levels import Candle

# SIMPLE, DECLARED thresholds (not an official standard — cf. module doctrine).
_DOJI_BODY_RATIO_MAX = 0.1       # body <= 10% of range = doji
_HAMMER_LOWER_WICK_MIN = 2.0     # lower wick >= 2x the body
_HAMMER_UPPER_WICK_MAX = 0.3     # upper wick <= 30% of the body
_MARUBOZU_BODY_RATIO_MIN = 0.9   # body >= 90% of range = marubozu (near-zero wick)


@dataclass(frozen=True)
class CandlePattern:
    """A pattern detected at index ``i`` of a candle series, with its factual basis."""

    index: int
    name: str
    direction: str  # "bullish" | "bearish" | "neutral"
    detail: str


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return c.high - c.low


def _upper_wick(c: Candle) -> float:
    return c.high - max(c.open, c.close)


def _lower_wick(c: Candle) -> float:
    return min(c.open, c.close) - c.low


def is_doji(c: Candle) -> bool:
    """Body near zero relative to the range — indecision between buyers/sellers."""
    rng = _range(c)
    if rng <= 0:
        return False
    return _body(c) / rng <= _DOJI_BODY_RATIO_MAX


def is_marubozu(c: Candle) -> bool | None:
    """Body that occupies almost the entire range (near-zero wick). None if range is zero.

    Returns ``True``/``False``, direction (bullish if close > open) is
    carried separately by the caller via ``c.close > c.open``.
    """
    rng = _range(c)
    if rng <= 0:
        return None
    return _body(c) / rng >= _MARUBOZU_BODY_RATIO_MIN


def is_hammer(c: Candle) -> bool:
    """Long lower wick, small body near the top of the range, near-zero upper
    wick — rejection of a tested-then-pushed-back low (bullish reading IF at
    the end of a decline, not judged here, only the shape)."""
    body = _body(c)
    if body <= 0:
        return False
    return (
        _lower_wick(c) >= _HAMMER_LOWER_WICK_MIN * body
        and _upper_wick(c) <= _HAMMER_UPPER_WICK_MAX * body
    )


def is_shooting_star(c: Candle) -> bool:
    """Mirror of the hammer: long upper wick, small body near the bottom of
    the range — rejection of a tested-then-pushed-back high (bearish reading
    IF at the end of a rally, not judged here, only the shape)."""
    body = _body(c)
    if body <= 0:
        return False
    return (
        _upper_wick(c) >= _HAMMER_LOWER_WICK_MIN * body
        and _lower_wick(c) <= _HAMMER_UPPER_WICK_MAX * body
    )


def is_bullish_engulfing(prev: Candle, cur: Candle) -> bool:
    """The current (bullish) candle fully engulfs the body of the previous
    (bearish) one — classic reversal."""
    prev_bearish = prev.close < prev.open
    cur_bullish = cur.close > cur.open
    if not (prev_bearish and cur_bullish):
        return False
    return cur.open <= prev.close and cur.close >= prev.open


def is_bearish_engulfing(prev: Candle, cur: Candle) -> bool:
    """Mirror: the current (bearish) candle engulfs the body of the previous
    (bullish) one."""
    prev_bullish = prev.close > prev.open
    cur_bearish = cur.close < cur.open
    if not (prev_bullish and cur_bearish):
        return False
    return cur.open >= prev.close and cur.close <= prev.open


def detect_patterns(candles: list[Candle]) -> list[CandlePattern]:
    """Walks the series and returns EVERY detected pattern, aligned to its real
    index. A candle may trigger no pattern at all (silence, not a fabricated
    absence) or several (e.g. doji AND marubozu never simultaneous by
    construction, but engulfing + doji on the same pair is possible)."""
    found: list[CandlePattern] = []
    for i, c in enumerate(candles):
        if is_doji(c):
            found.append(CandlePattern(i, "doji", "neutral", "corps <=10% du range"))
        marubozu = is_marubozu(c)
        if marubozu:
            direction = "bullish" if c.close > c.open else "bearish" if c.close < c.open else "neutral"
            found.append(CandlePattern(i, "marubozu", direction, "corps >=90% du range"))
        if is_hammer(c):
            found.append(CandlePattern(i, "hammer", "bullish", "mèche basse longue, corps en haut"))
        if is_shooting_star(c):
            found.append(CandlePattern(i, "shooting_star", "bearish", "mèche haute longue, corps en bas"))
        if i > 0:
            prev = candles[i - 1]
            if is_bullish_engulfing(prev, c):
                found.append(CandlePattern(i, "bullish_engulfing", "bullish", "corps englobe la bougie baissière précédente"))
            if is_bearish_engulfing(prev, c):
                found.append(CandlePattern(i, "bearish_engulfing", "bearish", "corps englobe la bougie haussière précédente"))
    return found
