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


# 13/08 -- backlog #293 (arXiv 2605.04004, "Structural Limits of OHLCV-Based
# Intraday Signals", May 2026): a 5-criteria SIMULTANEOUS falsification
# protocol tested against 14 intraday signal families over 947 days of 5min
# candles -- NONE survived all 5 at once. Reused here as a mandatory gate
# before promoting any future v8/8.x filter candidate, per the operator's own
# methodology rule already in CLAUDE.md (never mine one batch, split before
# looking, backtest_robustness before hard-gating). 2 of the 5 criteria were
# already covered above (walk-forward via chronological_train_validation_split
# + evaluate_filter_candidate_out_of_sample); this closes the remaining 3:
# t-stat on returns (not just win/loss), inter-period stability, and an
# explicit min-trade floor -- net-of-friction is a CONTRACT on the caller's
# input (see below), not a new statistical check.


@dataclass(frozen=True)
class TStatResult:
    t_stat: float
    n: int
    mean_return_pct: float
    survives: bool


def t_statistic_check(returns_pct: Sequence[float], *, min_t_stat: float = 2.0) -> TStatResult:
    """One-sample t-statistic on a sequence of per-trade returns (percent,
    net-of-friction -- see ``falsification_protocol_check``'s docstring for
    that contract). Pure stdlib (no scipy): t = mean / (stdev / sqrt(n)),
    sample stdev (n-1 denominator). A t-stat merely measures how far the mean
    is from zero relative to its own noise -- it says nothing about
    out-of-sample validity or friction, which is why this is one criterion
    among 5, never used alone."""
    n = len(returns_pct)
    if n < 2:
        return TStatResult(t_stat=0.0, n=n, mean_return_pct=0.0, survives=False)
    mean = sum(returns_pct) / n
    variance = sum((r - mean) ** 2 for r in returns_pct) / (n - 1)
    stdev = math.sqrt(variance)
    if stdev == 0:
        t_stat = 0.0
    else:
        t_stat = mean / (stdev / math.sqrt(n))
    return TStatResult(t_stat=t_stat, n=n, mean_return_pct=mean, survives=t_stat >= min_t_stat)


@dataclass(frozen=True)
class StabilityResult:
    block_means: list[float]
    positive_blocks: int
    n_blocks: int
    survives: bool


def inter_period_stability_check(
    returns_pct: Sequence[float], *, n_blocks: int = 4, min_positive_blocks: int | None = None,
) -> StabilityResult:
    """Splits ``returns_pct`` chronologically (input order is assumed
    chronological -- never shuffled here) into ``n_blocks`` contiguous
    blocks and checks the mean return stays positive in enough of them.
    Same doctrine as the manual quartile-stability checks used throughout
    this project's signal research (a mean that's only positive because of
    1-2 outlier trades in a single block is exactly the false-positive
    pattern this protocol exists to catch -- cf. the 10/08 wick-gate and the
    13/08 v10 breakout-signal research, both caught this way by hand before
    this function existed).

    ``min_positive_blocks`` defaults to ALL blocks (strictest reading of
    "stability" -- a single negative block is enough to reject) -- pass a
    lower number for a deliberately looser bar."""
    n = len(returns_pct)
    if n < n_blocks:
        return StabilityResult(block_means=[], positive_blocks=0, n_blocks=n_blocks, survives=False)
    required = n_blocks if min_positive_blocks is None else min_positive_blocks
    block_size = n // n_blocks
    block_means = []
    for i in range(n_blocks):
        start = i * block_size
        end = start + block_size if i < n_blocks - 1 else n
        block = returns_pct[start:end]
        block_means.append(sum(block) / len(block) if block else 0.0)
    positive_blocks = sum(1 for m in block_means if m > 0)
    return StabilityResult(
        block_means=block_means, positive_blocks=positive_blocks, n_blocks=n_blocks,
        survives=positive_blocks >= required,
    )


@dataclass(frozen=True)
class FalsificationProtocolVerdict:
    survives: bool
    criteria: dict[str, bool]
    reason: str
    t_stat: TStatResult | None
    stability: StabilityResult | None


def falsification_protocol_check(
    returns_pct: Sequence[float],
    *,
    min_trades: int = 30,
    min_t_stat: float = 2.0,
    n_stability_blocks: int = 4,
    min_positive_blocks: int | None = None,
) -> FalsificationProtocolVerdict:
    """The full 5-criteria simultaneous falsification protocol (#293,
    arXiv 2605.04004) -- a candidate filter must pass ALL 5 at once, not any
    subset, exactly as the paper applies it (14 signal families tested,
    zero survived all 5 -- a candidate passing 4/5 is still a rejection).

    CONTRACT on ``returns_pct`` (the caller's responsibility, not checked
    here -- mirrors ``evaluate_filter_candidate_out_of_sample``'s own
    validation-only contract):
      1. min_trades: len(returns_pct) >= min_trades (checked here).
      2. t-stat >= min_t_stat: checked here via ``t_statistic_check``.
      3. walk-forward out-of-sample: ``returns_pct`` MUST already be the
         VALIDATION half of a prior ``chronological_train_validation_split``
         -- never the train half, never an unsplit full sample.
      4. net-of-friction positive: each value in ``returns_pct`` MUST already
         include simulated fees/slippage (same doctrine as
         ``wick_filter_shadow.py``'s +/-1.3% simulated fees) -- this function
         has no way to verify that from the numbers alone, so it is a
         documented contract, not a runtime check.
      5. inter-period stability: checked here via
         ``inter_period_stability_check``.

    Returns a verdict with per-criterion detail (``criteria`` dict) so a
    caller can see exactly which of the 5 failed, not just a single bit."""
    n = len(returns_pct)
    criteria: dict[str, bool] = {}

    criteria["min_trades"] = n >= min_trades
    if not criteria["min_trades"]:
        return FalsificationProtocolVerdict(
            survives=False,
            criteria={**criteria, "t_stat": False, "net_of_friction_positive": False, "stability": False},
            reason=f"only {n} trades (< {min_trades} minimum) -- accumulate more before testing",
            t_stat=None, stability=None,
        )

    t_stat_result = t_statistic_check(returns_pct, min_t_stat=min_t_stat)
    criteria["t_stat"] = t_stat_result.survives

    mean_return = sum(returns_pct) / n
    criteria["net_of_friction_positive"] = mean_return > 0

    stability_result = inter_period_stability_check(
        returns_pct, n_blocks=n_stability_blocks, min_positive_blocks=min_positive_blocks,
    )
    criteria["stability"] = stability_result.survives

    survives = all(criteria.values())
    if survives:
        reason = f"all 5 criteria pass: n={n}, t_stat={t_stat_result.t_stat:.2f}, mean_return={mean_return:.2f}%, {stability_result.positive_blocks}/{stability_result.n_blocks} blocks positive"
    else:
        failed = [k for k, v in criteria.items() if not v]
        reason = f"failed criteria: {', '.join(failed)} (mean_return={mean_return:.2f}%, t_stat={t_stat_result.t_stat:.2f})"

    return FalsificationProtocolVerdict(
        survives=survives, criteria=criteria, reason=reason,
        t_stat=t_stat_result, stability=stability_result,
    )
