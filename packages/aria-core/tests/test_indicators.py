"""EMA/MACD (facts-only, déterministe) — écart réel entre CLAUDE.md et le code corrigé
le 10/07 (MACD/EMA n'étaient calculés nulle part avant ce module)."""
from __future__ import annotations

import math

import pytest

from aria_core.skills.indicators import bollinger_bands, ema_series, macd_series


def test_ema_series_too_short_all_none():
    assert ema_series([1.0, 2.0], period=5) == [None, None]


def test_ema_series_matches_hand_computed_values_for_linear_ramp():
    # closes = 1..10, period=3, k=0.5 : SMA(1,2,3)=2.0 a l'index 2, puis chaque pas
    # suivant vaut exactement closes[i-1] pour cette rampe lineaire de pas 1 (verifie
    # a la main : out[3]=4*0.5+2.0*0.5=3.0=closes[2], out[9]=10*0.5+8.0*0.5=9.0=closes[8]).
    closes = [float(i) for i in range(1, 11)]
    result = ema_series(closes, period=3)

    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)
    assert result[-1] == pytest.approx(9.0)


def test_ema_series_constant_closes_stays_constant():
    closes = [42.0] * 20
    result = ema_series(closes, period=5)
    assert result[:4] == [None, None, None, None]
    assert all(v == pytest.approx(42.0) for v in result[4:])


def test_macd_none_before_slow_ema_ready():
    closes = [float(i) for i in range(1, 30)]
    macd_line, signal_line, histogram = macd_series(closes, fast=3, slow=10, signal=4)
    # L'EMA lente (periode 10) n'est definie qu'a partir de l'index 9.
    assert all(v is None for v in macd_line[:9])
    assert macd_line[9] is not None


def test_macd_constant_closes_gives_zero_everywhere_defined():
    closes = [10.0] * 40
    macd_line, signal_line, histogram = macd_series(closes, fast=3, slow=10, signal=4)
    defined = [v for v in macd_line if v is not None]
    assert defined and all(v == pytest.approx(0.0) for v in defined)
    defined_signal = [v for v in signal_line if v is not None]
    assert defined_signal and all(v == pytest.approx(0.0) for v in defined_signal)
    defined_hist = [v for v in histogram if v is not None]
    assert defined_hist and all(v == pytest.approx(0.0) for v in defined_hist)


def test_macd_uptrend_gives_positive_histogram():
    # Tendance haussiere reguliere -> l'EMA rapide reste au-dessus de la lente,
    # donc la ligne MACD (et l'histogramme) doivent rester positifs une fois definis.
    closes = [100.0 + i * 2.0 for i in range(60)]
    macd_line, signal_line, histogram = macd_series(closes)
    defined_macd = [v for v in macd_line if v is not None]
    assert defined_macd and all(v > 0 for v in defined_macd)


def test_macd_never_crashes_on_short_series():
    macd_line, signal_line, histogram = macd_series([1.0, 2.0, 3.0])
    assert macd_line == [None, None, None]
    assert signal_line == [None, None, None]
    assert histogram == [None, None, None]


def test_bollinger_too_short_all_none():
    middle, upper, lower = bollinger_bands([1.0, 2.0], period=5)
    assert middle == [None, None]
    assert upper == [None, None]
    assert lower == [None, None]


def test_bollinger_constant_closes_bands_collapse_on_middle():
    closes = [50.0] * 25
    middle, upper, lower = bollinger_bands(closes, period=20)
    defined_idx = range(19, 25)
    for i in defined_idx:
        assert middle[i] == pytest.approx(50.0)
        # Écart-type nul sur une série constante -> bandes haute/basse == milieu.
        assert upper[i] == pytest.approx(50.0)
        assert lower[i] == pytest.approx(50.0)


def test_bollinger_matches_hand_computed_values():
    # Fenêtre [1,2,3,4,5], period=5, num_std=2 : moyenne=3, écart-type population
    # = sqrt(((-2)^2+(-1)^2+0^2+1^2+2^2)/5) = sqrt(2) ~= 1.4142.
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    middle, upper, lower = bollinger_bands(closes, period=5, num_std=2.0)
    assert middle[:4] == [None, None, None, None]
    assert middle[4] == pytest.approx(3.0)
    assert upper[4] == pytest.approx(3.0 + 2 * math.sqrt(2.0))
    assert lower[4] == pytest.approx(3.0 - 2 * math.sqrt(2.0))


def test_bollinger_upper_always_above_lower_when_defined():
    closes = [100.0 + (i % 7) * 3.5 for i in range(40)]
    middle, upper, lower = bollinger_bands(closes, period=10)
    for u, l in zip(upper, lower):
        if u is not None and l is not None:
            assert u >= l
