"""Signaux d'entrée : Fibonacci golden pocket + divergence RSI (déterministe, offline)."""
from __future__ import annotations

from aria_core.skills.entry_signals import (
    bullish_rsi_divergence,
    detect_entry,
    fibonacci_zone,
    rsi_series,
)
from aria_core.skills.ta_levels import Candle


def _candles(closes: list[float]) -> list[Candle]:
    return [Candle(ts=i, open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]


# ── RSI ──────────────────────────────────────────────────────────────────────

def test_rsi_rising_is_high():
    rsis = rsi_series([100 + i for i in range(20)])
    assert rsis[-1] is not None and rsis[-1] > 95  # que des gains -> RSI ~100


def test_rsi_falling_is_low():
    rsis = rsi_series([100 - i for i in range(20)])
    assert rsis[-1] is not None and rsis[-1] < 5


def test_rsi_warmup_is_none():
    rsis = rsi_series([100, 101, 102], period=14)
    assert all(r is None for r in rsis)  # trop court


# ── Fibonacci ────────────────────────────────────────────────────────────────

def test_fibonacci_zone_levels():
    fib = fibonacci_zone(_candles([100, 120, 140]))
    assert fib["high"] == 140 and fib["low"] == 100
    # 0.618 retracement = 140 - 40*0.618 = 115.28 ; 0.786 = 140 - 40*0.786 = 108.56
    assert abs(fib["gp_high"] - 115.28) < 0.1
    assert abs(fib["gp_low"] - 108.56) < 0.1


def test_fibonacci_flat_is_none():
    assert fibonacci_zone(_candles([100, 100, 100])) is None


# ── le setup complet ─────────────────────────────────────────────────────────

def _setup_series() -> list[float]:
    """Divergence haussière classique : capitulation (creux 1, RSI au plancher),
    fort rebond, puis retest légèrement plus BAS (creux 2) mais RSI plus HAUT.

    Les 15 bougies d'amorce garantissent que le RSI est « chauffé » (période 14)
    AVANT le premier creux — sinon il serait ignoré (en prod on a 120+ bougies)."""
    lead_in = [100.0] * 15
    capitulation = [100, 90, 82, 77]   # creux 1 = 77 (chute franche -> RSI ~0)
    bounce = [85, 93, 98, 101, 103]    # fort rebond -> RSI remonte
    retest = [96, 88, 79, 75]          # creux 2 = 75 (plus bas) mais RSI plus haut
    tail = [80]                        # petit rebond, prix courant dans le golden pocket
    return lead_in + capitulation + bounce + retest + tail


def test_bullish_divergence_detected():
    ok, base = bullish_rsi_divergence(_candles(_setup_series()), lookback=25)
    assert ok is True
    assert "RSI remonte" in base


def test_no_divergence_on_plain_downtrend():
    ok, _ = bullish_rsi_divergence(_candles([100 - i for i in range(30)]), lookback=25)
    assert ok is False


def _non_adjacent_divergence_series() -> list[float]:
    """19/07 -- reproduit le vrai cas trouvé en investiguant 8 candidats réels du
    pipeline momentum (0/8 divergence détectée, alors que 4/8 étaient dans le golden
    pocket) : 3 creux, où seule la paire NON-adjacente (le premier et le dernier)
    forme une vraie divergence -- la paire immédiate (2e et 3e creux) n'en forme
    aucune (RSI continue de baisser d'un creux à l'autre juste avant le rebond final).
    """
    lead_in = [100.0] * 15
    trough1 = [95, 92, 90]      # creux 1 = 90, RSI très bas (chauffe juste après lead_in)
    bounce1 = [96, 103, 108]
    trough2 = [104, 99, 95]     # creux 2 = 95, retracement léger, RSI encore assez haut
    bounce2 = [100, 105]
    trough3 = [98, 85, 70]      # creux 3 = 70, nouveau plus bas marqué
    tail = [75]                 # confirme le creux 3 comme pivot (minimum local)
    return lead_in + trough1 + bounce1 + trough2 + bounce2 + trough3 + tail


def test_bullish_divergence_detected_across_non_adjacent_pivots():
    """La paire de creux IMMÉDIATEMENT adjacente (2e, 3e) ne forme PAS de divergence
    ici (vérifié : RSI y baisse) -- seule la comparaison avec un creux plus ancien
    (1er, 3e) la révèle. L'ancien code (limité à pivots[-2]/pivots[-1]) aurait raté
    ce cas -- verrou de non-régression sur le correctif du 19/07."""
    ok, base = bullish_rsi_divergence(_candles(_non_adjacent_divergence_series()), lookback=25)
    assert ok is True
    assert "RSI remonte" in base
    assert "70" in base  # ancré sur le creux le plus récent (3e), pas un pivot intermédiaire


def test_detect_entry_fires_on_setup():
    sig = detect_entry(_candles(_setup_series()), lookback=25)
    assert sig.present is True
    assert sig.in_golden_pocket and sig.rsi_divergence
    assert sig.entry is not None and sig.invalidation < sig.entry < sig.target
    assert sig.rr is not None and sig.rr > 1  # R/R favorable par construction


def test_detect_entry_absent_on_uptrend():
    sig = detect_entry(_candles([100 + i for i in range(30)]), lookback=25)
    assert sig.present is False


def test_detect_entry_short_series_safe():
    sig = detect_entry(_candles([100, 101, 102]), lookback=25)
    assert sig.present is False
