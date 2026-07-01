from __future__ import annotations

from app.models.schemas import Candle, Timeframe

TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}


def resample_candles(candles: list[Candle], target: Timeframe) -> list[Candle]:
    if not candles:
        return []

    bucket_seconds = TIMEFRAME_SECONDS[target]
    buckets: dict[int, list[Candle]] = {}

    for candle in candles:
        bucket_ts = (candle.timestamp // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_ts, []).append(candle)

    result: list[Candle] = []
    for ts in sorted(buckets.keys()):
        group = buckets[ts]
        result.append(
            Candle(
                timestamp=ts,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return result