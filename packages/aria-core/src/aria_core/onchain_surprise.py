"""Measure how UNEXPECTED an observation is, not how large it is.

Why this exists (02/09, operator: *"mesurer la surprise, pas le niveau"*).
Every metric we built today shares one defect: it is read in absolute terms.
100 swaps/min means nothing on its own -- it is unremarkable for a token whose
baseline is 90, and an event for one whose baseline is 5. The same flaw was
measured directly on the renewal ratio: it decays structurally with age (1.00
at a token's birth, ~0.4 in week one, ~0.2 in week four), so any fixed
threshold on it measures the token's AGE rather than its narrative health.

**This module is not a hypothesis.** It is a correction of measurement, which
is why it can ship without the out-of-sample discipline the hypotheses require:
it makes no claim about what predicts anything. It only answers "how far is
this from what this token itself was doing a moment ago".

**Robust statistics, deliberately.** Median and MAD rather than mean and
standard deviation, because a single burst in the baseline window would inflate
the deviation enough to hide every subsequent event. On memecoin flow -- which
we measured to be bimodal on MEOW: 1s median gap against a 27.2s mean -- the
mean is not a description of anything.

**Baseline is causal, like everything else here.** It is built only from
observations strictly BEFORE the point being scored. A baseline that included
the observation would dampen exactly the events we are looking for.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass

# Below this, a baseline is not a distribution, it is a couple of points.
# Scoring against it would produce confident nonsense.
MIN_BASELINE_POINTS = 8

# 1.4826 * MAD estimates the standard deviation for a normal distribution --
# the standard consistency constant, so a "surprise" reads on the same scale as
# a z-score for the well-behaved case, while staying robust when it is not.
_MAD_TO_SIGMA = 1.4826

# A MAD of zero (a perfectly flat baseline) would divide by zero. This floor
# expresses the smallest deviation we accept as meaningful rather than letting
# a flat window produce an infinite surprise on the first non-zero value.
_MIN_SCALE = 1e-9


@dataclass(frozen=True)
class Surprise:
    """How improbable an observation is, plus what it was measured against.

    ``available=False`` when the baseline is too short: a missing surprise must
    be visible as missing, never rendered as 0.0 (which reads as "perfectly
    normal" -- the exact opposite of "we could not tell").
    """

    available: bool
    value: float | None = None          # signed, in robust sigmas
    baseline_median: float | None = None
    baseline_scale: float | None = None
    baseline_n: int = 0
    reason: str | None = None

    @property
    def magnitude(self) -> float:
        """Unsigned surprise -- for "something changed" regardless of direction."""
        return abs(self.value) if self.value is not None else 0.0


def surprise(observation: float | None, baseline: list[float]) -> Surprise:
    """How far ``observation`` sits from what ``baseline`` was doing.

    ``baseline`` must contain only values observed BEFORE the observation.
    Returns a signed robust z-score: +3 means "three robust sigmas above what
    this series was doing", -3 the same below.
    """
    if observation is None:
        return Surprise(False, reason="no_observation")
    clean = [float(v) for v in baseline if v is not None]
    if len(clean) < MIN_BASELINE_POINTS:
        return Surprise(False, baseline_n=len(clean), reason="baseline_too_short")

    med = st.median(clean)
    mad = st.median([abs(v - med) for v in clean])
    scale = max(mad * _MAD_TO_SIGMA, _MIN_SCALE)
    return Surprise(
        available=True,
        value=round((float(observation) - med) / scale, 4),
        baseline_median=round(med, 6),
        baseline_scale=round(scale, 6),
        baseline_n=len(clean),
    )


def rolling_surprise(series: list[float | None], *, window: int = 20) -> list[Surprise]:
    """Surprise at each point against the ``window`` points that preceded it.

    The first ``MIN_BASELINE_POINTS`` entries come back unavailable rather than
    computed against a stub -- a series does not become measurable just because
    we would like it to be.
    """
    out: list[Surprise] = []
    for i, value in enumerate(series):
        baseline = [v for v in series[max(0, i - window):i] if v is not None]
        out.append(surprise(value, baseline))
    return out


def multivariate_surprise(surprises: dict[str, Surprise]) -> dict:
    """Aggregate several dimensions WITHOUT collapsing them into one number.

    Returns how many dimensions moved and how far, never a single score. The
    operator's standing constraint: the same metric inverts meaning between the
    slow-base and fast-stampede regimes, so a merged figure would hide which
    dimension carried it.

    ``concurrent`` is the count of dimensions whose surprise exceeds 2 robust
    sigmas at the same instant -- the raw material for change-point detection,
    since a genuine regime shift moves several distributions together while
    noise moves one.
    """
    available = {k: s for k, s in surprises.items() if s.available and s.value is not None}
    if not available:
        return {"available": False, "reason": "no_dimension_available",
                "unavailable": sorted(surprises)}
    strong = {k: s.value for k, s in available.items() if abs(s.value) >= 2.0}
    return {
        "available": True,
        "dimensions": {k: s.value for k, s in available.items()},
        "concurrent": len(strong),
        "concurrent_dimensions": sorted(strong),
        "max_magnitude": round(max(abs(s.value) for s in available.values()), 4),
        # Named, never merged: which way each dimension moved is the part that
        # distinguishes a healthy acceleration from a contradiction.
        "directions": {k: ("up" if s.value > 0 else "down") for k, s in available.items()},
        "unavailable": sorted(k for k in surprises if k not in available),
    }
