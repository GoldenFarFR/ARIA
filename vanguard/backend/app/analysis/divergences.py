from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.indicators import candles_to_df, macd, rsi
from app.models.schemas import Candle, DivergenceSignal


def _find_pivots(series: pd.Series, window: int = 3) -> list[tuple[int, float]]:
    pivots: list[tuple[int, float]] = []
    values = series.dropna().tolist()
    indices = series.dropna().index.tolist()
    if len(values) < window * 2 + 1:
        return pivots

    for i in range(window, len(values) - window):
        local = values[i - window : i + window + 1]
        center = values[i]
        if center == min(local):
            pivots.append((int(indices[i]), center))
        elif center == max(local):
            pivots.append((int(indices[i]), center))
    return pivots


def _detect_divergence(
    price: pd.Series,
    indicator: pd.Series,
    indicator_name: str,
    bullish: bool,
) -> DivergenceSignal | None:
    price_pivots = _find_pivots(price)
    indicator_pivots = _find_pivots(indicator)

    if len(price_pivots) < 2 or len(indicator_pivots) < 2:
        return None

    p1_idx, p1_val = price_pivots[-2]
    p2_idx, p2_val = price_pivots[-1]
    i1_idx, i1_val = indicator_pivots[-2]
    i2_idx, i2_val = indicator_pivots[-1]

    if bullish:
        if p2_val < p1_val and i2_val > i1_val:
            strength = min(1.0, abs(i2_val - i1_val) / max(abs(i1_val), 1e-9))
            return DivergenceSignal(
                type="bullish",
                indicator=indicator_name,
                strength=round(strength, 2),
                description=(
                    f"Bullish {indicator_name} divergence: lower price, "
                    f"higher {indicator_name}"
                ),
            )
    else:
        if p2_val > p1_val and i2_val < i1_val:
            strength = min(1.0, abs(i1_val - i2_val) / max(abs(i1_val), 1e-9))
            return DivergenceSignal(
                type="bearish",
                indicator=indicator_name,
                strength=round(strength, 2),
                description=(
                    f"Bearish {indicator_name} divergence: higher price, "
                    f"lower {indicator_name}"
                ),
            )
    return None


def detect_divergences(candles: list[Candle]) -> list[DivergenceSignal]:
    df = candles_to_df(candles)
    if len(df) < 40:
        return []

    close = df["close"]
    rsi_series = rsi(close)
    macd_line, _, _ = macd(close)

    signals: list[DivergenceSignal] = []
    for bullish in (True, False):
        rsi_div = _detect_divergence(close, rsi_series, "RSI", bullish)
        if rsi_div:
            signals.append(rsi_div)
        macd_div = _detect_divergence(close, macd_line, "MACD", bullish)
        if macd_div:
            signals.append(macd_div)

    return signals