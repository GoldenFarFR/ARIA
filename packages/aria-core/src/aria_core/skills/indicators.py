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


# 07/08 -- operator request ("toutes les fonctionnalités dispo sur TradingView"):
# the classic oscillators/trend gauges that were announced nowhere and absent
# from this module until now (confirmed by exhaustive grep before writing).
# Each follows the same doctrine as everything above: standard formula, and
# ``None`` for every position still inside the warm-up window -- never a
# partially-warmed estimate silently passed off as a real reading.
_ADX_PERIOD = 14
_CCI_PERIOD = 20
_WILLIAMS_R_PERIOD = 14
_ROC_PERIOD = 12
_OBV_SLOPE_PERIOD = 20
_STOCH_RSI_PERIOD = 14


def _true_ranges(candles: list[Candle]) -> list[float | None]:
    """True Range per candle -- ``None`` on the first (no previous close).
    Extracted so ATR and ADX share one definition, never two that could drift."""
    out: list[float | None] = [None]
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        out.append(max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - prev_close),
            abs(candles[i].low - prev_close),
        ))
    return out


def adx_series(candles: list[Candle], *, period: int = _ADX_PERIOD) -> list[float | None]:
    """ADX (Average Directional Index) -- TREND STRENGTH, direction-agnostic.
    Below 20 = range/chop, above 25 = a real trend is in place.

    Wilder's smoothing throughout (the standard), so the warm-up is
    ``2 * period`` candles: ``period`` for the smoothed DI values, then
    another ``period`` to average DX into ADX. ``None`` before that, and
    ``None`` whenever the smoothed true range is zero (perfectly flat
    window) -- never a division by zero dressed up as a value."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < 2 * period + 1:
        return out
    tr = _true_ranges(candles)
    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    for i in range(1, n):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    # Wilder seeding: a plain sum over the first `period` values, then the
    # smoothing recurrence (prev - prev/period + current).
    smooth_tr = sum(v for v in tr[1:period + 1] if v is not None)
    smooth_plus = sum(plus_dm[1:period + 1])
    smooth_minus = sum(minus_dm[1:period + 1])

    dx_values: list[tuple[int, float]] = []
    for i in range(period + 1, n):
        current_tr = tr[i] or 0.0
        smooth_tr = smooth_tr - smooth_tr / period + current_tr
        smooth_plus = smooth_plus - smooth_plus / period + plus_dm[i]
        smooth_minus = smooth_minus - smooth_minus / period + minus_dm[i]
        if smooth_tr <= 0:
            continue
        di_plus = 100.0 * smooth_plus / smooth_tr
        di_minus = 100.0 * smooth_minus / smooth_tr
        di_sum = di_plus + di_minus
        if di_sum <= 0:
            continue
        dx_values.append((i, 100.0 * abs(di_plus - di_minus) / di_sum))

    if len(dx_values) < period:
        return out
    adx = sum(value for _idx, value in dx_values[:period]) / period
    out[dx_values[period - 1][0]] = adx
    for idx, value in dx_values[period:]:
        adx = (adx * (period - 1) + value) / period
        out[idx] = adx
    return out


def cci_series(candles: list[Candle], *, period: int = _CCI_PERIOD) -> list[float | None]:
    """CCI (Commodity Channel Index) -- how far the typical price sits from
    its own moving average, in units of mean absolute deviation. Below -100 =
    oversold, above +100 = overbought (the classic thresholds).

    ``None`` during warm-up and whenever the mean deviation is zero (a
    perfectly flat window) -- never a division by zero."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    typical = [(c.high + c.low + c.close) / 3.0 for c in candles]
    for i in range(period - 1, n):
        window = typical[i - period + 1:i + 1]
        sma = sum(window) / period
        mean_dev = sum(abs(v - sma) for v in window) / period
        if mean_dev <= 0:
            continue
        out[i] = (typical[i] - sma) / (0.015 * mean_dev)
    return out


def williams_r_series(candles: list[Candle], *, period: int = _WILLIAMS_R_PERIOD) -> list[float | None]:
    """Williams %R -- where the close sits in the window's high/low range,
    expressed from -100 (at the low) to 0 (at the high). Below -80 =
    oversold, above -20 = overbought.

    The inverted mirror of Stochastic %K; kept as its own function because
    the conventional thresholds are the ones traders actually reason with.
    ``None`` during warm-up or on a zero-range window."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        window = candles[i - period + 1:i + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        span = highest - lowest
        if span <= 0:
            continue
        out[i] = -100.0 * (highest - candles[i].close) / span
    return out


def roc_series(closes: list[float], *, period: int = _ROC_PERIOD) -> list[float | None]:
    """Rate of Change -- percentage move over ``period`` candles. The
    simplest momentum gauge: positive = rising, negative = falling.

    ``None`` during warm-up or when the reference close is zero."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0:
        return out
    for i in range(period, n):
        reference = closes[i - period]
        if reference <= 0:
            continue
        out[i] = (closes[i] / reference - 1.0) * 100.0
    return out


def obv_series(candles: list[Candle]) -> list[float]:
    """On-Balance Volume -- running total that adds the candle's volume on an
    up close and subtracts it on a down close. Absolute value is meaningless
    across tokens (it depends on volume scale); the SLOPE is the signal,
    which is what ``obv_slope_series`` below exposes."""
    out: list[float] = [0.0]
    for i in range(1, len(candles)):
        if candles[i].close > candles[i - 1].close:
            out.append(out[-1] + candles[i].volume)
        elif candles[i].close < candles[i - 1].close:
            out.append(out[-1] - candles[i].volume)
        else:
            out.append(out[-1])
    return out


def obv_slope_series(candles: list[Candle], *, period: int = _OBV_SLOPE_PERIOD) -> list[float | None]:
    """OBV slope, normalized as a PERCENTAGE of the window's total traded
    volume -- comparable across tokens, unlike raw OBV.

    Positive = volume is accumulating on up moves (buying pressure),
    negative = distribution. ``None`` during warm-up or on a zero-volume
    window -- never a division by zero."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    obv = obv_series(candles)
    for i in range(period, n):
        window_volume = sum(c.volume for c in candles[i - period + 1:i + 1])
        if window_volume <= 0:
            continue
        out[i] = 100.0 * (obv[i] - obv[i - period]) / window_volume
    return out


def stoch_rsi_series(
    candles: list[Candle], *, period: int = _STOCH_RSI_PERIOD, smooth: int = 3,
) -> list[float | None]:
    """Stochastic RSI (%K) -- where the RSI sits within its OWN recent range,
    0 to 100. Reacts far earlier than plain RSI (it is an oscillator applied
    to an oscillator), which is why scalpers reach for it.

    ``smooth`` (07/08, default 3) is the standard %K smoothing every
    charting platform applies by default -- added after checking the raw
    version against what a trader actually sees: unsmoothed, a third of the
    readings pin to exactly 0 or 100, so a threshold copied off a chart
    ("stochrsi < 20") would fire far more often here than there. Pass
    ``smooth=1`` for the raw series.

    Warm-up is genuinely ``2 * period`` (+ the smoothing): the RSI itself
    needs ``period`` candles, then this needs ``period`` RSI VALUES to
    measure a range. ``None`` before that and on a flat RSI window -- never
    a fabricated reading."""
    from aria_core.skills.entry_signals import rsi_series

    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0:
        return out
    rsi = rsi_series([c.close for c in candles], period=period)
    raw: list[float | None] = [None] * n
    for i in range(n):
        window = rsi[max(0, i - period + 1):i + 1]
        if len(window) < period or any(v is None for v in window):
            continue
        highest = max(window)
        lowest = min(window)
        span = highest - lowest
        if span <= 0:
            continue
        # Clamped: the ratio is mathematically in [0,1] (rsi[i] belongs to
        # the window it is compared against), but float division lands on
        # 100.00000000000001 when the current RSI IS the window maximum.
        # A threshold comparison would still behave, but an indicator whose
        # documented range is 0-100 must never report 100.000000001.
        raw[i] = min(100.0, max(0.0, 100.0 * (rsi[i] - lowest) / span))
    if smooth <= 1:
        return raw
    for i in range(n):
        window = raw[max(0, i - smooth + 1):i + 1]
        if len(window) < smooth or any(v is None for v in window):
            continue
        out[i] = sum(window) / smooth  # type: ignore[arg-type]
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 07/08 -- third batch (operator: "ajoute les 50 plus utilisés déjà").
# The remaining classics needed to bring signal_conditions' catalogue to 50.
# Same doctrine throughout: standard formula, ``None`` during warm-up and on
# any degenerate window (zero range, zero volume, zero deviation) -- never a
# fabricated reading, never a division by zero dressed up as a value.
# ═══════════════════════════════════════════════════════════════════════════

_AROON_PERIOD = 25
_ULTIMATE_PERIODS = (7, 14, 28)
_CMF_PERIOD = 20
_KELTNER_PERIOD = 20
_DONCHIAN_PERIOD = 20
_CHOPPINESS_PERIOD = 14
_ULCER_PERIOD = 14


def sma_series(values: list[float], period: int) -> list[float | None]:
    """Simple moving average. ``None`` until ``period`` values exist."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, n):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def wma_series(values: list[float], period: int) -> list[float | None]:
    """Weighted moving average -- linear weights, the most recent candle
    weighing ``period`` times the oldest."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    denominator = period * (period + 1) / 2
    for i in range(period - 1, n):
        window = values[i - period + 1:i + 1]
        out[i] = sum(v * (j + 1) for j, v in enumerate(window)) / denominator
    return out


def hull_ma_series(values: list[float], period: int) -> list[float | None]:
    """Hull moving average -- WMA(2*WMA(n/2) - WMA(n), sqrt(n)). Much lower
    lag than a plain MA, which is why it is a scalping staple."""
    n = len(values)
    out: list[float | None] = [None] * n
    if period < 2 or n < period:
        return out
    half = max(1, period // 2)
    root = max(1, int(math.sqrt(period)))
    wma_half = wma_series(values, half)
    wma_full = wma_series(values, period)
    raw: list[float | None] = [
        None if (a is None or b is None) else 2.0 * a - b
        for a, b in zip(wma_half, wma_full)
    ]
    first = next((i for i, v in enumerate(raw) if v is not None), None)
    if first is None:
        return out
    smoothed = wma_series([v for v in raw[first:]], root)  # type: ignore[misc]
    for offset, value in enumerate(smoothed):
        out[first + offset] = value
    return out


def vwma_series(candles: list[Candle], period: int) -> list[float | None]:
    """Volume-weighted moving average -- the average price actually traded,
    unlike a plain SMA which weighs a dead candle like a busy one."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        window = candles[i - period + 1:i + 1]
        volume = sum(c.volume for c in window)
        if volume <= 0:
            continue
        out[i] = sum(c.close * c.volume for c in window) / volume
    return out


def _pct_gap(values: list[float], reference: list[float | None]) -> list[float | None]:
    """``(value / reference - 1) * 100`` -- the shared shape of every
    "distance to a moving average" indicator, so the formula exists once."""
    out: list[float | None] = []
    for value, ref in zip(values, reference):
        if ref is None or ref <= 0:
            out.append(None)
            continue
        out.append((value / ref - 1.0) * 100.0)
    return out


def sma_distance_series(candles: list[Candle], period: int) -> list[float | None]:
    closes = [c.close for c in candles]
    return _pct_gap(closes, sma_series(closes, period))


def wma_distance_series(candles: list[Candle], period: int) -> list[float | None]:
    closes = [c.close for c in candles]
    return _pct_gap(closes, wma_series(closes, period))


def hull_distance_series(candles: list[Candle], period: int) -> list[float | None]:
    closes = [c.close for c in candles]
    return _pct_gap(closes, hull_ma_series(closes, period))


def vwma_distance_series(candles: list[Candle], period: int) -> list[float | None]:
    return _pct_gap([c.close for c in candles], vwma_series(candles, period))


def supertrend_series(candles: list[Candle], *, period: int = 10, multiplier: float = 3.0) -> list[float | None]:
    """SuperTrend as a DIRECTION series: +1 = uptrend, -1 = downtrend.

    Standard ATR-banded trend follower. The bands "ratchet" (an upper band
    only moves down while price stays below it, and vice versa) -- that
    memory is the whole point of the indicator, so it is computed
    iteratively rather than per-window."""
    n = len(candles)
    out: list[float | None] = [None] * n
    atr = atr_series(candles, period=period)
    first = next((i for i, v in enumerate(atr) if v is not None), None)
    if first is None:
        return out
    upper = lower = None
    direction = 1
    for i in range(first, n):
        mid = (candles[i].high + candles[i].low) / 2.0
        band = multiplier * (atr[i] or 0.0)
        basic_upper = mid + band
        basic_lower = mid - band
        if upper is None:
            upper, lower = basic_upper, basic_lower
        else:
            upper = basic_upper if (basic_upper < upper or candles[i - 1].close > upper) else upper
            lower = basic_lower if (basic_lower > lower or candles[i - 1].close < lower) else lower
        if candles[i].close > upper:
            direction = 1
        elif candles[i].close < lower:
            direction = -1
        out[i] = float(direction)
    return out


def aroon_oscillator_series(candles: list[Candle], *, period: int = _AROON_PERIOD) -> list[float | None]:
    """Aroon Oscillator = Aroon Up - Aroon Down, from -100 to +100. Measures
    how RECENTLY the window's high/low were made: strongly positive means the
    high is fresh (uptrend), strongly negative the opposite."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        window = candles[i - period + 1:i + 1]
        highs = [c.high for c in window]
        lows = [c.low for c in window]
        since_high = period - 1 - highs.index(max(highs))
        since_low = period - 1 - lows.index(min(lows))
        aroon_up = 100.0 * (period - since_high) / period
        aroon_down = 100.0 * (period - since_low) / period
        out[i] = aroon_up - aroon_down
    return out


def trix_series(closes: list[float], *, period: int = 15) -> list[float | None]:
    """TRIX -- rate of change of a TRIPLE-smoothed EMA, in percent. The
    triple smoothing filters out the noise a raw ROC would react to."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0:
        return out
    ema1 = ema_series(closes, period)
    first1 = next((i for i, v in enumerate(ema1) if v is not None), None)
    if first1 is None:
        return out
    ema2_partial = ema_series([v for v in ema1[first1:]], period)  # type: ignore[misc]
    ema2: list[float | None] = [None] * first1 + list(ema2_partial)
    first2 = next((i for i, v in enumerate(ema2) if v is not None), None)
    if first2 is None:
        return out
    ema3_partial = ema_series([v for v in ema2[first2:]], period)  # type: ignore[misc]
    ema3: list[float | None] = [None] * first2 + list(ema3_partial)
    for i in range(1, n):
        prev, cur = ema3[i - 1], ema3[i]
        if prev is None or cur is None or prev <= 0:
            continue
        out[i] = (cur / prev - 1.0) * 100.0
    return out


def _directional_indicators(candles: list[Candle], period: int) -> tuple[list[float | None], list[float | None]]:
    """(DI+, DI-) -- the two directional components ADX averages. Shares
    ``_true_ranges`` with ADX so the two can never drift apart."""
    n = len(candles)
    plus_out: list[float | None] = [None] * n
    minus_out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return plus_out, minus_out
    tr = _true_ranges(candles)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, n):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    smooth_tr = sum(v for v in tr[1:period + 1] if v is not None)
    smooth_plus = sum(plus_dm[1:period + 1])
    smooth_minus = sum(minus_dm[1:period + 1])
    for i in range(period + 1, n):
        smooth_tr = smooth_tr - smooth_tr / period + (tr[i] or 0.0)
        smooth_plus = smooth_plus - smooth_plus / period + plus_dm[i]
        smooth_minus = smooth_minus - smooth_minus / period + minus_dm[i]
        if smooth_tr <= 0:
            continue
        plus_out[i] = 100.0 * smooth_plus / smooth_tr
        minus_out[i] = 100.0 * smooth_minus / smooth_tr
    return plus_out, minus_out


def di_plus_series(candles: list[Candle], *, period: int = _ADX_PERIOD) -> list[float | None]:
    return _directional_indicators(candles, period)[0]


def di_minus_series(candles: list[Candle], *, period: int = _ADX_PERIOD) -> list[float | None]:
    return _directional_indicators(candles, period)[1]


def vortex_series(candles: list[Candle], *, period: int = 14) -> list[float | None]:
    """Vortex Indicator as VI+ minus VI- -- positive = uptrend pressure.
    Built on the distance between each candle's extreme and the PREVIOUS
    candle's opposite extreme, which is what makes it react to reversals."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    tr = _true_ranges(candles)
    vm_plus = [0.0]
    vm_minus = [0.0]
    for i in range(1, n):
        vm_plus.append(abs(candles[i].high - candles[i - 1].low))
        vm_minus.append(abs(candles[i].low - candles[i - 1].high))
    for i in range(period, n):
        tr_sum = sum(v for v in tr[i - period + 1:i + 1] if v is not None)
        if tr_sum <= 0:
            continue
        out[i] = (sum(vm_plus[i - period + 1:i + 1]) - sum(vm_minus[i - period + 1:i + 1])) / tr_sum
    return out


def ppo_series(closes: list[float], *, period: int = _EMA_SLOW) -> list[float | None]:
    """Percentage Price Oscillator -- MACD expressed as a PERCENTAGE of the
    slow EMA, so thresholds are comparable across tokens (unlike raw MACD,
    which is a price difference)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    fast = max(2, round(period * 12 / 26))
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, period)
    for i in range(n):
        if ema_fast[i] is None or ema_slow[i] is None or ema_slow[i] <= 0:
            continue
        out[i] = 100.0 * (ema_fast[i] - ema_slow[i]) / ema_slow[i]
    return out


def awesome_oscillator_series(candles: list[Candle], *, period: int = 34) -> list[float | None]:
    """Awesome Oscillator -- SMA(5) minus SMA(``period``) of the median
    price, normalized here as a PERCENTAGE of the slow average so it is
    comparable across tokens (Bill Williams' original is absolute)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    median = [(c.high + c.low) / 2.0 for c in candles]
    fast = sma_series(median, 5)
    slow = sma_series(median, period)
    for i in range(n):
        if fast[i] is None or slow[i] is None or slow[i] <= 0:
            continue
        out[i] = 100.0 * (fast[i] - slow[i]) / slow[i]
    return out


def ultimate_oscillator_series(candles: list[Candle], *, period: int = 28) -> list[float | None]:
    """Ultimate Oscillator -- blends three lookbacks (period/4, period/2,
    period) so a single timeframe's noise cannot dominate. 0 to 100, below
    30 = oversold, above 70 = overbought."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period < 4 or n < period + 1:
        return out
    short, mid, long = max(1, period // 4), max(2, period // 2), period
    buying_pressure: list[float] = [0.0]
    true_range: list[float] = [0.0]
    for i in range(1, n):
        prev_close = candles[i - 1].close
        low = min(candles[i].low, prev_close)
        high = max(candles[i].high, prev_close)
        buying_pressure.append(candles[i].close - low)
        true_range.append(high - low)
    for i in range(long, n):
        averages = []
        for window, weight in ((short, 4.0), (mid, 2.0), (long, 1.0)):
            tr_sum = sum(true_range[i - window + 1:i + 1])
            if tr_sum <= 0:
                averages = []
                break
            averages.append(weight * sum(buying_pressure[i - window + 1:i + 1]) / tr_sum)
        if not averages:
            continue
        out[i] = 100.0 * sum(averages) / 7.0
    return out


def cmo_series(closes: list[float], *, period: int = 14) -> list[float | None]:
    """Chande Momentum Oscillator -- (gains - losses) / (gains + losses),
    from -100 to +100. Unlike RSI it does not smooth, so it reacts faster."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    for i in range(period, n):
        gains = losses = 0.0
        for j in range(i - period + 1, i + 1):
            change = closes[j] - closes[j - 1]
            if change > 0:
                gains += change
            else:
                losses -= change
        total = gains + losses
        if total <= 0:
            continue
        out[i] = 100.0 * (gains - losses) / total
    return out


def dpo_series(closes: list[float], *, period: int = 20) -> list[float | None]:
    """Detrended Price Oscillator -- price minus a SHIFTED moving average,
    as a percentage. Strips the trend out to expose the cycle underneath."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if period <= 0:
        return out
    shift = period // 2 + 1
    sma = sma_series(closes, period)
    for i in range(n):
        idx = i - shift
        if idx < 0 or sma[idx] is None or sma[idx] <= 0:
            continue
        out[i] = (closes[i] / sma[idx] - 1.0) * 100.0
    return out


def stochastic_d_series(candles: list[Candle], *, period: int = _STOCHASTIC_PERIOD, smooth: int = 3) -> list[float | None]:
    """Stochastic %D -- %K smoothed over ``smooth`` candles. The slower,
    less twitchy line traders actually act on."""
    k = stochastic_k_series(candles, period=period)
    n = len(k)
    out: list[float | None] = [None] * n
    for i in range(n):
        window = k[max(0, i - smooth + 1):i + 1]
        if len(window) < smooth or any(v is None for v in window):
            continue
        out[i] = sum(window) / smooth  # type: ignore[arg-type]
    return out


def momentum_series(closes: list[float], *, period: int = 10) -> list[float | None]:
    """Raw momentum as a percentage -- the same shape as ROC, kept separate
    because charting platforms expose both and traders configure them with
    different periods."""
    return roc_series(closes, period=period)


def bop_series(candles: list[Candle], *, period: int = 14) -> list[float | None]:
    """Balance of Power -- (close - open) / (high - low), averaged over
    ``period``. Measures who actually won each candle, buyers or sellers,
    independently of the size of the move."""
    n = len(candles)
    out: list[float | None] = [None] * n
    raw: list[float | None] = []
    for c in candles:
        span = c.high - c.low
        raw.append(None if span <= 0 else (c.close - c.open) / span)
    if period <= 0:
        return out
    for i in range(period - 1, n):
        window = raw[i - period + 1:i + 1]
        usable = [v for v in window if v is not None]
        if len(usable) < period:
            continue
        out[i] = sum(usable) / period
    return out


def cmf_series(candles: list[Candle], *, period: int = _CMF_PERIOD) -> list[float | None]:
    """Chaikin Money Flow -- volume weighted by where each candle closed
    within its own range, summed over ``period``. From -1 to +1: positive =
    accumulation, negative = distribution."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    money_flow_volume: list[float] = []
    for c in candles:
        span = c.high - c.low
        if span <= 0:
            money_flow_volume.append(0.0)
            continue
        multiplier = ((c.close - c.low) - (c.high - c.close)) / span
        money_flow_volume.append(multiplier * c.volume)
    for i in range(period - 1, n):
        volume = sum(c.volume for c in candles[i - period + 1:i + 1])
        if volume <= 0:
            continue
        out[i] = sum(money_flow_volume[i - period + 1:i + 1]) / volume
    return out


def ad_line_slope_series(candles: list[Candle], *, period: int = 20) -> list[float | None]:
    """Accumulation/Distribution line slope, normalized by the window's own
    traded volume (percent) -- comparable across tokens, unlike the raw
    running total."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    ad: list[float] = [0.0]
    for i in range(1, n):
        c = candles[i]
        span = c.high - c.low
        if span <= 0:
            ad.append(ad[-1])
            continue
        multiplier = ((c.close - c.low) - (c.high - c.close)) / span
        ad.append(ad[-1] + multiplier * c.volume)
    for i in range(period, n):
        volume = sum(c.volume for c in candles[i - period + 1:i + 1])
        if volume <= 0:
            continue
        out[i] = 100.0 * (ad[i] - ad[i - period]) / volume
    return out


def force_index_series(candles: list[Candle], *, period: int = 13) -> list[float | None]:
    """Force Index -- price change times volume, EMA-smoothed, expressed as
    a percentage of the window's average traded value so it is comparable
    across tokens."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n < 2 or period <= 0:
        return out
    raw = [0.0]
    for i in range(1, n):
        raw.append((candles[i].close - candles[i - 1].close) * candles[i].volume)
    smoothed = ema_series(raw, period)
    for i in range(n):
        if smoothed[i] is None:
            continue
        window = candles[max(0, i - period + 1):i + 1]
        traded_value = sum(c.close * c.volume for c in window)
        if traded_value <= 0:
            continue
        out[i] = 100.0 * smoothed[i] * len(window) / traded_value
    return out


def ease_of_movement_series(candles: list[Candle], *, period: int = 14) -> list[float | None]:
    """Ease of Movement -- how far price travelled per unit of volume.
    High positive = price rising on little volume (easy move up)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n < 2 or period <= 0:
        return out
    raw: list[float | None] = [None]
    for i in range(1, n):
        mid_now = (candles[i].high + candles[i].low) / 2.0
        mid_prev = (candles[i - 1].high + candles[i - 1].low) / 2.0
        span = candles[i].high - candles[i].low
        if candles[i].volume <= 0 or span <= 0 or mid_prev <= 0:
            raw.append(None)
            continue
        distance = (mid_now - mid_prev) / mid_prev * 100.0
        box_ratio = (candles[i].volume / 1e6) / span
        raw.append(distance / box_ratio if box_ratio > 0 else None)
    for i in range(period - 1, n):
        window = raw[i - period + 1:i + 1]
        usable = [v for v in window if v is not None]
        if len(usable) < period:
            continue
        out[i] = sum(usable) / period
    return out


def pvt_slope_series(candles: list[Candle], *, period: int = 20) -> list[float | None]:
    """Price Volume Trend slope, normalized by the window's volume (percent).
    Like OBV but weighted by the SIZE of each move, not just its sign."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    pvt: list[float] = [0.0]
    for i in range(1, n):
        prev_close = candles[i - 1].close
        if prev_close <= 0:
            pvt.append(pvt[-1])
            continue
        pvt.append(pvt[-1] + candles[i].volume * (candles[i].close - prev_close) / prev_close)
    for i in range(period, n):
        volume = sum(c.volume for c in candles[i - period + 1:i + 1])
        if volume <= 0:
            continue
        out[i] = 100.0 * (pvt[i] - pvt[i - period]) / volume
    return out


def relative_volume_series(candles: list[Candle], *, period: int = 20) -> list[float | None]:
    """Relative volume -- this candle's volume as a MULTIPLE of the recent
    average. 1 = normal, 3 = three times the usual activity."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    for i in range(period, n):
        window = candles[i - period:i]
        average = sum(c.volume for c in window) / period
        if average <= 0:
            continue
        out[i] = candles[i].volume / average
    return out


def keltner_position_series(candles: list[Candle], *, period: int = _KELTNER_PERIOD, multiplier: float = 2.0) -> list[float | None]:
    """Position within the Keltner channel: 0 = lower band, 1 = upper band
    (can leave [0,1] when price breaks out). The ATR-based cousin of %B."""
    n = len(candles)
    out: list[float | None] = [None] * n
    closes = [c.close for c in candles]
    ema = ema_series(closes, period)
    atr = atr_series(candles, period=period)
    for i in range(n):
        if ema[i] is None or atr[i] is None:
            continue
        band = multiplier * atr[i]
        if band <= 0:
            continue
        lower = ema[i] - band
        out[i] = (closes[i] - lower) / (2.0 * band)
    return out


def donchian_position_series(candles: list[Candle], *, period: int = _DONCHIAN_PERIOD) -> list[float | None]:
    """Position within the Donchian channel (the window's own high/low):
    0 = at the period low, 1 = at the period high. The basis of every
    breakout system."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        window = candles[i - period + 1:i + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        span = highest - lowest
        if span <= 0:
            continue
        out[i] = (candles[i].close - lowest) / span
    return out


def bollinger_width_series(closes: list[float], *, period: int = _BOLLINGER_PERIOD, num_std: float = _BOLLINGER_NUM_STD) -> list[float | None]:
    """Bollinger band WIDTH as a percentage of the middle band -- the
    "squeeze" gauge: a very low value means volatility has compressed and a
    breakout often follows."""
    middle, upper, lower = bollinger_bands(closes, period=period, num_std=num_std)
    out: list[float | None] = []
    for mid, up, low in zip(middle, upper, lower):
        if mid is None or up is None or low is None or mid <= 0:
            out.append(None)
            continue
        out.append(100.0 * (up - low) / mid)
    return out


def natr_series(candles: list[Candle], *, period: int = _ATR_PERIOD) -> list[float | None]:
    """Normalized ATR -- ATR as a percentage of the close. Identical intent
    to the ``atr`` condition already exposed; kept as its own function
    because it is a named indicator on every charting platform."""
    atr = atr_series(candles, period=period)
    out: list[float | None] = []
    for candle, value in zip(candles, atr):
        if value is None or candle.close <= 0:
            out.append(None)
            continue
        out.append(100.0 * value / candle.close)
    return out


def choppiness_series(candles: list[Candle], *, period: int = _CHOPPINESS_PERIOD) -> list[float | None]:
    """Choppiness Index, 0 to 100. Above ~61 = the market is ranging (a
    trend-following entry is likely to fail), below ~38 = a real trend."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 1 or n < period + 1:
        return out
    tr = _true_ranges(candles)
    log_period = math.log10(period)
    if log_period <= 0:
        return out
    for i in range(period, n):
        tr_sum = sum(v for v in tr[i - period + 1:i + 1] if v is not None)
        window = candles[i - period + 1:i + 1]
        span = max(c.high for c in window) - min(c.low for c in window)
        if tr_sum <= 0 or span <= 0:
            continue
        out[i] = 100.0 * math.log10(tr_sum / span) / log_period
    return out


def ulcer_index_series(candles: list[Candle], *, period: int = _ULCER_PERIOD) -> list[float | None]:
    """Ulcer Index -- root-mean-square of the drawdown from the window's
    running high, in percent. Measures DOWNSIDE volatility only, unlike ATR
    which treats an up-spike and a crash alike."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    closes = [c.close for c in candles]
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        peak = window[0]
        squares = []
        for value in window:
            peak = max(peak, value)
            if peak <= 0:
                continue
            squares.append((100.0 * (value - peak) / peak) ** 2)
        if not squares:
            continue
        out[i] = math.sqrt(sum(squares) / len(squares))
    return out


def fisher_transform_series(candles: list[Candle], *, period: int = 9) -> list[float | None]:
    """Fisher Transform -- maps price position into a near-Gaussian series,
    which makes extremes far sharper than on a raw oscillator. Turning
    points stand out; the absolute level matters less."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    median = [(c.high + c.low) / 2.0 for c in candles]
    value = 0.0
    for i in range(period - 1, n):
        window = median[i - period + 1:i + 1]
        highest, lowest = max(window), min(window)
        span = highest - lowest
        if span <= 0:
            continue
        raw = 2.0 * ((median[i] - lowest) / span - 0.5)
        value = 0.66 * raw + 0.67 * value
        value = min(0.999, max(-0.999, value))
        out[i] = 0.5 * math.log((1.0 + value) / (1.0 - value))
    return out


def body_ratio_series(candles: list[Candle], period: int = 1) -> list[float | None]:
    """Candle body as a share of its total range -- 1 = a full marubozu
    (pure conviction), near 0 = a doji (indecision). ``period`` unused,
    single-candle shape kept in the uniform signature."""
    out: list[float | None] = []
    for c in candles:
        span = c.high - c.low
        out.append(None if span <= 0 else abs(c.close - c.open) / span)
    return out


def upper_wick_ratio_series(candles: list[Candle], period: int = 1) -> list[float | None]:
    """Upper-wick share of the candle range -- the mirror of
    ``hammer_wick_ratio``: how hard the high was rejected by sellers."""
    out: list[float | None] = []
    for c in candles:
        span = c.high - c.low
        out.append(None if span <= 0 else (c.high - max(c.open, c.close)) / span)
    return out


def close_position_series(candles: list[Candle], period: int = 1) -> list[float | None]:
    """Where the candle CLOSED within its own range: 0 = on the low,
    1 = on the high. The simplest read of who held control at the bell."""
    out: list[float | None] = []
    for c in candles:
        span = c.high - c.low
        out.append(None if span <= 0 else (c.close - c.low) / span)
    return out
