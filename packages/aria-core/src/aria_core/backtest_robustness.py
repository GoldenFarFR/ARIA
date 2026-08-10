"""Backtest overfitting checks -- Monte-Carlo permutation test, multiple-
comparison (Bonferroni) correction, binomial consistency check. Pure stdlib,
no numpy/scipy dependency. Built to satisfy backlog #266 ("apply on next
batch of v8 trades before any new empirical recalibration") -- reusable on
any future backtest/live comparison, not a one-off script.

Founding case (10/08): the wick-gate threshold (``wick_filter_shadow.py``)
was picked from a single 58-trade retrospective sample (05/08), where ~6
unrelated filter families were tested against the SAME sample (SL/TP grids,
RVOL gating, volatility squeeze, regime/weekday/align, wick ratio) and the
one reaching p=0.026 (wick ratio) was kept -- a textbook multiple-comparisons
setup: testing several independent hypotheses on one sample inflates the
chance of a false positive well above the nominal 5%. Live ``scalping_v8``
(43 real closed trades, hard-gated on exactly this threshold) then returned
0 winners, not the claimed 60%. ``binomial_consistency_check`` and
``bonferroni_correction`` below formalize why that gap should have been
expected, not a surprise -- see the applied numbers in
``docs/HANDOFF_PIPELINE_MOMENTUM.md`` (2026.08.10 entry).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class BinomialConsistencyResult:
    observed_successes: int
    n_trials: int
    claimed_p: float
    observed_rate: float
    p_value: float
    rejects_claim: bool


def binomial_consistency_check(
    observed_successes: int, n_trials: int, claimed_p: float, *, alpha: float = 0.05,
) -> BinomialConsistencyResult:
    """Exact binomial test: is ``observed_successes`` out of ``n_trials``
    plausible if the TRUE win probability were ``claimed_p`` (e.g. a
    backtest's claimed win rate)? Returns the one-sided lower-tail p-value
    P(X <= observed | true p = claimed_p) -- the relevant direction when live
    performance underperforms a backtest claim. ``rejects_claim=True`` means
    the observed result would be implausibly rare if the claim were true."""
    if n_trials <= 0:
        raise ValueError("n_trials must be positive")
    if not 0.0 <= claimed_p <= 1.0:
        raise ValueError("claimed_p must be in [0, 1]")
    if not 0 <= observed_successes <= n_trials:
        raise ValueError("observed_successes must be within [0, n_trials]")
    p_value = sum(
        math.comb(n_trials, k) * (claimed_p ** k) * ((1 - claimed_p) ** (n_trials - k))
        for k in range(0, observed_successes + 1)
    )
    return BinomialConsistencyResult(
        observed_successes=observed_successes,
        n_trials=n_trials,
        claimed_p=claimed_p,
        observed_rate=observed_successes / n_trials,
        p_value=p_value,
        rejects_claim=p_value < alpha,
    )


@dataclass(frozen=True)
class BonferroniResult:
    raw_p_values: tuple[float, ...]
    n_comparisons: int
    alpha: float
    corrected_alpha: float
    survivors: tuple[int, ...]


def bonferroni_correction(
    p_values: list[float], *, n_comparisons: int | None = None, alpha: float = 0.05,
) -> BonferroniResult:
    """Family-wise error correction for several hypotheses tested on the SAME
    sample (e.g. several candidate entry filters tried on one backtest
    batch) -- each additional hypothesis tested raises the chance of a false
    positive by chance alone, so the significance bar must tighten with the
    number of comparisons actually made. ``n_comparisons`` lets the caller
    state the TRUE family size even when only some of those p-values are
    known/recorded (the real 10/08 case: only the winning filter's p=0.026
    was logged, but ~6 filter families were tried against the same sample)
    -- defaults to ``len(p_values)`` when every comparison's p-value is
    known."""
    if not p_values:
        raise ValueError("p_values must be non-empty")
    n = n_comparisons if n_comparisons is not None else len(p_values)
    if n < len(p_values):
        raise ValueError("n_comparisons cannot be smaller than the number of known p_values")
    corrected_alpha = alpha / n
    survivors = tuple(i for i, p in enumerate(p_values) if p < corrected_alpha)
    return BonferroniResult(
        raw_p_values=tuple(p_values), n_comparisons=n, alpha=alpha,
        corrected_alpha=corrected_alpha, survivors=survivors,
    )


@dataclass(frozen=True)
class PermutationTestResult:
    observed_diff: float
    n_permutations: int
    p_value: float
    group_a_n: int
    group_b_n: int


def permutation_test_winrate_diff(
    group_a_outcomes: list[bool], group_b_outcomes: list[bool], *,
    n_permutations: int = 10000, seed: int | None = None,
) -> PermutationTestResult:
    """Monte-Carlo permutation test for a win-rate difference between two
    groups (e.g. "wick_ratio >= threshold" vs "< threshold" trades). Pools
    both groups' outcomes, repeatedly reshuffles which outcome belongs to
    which group, and counts how often a random split produces a win-rate gap
    at least as large as the one actually observed -- the resulting fraction
    is an empirical p-value that makes no distributional assumption (unlike
    Fisher's exact test, whose null model assumes independence the trading
    data may not satisfy -- e.g. correlated market regimes across trades).
    ``seed`` is exposed for reproducible tests; omit it for real analysis."""
    if not group_a_outcomes or not group_b_outcomes:
        raise ValueError("both groups must be non-empty")
    rng = random.Random(seed)
    n_a = len(group_a_outcomes)
    n_b = len(group_b_outcomes)
    pooled = list(group_a_outcomes) + list(group_b_outcomes)
    observed_diff = (sum(group_a_outcomes) / n_a) - (sum(group_b_outcomes) / n_b)
    at_least_as_extreme = 0
    for _ in range(n_permutations):
        rng.shuffle(pooled)
        perm_a, perm_b = pooled[:n_a], pooled[n_a:]
        perm_diff = (sum(perm_a) / n_a) - (sum(perm_b) / n_b)
        if abs(perm_diff) >= abs(observed_diff):
            at_least_as_extreme += 1
    p_value = at_least_as_extreme / n_permutations
    return PermutationTestResult(
        observed_diff=observed_diff, n_permutations=n_permutations, p_value=p_value,
        group_a_n=n_a, group_b_n=n_b,
    )
