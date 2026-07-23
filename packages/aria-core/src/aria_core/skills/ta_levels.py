"""Deterministic (facts-only) technical analysis engine for ARIA.

This module derives technical levels — high / low, supports, resistances,
trend — **purely** from an OHLCV series given as input. No level, no trend,
no justification is ever fabricated: every value is computed deterministically
(same candles -> same result) and every level carries a **factual basis**
("resistance = high tested N times over M candles") that makes its origin
explicit.

Principle (extension of the facts-only dome): if data isn't in the series, it
doesn't appear in the result. An empty window or a single-candle window never
raises -- it produces a coherent, degraded ``TALevels``.

``suggest_entry_zone`` proposes an entry / invalidation / target zone
**derived from real levels** (nearby support = invalidation below the
support; target = next resistance), never a fabricated target.

NOTE (translation, 23/07): the field names below (``plus_haut``, ``tendance``,
etc.) and the French text values they hold ("haussière", "Résistance = ...")
are FUNCTIONAL data returned by this engine (asserted on by tests, likely
consumed downstream) -- kept untouched. Only this module's own narrative
(docstrings/comments) is in English.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean


@dataclass(frozen=True)
class Candle:
    """An OHLCV candle. ``ts`` is a timestamp (epoch seconds or candle index)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Level:
    """A technical level derived from the data, with its factual basis.

    ``type`` in {``"support"``, ``"resistance"``}. ``touches`` is the number of
    pivots grouped into this level (how many times the price "tested" it).
    ``base`` is the level's factual text justification.
    """

    prix: float
    type: str
    touches: int
    base: str


@dataclass(frozen=True)
class TALevels:
    """Result of the deterministic technical analysis of an OHLCV window.

    All fields derive solely from the supplied candles. ``supports`` and
    ``resistances`` are sorted from most significant (most tested) to least
    significant. ``bases`` gathers the global factual justifications
    (extremes, trend) for a synthetic display.
    """

    plus_haut: float | None
    plus_bas: float | None
    dernier_close: float | None
    tendance: str
    tendance_base: str
    supports: list[Level] = field(default_factory=list)
    resistances: list[Level] = field(default_factory=list)
    n_bougies: int = 0
    bases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EntryZone:
    """Entry zone derived from real levels (never a fabricated target)."""

    entree: float
    invalidation: float
    cible: float
    base: str


# Pivot clustering tolerance: 2% of the window's amplitude.
_CLUSTER_TOL_FRAC = 0.02
# Relative threshold to decide the trend (0.5% gap between short/long MA).
_TREND_EPS = 0.005
# Invalidation margin below the support (3%).
_INVALIDATION_MARGIN = 0.03
_EPS = 1e-9


def _fmt(price: float) -> str:
    """Readable price format, adapted to crypto scales (large and micro)."""
    ap = abs(price)
    if ap == 0:
        return "0"
    if ap >= 1000:
        return f"{price:,.0f}"
    if ap >= 1:
        return f"{price:.2f}"
    if ap >= 0.01:
        return f"{price:.4f}"
    return f"{price:.8f}".rstrip("0").rstrip(".")


def _pivot_window(n: int) -> int:
    """Half-window width for pivot detection, adapted to the size."""
    if n <= 2:
        return 0
    if n < 5:
        return 1
    return 2


def _pivot_indices(values: list[float], k: int, kind: str) -> list[int]:
    """Indices of local pivots (extrema of a sliding window of half-width k).

    A point is a high pivot if it equals the maximum of its window (same for
    low with the minimum). Ties count: a plateau at the high produces several
    pivots -- this is intentional, it reflects repeated tests of the level.
    """
    out: list[int] = []
    m = len(values)
    for i in range(m):
        lo = max(0, i - k)
        hi = min(m, i + k + 1)
        window = values[lo:hi]
        if kind == "high" and values[i] >= max(window):
            out.append(i)
        elif kind == "low" and values[i] <= min(window):
            out.append(i)
    return out


def _cluster_prices(prices: list[float], tol: float) -> list[list[float]]:
    """Groups sorted prices into clusters whose internal spread is <= tol."""
    if not prices:
        return []
    ordered = sorted(prices)
    clusters: list[list[float]] = [[ordered[0]]]
    for p in ordered[1:]:
        if abs(p - clusters[-1][-1]) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters


def _build_levels(
    prices: list[float],
    tol: float,
    extreme: float,
    kind: str,
    n: int,
) -> list[Level]:
    """Builds levels (support/resistance) from pivot prices.

    A cluster is only kept if it's tested at least twice OR if it contains
    the window's global extreme (high/low) -- the latter always kept even if
    tested only once (it's a hard fact).
    """
    is_res = kind == "resistance"
    levels: list[Level] = []
    for cluster in _cluster_prices(prices, tol):
        rep = max(cluster) if is_res else min(cluster)
        touches = len(cluster)
        contains_extreme = abs(rep - extreme) <= max(tol, _EPS)
        if touches < 2 and not contains_extreme:
            continue
        libelle = "Résistance" if is_res else "Support"
        ancrage = "plus-haut" if is_res else "plus-bas"
        base = (
            f"{libelle} = {ancrage} à {_fmt(rep)} testé "
            f"{touches} fois sur {n} bougies."
        )
        levels.append(Level(prix=rep, type=kind, touches=touches, base=base))
    # Sort: most tested first, then by price (deterministic).
    levels.sort(key=lambda lv: (-lv.touches, lv.prix))
    return levels


def _trend(closes: list[float]) -> tuple[str, str]:
    """Trend by comparing short MA (last third) vs. long MA (whole window).

    Returns (trend, factual basis). Fewer than two candles -> indeterminate.
    """
    n = len(closes)
    if n < 2:
        return "indéterminée", "Moins de deux bougies — tendance indéterminée."
    short_w = max(1, n // 3)
    mm_short = fmean(closes[-short_w:])
    mm_long = fmean(closes)
    delta = closes[-1] - closes[0]
    if mm_long != 0:
        rel = (mm_short - mm_long) / abs(mm_long)
    else:
        rel = 0.0
    if rel > _TREND_EPS:
        tendance = "haussière"
    elif rel < -_TREND_EPS:
        tendance = "baissière"
    else:
        tendance = "neutre"
    base = (
        f"Tendance {tendance} : moyenne courte ({_fmt(mm_short)}) "
        f"{'au-dessus' if mm_short >= mm_long else 'en dessous'} de la moyenne "
        f"longue ({_fmt(mm_long)}) ; variation close {_fmt(closes[0])} → "
        f"{_fmt(closes[-1])} ({'+' if delta >= 0 else ''}{_fmt(delta)}) "
        f"sur {n} bougies."
    )
    return tendance, base


def compute_levels(candles: list[Candle]) -> TALevels:
    """Deterministically derives the technical levels of an OHLCV window.

    Every level and the trend carry a factual basis. An empty window or a
    single-candle window never raises -- it returns a coherent ``TALevels``.
    """
    n = len(candles)
    if n == 0:
        return TALevels(
            plus_haut=None,
            plus_bas=None,
            dernier_close=None,
            tendance="indéterminée",
            tendance_base="Aucune donnée fournie — analyse impossible.",
            supports=[],
            resistances=[],
            n_bougies=0,
            bases=["Aucune donnée fournie — analyse impossible."],
        )

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]

    plus_haut = max(highs)
    plus_bas = min(lows)
    dernier_close = closes[-1]
    span = plus_haut - plus_bas
    tol = span * _CLUSTER_TOL_FRAC if span > 0 else 0.0

    k = _pivot_window(n)
    pivot_high_prices = [highs[i] for i in _pivot_indices(highs, k, "high")]
    pivot_low_prices = [lows[i] for i in _pivot_indices(lows, k, "low")]

    resistances = _build_levels(pivot_high_prices, tol, plus_haut, "resistance", n)
    supports = _build_levels(pivot_low_prices, tol, plus_bas, "support", n)

    tendance, tendance_base = _trend(closes)

    bases = [
        f"Plus-haut de fenêtre = {_fmt(plus_haut)} (max des hauts sur {n} bougies).",
        f"Plus-bas de fenêtre = {_fmt(plus_bas)} (min des bas sur {n} bougies).",
        tendance_base,
    ]

    return TALevels(
        plus_haut=plus_haut,
        plus_bas=plus_bas,
        dernier_close=dernier_close,
        tendance=tendance,
        tendance_base=tendance_base,
        supports=supports,
        resistances=resistances,
        n_bougies=n,
        bases=bases,
    )


def suggest_entry_zone(levels: TALevels) -> EntryZone | None:
    """Proposes entry / invalidation / target **derived from real levels**.

    - Entry = nearest support at or below (<=) the last close (falls back to
      the window low absent a qualified support).
    - Invalidation = just below that support (3% margin): the thesis fails
      if the support breaks.
    - Target = next resistance above the close (falls back to the window
      high absent one).

    Returns ``None`` if the levels don't allow a coherent zone (missing
    data) -- never a fabricated target.
    """
    if levels.dernier_close is None or levels.plus_bas is None or levels.plus_haut is None:
        return None

    close = levels.dernier_close

    supports_below = [lv for lv in levels.supports if lv.prix <= close + _EPS]
    if supports_below:
        support = max(supports_below, key=lambda lv: lv.prix)
        support_prix = support.prix
        support_desc = f"support à {_fmt(support_prix)} (testé {support.touches} fois)"
    else:
        support_prix = levels.plus_bas
        support_desc = f"plus-bas de fenêtre à {_fmt(support_prix)}"

    resistances_above = [lv for lv in levels.resistances if lv.prix > close + _EPS]
    if resistances_above:
        resistance = min(resistances_above, key=lambda lv: lv.prix)
        resistance_prix = resistance.prix
        resistance_desc = (
            f"résistance suivante à {_fmt(resistance_prix)} "
            f"(testée {resistance.touches} fois)"
        )
    else:
        resistance_prix = levels.plus_haut
        resistance_desc = f"plus-haut de fenêtre à {_fmt(resistance_prix)}"

    entree = support_prix
    invalidation = support_prix * (1.0 - _INVALIDATION_MARGIN)
    cible = resistance_prix

    base = (
        f"Zone dérivée des niveaux réels — entrée au {support_desc}, "
        f"invalidation {_INVALIDATION_MARGIN:.0%} sous ce support "
        f"({_fmt(invalidation)}), cible sur la {resistance_desc}. "
        f"Dernier close observé : {_fmt(close)}."
    )

    return EntryZone(
        entree=entree,
        invalidation=invalidation,
        cible=cible,
        base=base,
    )
