from __future__ import annotations

from app.models.schemas import Candle, FibonacciAnalysis, FibonacciLevel

FIB_RATIOS = [
    (0.0, "0%"),
    (0.236, "23.6%"),
    (0.382, "38.2%"),
    (0.5, "50%"),
    (0.618, "61.8%"),
    (0.786, "78.6%"),
    (1.0, "100%"),
    (1.272, "127.2%"),
    (1.618, "161.8%"),
]


def find_swing_points(candles: list[Candle], lookback: int = 50) -> tuple[float, float, str]:
    subset = candles[-lookback:] if len(candles) > lookback else candles
    if len(subset) < 5:
        last = candles[-1].close if candles else 0.0
        return last, last, "neutral"

    highs = [c.high for c in subset]
    lows = [c.low for c in subset]
    swing_high = max(highs)
    swing_low = min(lows)

    first_half = subset[: len(subset) // 2]
    second_half = subset[len(subset) // 2 :]
    first_mid = sum(c.close for c in first_half) / len(first_half)
    second_mid = sum(c.close for c in second_half) / len(second_half)
    trend = "bullish" if second_mid > first_mid else "bearish"
    return swing_high, swing_low, trend


def compute_fibonacci(candles: list[Candle]) -> FibonacciAnalysis | None:
    if len(candles) < 10:
        return None

    swing_high, swing_low, trend = find_swing_points(candles)
    if swing_high == swing_low:
        return None

    diff = swing_high - swing_low
    levels: list[FibonacciLevel] = []

    if trend == "bullish":
        for ratio, label in FIB_RATIOS:
            price = swing_high - diff * ratio
            levels.append(FibonacciLevel(level=ratio, price=price, label=label))
    else:
        for ratio, label in FIB_RATIOS:
            price = swing_low + diff * ratio
            levels.append(FibonacciLevel(level=ratio, price=price, label=label))

    return FibonacciAnalysis(
        swing_high=swing_high,
        swing_low=swing_low,
        trend=trend,
        levels=levels,
    )