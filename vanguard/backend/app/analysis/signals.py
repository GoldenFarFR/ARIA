from __future__ import annotations

from app.analysis.fibonacci import compute_fibonacci
from app.models.schemas import (
    BuySignal,
    Candle,
    DivergenceSignal,
    IndicatorSnapshot,
    SignalType,
)


def find_support_resistance(candles: list[Candle], zones: int = 3) -> tuple[list[float], list[float]]:
    if len(candles) < 20:
        return [], []

    closes = [c.close for c in candles[-60:]]
    current = closes[-1]
    supports = sorted({min(closes[i : i + 5]) for i in range(0, len(closes) - 5, 5)})[:zones]
    resistances = sorted({max(closes[i : i + 5]) for i in range(0, len(closes) - 5, 5)}, reverse=True)[:zones]
    supports = [s for s in supports if s < current]
    resistances = [r for r in resistances if r > current]
    return supports, resistances


def score_buy_signal(
    indicators: IndicatorSnapshot,
    divergences: list[DivergenceSignal],
    candles: list[Candle],
) -> BuySignal:
    score = 50.0
    reasons: list[str] = []

    if indicators.rsi is not None:
        if indicators.rsi < 30:
            score += 15
            reasons.append(f"RSI oversold ({indicators.rsi:.1f})")
        elif indicators.rsi > 70:
            score -= 15
            reasons.append(f"RSI overbought ({indicators.rsi:.1f})")
        elif 40 <= indicators.rsi <= 55:
            score += 5
            reasons.append(f"RSI in bounce zone ({indicators.rsi:.1f})")

    if (
        indicators.macd is not None
        and indicators.macd_signal is not None
        and indicators.macd_histogram is not None
    ):
        if indicators.macd > indicators.macd_signal and indicators.macd_histogram > 0:
            score += 12
            reasons.append("MACD bullish (positive cross)")
        elif indicators.macd < indicators.macd_signal and indicators.macd_histogram < 0:
            score -= 12
            reasons.append("MACD bearish")

    if indicators.ema_9 and indicators.ema_21 and indicators.ema_50:
        if indicators.ema_9 > indicators.ema_21 > indicators.ema_50:
            score += 10
            reasons.append("Bullish EMA alignment (9 > 21 > 50)")
        elif indicators.ema_9 < indicators.ema_21 < indicators.ema_50:
            score -= 10
            reasons.append("Bearish EMA alignment")

    bullish_divs = [d for d in divergences if d.type == "bullish"]
    bearish_divs = [d for d in divergences if d.type == "bearish"]
    if bullish_divs:
        bonus = sum(d.strength for d in bullish_divs) * 10
        score += bonus
        reasons.append(f"{len(bullish_divs)} bullish divergence(s) detected")
    if bearish_divs:
        penalty = sum(d.strength for d in bearish_divs) * 10
        score -= penalty
        reasons.append(f"{len(bearish_divs)} bearish divergence(s) detected")

    fib = compute_fibonacci(candles)
    if fib and candles:
        price = candles[-1].close
        golden = next((lvl for lvl in fib.levels if abs(lvl.level - 0.618) < 0.001), None)
        if golden and abs(price - golden.price) / max(price, 1e-12) < 0.02:
            score += 8
            reasons.append("Price near Fibonacci 61.8% level")

    score = max(0.0, min(100.0, score))

    if score >= 70:
        signal_type = SignalType.BUY
    elif score >= 55:
        signal_type = SignalType.WATCH
    elif score <= 35:
        signal_type = SignalType.SELL
    else:
        signal_type = SignalType.NEUTRAL

    supports, resistances = find_support_resistance(candles)
    entry_zone = None
    stop_loss = None
    take_profit: list[float] = []

    if candles and indicators.atr:
        price = candles[-1].close
        entry_low = price - indicators.atr * 0.5
        entry_high = price + indicators.atr * 0.2
        entry_zone = (entry_low, entry_high)
        stop_loss = price - indicators.atr * 1.5
        take_profit = [price + indicators.atr * multiplier for multiplier in (1.5, 2.5, 4.0)]
        if resistances:
            take_profit[0] = min(take_profit[0], resistances[0])

    if not reasons:
        reasons.append("Neutral signal — no strong confluence")

    return BuySignal(
        score=round(score, 1),
        signal_type=signal_type,
        reasons=reasons,
        entry_zone=entry_zone,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )