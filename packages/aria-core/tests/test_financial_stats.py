"""financial_stats.py -- shared pure statistics helpers (SSOT for the Sortino
ratio, reused by services/smart_money.py and performance_breakdown.py, #150)."""
from __future__ import annotations

import math

import pytest

from aria_core import financial_stats as fs


def test_sortino_ratio_below_min_trades_unavailable():
    assert fs.sortino_ratio([0.1, 0.2]) is None


def test_sortino_ratio_no_losses_unavailable_not_infinite():
    returns = [0.1, 0.2, 0.3, 0.1, 0.2]
    assert fs.sortino_ratio(returns) is None


def test_sortino_ratio_mixed_returns_computed():
    returns = [0.2, -0.1, 0.3, -0.2, 0.1]
    result = fs.sortino_ratio(returns)
    assert result is not None
    expected = 0.06 / math.sqrt(0.025)
    assert result == pytest.approx(expected, rel=1e-6)


def test_sortino_ratio_custom_min_trades():
    returns = [0.1, -0.1, 0.2]
    assert fs.sortino_ratio(returns, min_trades=5) is None
    assert fs.sortino_ratio(returns, min_trades=3) is not None


def test_sortino_ratio_scale_invariant_percent_vs_fraction():
    """The ratio itself doesn't care whether returns are expressed as
    fractions (0.2) or percent (20.0) -- both scales cancel out identically,
    which is exactly why performance_breakdown.py can feed pnl_pct (percent)
    directly while smart_money.py feeds return_pct (fraction)."""
    fraction_returns = [0.2, -0.1, 0.3, -0.2, 0.1]
    percent_returns = [r * 100 for r in fraction_returns]
    assert fs.sortino_ratio(fraction_returns) == pytest.approx(fs.sortino_ratio(percent_returns), rel=1e-9)
