"""Liquidity-rotation signal (07/23, operator request: sense whether capital is
rotating INTO a low-info token right now, since fundamentals can't be judged
on these). Pure, deterministic, DB-free -- no network call in this module."""
from __future__ import annotations

import pytest

from aria_core.skills.liquidity_rotation import compute_liquidity_rotation


def test_no_transactions_anywhere_reports_missing_data_honestly():
    r = compute_liquidity_rotation(
        buys_h1=0, sells_h1=0, buys_24h=0, sells_24h=0,
        volume_h1_usd=0.0, volume_24h_usd=0.0,
    )
    assert r.buy_pressure_h1 is None
    assert r.buy_pressure_24h is None
    assert r.pressure_accelerating is None
    assert r.volume_acceleration_ratio is None
    assert r.score == 0.0


def test_buy_pressure_computed_correctly():
    r = compute_liquidity_rotation(
        buys_h1=8, sells_h1=2, buys_24h=50, sells_24h=50,
        volume_h1_usd=1000.0, volume_24h_usd=24_000.0,
    )
    assert r.buy_pressure_h1 == pytest.approx(0.8)
    assert r.buy_pressure_24h == pytest.approx(0.5)


def test_pressure_accelerating_true_when_h1_stronger_than_24h():
    r = compute_liquidity_rotation(
        buys_h1=9, sells_h1=1, buys_24h=50, sells_24h=50,
        volume_h1_usd=1000.0, volume_24h_usd=24_000.0,
    )
    assert r.pressure_accelerating is True


def test_pressure_accelerating_false_when_h1_weaker_than_24h():
    r = compute_liquidity_rotation(
        buys_h1=1, sells_h1=9, buys_24h=50, sells_24h=50,
        volume_h1_usd=1000.0, volume_24h_usd=24_000.0,
    )
    assert r.pressure_accelerating is False


def test_pressure_score_never_negative_on_a_worse_h1():
    """A much worse h1 pressure than the 24h average contributes 0 points,
    never a negative score."""
    r = compute_liquidity_rotation(
        buys_h1=0, sells_h1=10, buys_24h=50, sells_24h=50,
        volume_h1_usd=0.0, volume_24h_usd=24_000.0,
    )
    assert r.score >= 0.0


def test_pressure_score_capped_at_5_points_even_with_extreme_delta():
    r = compute_liquidity_rotation(
        buys_h1=100, sells_h1=0, buys_24h=1, sells_24h=99,  # ~100pp delta, way past the 40pp cap
        volume_h1_usd=0.0, volume_24h_usd=0.0,  # zero volume signal so only pressure contributes
    )
    assert r.score == pytest.approx(5.0)


def test_volume_acceleration_ratio_computed_correctly():
    # h1 volume run-rated to 24h (x24) equals exactly the real 24h volume -> ratio 1.0
    r = compute_liquidity_rotation(
        buys_h1=0, sells_h1=0, buys_24h=0, sells_24h=0,
        volume_h1_usd=1000.0, volume_24h_usd=24_000.0,
    )
    assert r.volume_acceleration_ratio == pytest.approx(1.0)


def test_volume_acceleration_ratio_never_clamped_in_the_raw_number():
    """The AUTONOMOPOLY-shaped case: h1 volume ~= 24h volume (heavy recent
    concentration) -> a large ratio, reported honestly even past the 4x
    scoring cap."""
    r = compute_liquidity_rotation(
        buys_h1=194, sells_h1=142, buys_24h=216, sells_24h=143,
        volume_h1_usd=61_312.84, volume_24h_usd=61_322.9,
    )
    assert r.volume_acceleration_ratio == pytest.approx(24.0, rel=0.01)  # NOT capped at 4


def test_volume_score_capped_at_5_points_even_with_extreme_ratio():
    r = compute_liquidity_rotation(
        buys_h1=0, sells_h1=0, buys_24h=0, sells_24h=0,  # zero pressure signal
        volume_h1_usd=100_000.0, volume_24h_usd=1_000.0,  # extreme run-rate
    )
    assert r.score == pytest.approx(5.0)


def test_zero_24h_volume_reports_missing_acceleration_honestly():
    r = compute_liquidity_rotation(
        buys_h1=5, sells_h1=5, buys_24h=10, sells_24h=10,
        volume_h1_usd=500.0, volume_24h_usd=0.0,
    )
    assert r.volume_acceleration_ratio is None


def test_full_score_blends_both_signals_up_to_10():
    r = compute_liquidity_rotation(
        buys_h1=10, sells_h1=0, buys_24h=1, sells_24h=99,  # max pressure acceleration
        volume_h1_usd=10_000.0, volume_24h_usd=1_000.0,     # max volume acceleration
    )
    assert r.score == pytest.approx(10.0)


def test_reasons_are_always_populated_and_human_readable():
    r = compute_liquidity_rotation(
        buys_h1=8, sells_h1=2, buys_24h=50, sells_24h=50,
        volume_h1_usd=1000.0, volume_24h_usd=24_000.0,
    )
    assert len(r.reasons) == 2
    assert all(isinstance(x, str) and x for x in r.reasons)


def test_missing_txn_data_but_present_volume_data_still_scores_partially():
    r = compute_liquidity_rotation(
        buys_h1=0, sells_h1=0, buys_24h=0, sells_24h=0,
        volume_h1_usd=10_000.0, volume_24h_usd=1_000.0,
    )
    assert r.buy_pressure_h1 is None
    assert r.volume_acceleration_ratio == pytest.approx(240.0)
    assert r.score == pytest.approx(5.0)  # only the volume half contributes
