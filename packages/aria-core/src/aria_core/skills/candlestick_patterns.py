"""Détection de patterns de bougies japonaises (facts-only, déterministe).

Complète `indicators.py` (EMA/MACD/Bollinger) et `entry_signals.py` (Fibonacci/
divergence RSI) — la « disposition des bougies » demandée par l'opérateur le 10/07,
pensée comme brique d'entrée d'un futur moteur de backtest (cf.
`docs/architecture-extensibilite.md`) plutôt que comme un signal isolé.

Chaque détecteur est une fonction pure sur des ratios OHLC réels (corps/mèches),
avec des seuils SIMPLES et DÉCLARÉS ici (aucune définition universelle n'existe
pour ces patterns) — même doctrine que `btc_cycles` pour ses heuristiques. Jamais
un jugement de tendance : ces fonctions décrivent la FORME d'une bougie (ou d'une
paire), pas ce qui va se passer ensuite — l'interprétation reste au moteur de
backtest ou au LLM, ancrée sur des chiffres réels.
"""
from __future__ import annotations

from dataclasses import dataclass

from aria_core.skills.ta_levels import Candle

# Seuils SIMPLES et DÉCLARÉS (pas une norme officielle — cf. doctrine module).
_DOJI_BODY_RATIO_MAX = 0.1       # corps <= 10% du range = doji
_HAMMER_LOWER_WICK_MIN = 2.0     # mèche basse >= 2x le corps
_HAMMER_UPPER_WICK_MAX = 0.3     # mèche haute <= 30% du corps
_MARUBOZU_BODY_RATIO_MIN = 0.9   # corps >= 90% du range = marubozu (quasi sans mèche)


@dataclass(frozen=True)
class CandlePattern:
    """Un pattern détecté à l'index ``i`` d'une série de bougies, avec sa base factuelle."""

    index: int
    name: str
    direction: str  # "bullish" | "bearish" | "neutral"
    detail: str


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return c.high - c.low


def _upper_wick(c: Candle) -> float:
    return c.high - max(c.open, c.close)


def _lower_wick(c: Candle) -> float:
    return min(c.open, c.close) - c.low


def is_doji(c: Candle) -> bool:
    """Corps quasi nul par rapport au range — indécision entre acheteurs/vendeurs."""
    rng = _range(c)
    if rng <= 0:
        return False
    return _body(c) / rng <= _DOJI_BODY_RATIO_MAX


def is_marubozu(c: Candle) -> bool | None:
    """Corps qui occupe presque tout le range (quasi sans mèche). None si range nul.

    Retourne ``True``/``False``, la direction (haussière si close > open) est
    portée séparément par l'appelant via ``c.close > c.open``.
    """
    rng = _range(c)
    if rng <= 0:
        return None
    return _body(c) / rng >= _MARUBOZU_BODY_RATIO_MIN


def is_hammer(c: Candle) -> bool:
    """Mèche basse longue, corps petit en haut du range, mèche haute quasi nulle —
    rejet d'un plus-bas testé puis repoussé (lecture haussière SI en fin de baisse,
    pas jugé ici, seulement la forme)."""
    body = _body(c)
    if body <= 0:
        return False
    return (
        _lower_wick(c) >= _HAMMER_LOWER_WICK_MIN * body
        and _upper_wick(c) <= _HAMMER_UPPER_WICK_MAX * body
    )


def is_shooting_star(c: Candle) -> bool:
    """Symétrique du marteau : mèche haute longue, corps petit en bas du range —
    rejet d'un plus-haut testé puis repoussé (lecture baissière SI en fin de
    hausse, pas jugé ici, seulement la forme)."""
    body = _body(c)
    if body <= 0:
        return False
    return (
        _upper_wick(c) >= _HAMMER_LOWER_WICK_MIN * body
        and _lower_wick(c) <= _HAMMER_UPPER_WICK_MAX * body
    )


def is_bullish_engulfing(prev: Candle, cur: Candle) -> bool:
    """La bougie courante (haussière) englobe entièrement le corps de la précédente
    (baissière) — retournement classique."""
    prev_bearish = prev.close < prev.open
    cur_bullish = cur.close > cur.open
    if not (prev_bearish and cur_bullish):
        return False
    return cur.open <= prev.close and cur.close >= prev.open


def is_bearish_engulfing(prev: Candle, cur: Candle) -> bool:
    """Symétrique : la bougie courante (baissière) englobe le corps de la
    précédente (haussière)."""
    prev_bullish = prev.close > prev.open
    cur_bearish = cur.close < cur.open
    if not (prev_bullish and cur_bearish):
        return False
    return cur.open >= prev.close and cur.close <= prev.open


def detect_patterns(candles: list[Candle]) -> list[CandlePattern]:
    """Parcourt la série et retourne CHAQUE pattern détecté, aligné sur son index
    réel. Une bougie peut ne déclencher aucun pattern (silence, pas une absence
    inventée) ou plusieurs (ex. doji ET marubozu jamais simultanés par construction,
    mais engulfing + doji sur la même paire est possible)."""
    found: list[CandlePattern] = []
    for i, c in enumerate(candles):
        if is_doji(c):
            found.append(CandlePattern(i, "doji", "neutral", "corps <=10% du range"))
        marubozu = is_marubozu(c)
        if marubozu:
            direction = "bullish" if c.close > c.open else "bearish" if c.close < c.open else "neutral"
            found.append(CandlePattern(i, "marubozu", direction, "corps >=90% du range"))
        if is_hammer(c):
            found.append(CandlePattern(i, "hammer", "bullish", "mèche basse longue, corps en haut"))
        if is_shooting_star(c):
            found.append(CandlePattern(i, "shooting_star", "bearish", "mèche haute longue, corps en bas"))
        if i > 0:
            prev = candles[i - 1]
            if is_bullish_engulfing(prev, c):
                found.append(CandlePattern(i, "bullish_engulfing", "bullish", "corps englobe la bougie baissière précédente"))
            if is_bearish_engulfing(prev, c):
                found.append(CandlePattern(i, "bearish_engulfing", "bearish", "corps englobe la bougie haussière précédente"))
    return found
