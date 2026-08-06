"""General technical indicators (facts-only, deterministic) — EMA, MACD, Bollinger, ATR.

Complements `entry_signals.rsi_series` (Wilder RSI) and `ta_levels` (levels/trend).
`CLAUDE.md` has long announced a "TA Engine (RSI/MACD/EMA/fibo/divergences)" —
MACD and EMA were in fact never computed anywhere before this module (a gap
discovered on 10/07 while checking the real code before writing anything).
Bollinger Bands, meanwhile, were never announced but were missing to cover
the 10/07 operator request (RSI + Bollinger + volumes + candles as inputs
to a future backtest engine — see `docs/architecture-extensibilite.md`).
ATR (Average True Range, 19/07) answers the same gap as EMA/MACD originally:
announced nowhere but absent from the codebase (confirmed by exhaustive
grep) until a Gemini cross-review flagged that a fixed-percentage trailing
stop ignores a token's real volatility.

Everything is derived from the provided close series (or, for ATR, the full
high/low/close candles) — same inputs -> same result. No made-up value: an
insufficient warm-up period -> ``None`` at those positions, never an estimate.
"""
from __future__ import annotations

import math

from aria_core.skills.ta_levels import Candle

_EMA_FAST = 12
_EMA_SLOW = 26
_MACD_SIGNAL = 9
_BOLLINGER_PERIOD = 20
_BOLLINGER_NUM_STD = 2.0
_ATR_PERIOD = 14
# 08/01 -- added for the 5-variant scalping comparison (V1 Bollinger %B, V2
# VWAP Z-score, V3 Stochastic %K) -- operator-provided spec, cross-checked
# against standard conventions before implementing.
_VWAP_ZSCORE_PERIOD = 20
_STOCHASTIC_PERIOD = 14


def ema_series(closes: list[float], period: int) -> list[float | None]:
    """EMA aligned on ``closes``. Seeded by the SMA of the first ``period``
    closes (standard convention), then EMA recursion. ``None`` during the
    warm-up period."""
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
    """Standard MACD (line, signal, histogram), aligned on ``closes``.

    MACD line = fast EMA - slow EMA. Signal = EMA of the MACD line. Histogram
    = MACD - signal. ``None`` until the slow EMA (the longest warm-up period)
    is available.
    """
    n = len(closes)
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)

    macd_line: list[float | None] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # Signal EMA applied only on the defined segment of the MACD line
    # (otherwise the leading Nones would throw off ema_series' SMA seeding).
    first_defined = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n
    if first_defined is not None:
        defined_macd = [v for v in macd_line[first_defined:]]  # all non-None from here on
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
    """Bollinger Bands (middle = SMA, upper/lower = SMA ± ``num_std`` population
    standard deviations over the same window). ``None`` during the warm-up period.

    Standard convention: POPULATION standard deviation (``period`` divisor, not
    ``period - 1``) over the sliding window — not the sample standard deviation.
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


def atr_series(candles: list[Candle], *, period: int = _ATR_PERIOD) -> list[float | None]:
    """Wilder's Average True Range (ATR), aligned on ``candles`` — measures an
    asset's RAW VOLATILITY (normal "breathing" amplitude), without indicating
    direction (19/07, Gemini cross-review: replaces a fixed-percentage
    trailing stop with a width that adapts to each token).

    True Range of a candle = max(high-low, |high - previous close|,
    |low - previous close|) — also captures gaps, not just intra-candle
    amplitude. The very first candle has no previous close, uses high-low
    alone (standard convention, no made-up data).

    Seeded by a simple average of the first ``period`` True Ranges, then
    Wilder smoothing (``atr = (previous_atr * (period-1) + tr) / period`` —
    alpha = 1/period, deliberately NOT the 2/(period+1) of a classic EMA:
    that's the historical ATR convention, different from ``ema_series``
    above). ``None`` during the warm-up period, never a made-up value."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out

    true_ranges: list[float] = [0.0] * n
    for i, c in enumerate(candles):
        high_low = c.high - c.low
        if i == 0:
            true_ranges[i] = high_low
        else:
            prev_close = candles[i - 1].close
            true_ranges[i] = max(high_low, abs(c.high - prev_close), abs(c.low - prev_close))

    atr = sum(true_ranges[:period]) / period
    out[period - 1] = atr
    for i in range(period, n):
        atr = (atr * (period - 1) + true_ranges[i]) / period
        out[i] = atr
    return out


def bollinger_percent_b(
    closes: list[float], *, period: int = _BOLLINGER_PERIOD, num_std: float = _BOLLINGER_NUM_STD,
) -> list[float | None]:
    """%B (08/01, V1 scalping variant) -- position relative du prix dans le
    canal de Bollinger: 0 = touche la bande basse, 1 = touche la bande
    haute, peut sortir de [0,1] si le prix quitte le canal. ``None`` pendant
    le warmup ou si le canal est nul (upper == lower, marché parfaitement
    plat) -- jamais une division par zéro déguisée en valeur."""
    n = len(closes)
    _, upper, lower = bollinger_bands(closes, period=period, num_std=num_std)
    out: list[float | None] = [None] * n
    for i in range(n):
        if upper[i] is None or lower[i] is None:
            continue
        width = upper[i] - lower[i]
        if width <= 0:
            continue
        out[i] = (closes[i] - lower[i]) / width
    return out


def vwap_series(candles: list[Candle], *, period: int | None = None) -> list[float | None]:
    """VWAP (08/01) -- prix moyen pondéré par le volume, sur le prix typique
    (high+low+close)/3. ``period=None`` (défaut) = VWAP CUMULATIF depuis la
    première bougie fournie (convention intraday classique -- l'appelant
    contrôle le "reset" en changeant la fenêtre de bougies passée). Un
    ``period`` entier donne un VWAP GLISSANT sur cette fenêtre à la place
    (utilisé par ``vwap_zscore_series`` ci-dessous -- plus adapté à un token
    qui trade en continu 24/7, sans "ouverture de session" claire à ancrer
    un VWAP cumulatif dessus). ``None`` tant que le volume cumulé de la
    fenêtre est nul -- jamais une division par zéro déguisée."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n == 0:
        return out
    typical_prices = [(c.high + c.low + c.close) / 3.0 for c in candles]
    volumes = [c.volume for c in candles]
    if period is None:
        cum_pv = 0.0
        cum_vol = 0.0
        for i in range(n):
            cum_pv += typical_prices[i] * volumes[i]
            cum_vol += volumes[i]
            if cum_vol > 0:
                out[i] = cum_pv / cum_vol
        return out
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        window_pv = sum(typical_prices[j] * volumes[j] for j in range(i - period + 1, i + 1))
        window_vol = sum(volumes[i - period + 1 : i + 1])
        if window_vol > 0:
            out[i] = window_pv / window_vol
    return out


def vwap_zscore_series(candles: list[Candle], *, period: int = _VWAP_ZSCORE_PERIOD) -> list[float | None]:
    """Z-score (08/01, V2/V5 scalping variants) de l'écart (close - VWAP
    glissant) sur une fenêtre de ``period`` bougies -- mesure à quel point le
    prix actuel s'écarte de sa moyenne pondérée par le volume RÉCENTE, en
    unités d'écart-type de cet écart. <= -2.5 = survente "institutionnelle"
    (chute excessive vs le volume réellement échangé), >= +2.5 = surachat.

    Warmup RÉEL = ``2 * period`` bougies, pas ``period`` seul : le VWAP
    glissant lui-même a besoin de ``period`` bougies pour se stabiliser à
    CHAQUE point, et cette fonction exige ensuite ``period`` VALEURS DE VWAP
    déjà stabilisées pour calculer l'écart-type de la fenêtre -- jamais une
    valeur partielle silencieuse. ``None`` avant ce double warmup ou si
    l'écart-type de la fenêtre est nul (marché parfaitement plat) -- jamais
    une division par zéro déguisée."""
    n = len(candles)
    closes = [c.close for c in candles]
    vwap = vwap_series(candles, period=period)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        start = i - period + 1
        window_vwap = vwap[start : i + 1]
        if any(v is None for v in window_vwap):
            continue
        deviations = [closes[start + j] - window_vwap[j] for j in range(period)]
        mean_dev = sum(deviations) / period
        variance = sum((d - mean_dev) ** 2 for d in deviations) / period
        std = math.sqrt(variance)
        if std <= 0:
            continue
        out[i] = (deviations[-1] - mean_dev) / std
    return out


def stochastic_k_series(candles: list[Candle], *, period: int = _STOCHASTIC_PERIOD) -> list[float | None]:
    """%K rapide (08/01, V3/V4 scalping variants) -- position du close actuel
    dans le range [plus bas, plus haut] des ``period`` dernières bougies, en
    pourcentage. <= 15 = survente, >= 85 = surachat (conventions classiques
    du Stochastique). ``None`` pendant le warmup ou si le range de la fenêtre
    est nul (aucune volatilité observée) -- jamais une division par zéro
    déguisée."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        window = candles[i - period + 1 : i + 1]
        lowest = min(c.low for c in window)
        highest = max(c.high for c in window)
        rng = highest - lowest
        if rng <= 0:
            continue
        out[i] = (candles[i].close - lowest) / rng * 100.0
    return out


def mfi_series(candles: list[Candle], *, period: int = 10) -> list[float | None]:
    """Money Flow Index (06/08, scalping_v9 -- operator spec, MFI length 10):
    volume-weighted RSI analogue. Typical price = (H+L+C)/3; raw money flow =
    typical price x volume; a candle whose typical price rose vs the previous
    one contributes positive flow, fell -> negative flow, unchanged ->
    neither (standard MFI convention). MFI = 100 - 100/(1 + pos/neg) over the
    trailing ``period`` window. <= 20 = oversold, >= 80 = overbought
    (operator's charted limits). ``None`` during warmup (needs ``period``
    DELTAS, i.e. period+1 candles) or when the window has zero total flow on
    both sides; a window with zero NEGATIVE flow reads 100.0 (pure inflow),
    zero POSITIVE flow reads 0.0 -- never a division by zero."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    typical = [(c.high + c.low + c.close) / 3.0 for c in candles]
    pos_flows: list[float] = [0.0] * n
    neg_flows: list[float] = [0.0] * n
    for i in range(1, n):
        flow = typical[i] * (candles[i].volume or 0.0)
        if typical[i] > typical[i - 1]:
            pos_flows[i] = flow
        elif typical[i] < typical[i - 1]:
            neg_flows[i] = flow
    for i in range(period, n):
        pos = sum(pos_flows[i - period + 1 : i + 1])
        neg = sum(neg_flows[i - period + 1 : i + 1])
        if pos <= 0 and neg <= 0:
            continue
        if neg <= 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + pos / neg)
    return out


def hammer_wick_ratio(candle: Candle) -> float | None:
    """Lower-wick ratio of a single candle (08/05, scalping_v8 + wick shadow
    filter): ``(min(open, close) - low) / (high - low)`` -- the share of the
    candle's total range sitting BELOW the body, i.e. how hard the low was
    rejected by real buyers. 0 = no lower wick at all (monolithic dump),
    1 = the whole candle is lower wick (pure hammer).

    Empirical basis (05/08 backtest, 58 real closed scalping/swing trades
    reconstructed candle-by-candle): entries whose signal candle had a ratio
    >= 0.3 won 60% (9W/6L) vs 25.6% (11W/32L) below it, Fisher exact
    p=0.026, consistent across pockets AND periods -- the only entry-side
    discriminator that survived confound checks that day (RVOL, volatility
    squeeze, pre-entry momentum, regime and weekday all failed them). Same
    formula as the LetItRide wick-detection study surfaced independently by
    the community-research workflow the same day.

    ``None`` on a zero-range candle (high == low) -- never a fabricated
    ratio, same doctrine as every other indicator here."""
    rng = candle.high - candle.low
    if rng <= 0:
        return None
    return (min(candle.open, candle.close) - candle.low) / rng
