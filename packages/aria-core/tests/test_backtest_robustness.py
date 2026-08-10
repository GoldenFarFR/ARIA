"""Statistical robustness checks for backtest overfitting (backlog #266) --
pure functions, no DB/network."""
from __future__ import annotations

import pytest

from aria_core.backtest_robustness import (
    binomial_consistency_check,
    bonferroni_correction,
    chronological_train_validation_split,
    evaluate_filter_candidate_out_of_sample,
    permutation_test_winrate_diff,
)


def test_binomial_consistency_rejects_zero_wins_against_high_claim():
    # The real 10/08 case: 0 wins out of 43 live trades vs a 60% backtest claim.
    result = binomial_consistency_check(observed_successes=0, n_trials=43, claimed_p=0.60)
    assert result.p_value < 1e-10
    assert result.rejects_claim is True
    assert result.observed_rate == 0.0


def test_binomial_consistency_accepts_plausible_result():
    # 6 wins out of 10 is entirely plausible under a claimed 60% true rate.
    result = binomial_consistency_check(observed_successes=6, n_trials=10, claimed_p=0.60)
    assert result.rejects_claim is False


def test_binomial_consistency_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        binomial_consistency_check(observed_successes=0, n_trials=0, claimed_p=0.5)
    with pytest.raises(ValueError):
        binomial_consistency_check(observed_successes=0, n_trials=10, claimed_p=1.5)
    with pytest.raises(ValueError):
        binomial_consistency_check(observed_successes=11, n_trials=10, claimed_p=0.5)


def test_bonferroni_correction_family_size_from_known_p_values():
    result = bonferroni_correction([0.01, 0.04, 0.2])
    assert result.n_comparisons == 3
    assert result.corrected_alpha == pytest.approx(0.05 / 3)
    assert result.survivors == (0,)  # only 0.01 < 0.0167


def test_bonferroni_correction_explicit_family_size_larger_than_known_p_values():
    # The real 10/08 case: only 1 p-value (wick ratio, 0.026) was recorded,
    # but ~6 filter families were tried against the same 58-trade sample.
    result = bonferroni_correction([0.026], n_comparisons=6)
    assert result.corrected_alpha == pytest.approx(0.05 / 6)
    assert result.survivors == ()  # 0.026 does not survive correction


def test_bonferroni_correction_rejects_family_size_smaller_than_known_p_values():
    with pytest.raises(ValueError):
        bonferroni_correction([0.01, 0.02], n_comparisons=1)


def test_bonferroni_correction_rejects_empty_input():
    with pytest.raises(ValueError):
        bonferroni_correction([])


def test_permutation_test_detects_real_difference():
    group_a = [True] * 8 + [False] * 2  # 80% win rate
    group_b = [True] * 2 + [False] * 8  # 20% win rate
    result = permutation_test_winrate_diff(group_a, group_b, n_permutations=5000, seed=42)
    assert result.observed_diff == pytest.approx(0.6)
    assert result.p_value < 0.05


def test_permutation_test_no_difference_yields_high_p_value():
    group_a = [True, False, True, False, True]
    group_b = [False, True, False, True, False]
    result = permutation_test_winrate_diff(group_a, group_b, n_permutations=5000, seed=42)
    assert result.p_value > 0.3


def test_permutation_test_rejects_empty_groups():
    with pytest.raises(ValueError):
        permutation_test_winrate_diff([], [True], n_permutations=100)
    with pytest.raises(ValueError):
        permutation_test_winrate_diff([True], [], n_permutations=100)


def test_permutation_test_reproducible_with_seed():
    group_a = [True, False, True]
    group_b = [False, True, False, True]
    r1 = permutation_test_winrate_diff(group_a, group_b, n_permutations=1000, seed=7)
    r2 = permutation_test_winrate_diff(group_a, group_b, n_permutations=1000, seed=7)
    assert r1.p_value == r2.p_value


def test_chronological_split_never_shuffles_and_keeps_order():
    records = list(range(10))  # already time-ordered: 0 = oldest, 9 = newest
    result = chronological_train_validation_split(records, train_fraction=0.7, min_validation_size=1)
    assert result.train == (0, 1, 2, 3, 4, 5, 6)
    assert result.validation == (7, 8, 9)
    assert result.insufficient_validation is False


def test_chronological_split_flags_insufficient_validation():
    records = list(range(10))
    result = chronological_train_validation_split(records, train_fraction=0.9, min_validation_size=5)
    assert result.validation == (9,)
    assert result.insufficient_validation is True


def test_chronological_split_rejects_invalid_fraction():
    with pytest.raises(ValueError):
        chronological_train_validation_split([1, 2, 3], train_fraction=0.0)
    with pytest.raises(ValueError):
        chronological_train_validation_split([1, 2, 3], train_fraction=1.0)


def test_chronological_split_rejects_invalid_min_validation_size():
    with pytest.raises(ValueError):
        chronological_train_validation_split([1, 2, 3], min_validation_size=0)


def test_out_of_sample_verdict_insufficient_data_below_minimum():
    verdict = evaluate_filter_candidate_out_of_sample(
        [True, True, False], claimed_p=0.6, min_validation_size=10,
    )
    assert verdict.status == "insufficient_validation_data"
    assert verdict.binomial is None
    assert verdict.permutation is None


def test_out_of_sample_verdict_rejects_the_real_wick_gate_case():
    # The real 10/08 wick-gate case, reframed as an out-of-sample check:
    # 0/43 validation trades vs a claimed 60% win rate.
    verdict = evaluate_filter_candidate_out_of_sample(
        [False] * 43, claimed_p=0.60, min_validation_size=10,
    )
    assert verdict.status == "rejected"
    assert verdict.binomial.rejects_claim is True


def test_out_of_sample_verdict_survives_plausible_claim():
    validation = [True] * 7 + [False] * 3  # 70% observed, claim was 60%
    verdict = evaluate_filter_candidate_out_of_sample(
        validation, claimed_p=0.60, min_validation_size=5,
    )
    assert verdict.status == "survives"
    assert verdict.binomial.rejects_claim is False


def test_out_of_sample_verdict_rejects_when_no_significant_diff_vs_control():
    # Plausible raw win rate, but indistinguishable from an out-of-sample
    # control group -- the observed edge is plausibly just noise.
    validation = [True, False, True, False, True]
    control = [True, False, False, True, True]
    verdict = evaluate_filter_candidate_out_of_sample(
        validation, claimed_p=0.5, control_outcomes=control,
        min_validation_size=5, n_permutations=2000, seed=11,
    )
    assert verdict.status == "rejected"
    assert verdict.permutation is not None


def test_out_of_sample_verdict_end_to_end_with_split():
    # Full workflow: chronological split first, THEN test only the
    # validation half -- exactly the order CLAUDE.md's methodology rule
    # requires (never test the train half or the full unsplit sample).
    trades = [True] * 6 + [False] * 3 + [True, False]  # oldest -> newest
    split = chronological_train_validation_split(trades, train_fraction=0.7, min_validation_size=2)
    assert split.validation == (False, True, False)
    verdict = evaluate_filter_candidate_out_of_sample(
        list(split.validation), claimed_p=0.6, min_validation_size=2,
    )
    assert verdict.validation_n == 3
