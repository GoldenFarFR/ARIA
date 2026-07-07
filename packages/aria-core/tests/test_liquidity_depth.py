"""Profondeur de liquidité : un marché mince par rapport à la market cap est fragile."""
from __future__ import annotations

from aria_core.skills.liquidity_depth import DEFAULT_MIN_RATIO, assess_liquidity_depth


def test_operator_example_100k_mcap_20k_liq_is_thin():
    # 100k mcap / 20k liq = 20% < 30% -> pas ouf (intuition opérateur)
    d = assess_liquidity_depth(20_000, 100_000)
    assert d.ratio == 0.2
    assert d.healthy is False
    assert "mince" in d.note


def test_100k_mcap_40k_liq_is_healthy():
    d = assess_liquidity_depth(40_000, 100_000)
    assert d.ratio == 0.4
    assert d.healthy is True


def test_at_floor_is_healthy():
    d = assess_liquidity_depth(30_000, 100_000)  # pile 30%
    assert d.healthy is True


def test_unknown_market_cap_is_indeterminate():
    for mcap in (None, 0):
        d = assess_liquidity_depth(50_000, mcap)
        assert d.healthy is None
        assert d.ratio is None


def test_custom_min_ratio_per_launchpad():
    # un launchpad plus exigeant (40%) rejette 35%
    d = assess_liquidity_depth(35_000, 100_000, min_ratio=0.40)
    assert d.healthy is False


def test_default_floor_is_30pct():
    assert DEFAULT_MIN_RATIO == 0.30
