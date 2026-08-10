"""Backtest overfitting checks -- Monte-Carlo permutation test, multiple-
comparison (Bonferroni) correction, binomial consistency check, chronological
train/validation split. Pure stdlib, no numpy/scipy dependency. Built to
satisfy backlog #266 ("apply on next batch of v8 trades before any new
empirical recalibration") -- reusable on any future backtest/live comparison,
not a one-off script.

The permutation/Bonferroni/binomial trio was built 10/08 and applied inline
that same day (see below); the chronological split + out-of-sample verdict
were the part of #266 still explicitly marked "never built" -- added 10/08
(later pass) to close the gap and operationalize CLAUDE.md's scalping_v8
methodology rule end to end: (1) split BEFORE looking for a pattern, (2) test
the SPLIT-OFF validation set with the statistical tools below, (3) too little
validation data is a reason to keep shadow-logging, never a reason to test
anyway.

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
from typing import Sequence, TypeVar

T = TypeVar("T")


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


@dataclass(frozen=True)
class ChronologicalSplitResult:
    train: tuple
    validation: tuple
    train_fraction_requested: float
    min_validation_size: int
    insufficient_validation: bool


def chronological_train_validation_split(
    records: Sequence[T], *, train_fraction: float = 0.7, min_validation_size: int = 10,
) -> ChronologicalSplitResult:
    """Splits an ALREADY time-ordered sequence into train/validation without
    shuffling -- ``records[0]`` must be the earliest, ``records[-1]`` the most
    recent (caller's responsibility; this function trusts the given order).

    Deliberately never randomizes: CLAUDE.md's scalping_v8 methodology rule
    (1) requires the split to happen chronologically (train = earlier trades,
    validation = later, out-of-time trades) so validation genuinely represents
    "what a filter would have faced going forward", not just a random held-out
    sample drawn from the same period a pattern was mined on -- a random split
    would leak information (a filter mined on data adjacent in time to a
    "held-out" random sample can still overfit to the same regime)."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")
    if min_validation_size <= 0:
        raise ValueError("min_validation_size must be positive")
    ordered = list(records)
    split_idx = round(len(ordered) * train_fraction)
    train = tuple(ordered[:split_idx])
    validation = tuple(ordered[split_idx:])
    return ChronologicalSplitResult(
        train=train,
        validation=validation,
        train_fraction_requested=train_fraction,
        min_validation_size=min_validation_size,
        insufficient_validation=len(validation) < min_validation_size,
    )


@dataclass(frozen=True)
class OutOfSampleVerdict:
    status: str  # "insufficient_validation_data" | "rejected" | "survives"
    reason: str
    validation_n: int
    binomial: BinomialConsistencyResult | None
    permutation: PermutationTestResult | None


def evaluate_filter_candidate_out_of_sample(
    validation_outcomes: list[bool],
    *,
    claimed_p: float,
    control_outcomes: list[bool] | None = None,
    min_validation_size: int = 10,
    alpha: float = 0.05,
    n_permutations: int = 10000,
    seed: int | None = None,
) -> OutOfSampleVerdict:
    """Applies CLAUDE.md's full 3-step scalping_v8 methodology rule to a
    filter candidate's OUT-OF-SAMPLE results. ``validation_outcomes`` MUST be
    the ``validation`` half of a prior ``chronological_train_validation_split``
    -- never the train half (where the pattern was found) and never the full
    unsplit sample, or this collapses back into the exact 10/08 wick-gate
    mistake this module exists to prevent.

    Step (3) of the rule: too little validation data is a reason to keep
    shadow-logging, never a reason to test anyway -- returns
    ``insufficient_validation_data`` rather than a false-confidence verdict.

    ``control_outcomes``, if given (e.g. the "filter says HOLD" trades from
    the same validation window), additionally runs the permutation test --
    a real edge should show a significant gap against its own out-of-sample
    control group, not just a plausible-looking raw win rate."""
    n = len(validation_outcomes)
    if n < min_validation_size:
        return OutOfSampleVerdict(
            status="insufficient_validation_data",
            reason=(
                f"only {n} validation trades (< {min_validation_size} minimum) -- "
                "accumulate more shadow-mode observations before testing, per "
                "CLAUDE.md's scalping_v8 methodology rule (3)"
            ),
            validation_n=n, binomial=None, permutation=None,
        )
    successes = sum(validation_outcomes)
    binomial = binomial_consistency_check(successes, n, claimed_p, alpha=alpha)
    permutation = None
    if control_outcomes:
        permutation = permutation_test_winrate_diff(
            validation_outcomes, control_outcomes, n_permutations=n_permutations, seed=seed,
        )
    if binomial.rejects_claim:
        return OutOfSampleVerdict(
            status="rejected",
            reason=(
                f"binomial check rejects the claim out-of-sample: {successes}/{n} observed "
                f"vs claimed_p={claimed_p} (p={binomial.p_value:.4g} < alpha={alpha})"
            ),
            validation_n=n, binomial=binomial, permutation=permutation,
        )
    if permutation is not None and permutation.p_value >= alpha:
        return OutOfSampleVerdict(
            status="rejected",
            reason=(
                f"permutation test found no significant difference vs control, out-of-sample "
                f"(p={permutation.p_value:.4g} >= alpha={alpha}) -- observed edge is plausibly noise"
            ),
            validation_n=n, binomial=binomial, permutation=permutation,
        )
    return OutOfSampleVerdict(
        status="survives",
        reason=(
            "observed rate consistent with the claim, and significantly different from "
            "control when tested, on genuinely out-of-sample (validation-only) trades"
        ),
        validation_n=n, binomial=binomial, permutation=permutation,
    )
