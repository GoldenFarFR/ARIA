"""Indicateurs techniques généraux (facts-only, déterministe) — EMA, MACD, Bollinger.

Complète `entry_signals.rsi_series` (RSI de Wilder) et `ta_levels` (niveaux/tendance).
`CLAUDE.md` annonce depuis longtemps un "Moteur TA (RSI/MACD/EMA/fibo/divergences)" —
MACD et EMA n'étaient en réalité jamais calculés nulle part avant ce module (écart
découvert le 10/07 en vérifiant le code réel avant d'écrire quoi que ce soit). Les
bandes de Bollinger, elles, n'ont jamais été annoncées mais manquaient pour couvrir
la demande opérateur du 10/07 (RSI + Bollinger + volumes + bougies comme entrées d'un
futur moteur de backtest — cf. `docs/architecture-extensibilite.md`).

Tout est dérivé de la série de closes fournie (mêmes closes → même résultat). Aucune
valeur inventée : période de chauffe insuffisante → ``None`` à ces positions, jamais
une estimation.
"""
from __future__ import annotations

import math

_EMA_FAST = 12
_EMA_SLOW = 26
_MACD_SIGNAL = 9
_BOLLINGER_PERIOD = 20
_BOLLINGER_NUM_STD = 2.0


def ema_series(closes: list[float], period: int) -> list[float | None]:
    """EMA alignée sur ``closes``. Amorçage par SMA des ``period`` premiers closes
    (convention standard), puis récursion EMA. ``None`` pendant la période de chauffe."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out

    k = 2.0 / (period + 1)
    sma = sum(closes[:period]) / period
    out[period - 1] = sma
    prev = sma
    for i in range(period, n):
        prev = closes[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd_series(
    closes: list[float],
    *,
    fast: int = _EMA_FAST,
    slow: int = _EMA_SLOW,
    signal: int = _MACD_SIGNAL,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD standard (ligne, signal, histogramme), alignés sur ``closes``.

    Ligne MACD = EMA rapide - EMA lente. Signal = EMA de la ligne MACD. Histogramme =
    MACD - signal. ``None`` tant que l'EMA lente (la plus longue période de chauffe)
    n'est pas encore disponible.
    """
    n = len(closes)
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)

    macd_line: list[float | None] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # EMA du signal appliquée uniquement sur le segment défini de la ligne MACD
    # (sinon les None en tête faussent l'amorçage SMA de ema_series).
    first_defined = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n
    if first_defined is not None:
        defined_macd = [v for v in macd_line[first_defined:]]  # tous non-None a partir d'ici
        signal_on_defined = ema_series(defined_macd, signal)  # type: ignore[arg-type]
        for offset, value in enumerate(signal_on_defined):
            if value is None:
                continue
            idx = first_defined + offset
            signal_line[idx] = value
            histogram[idx] = macd_line[idx] - value

    return macd_line, signal_line, histogram


def bollinger_bands(
    closes: list[float],
    *,
    period: int = _BOLLINGER_PERIOD,
    num_std: float = _BOLLINGER_NUM_STD,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Bandes de Bollinger (milieu = SMA, haut/bas = SMA ± ``num_std`` écarts-types
    de population sur la même fenêtre). ``None`` pendant la période de chauffe.

    Convention standard : écart-type de POPULATION (diviseur ``period``, pas
    ``period - 1``) sur la fenêtre glissante — pas l'écart-type de l'échantillon.
    """
    n = len(closes)
    middle: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return middle, upper, lower

    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        middle[i] = mean
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return middle, upper, lower
