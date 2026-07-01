from __future__ import annotations

import numpy as np
import pandas as pd

from app.models.schemas import Candle, IndicatorSnapshot


def candles_to_df(candles: list[Candle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.DataFrame(
        {
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    line = fast_ema - slow_ema
    signal_line = ema(line, signal)
    histogram = line - signal_line
    return line, signal_line, histogram


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def compute_indicators(candles: list[Candle]) -> IndicatorSnapshot:
    df = candles_to_df(candles)
    if len(df) < 30:
        return IndicatorSnapshot()

    close = df["close"]
    line, signal_line, histogram = macd(close)

    return IndicatorSnapshot(
        rsi=float(rsi(close).iloc[-1]) if len(close) >= 15 else None,
        macd=float(line.iloc[-1]) if not np.isnan(line.iloc[-1]) else None,
        macd_signal=float(signal_line.iloc[-1]) if not np.isnan(signal_line.iloc[-1]) else None,
        macd_histogram=float(histogram.iloc[-1]) if not np.isnan(histogram.iloc[-1]) else None,
        ema_9=float(ema(close, 9).iloc[-1]) if len(close) >= 9 else None,
        ema_21=float(ema(close, 21).iloc[-1]) if len(close) >= 21 else None,
        ema_50=float(ema(close, 50).iloc[-1]) if len(close) >= 50 else None,
        sma_200=float(sma(close, 200).iloc[-1]) if len(close) >= 200 else None,
        atr=float(atr(df).iloc[-1]) if len(df) >= 15 else None,
        volume_sma=float(sma(df["volume"], 20).iloc[-1]) if len(df) >= 20 else None,
    )