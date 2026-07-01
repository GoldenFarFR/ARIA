from __future__ import annotations

from datetime import datetime, timezone

from app.analysis.divergences import detect_divergences
from app.analysis.fibonacci import compute_fibonacci
from app.analysis.indicators import compute_indicators
from app.analysis.signals import find_support_resistance, score_buy_signal
from app.models.schemas import (
    PairAnalysis,
    PairSummary,
    SignalType,
    Timeframe,
    TimeframeAnalysis,
)
from app.services.candle_aggregator import resample_candles
from app.services.geckoterminal import DIRECT_FETCH, geckoterminal_client

ALL_TIMEFRAMES = [
    Timeframe.M1,
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.M30,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
]

DEFAULT_TIMEFRAMES = [Timeframe.M5, Timeframe.H1, Timeframe.H4]


def _compute_trend_index(analyses: list[TimeframeAnalysis]) -> float:
    if not analyses:
        return 0.0
    bullish = 0
    bearish = 0
    for entry in analyses:
        ema9 = entry.indicators.ema_9
        ema21 = entry.indicators.ema_21
        if ema9 is not None and ema21 is not None:
            if ema9 > ema21:
                bullish += 1
            elif ema9 < ema21:
                bearish += 1
    direction = (bullish - bearish) / len(analyses)
    avg_score = sum(a.buy_signal.score for a in analyses) / len(analyses)
    trend = direction * 50.0 + (avg_score - 50.0) * 0.6
    return round(max(-100.0, min(100.0, trend)), 1)


class AnalysisEngine:
    async def analyze_pair(
        self,
        pair: PairSummary,
        timeframes: list[Timeframe] | None = None,
    ) -> PairAnalysis:
        selected = timeframes or DEFAULT_TIMEFRAMES
        analyses: list[TimeframeAnalysis] = []

        needed_base: set[Timeframe] = set()
        for tf in selected:
            if tf in DIRECT_FETCH:
                needed_base.add(tf)
            elif tf.value == "30m":
                needed_base.add(Timeframe.M15)
            elif tf.value == "4h":
                needed_base.add(Timeframe.H1)

        base_cache: dict[Timeframe, list] = {}
        for base_tf in needed_base:
            base_cache[base_tf] = await geckoterminal_client.get_ohlcv(
                pair.chain_id, pair.pair_address, base_tf
            )

        for tf in selected:
            if tf in base_cache:
                candles = base_cache[tf]
            elif tf.value == "30m":
                candles = resample_candles(base_cache.get(Timeframe.M15, []), tf)
            elif tf.value == "4h":
                candles = resample_candles(base_cache.get(Timeframe.H1, []), tf)
            else:
                candles = await geckoterminal_client.get_ohlcv(
                    pair.chain_id, pair.pair_address, tf
                )
            if len(candles) < 10:
                continue

            indicators = compute_indicators(candles)
            divergences = detect_divergences(candles)
            fibonacci = compute_fibonacci(candles)
            buy_signal = score_buy_signal(indicators, divergences, candles)
            supports, resistances = find_support_resistance(candles)

            analyses.append(
                TimeframeAnalysis(
                    timeframe=tf,
                    indicators=indicators,
                    divergences=divergences,
                    fibonacci=fibonacci,
                    buy_signal=buy_signal,
                    support_levels=supports,
                    resistance_levels=resistances,
                )
            )

        if not analyses:
            return PairAnalysis(
                pair=pair,
                analyzed_at=datetime.now(timezone.utc),
                timeframes=[],
                global_score=0,
                trend_index=0,
                consensus=SignalType.NEUTRAL,
                summary="Insufficient data for analysis.",
            )

        global_score = sum(a.buy_signal.score for a in analyses) / len(analyses)
        buy_count = sum(1 for a in analyses if a.buy_signal.signal_type == SignalType.BUY)
        sell_count = sum(1 for a in analyses if a.buy_signal.signal_type == SignalType.SELL)
        watch_count = sum(1 for a in analyses if a.buy_signal.signal_type == SignalType.WATCH)

        if buy_count >= max(sell_count, watch_count) and buy_count >= 2:
            consensus = SignalType.BUY
            summary = f"Bullish confluence on {buy_count}/{len(analyses)} timeframes."
        elif sell_count > buy_count and sell_count >= 2:
            consensus = SignalType.SELL
            summary = f"Bearish pressure on {sell_count}/{len(analyses)} timeframes."
        elif watch_count >= 2:
            consensus = SignalType.WATCH
            summary = f"Watch zone — {watch_count} timeframes on alert."
        else:
            consensus = SignalType.NEUTRAL
            summary = "No clear multi-timeframe consensus."

        return PairAnalysis(
            pair=pair,
            analyzed_at=datetime.now(timezone.utc),
            timeframes=analyses,
            global_score=round(global_score, 1),
            trend_index=_compute_trend_index(analyses),
            consensus=consensus,
            summary=summary,
        )


analysis_engine = AnalysisEngine()