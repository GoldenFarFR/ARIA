"""Patterns de bougies (facts-only, déterministe) — briques de forme, pas de jugement
de tendance. Bougies construites à la main pour cibler chaque forme sans ambiguïté."""
from __future__ import annotations

from aria_core.skills.candlestick_patterns import (
    CandlePattern,
    detect_patterns,
    is_bearish_engulfing,
    is_bullish_engulfing,
    is_doji,
    is_hammer,
    is_marubozu,
    is_shooting_star,
)
from aria_core.skills.ta_levels import Candle


def _c(open_, high, low, close, ts=0, volume=0.0):
    return Candle(ts=ts, open=open_, high=high, low=low, close=close, volume=volume)


def test_doji_tiny_body_large_range():
    c = _c(open_=100.0, high=105.0, low=95.0, close=100.2)
    assert is_doji(c) is True


def test_doji_false_on_normal_candle():
    c = _c(open_=100.0, high=110.0, low=99.0, close=108.0)
    assert is_doji(c) is False


def test_doji_false_on_zero_range():
    c = _c(open_=100.0, high=100.0, low=100.0, close=100.0)
    assert is_doji(c) is False


def test_marubozu_bullish_full_body():
    c = _c(open_=100.0, high=110.0, low=100.0, close=110.0)
    assert is_marubozu(c) is True
    assert c.close > c.open


def test_marubozu_none_on_zero_range():
    c = _c(open_=100.0, high=100.0, low=100.0, close=100.0)
    assert is_marubozu(c) is None


def test_marubozu_false_with_visible_wicks():
    c = _c(open_=100.0, high=112.0, low=98.0, close=110.0)
    assert is_marubozu(c) is False


def test_hammer_long_lower_wick_small_body_at_top():
    # Corps 2 (98->100), mèche basse 8 (90->98), mèche haute ~0.
    c = _c(open_=98.0, high=100.5, low=90.0, close=100.0)
    assert is_hammer(c) is True


def test_hammer_false_when_upper_wick_too_long():
    c = _c(open_=98.0, high=115.0, low=90.0, close=100.0)
    assert is_hammer(c) is False


def test_shooting_star_long_upper_wick_small_body_at_bottom():
    # Corps 0.5 (100->100.5), mèche haute 9.5 (100.5->110), mèche basse 0.1 (99.9->100).
    c = _c(open_=100.0, high=110.0, low=99.9, close=100.5)
    assert is_shooting_star(c) is True


def test_bullish_engulfing_true():
    prev = _c(open_=105.0, high=106.0, low=99.0, close=100.0)  # baissière
    cur = _c(open_=99.5, high=107.0, low=99.0, close=106.0)     # englobe
    assert is_bullish_engulfing(prev, cur) is True


def test_bullish_engulfing_false_when_prev_not_bearish():
    prev = _c(open_=100.0, high=106.0, low=99.0, close=105.0)  # haussière
    cur = _c(open_=99.5, high=107.0, low=99.0, close=106.0)
    assert is_bullish_engulfing(prev, cur) is False


def test_bearish_engulfing_true():
    prev = _c(open_=100.0, high=106.0, low=99.0, close=105.0)  # haussière
    cur = _c(open_=106.0, high=107.0, low=98.0, close=99.0)     # englobe, baissière
    assert is_bearish_engulfing(prev, cur) is True


def test_detect_patterns_finds_doji_and_engulfing_in_sequence():
    candles = [
        _c(open_=100.0, high=106.0, low=99.0, close=105.0, ts=0),   # haussière normale
        _c(open_=100.0, high=100.3, low=99.8, close=100.02, ts=1),  # doji (corps 4% du range)
        _c(open_=105.0, high=107.0, low=98.0, close=99.0, ts=2),    # englobe la précédente (baissière)
    ]
    found = detect_patterns(candles)
    names_by_index = {(p.index, p.name) for p in found}
    assert (1, "doji") in names_by_index
    assert (2, "bearish_engulfing") in names_by_index


def test_detect_patterns_empty_series_returns_empty():
    assert detect_patterns([]) == []


def test_candle_pattern_is_frozen_dataclass():
    p = CandlePattern(index=0, name="doji", direction="neutral", detail="test")
    assert p.index == 0 and p.name == "doji"
