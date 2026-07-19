"""Signaux d'entrée haute qualité (facts-only, déterministe) — le viseur du chasseur.

Encode un setup d'entrée éprouvé : **prix dans la zone Fibonacci profonde** (golden
pocket 0,618–0,786, le « support rouge ») **+ divergence haussière RSI**, formés dans
une fenêtre de **≤ 25 bougies**. Quand les deux coïncident, c'est historiquement l'un
des meilleurs points d'entrée pour le ratio risque/récompense (invalidation serrée sous
le support, cible = retour vers le haut du range → R/R généreux).

Tout est dérivé de la série OHLCV réelle (mêmes bougies → même résultat). Aucune valeur
inventée : sans setup, ``present=False`` (le rapport omet simplement le signal). C'est une
**hypothèse** (intuition opérateur) que le track-record valide — jamais un dogme.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aria_core.skills.ta_levels import Candle

_FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)
_DEFAULT_LOOKBACK = 25
_RSI_PERIOD = 14


@dataclass(frozen=True)
class EntrySignal:
    """Un point d'entrée détecté (ou son absence), avec sa base factuelle et son R/R."""

    present: bool
    reasons: list[str] = field(default_factory=list)
    in_golden_pocket: bool = False
    rsi_divergence: bool = False
    entry: float | None = None
    invalidation: float | None = None
    target: float | None = None
    rr: float | None = None
    lookback_used: int = 0


def rsi_series(closes: list[float], period: int = _RSI_PERIOD) -> list[float | None]:
    """RSI de Wilder aligné sur ``closes`` (None pendant la période de chauffe)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    def _val(ag: float, al: float) -> float:
        if al == 0:
            return 100.0 if ag > 0 else 50.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _val(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _val(avg_gain, avg_loss)
    return out


def fibonacci_zone(candles: list[Candle]) -> dict | None:
    """Golden pocket (0,618–0,786) + niveaux, du plus-bas au plus-haut de la fenêtre.

    Mesure la jambe swing bas → swing haut ; les retracements sont des SUPPORTS sous
    le plus-haut. Retourne None si la fenêtre est plate/trop courte.
    """
    if len(candles) < 2:
        return None
    hi = max(c.high for c in candles)
    lo = min(c.low for c in candles)
    if hi <= lo:
        return None
    diff = hi - lo
    levels = {r: hi - diff * r for r in _FIB_RATIOS}
    # Golden pocket : entre le retracement 0,618 et 0,786 (zone profonde « rouge »).
    return {
        "high": hi,
        "low": lo,
        "levels": levels,
        "gp_high": levels[0.618],  # borne haute de la zone (retracement moins profond)
        "gp_low": levels[0.786],   # borne basse (retracement plus profond)
    }


def bullish_rsi_divergence(
    candles: list[Candle], *, lookback: int = _DEFAULT_LOOKBACK, period: int = _RSI_PERIOD
) -> tuple[bool, str]:
    """Divergence haussière : prix fait un plus-bas plus BAS, RSI fait un creux plus HAUT.

    Compare le DERNIER creux (minimum local) de la fenêtre à chaque creux ANTÉRIEUR,
    en partant du plus récent -- pas seulement l'avant-dernier immédiat (19/07,
    corrigé après investigation empirique sur candidats réels du pipeline momentum :
    0 divergence détectée sur 8 candidats avec données exploitables, contre 4 golden
    pocket atteints seuls -- la comparaison n'examinait QUE la paire de creux
    immédiatement adjacente, ratant toute divergence formée sur une jambe plus large
    de la même fenêtre). Même DÉFINITION stricte du signal (prix plus bas + RSI plus
    haut) qu'avant -- seule la PORTÉE de la recherche est élargie, pas le critère.
    Signal classique d'essoufflement de la baisse. Retourne (present, base factuelle).
    """
    # RSI calculé sur la série COMPLÈTE (chauffé avant la fenêtre), puis on ne
    # cherche les creux que dans les `lookback` dernières bougies. Ainsi un setup
    # récent a un RSI défini même si la fenêtre est courte.
    closes_all = [c.close for c in candles]
    rsis = rsi_series(closes_all, period)
    start = max(1, len(candles) - lookback) if lookback else 1
    pivots: list[tuple[int, float, float]] = []
    for i in range(start, len(candles) - 1):
        r = rsis[i]
        if r is None:
            continue
        if candles[i].low <= candles[i - 1].low and candles[i].low <= candles[i + 1].low:
            pivots.append((i, candles[i].low, r))
    if len(pivots) < 2:
        return False, ""
    _, l2, r2 = pivots[-1]
    for _, l1, r1 in reversed(pivots[:-1]):
        if l2 < l1 and r2 > r1:
            return True, f"plus-bas prix {l2:.6g} < {l1:.6g} mais RSI remonte ({r1:.0f} → {r2:.0f})"
    return False, ""


def detect_entry(
    candles: list[Candle],
    *,
    lookback: int = _DEFAULT_LOOKBACK,
    tolerance: float = 0.03,
) -> EntrySignal:
    """Détecte le setup « golden pocket + divergence RSI » sur ≤ ``lookback`` bougies.

    ``present`` seulement si le prix courant est dans (ou tout près de) la zone
    Fibonacci profonde ET qu'une divergence haussière RSI est présente. Fournit alors
    entrée/invalidation/cible dérivées des niveaux réels + le R/R.
    """
    if len(candles) < _RSI_PERIOD + 2:
        return EntrySignal(present=False, reasons=["série trop courte pour un signal fiable"])

    window = candles[-lookback:]
    fib = fibonacci_zone(window)
    div, div_base = bullish_rsi_divergence(candles, lookback=lookback)
    close = candles[-1].close
    reasons: list[str] = []

    in_gp = False
    if fib is not None:
        gp_low, gp_high = fib["gp_low"], fib["gp_high"]  # gp_low < gp_high
        if gp_low * (1 - tolerance) <= close <= gp_high * (1 + tolerance):
            in_gp = True
            reasons.append(f"prix {close:.6g} dans la zone Fibonacci 0,618–0,786 (support profond)")
    if div:
        reasons.append("divergence haussière RSI : " + div_base)

    if not (in_gp and div and fib is not None):
        return EntrySignal(
            present=False, reasons=reasons or ["setup non réuni"],
            in_golden_pocket=in_gp, rsi_divergence=div, lookback_used=len(window),
        )

    # Zone dérivée des niveaux réels : invalidation sous le support profond, cible =
    # retour vers le haut du range (retest du swing haut) → R/R généreux par construction.
    entry = close
    invalidation = fib["gp_low"] * (1 - 0.02)
    target = fib["high"]
    rr = None
    if entry > invalidation and target > entry:
        rr = round((target - entry) / (entry - invalidation), 1)
    return EntrySignal(
        present=True,
        reasons=reasons,
        in_golden_pocket=True,
        rsi_divergence=True,
        entry=entry,
        invalidation=invalidation,
        target=target,
        rr=rr,
        lookback_used=len(window),
    )
