"""EMA/MACD (facts-only, déterministe) — écart réel entre CLAUDE.md et le code corrigé
le 10/07 (MACD/EMA n'étaient calculés nulle part avant ce module)."""
from __future__ import annotations

import pytest

from aria_core.skills.indicators import ema_series, macd_series


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
