"""Configurable per-token entry conditions (07/08, operator request).

Until now scalping_v9's entry was hard-wired to "RSI AND MFI both below
their limits on the same closed candle" (``_both_below``). The operator
asked for a real choice of indicator per token ("toutes les fonctionnalités
dispo sur TradingView") -- this module is the seam that makes that possible
without each pocket re-implementing indicator plumbing.

A CONDITION is one indicator compared to one threshold: ``rsi(18)<21``.
A SPEC is several conditions that must ALL hold on the SAME closed candle
-- v9's founding doctrine ("quand les deux en même temps le sont", 06/08
operator spec) generalized from exactly two indicators to any number of
them. A single-condition spec is legal and simply means "this one signal
alone triggers".

Only indicators that REALLY exist in ``skills/indicators.py`` are exposed
(``INDICATORS`` below is built from those functions, never a wish list) --
offering a name the engine cannot actually compute would be the same class
of silent lie as the timeframe table that produced the cbXRP bug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from aria_core.skills import entry_signals, indicators  # noqa: F401 (entry_signals used dynamically)
from aria_core.skills.ta_levels import Candle

# Operators a condition may use. Deliberately just these two: an entry
# signal is always "this measure crossed below/above a level" -- anything
# needing equality on a float would be a bug, not a strategy.
_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "<": lambda value, threshold: value < threshold,
    ">": lambda value, threshold: value > threshold,
}


@dataclass(frozen=True)
class IndicatorSpec:
    """One indicator the engine can actually compute.

    ``series`` takes the candle list plus a period and returns a value per
    candle (``None`` during warm-up -- never a partially-warmed number).
    ``period_bounds``/``threshold_bounds`` are validated at parse time so a
    typo is refused with a readable reason instead of silently producing a
    series of ``None`` that reads as "no signal" forever.
    """

    key: str
    label: str
    series: Callable[[list[Candle], int], list[float | None]]
    default_period: int
    period_bounds: tuple[int, int]
    threshold_bounds: tuple[float, float]
    default_threshold: float
    default_operator: str
    unit: str = ""
    # Candles needed on top of `period` before the value stabilizes. Most
    # indicators are ready at `period`; vwapz needs 2x (see its own docstring
    # in indicators.py -- the rolling VWAP must itself stabilize first).
    warmup_multiplier: int = 1
    # 07/08 -- set when this indicator's SCALE deliberately differs from what
    # a charting platform displays, so a threshold read off a chart cannot be
    # copied across as-is. Empty means "same scale, a chart threshold
    # transfers directly". Surfaced in the template and in /v9indics: an
    # operator must never discover this by watching a pocket misbehave.
    scale_note: str = ""


def _rsi(candles: list[Candle], period: int) -> list[float | None]:
    # Resolved through the MODULE (never a captured `from ... import`) so a
    # test monkeypatching entry_signals.rsi_series actually takes effect --
    # every existing v9 test injects its series that way.
    return entry_signals.rsi_series([c.close for c in candles], period=period)


def _mfi(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.mfi_series(candles, period=period)


def _stoch(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.stochastic_k_series(candles, period=period)


def _percent_b(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.bollinger_percent_b([c.close for c in candles], period=period)


def _vwap_z(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.vwap_zscore_series(candles, period=period)


def _macd_hist(candles: list[Candle], period: int) -> list[float | None]:
    """MACD histogram. ``period`` is the SLOW EMA length; fast and signal
    keep their standard 12/26/9 proportions relative to it (fast = slow*12/26
    rounded, signal = 9) so a single number stays enough to configure it --
    the same one-number-per-indicator shape as every other condition here."""
    closes = [c.close for c in candles]
    fast = max(2, round(period * 12 / 26))
    _line, _signal, histogram = indicators.macd_series(
        closes, fast=fast, slow=period, signal=9,
    )
    return histogram


def _price_vs_ema(candles: list[Candle], period: int) -> list[float | None]:
    """Close as a PERCENTAGE of its EMA, minus 100 -- so the threshold is
    readable as "price is N% below/above its moving average" rather than an
    absolute price that would differ per token."""
    closes = [c.close for c in candles]
    ema = indicators.ema_series(closes, period)
    out: list[float | None] = []
    for close, avg in zip(closes, ema):
        if avg is None or avg <= 0:
            out.append(None)
            continue
        out.append((close / avg - 1.0) * 100.0)
    return out


def _wick(candles: list[Candle], period: int) -> list[float | None]:
    """Lower-wick ratio of each candle (``period`` unused -- it is a
    single-candle shape, kept in the same signature for uniformity)."""
    return [indicators.hammer_wick_ratio(c) for c in candles]


def _atr_pct(candles: list[Candle], period: int) -> list[float | None]:
    """ATR as a PERCENTAGE of the close -- comparable across tokens, unlike
    the raw absolute ATR (which is a price and means nothing on its own)."""
    atr = indicators.atr_series(candles, period=period)
    out: list[float | None] = []
    for candle, value in zip(candles, atr):
        if value is None or candle.close <= 0:
            out.append(None)
            continue
        out.append(value / candle.close * 100.0)
    return out


def _vwap_dist(candles: list[Candle], period: int) -> list[float | None]:
    """Distance from the rolling VWAP, in percent. Distinct from ``vwapz``:
    this is the raw gap, that one is the gap normalized by its own recent
    standard deviation."""
    vwap = indicators.vwap_series(candles, period=period)
    out: list[float | None] = []
    for candle, value in zip(candles, vwap):
        if value is None or value <= 0:
            out.append(None)
            continue
        out.append((candle.close / value - 1.0) * 100.0)
    return out


def _adx(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.adx_series(candles, period=period)


def _cci(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.cci_series(candles, period=period)


def _williams_r(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.williams_r_series(candles, period=period)


def _roc(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.roc_series([c.close for c in candles], period=period)


def _obv_slope(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.obv_slope_series(candles, period=period)


def _stoch_rsi(candles: list[Candle], period: int) -> list[float | None]:
    return indicators.stoch_rsi_series(candles, period=period)


def _bull_divergence(candles: list[Candle], period: int) -> list[float | None]:
    """Bullish RSI divergence as a 0/1 series so it fits the same
    "value vs threshold" shape as every other condition (use ``>0.5``).

    Evaluated on the window ENDING at each candle -- the same detector v8
    uses, applied point by point rather than once on the whole list, so a
    divergence present 40 candles ago never reads as present today.
    ``period`` is the lookback window. ``None`` before the window is full,
    never a False that would read as "checked and absent"."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 1:
        return out
    for idx in range(period - 1, n):
        window = candles[idx - period + 1:idx + 1]
        present, _reason = entry_signals.bullish_rsi_divergence(window)
        out[idx] = 1.0 if present else 0.0
    return out


def _golden_pocket(candles: list[Candle], period: int) -> list[float | None]:
    """1 when the close sits inside the 0.618-0.786 Fibonacci retracement of
    the window's own low-to-high leg, 0 otherwise (use ``>0.5``).

    Uses the same naive-extremes ``fibonacci_zone`` the rest of the codebase
    exposes -- with its documented caveat: it measures the extremes of the
    window it is handed, it does not detect a real swing leg."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if period <= 1:
        return out
    for idx in range(period - 1, n):
        window = candles[idx - period + 1:idx + 1]
        zone = entry_signals.fibonacci_zone(window)
        if not zone:
            continue
        low = zone.get("gp_low")
        high = zone.get("gp_high")
        if low is None or high is None:
            continue
        close = candles[idx].close
        out[idx] = 1.0 if low <= close <= high else 0.0
    return out


# ── third batch adapters (07/08): the 32 remaining classics ─────────────────
# Each is a thin (candles, period) shim over its indicators.py function, so
# the registry below stays one uniform shape regardless of whether the real
# function takes candles or closes.

def _closes(candles: list[Candle]) -> list[float]:
    return [c.close for c in candles]


def _smadist(c, p): return indicators.sma_distance_series(c, p)
def _wmadist(c, p): return indicators.wma_distance_series(c, p)
def _hulldist(c, p): return indicators.hull_distance_series(c, p)
def _vwmadist(c, p): return indicators.vwma_distance_series(c, p)
def _supertrend(c, p): return indicators.supertrend_series(c, period=p)
def _aroon(c, p): return indicators.aroon_oscillator_series(c, period=p)
def _trix(c, p): return indicators.trix_series(_closes(c), period=p)
def _diplus(c, p): return indicators.di_plus_series(c, period=p)
def _diminus(c, p): return indicators.di_minus_series(c, period=p)
def _vortex(c, p): return indicators.vortex_series(c, period=p)
def _ppo(c, p): return indicators.ppo_series(_closes(c), period=p)
def _awesome(c, p): return indicators.awesome_oscillator_series(c, period=p)
def _ultimate(c, p): return indicators.ultimate_oscillator_series(c, period=p)
def _cmo(c, p): return indicators.cmo_series(_closes(c), period=p)
def _dpo(c, p): return indicators.dpo_series(_closes(c), period=p)
def _stochd(c, p): return indicators.stochastic_d_series(c, period=p)
def _momentum(c, p): return indicators.momentum_series(_closes(c), period=p)
def _bop(c, p): return indicators.bop_series(c, period=p)
def _fisher(c, p): return indicators.fisher_transform_series(c, period=p)
def _cmf(c, p): return indicators.cmf_series(c, period=p)
def _adslope(c, p): return indicators.ad_line_slope_series(c, period=p)
def _forceindex(c, p): return indicators.force_index_series(c, period=p)
def _eom(c, p): return indicators.ease_of_movement_series(c, period=p)
def _pvtslope(c, p): return indicators.pvt_slope_series(c, period=p)
def _rvol(c, p): return indicators.relative_volume_series(c, period=p)
def _keltner(c, p): return indicators.keltner_position_series(c, period=p)
def _donchian(c, p): return indicators.donchian_position_series(c, period=p)
def _bbwidth(c, p): return indicators.bollinger_width_series(_closes(c), period=p)
def _natr(c, p): return indicators.natr_series(c, period=p)
def _choppiness(c, p): return indicators.choppiness_series(c, period=p)
def _ulcer(c, p): return indicators.ulcer_index_series(c, period=p)
def _bodyratio(c, p): return indicators.body_ratio_series(c, p)
def _upperwick(c, p): return indicators.upper_wick_ratio_series(c, p)
def _closepos(c, p): return indicators.close_position_series(c, p)


INDICATORS: dict[str, IndicatorSpec] = {
    spec.key: spec
    for spec in (
        IndicatorSpec(
            key="rsi", label="RSI (force du mouvement)", series=_rsi,
            default_period=18, period_bounds=(2, 100),
            threshold_bounds=(1.0, 99.0), default_threshold=21.0,
            default_operator="<",
        ),
        IndicatorSpec(
            key="mfi", label="MFI (RSI pondéré par le volume)", series=_mfi,
            default_period=10, period_bounds=(2, 100),
            threshold_bounds=(1.0, 99.0), default_threshold=20.0,
            default_operator="<",
        ),
        IndicatorSpec(
            key="stoch", label="Stochastique %K", series=_stoch,
            default_period=14, period_bounds=(2, 100),
            threshold_bounds=(1.0, 99.0), default_threshold=15.0,
            default_operator="<",
        ),
        IndicatorSpec(
            key="pctb", label="%B (position dans les bandes de Bollinger)",
            series=_percent_b, default_period=20, period_bounds=(2, 100),
            # %B leaves [0,1] whenever price exits the channel -- the bounds
            # are deliberately wider than the "textbook" range.
            threshold_bounds=(-2.0, 3.0), default_threshold=0.05,
            default_operator="<",
        ),
        IndicatorSpec(
            key="vwapz", label="Écart au VWAP (en écarts-types)",
            series=_vwap_z, default_period=20, period_bounds=(2, 100),
            threshold_bounds=(-6.0, 6.0), default_threshold=-2.5,
            default_operator="<", unit="σ", warmup_multiplier=2,
        ),
        IndicatorSpec(
            key="macdhist", label="Histogramme MACD", series=_macd_hist,
            default_period=26, period_bounds=(4, 200),
            # An absolute price-scale value: bounds are intentionally loose,
            # the useful thresholds are near zero (crossing sign).
            threshold_bounds=(-1e9, 1e9), default_threshold=0.0,
            default_operator=">",
            scale_note="un seul paramètre : la période lente ; rapide et signal gardent les proportions 12/26/9",
        ),
        IndicatorSpec(
            key="emadist", label="Écart du prix à son EMA (%)",
            series=_price_vs_ema, default_period=20, period_bounds=(2, 200),
            threshold_bounds=(-99.0, 500.0), default_threshold=-3.0,
            default_operator="<", unit="%",
        ),
        IndicatorSpec(
            key="wick", label="Mèche basse de la bougie (ratio)",
            series=_wick, default_period=1, period_bounds=(1, 1),
            threshold_bounds=(0.0, 1.0), default_threshold=0.30,
            default_operator=">",
        ),
        # 07/08 -- second batch (operator: "où sont les autres fonctions ?").
        # Five were already implemented but never exposed (atr/vwap/bullish
        # divergence/golden pocket), six were genuinely missing from the
        # codebase and are newly written in indicators.py (adx/cci/willr/roc/
        # obvslope/stochrsi).
        IndicatorSpec(
            key="atr", label="ATR — volatilité de la bougie (%)",
            series=_atr_pct, default_period=14, period_bounds=(2, 100),
            threshold_bounds=(0.0, 100.0), default_threshold=3.0,
            default_operator=">", unit="%",
        ),
        IndicatorSpec(
            key="vwapdist", label="Écart brut au VWAP (%)",
            series=_vwap_dist, default_period=20, period_bounds=(2, 200),
            threshold_bounds=(-99.0, 500.0), default_threshold=-2.0,
            default_operator="<", unit="%",
        ),
        IndicatorSpec(
            key="adx", label="ADX — force de la tendance",
            series=_adx, default_period=14, period_bounds=(2, 100),
            threshold_bounds=(0.0, 100.0), default_threshold=25.0,
            default_operator=">", warmup_multiplier=2,
        ),
        IndicatorSpec(
            key="cci", label="CCI (Commodity Channel Index)",
            series=_cci, default_period=20, period_bounds=(2, 200),
            threshold_bounds=(-500.0, 500.0), default_threshold=-100.0,
            default_operator="<",
        ),
        IndicatorSpec(
            key="willr", label="Williams %R", series=_williams_r,
            default_period=14, period_bounds=(2, 100),
            threshold_bounds=(-100.0, 0.0), default_threshold=-80.0,
            default_operator="<",
        ),
        IndicatorSpec(
            key="roc", label="ROC — variation sur N bougies (%)",
            series=_roc, default_period=12, period_bounds=(1, 200),
            threshold_bounds=(-99.0, 500.0), default_threshold=-5.0,
            default_operator="<", unit="%",
        ),
        IndicatorSpec(
            key="obvslope", label="Pente OBV — pression acheteuse (%)",
            series=_obv_slope, default_period=20, period_bounds=(2, 200),
            threshold_bounds=(-100.0, 100.0), default_threshold=10.0,
            default_operator=">", unit="%",
            scale_note="PENTE normalisée sur la fenêtre (TradingView affiche l'OBV cumulé brut)",
        ),
        IndicatorSpec(
            key="stochrsi", label="Stochastic RSI", series=_stoch_rsi,
            default_period=14, period_bounds=(2, 100),
            threshold_bounds=(0.0, 100.0), default_threshold=20.0,
            default_operator="<", warmup_multiplier=2,
        ),
        IndicatorSpec(
            key="divergence", label="Divergence RSI haussière (1 = présente)",
            series=_bull_divergence, default_period=25, period_bounds=(5, 200),
            threshold_bounds=(0.0, 1.0), default_threshold=0.5,
            default_operator=">",
        ),
        IndicatorSpec(
            key="goldenpocket", label="Prix dans la poche dorée Fibonacci (1 = oui)",
            series=_golden_pocket, default_period=25, period_bounds=(5, 200),
            threshold_bounds=(0.0, 1.0), default_threshold=0.5,
            default_operator=">",
        ),
        # ── Moyennes mobiles / tendance ──
        IndicatorSpec(key="smadist", label="Écart du prix à sa SMA (%)", series=_smadist,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-99.0, 500.0),
            default_threshold=-3.0, default_operator="<", unit="%"),
        IndicatorSpec(key="wmadist", label="Écart du prix à sa WMA (%)", series=_wmadist,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-99.0, 500.0),
            default_threshold=-3.0, default_operator="<", unit="%"),
        IndicatorSpec(key="hulldist", label="Écart du prix à sa Hull MA (%)", series=_hulldist,
            default_period=20, period_bounds=(4, 200), threshold_bounds=(-99.0, 500.0),
            default_threshold=-3.0, default_operator="<", unit="%"),
        IndicatorSpec(key="vwmadist", label="Écart du prix à sa VWMA (%)", series=_vwmadist,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-99.0, 500.0),
            default_threshold=-3.0, default_operator="<", unit="%"),
        IndicatorSpec(key="supertrend", label="SuperTrend (1 = haussier, -1 = baissier)",
            series=_supertrend, default_period=10, period_bounds=(2, 100),
            threshold_bounds=(-1.0, 1.0), default_threshold=0.0, default_operator=">",
            scale_note="renvoie seulement la DIRECTION (+1/-1), pas le niveau de la bande"),
        IndicatorSpec(key="aroon", label="Aroon Oscillator", series=_aroon,
            default_period=25, period_bounds=(2, 200), threshold_bounds=(-100.0, 100.0),
            default_threshold=50.0, default_operator=">",
            scale_note="oscillateur Up moins Down (TradingView affiche les deux lignes séparément)"),
        IndicatorSpec(key="trix", label="TRIX (%)", series=_trix,
            default_period=15, period_bounds=(2, 100), threshold_bounds=(-50.0, 50.0),
            default_threshold=0.0, default_operator=">", unit="%", warmup_multiplier=3),
        IndicatorSpec(key="diplus", label="DI+ (pression haussière)", series=_diplus,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(0.0, 100.0),
            default_threshold=25.0, default_operator=">"),
        IndicatorSpec(key="diminus", label="DI- (pression baissière)", series=_diminus,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(0.0, 100.0),
            default_threshold=25.0, default_operator="<"),
        IndicatorSpec(key="vortex", label="Vortex (VI+ moins VI-)", series=_vortex,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(-3.0, 3.0),
            default_threshold=0.0, default_operator=">",
            scale_note="VI+ moins VI- (TradingView affiche les deux lignes séparément)"),
        IndicatorSpec(key="ppo", label="PPO — MACD en pourcentage", series=_ppo,
            default_period=26, period_bounds=(4, 200), threshold_bounds=(-100.0, 100.0),
            default_threshold=0.0, default_operator=">", unit="%"),
        # ── Oscillateurs ──
        IndicatorSpec(key="awesome", label="Awesome Oscillator (%)", series=_awesome,
            default_period=34, period_bounds=(6, 200), threshold_bounds=(-100.0, 100.0),
            default_threshold=0.0, default_operator=">", unit="%",
            scale_note="normalisé en % de la moyenne lente (TradingView l'affiche en valeur absolue de prix)"),
        IndicatorSpec(key="ultimate", label="Ultimate Oscillator", series=_ultimate,
            default_period=28, period_bounds=(4, 200), threshold_bounds=(0.0, 100.0),
            default_threshold=30.0, default_operator="<",
            scale_note="un seul paramètre : la période longue ; les deux autres valent période/4 et période/2"),
        IndicatorSpec(key="cmo", label="Chande Momentum Oscillator", series=_cmo,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(-100.0, 100.0),
            default_threshold=-50.0, default_operator="<"),
        IndicatorSpec(key="dpo", label="Detrended Price Oscillator (%)", series=_dpo,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-99.0, 500.0),
            default_threshold=-3.0, default_operator="<", unit="%"),
        IndicatorSpec(key="stochd", label="Stochastique %D (lissé)", series=_stochd,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(0.0, 100.0),
            default_threshold=20.0, default_operator="<"),
        IndicatorSpec(key="momentum", label="Momentum (%)", series=_momentum,
            default_period=10, period_bounds=(1, 200), threshold_bounds=(-99.0, 500.0),
            default_threshold=-5.0, default_operator="<", unit="%"),
        IndicatorSpec(key="bop", label="Balance of Power", series=_bop,
            default_period=14, period_bounds=(1, 100), threshold_bounds=(-1.0, 1.0),
            default_threshold=0.0, default_operator=">"),
        IndicatorSpec(key="fisher", label="Fisher Transform", series=_fisher,
            default_period=9, period_bounds=(2, 100), threshold_bounds=(-6.0, 6.0),
            default_threshold=-1.5, default_operator="<"),
        # ── Volume ──
        IndicatorSpec(key="cmf", label="Chaikin Money Flow", series=_cmf,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-1.0, 1.0),
            default_threshold=0.05, default_operator=">"),
        IndicatorSpec(key="adslope", label="Pente Accumulation/Distribution (%)", series=_adslope,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-100.0, 100.0),
            default_threshold=10.0, default_operator=">", unit="%",
            scale_note="PENTE normalisée sur la fenêtre (TradingView affiche la ligne A/D cumulée)"),
        IndicatorSpec(key="forceindex", label="Force Index (%)", series=_forceindex,
            default_period=13, period_bounds=(2, 100), threshold_bounds=(-100.0, 100.0),
            default_threshold=0.0, default_operator=">", unit="%",
            scale_note="normalisé en % de la valeur échangée (TradingView l'affiche en absolu)"),
        IndicatorSpec(key="eom", label="Ease of Movement", series=_eom,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(-100000.0, 100000.0),
            default_threshold=0.0, default_operator=">",
            scale_note="échelle propre à ARIA (le facteur de volume diffère de TradingView)"),
        IndicatorSpec(key="pvtslope", label="Pente Price Volume Trend (%)", series=_pvtslope,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-100.0, 100.0),
            default_threshold=5.0, default_operator=">", unit="%",
            scale_note="PENTE normalisée sur la fenêtre (TradingView affiche le PVT cumulé)"),
        IndicatorSpec(key="rvol", label="Volume relatif (multiple de la moyenne)", series=_rvol,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(0.0, 100.0),
            default_threshold=2.0, default_operator=">"),
        # ── Volatilité / canaux ──
        IndicatorSpec(key="keltner", label="Position dans le canal de Keltner", series=_keltner,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(-3.0, 4.0),
            default_threshold=0.1, default_operator="<"),
        IndicatorSpec(key="donchian", label="Position dans le canal de Donchian", series=_donchian,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(0.0, 1.0),
            default_threshold=0.1, default_operator="<"),
        IndicatorSpec(key="bbwidth", label="Largeur des bandes de Bollinger (%)", series=_bbwidth,
            default_period=20, period_bounds=(2, 200), threshold_bounds=(0.0, 500.0),
            default_threshold=5.0, default_operator="<", unit="%"),
        IndicatorSpec(key="natr", label="ATR normalisé (%)", series=_natr,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(0.0, 100.0),
            default_threshold=3.0, default_operator=">", unit="%"),
        IndicatorSpec(key="choppiness", label="Choppiness — marché en range", series=_choppiness,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(0.0, 100.0),
            default_threshold=38.0, default_operator="<"),
        IndicatorSpec(key="ulcer", label="Ulcer Index — volatilité baissière", series=_ulcer,
            default_period=14, period_bounds=(2, 100), threshold_bounds=(0.0, 100.0),
            default_threshold=5.0, default_operator="<"),
        # ── Forme de bougie ──
        IndicatorSpec(key="bodyratio", label="Corps de la bougie (ratio)", series=_bodyratio,
            default_period=1, period_bounds=(1, 1), threshold_bounds=(0.0, 1.0),
            default_threshold=0.6, default_operator=">"),
        IndicatorSpec(key="upperwick", label="Mèche haute de la bougie (ratio)", series=_upperwick,
            default_period=1, period_bounds=(1, 1), threshold_bounds=(0.0, 1.0),
            default_threshold=0.3, default_operator=">"),
        IndicatorSpec(key="closepos", label="Position du close dans la bougie", series=_closepos,
            default_period=1, period_bounds=(1, 1), threshold_bounds=(0.0, 1.0),
            default_threshold=0.7, default_operator=">"),
    )
}


@dataclass(frozen=True)
class Condition:
    indicator: str
    period: int
    operator: str
    threshold: float

    def format(self) -> str:
        """Canonical text form -- what round-trips through parse()."""
        threshold = f"{self.threshold:g}"
        if self.indicator == "wick":
            return f"wick{self.operator}{threshold}"
        return f"{self.indicator}({self.period}){self.operator}{threshold}"

    def label(self) -> str:
        spec = INDICATORS[self.indicator]
        if self.indicator == "wick":
            return f"{spec.label} {self.operator} {self.threshold:g}"
        return f"{spec.label} sur {self.period} bougies {self.operator} {self.threshold:g}{spec.unit}"


DEFAULT_SPEC = "rsi(18)<21,mfi(10)<20"

# rsi(18)<21 / wick>0.3 / macdhist(26)>0 -- period optional (defaults apply).
_CONDITION_RE = re.compile(
    r"^([a-z]+)"           # indicator key
    r"(?:\((\d+)\))?"      # optional (period)
    r"\s*([<>])\s*"        # operator
    r"(-?\d+(?:\.\d+)?)$"  # threshold
)


def parse(text: str) -> tuple[list[Condition], str]:
    """``"rsi(18)<21,mfi(10)<20"`` -> ``([Condition, ...], "")``.

    Returns ``([], reason)`` on any problem -- an unknown indicator, an
    out-of-bounds period/threshold, or an empty spec. Never raises and never
    silently drops a condition: a spec that half-parses would open positions
    on weaker criteria than the operator configured.
    """
    raw = (text or "").strip().lower()
    if not raw:
        return [], "aucune condition fournie"
    conditions: list[Condition] = []
    seen: set[str] = set()
    for chunk in raw.split(","):
        piece = chunk.strip().replace(" ", "")
        if not piece:
            continue
        match = _CONDITION_RE.match(piece)
        if not match:
            return [], f"condition illisible : {chunk.strip()}"
        key, period_s, operator, threshold_s = match.groups()
        spec = INDICATORS.get(key)
        if spec is None:
            known = "/".join(sorted(INDICATORS))
            return [], f"indicateur inconnu : {key} (connus : {known})"
        if key in seen:
            return [], f"indicateur en double : {key}"
        seen.add(key)
        period = int(period_s) if period_s else spec.default_period
        low, high = spec.period_bounds
        if not (low <= period <= high):
            return [], f"{key} : période {period} hors bornes [{low}-{high}]"
        threshold = float(threshold_s)
        t_low, t_high = spec.threshold_bounds
        if not (t_low <= threshold <= t_high):
            return [], f"{key} : seuil {threshold:g} hors bornes [{t_low:g}-{t_high:g}]"
        if operator not in _OPERATORS:
            return [], f"opérateur inconnu : {operator}"
        conditions.append(Condition(key, period, operator, threshold))
    if not conditions:
        return [], "aucune condition fournie"
    return conditions, ""


def format_spec(conditions: list[Condition]) -> str:
    return ",".join(c.format() for c in conditions)


def describe(conditions: list[Condition]) -> str:
    """Human-readable summary for a thesis line or a Telegram reply."""
    return " ET ".join(c.label() for c in conditions)


def min_candles(conditions: list[Condition]) -> int:
    """Candles needed before EVERY condition can produce a real value --
    the longest warm-up across the spec, plus one so a transition (value
    now vs value on the previous candle) is computable at all."""
    if not conditions:
        return 0
    longest = max(
        c.period * INDICATORS[c.indicator].warmup_multiplier for c in conditions
    )
    return longest + 1


def evaluate(conditions: list[Condition], candles: list[Candle]) -> list[bool | None]:
    """Per-candle verdict: True = every condition holds, False = at least one
    does not, ``None`` = at least one is still warming up (and none has
    already answered False).

    The three-state return is deliberate, matching the existing
    ``_both_below`` contract v9 relies on: a warming-up indicator must never
    read as "condition not met" (that would let a transition fire the moment
    warm-up ends, on a candle nobody actually evaluated), and must never read
    as met either.
    """
    if not conditions or not candles:
        return [None] * len(candles)
    series_by_condition = [
        INDICATORS[c.indicator].series(candles, c.period) for c in conditions
    ]
    out: list[bool | None] = []
    for idx in range(len(candles)):
        # Warm-up ALWAYS wins over a failed condition -- never short-circuit
        # on the first False. Caught by an equivalence test against the old
        # hard-wired _both_below: with RSI(18) still warming and MFI(10)
        # already computable-and-false, short-circuiting returned False,
        # so the moment RSI warmed up and the spec became True the sequence
        # read False -> True and fired a transition on a candle that was
        # never actually evaluated. A real phantom-buy bug.
        warming = False
        failed = False
        for condition, series in zip(conditions, series_by_condition):
            value = series[idx] if idx < len(series) else None
            if value is None:
                warming = True
                continue
            if not _OPERATORS[condition.operator](value, condition.threshold):
                failed = True
        out.append(None if warming else (False if failed else True))
    return out


def current_values(conditions: list[Condition], candles: list[Candle]) -> dict[str, float | None]:
    """Last computed value per indicator -- for logging/thesis, never for the
    decision itself (which goes through ``evaluate``)."""
    values: dict[str, float | None] = {}
    for condition in conditions:
        series = INDICATORS[condition.indicator].series(candles, condition.period)
        values[condition.indicator] = series[-1] if series else None
    return values


def as_template_indicators() -> list[dict[str, object]]:
    """Every indicator the operator template may offer in its dropdown, with
    its real bounds -- so the HTML never hard-codes a list that could drift
    from what the engine can actually compute (same doctrine as
    ``pocket_spec.as_template_timeframes``)."""
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "default_period": spec.default_period,
            "period_min": spec.period_bounds[0],
            "period_max": spec.period_bounds[1],
            "default_threshold": spec.default_threshold,
            "threshold_min": spec.threshold_bounds[0],
            "threshold_max": spec.threshold_bounds[1],
            "default_operator": spec.default_operator,
            "unit": spec.unit,
            "has_period": spec.period_bounds[0] != spec.period_bounds[1],
            "scale_note": spec.scale_note,
        }
        for spec in INDICATORS.values()
    ]
