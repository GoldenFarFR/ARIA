"""Moteur d'analyse technique déterministe (facts-only) pour ARIA.

Ce module dérive des niveaux techniques — plus-haut / plus-bas, supports,
résistances, tendance — **purement** à partir d'une série OHLCV fournie en
entrée. Aucun niveau, aucune tendance, aucune justification n'est inventé :
chaque valeur est calculée de façon déterministe (mêmes bougies → même résultat)
et chaque niveau porte une **base factuelle** (« résistance = plus-haut testé N
fois sur M bougies ») qui explicite d'où il vient.

Principe (extension du dôme facts-only) : si une donnée n'est pas dans la série,
elle n'apparaît pas dans le résultat. Une fenêtre vide ou d'une seule bougie ne
lève jamais d'exception — elle produit un ``TALevels`` cohérent et dégradé.

``suggest_entry_zone`` propose une zone d'entrée / invalidation / cible
**dérivée des niveaux réels** (support proche = invalidation sous le support ;
cible = résistance suivante), jamais un objectif fabriqué.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean


@dataclass(frozen=True)
class Candle:
    """Une bougie OHLCV. ``ts`` est un horodatage (epoch s ou index de bougie)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Level:
    """Un niveau technique dérivé des données, avec sa base factuelle.

    ``type`` ∈ {``"support"``, ``"resistance"``}. ``touches`` est le nombre de
    pivots regroupés dans ce niveau (combien de fois le prix l'a « testé »).
    ``base`` est la justification textuelle factuelle du niveau.
    """

    prix: float
    type: str
    touches: int
    base: str


@dataclass(frozen=True)
class TALevels:
    """Résultat de l'analyse technique déterministe d'une fenêtre OHLCV.

    Tous les champs dérivent uniquement des bougies fournies. ``supports`` et
    ``resistances`` sont triés du plus significatif (le plus testé) au moins
    significatif. ``bases`` regroupe les justifications factuelles globales
    (extrêmes, tendance) pour un affichage synthétique.
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
    """Zone d'entrée dérivée des niveaux réels (jamais un objectif fabriqué)."""

    entree: float
    invalidation: float
    cible: float
    base: str


# Tolérance de regroupement des pivots : 2 % de l'amplitude de la fenêtre.
_CLUSTER_TOL_FRAC = 0.02
# Seuil relatif de départage de la tendance (0,5 % d'écart MM courte/longue).
_TREND_EPS = 0.005
# Marge d'invalidation sous le support (3 %).
_INVALIDATION_MARGIN = 0.03
_EPS = 1e-9


def _fmt(price: float) -> str:
    """Format lisible d'un prix, adapté aux échelles crypto (grandes et micro)."""
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
    """Demi-largeur de fenêtre pour la détection de pivots, adaptée à la taille."""
    if n <= 2:
        return 0
    if n < 5:
        return 1
    return 2


def _pivot_indices(values: list[float], k: int, kind: str) -> list[int]:
    """Indices des pivots locaux (extrema d'une fenêtre glissante de demi-largeur k).

    Un point est un pivot haut s'il vaut le maximum de sa fenêtre (idem bas pour
    le minimum). L'égalité compte : un plateau au plus-haut produit plusieurs
    pivots — c'est voulu, cela reflète des tests répétés du niveau.
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
    """Regroupe des prix triés en grappes dont l'écart interne est ≤ tol."""
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
    """Construit les niveaux (support/résistance) à partir des prix de pivots.

    On ne retient qu'une grappe si elle est testée au moins deux fois OU si elle
    contient l'extrême global de la fenêtre (plus-haut / plus-bas) — ce dernier
    étant toujours conservé même testé une seule fois (c'est un fait dur).
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
    # Tri : le plus testé d'abord, puis par prix (déterministe).
    levels.sort(key=lambda lv: (-lv.touches, lv.prix))
    return levels


def _trend(closes: list[float]) -> tuple[str, str]:
    """Tendance par comparaison MM courte (dernier tiers) vs MM longue (fenêtre).

    Renvoie (tendance, base factuelle). Moins de deux bougies → indéterminée.
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
    """Dérive de façon déterministe les niveaux techniques d'une fenêtre OHLCV.

    Chaque niveau et la tendance portent une base factuelle. Une fenêtre vide ou
    d'une seule bougie ne lève jamais — elle renvoie un ``TALevels`` cohérent.
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
    """Propose entrée / invalidation / cible **dérivées des niveaux réels**.

    - Entrée = support le plus proche au niveau (≤) du dernier close (repli sur
      le plus-bas de fenêtre à défaut de support qualifié).
    - Invalidation = juste sous ce support (marge de 3 %) : la thèse tombe si le
      support cède.
    - Cible = résistance suivante au-dessus du close (repli sur le plus-haut de
      fenêtre à défaut).

    Renvoie ``None`` si les niveaux ne permettent pas une zone cohérente (données
    absentes) — jamais un objectif fabriqué.
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
