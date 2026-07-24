"""In-house Gini coefficient for holder distribution (07/24) -- build-in-house
alternative to a paid provider (BaseScan-style "concentration analytics",
found not exposed by any free API during a Bazaar scan) that computes this
exact metric from on-chain data ARIA already fetches for free.

Values hand-verified against the standard discrete Gini formula before
being encoded as test expectations here."""
from __future__ import annotations

from aria_core.skills.acp_onchain_scan import _gini_coefficient


def test_perfect_equality_is_zero():
    assert _gini_coefficient([10.0, 10.0, 10.0, 10.0]) == 0.0


def test_two_equal_holders_is_zero():
    assert _gini_coefficient([50.0, 50.0]) == 0.0


def test_extreme_inequality_is_high():
    # Hand-verified: n=4, shares [0, 0, 0, 100] -> Gini == 0.75 exactly
    # (the finite-n formula never reaches 1.0 with a fixed small n).
    gini = _gini_coefficient([0.0, 0.0, 0.0, 100.0])
    assert gini is not None
    assert round(gini, 4) == 0.75


def test_more_unequal_distribution_scores_higher_than_more_equal_one():
    equalish = _gini_coefficient([20.0, 25.0, 25.0, 30.0])
    unequal = _gini_coefficient([1.0, 2.0, 7.0, 90.0])
    assert equalish is not None
    assert unequal is not None
    assert unequal > equalish


def test_single_holder_is_undefined():
    assert _gini_coefficient([100.0]) is None


def test_empty_list_is_undefined():
    assert _gini_coefficient([]) is None


def test_result_always_bounded_between_zero_and_one():
    gini = _gini_coefficient([0.001, 0.001, 0.001, 99.997])
    assert gini is not None
    assert 0.0 <= gini <= 1.0
