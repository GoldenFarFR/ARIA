"""Statistical robustness checks for backtest overfitting (backlog #266) --
pure functions, no DB/network."""
from __future__ import annotations

import pytest

from aria_core.backtest_robustness import (
    binomial_consistency_check,
    bonferroni_correction,
    chronological_train_validation_split,
    evaluate_filter_candidate_out_of_sample,
    falsification_protocol_check,
    inter_period_stability_check,
    permutation_test_winrate_diff,
    t_statistic_check,
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


# --- #293 falsification protocol (arXiv 2605.04004) ------------------------


def test_t_statistic_check_survives_on_a_consistent_positive_edge():
    returns = [2.0, 1.5, 2.5, 1.8] * 8  # 32 trades, mean 1.95%, low noise
    result = t_statistic_check(returns, min_t_stat=2.0)
    assert result.survives is True
    assert result.t_stat > 2.0
    assert result.mean_return_pct == pytest.approx(1.95)


def test_t_statistic_check_rejects_pure_noise_around_zero():
    returns = ([5.0, -5.0] * 15)  # mean exactly 0, pure alternating noise
    result = t_statistic_check(returns, min_t_stat=2.0)
    assert result.survives is False
    assert result.t_stat == pytest.approx(0.0)


def test_t_statistic_check_handles_zero_variance_without_crashing():
    result = t_statistic_check([1.0] * 30, min_t_stat=2.0)
    assert result.t_stat == 0.0  # stdev=0 -- never a division by zero
    assert result.survives is False


def test_inter_period_stability_check_requires_every_block_positive_by_default():
    returns = [1.0] * 4 + [1.0] * 4 + [1.0] * 4 + [-1.0] * 4  # 3 good blocks, 1 bad
    result = inter_period_stability_check(returns, n_blocks=4)
    assert result.positive_blocks == 3
    assert result.survives is False  # strict: ALL blocks required by default


def test_inter_period_stability_check_looser_bar_via_min_positive_blocks():
    returns = [1.0] * 4 + [1.0] * 4 + [1.0] * 4 + [-1.0] * 4
    result = inter_period_stability_check(returns, n_blocks=4, min_positive_blocks=3)
    assert result.survives is True


def test_falsification_protocol_rejects_below_min_trades():
    verdict = falsification_protocol_check([2.0] * 20, min_trades=30)
    assert verdict.survives is False
    assert verdict.criteria["min_trades"] is False
    assert "20 trades" in verdict.reason


def test_falsification_protocol_survives_all_5_criteria_at_once():
    # 32 trades, consistent positive edge, stable across all 4 blocks --
    # the ONE case the paper found none of its 14 tested signal families
    # ever achieved simultaneously.
    returns = [2.0, 1.5, 2.5, 1.8] * 8
    verdict = falsification_protocol_check(returns, min_trades=30)
    assert verdict.survives is True
    assert all(verdict.criteria.values())
    assert verdict.t_stat.survives is True
    assert verdict.stability.survives is True


def test_falsification_protocol_rejects_on_stability_alone_despite_positive_mean_and_t_stat():
    # 24 strong-positive trades + 8 strongly negative ones in the LAST block:
    # min_trades passes, t-stat passes (mean far from zero), net-of-friction
    # passes (mean=3.5% > 0) -- but block 4 is entirely negative, exactly the
    # "1-2 outlier trades inflate a misleading global average" pattern this
    # criterion exists to catch (cf. the 10/08 wick-gate incident).
    returns = [5.0] * 24 + [-1.0] * 8
    verdict = falsification_protocol_check(returns, min_trades=30)
    assert verdict.survives is False
    assert verdict.criteria["min_trades"] is True
    assert verdict.criteria["t_stat"] is True
    assert verdict.criteria["net_of_friction_positive"] is True
    assert verdict.criteria["stability"] is False


def test_falsification_protocol_reproduces_the_real_wick_gate_rejection():
    # The real 10/08 case reframed for this protocol: 43 near-zero/negative
    # live trades against a claimed strong edge -- must reject decisively.
    returns = [-0.5] * 43
    verdict = falsification_protocol_check(returns, min_trades=30)
    assert verdict.survives is False
    assert verdict.criteria["net_of_friction_positive"] is False
