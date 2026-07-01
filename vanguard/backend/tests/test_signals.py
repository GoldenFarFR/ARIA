from app.analysis.signals import score_buy_signal
from app.models.schemas import Candle, IndicatorSnapshot, SignalType


def _candles(n: int = 50, price: float = 100.0) -> list[Candle]:
    return [
        Candle(
            timestamp=i,
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=1000.0,
        )
        for i in range(n)
    ]


def test_oversold_rsi_increases_score():
    indicators = IndicatorSnapshot(rsi=25.0)
    signal = score_buy_signal(indicators, [], _candles())
    assert signal.score > 50
    assert any("oversold" in r.lower() for r in signal.reasons)


def test_overbought_rsi_decreases_score():
    indicators = IndicatorSnapshot(rsi=75.0)
    signal = score_buy_signal(indicators, [], _candles())
    assert signal.score < 50
    assert signal.signal_type in (SignalType.SELL, SignalType.NEUTRAL, SignalType.WATCH)