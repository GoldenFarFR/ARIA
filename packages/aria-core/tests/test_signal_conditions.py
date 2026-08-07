"""Configurable per-token entry conditions (07/08, operator request:
"toutes les fonctionnalités dispo sur TradingView")."""

import random

import pytest

from aria_core import signal_conditions as sc
from aria_core.skills import indicators
from aria_core.skills.entry_signals import rsi_series
from aria_core.skills.ta_levels import Candle


def _series(n=200, seed=0):
    random.seed(seed)
    price = 100.0
    out = []
    for i in range(n):
        price *= 1 + random.uniform(-0.04, 0.035)
        out.append(Candle(ts=float(i), open=price, high=price * 1.012,
                          low=price * 0.982, close=price, volume=1000.0 + i * 3))
    return out


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parses_the_default_spec():
    conditions, error = sc.parse(sc.DEFAULT_SPEC)
    assert error == ""
    assert [c.format() for c in conditions] == ["rsi(18)<21", "mfi(10)<20"]


def test_every_exposed_indicator_really_parses_and_computes():
    """The dropdown may only ever offer indicators the engine can actually
    compute -- the whole point of building INDICATORS from the real
    functions in indicators.py rather than from a wish list."""
    candles = _series()
    for key, spec in sc.INDICATORS.items():
        text = (
            f"{key}{spec.default_operator}{spec.default_threshold:g}"
            if not spec.period_bounds[0] != spec.period_bounds[1]
            else f"{key}({spec.default_period}){spec.default_operator}{spec.default_threshold:g}"
        )
        conditions, error = sc.parse(text)
        assert error == "", f"{key}: {error}"
        verdicts = sc.evaluate(conditions, candles)
        assert len(verdicts) == len(candles)
        assert all(v in (True, False, None) for v in verdicts)


def test_period_defaults_when_omitted():
    conditions, error = sc.parse("rsi<30")
    assert error == ""
    assert conditions[0].period == sc.INDICATORS["rsi"].default_period


@pytest.mark.parametrize("text,fragment", [
    ("", "aucune condition"),
    ("foo(3)<5", "indicateur inconnu"),
    ("rsi(1)<21", "hors bornes"),
    ("rsi(18)<200", "hors bornes"),
    ("rsi(18)<21,rsi(9)<30", "double"),
    ("rsi 18 21", "illisible"),
    ("rsi(18)=21", "illisible"),
])
def test_invalid_specs_are_refused_with_a_readable_reason(text, fragment):
    conditions, error = sc.parse(text)
    assert conditions == []
    assert fragment in error


def test_spec_round_trips_through_format():
    original = "stoch(14)<15,wick>0.3,vwapz(20)<-2.5"
    conditions, error = sc.parse(original)
    assert error == ""
    reparsed, error2 = sc.parse(sc.format_spec(conditions))
    assert error2 == ""
    assert [c.format() for c in reparsed] == [c.format() for c in conditions]


# ── evaluation ───────────────────────────────────────────────────────────────

def test_generic_engine_exactly_reproduces_the_hardwired_rsi_mfi_behavior():
    """The whole migration rests on this: with the default spec, the
    configurable engine must agree candle-for-candle with the RSI+MFI logic
    v9 ran before -- including the None (warm-up) states, not just the
    True ones."""
    from aria_core import scalping_v9 as v9

    conditions, _ = sc.parse(sc.DEFAULT_SPEC)
    for seed in range(8):
        candles = _series(seed=seed)
        new = sc.evaluate(conditions, candles)
        rsi = rsi_series([c.close for c in candles], period=18)
        mfi = indicators.mfi_series(candles, period=10)
        old = [
            v9._both_below(rsi[i], mfi[i], rsi_lower=21, mfi_lower=20)
            for i in range(len(candles))
        ]
        assert new == old, f"divergence sur seed {seed}"


def test_warmup_wins_over_a_failed_condition():
    """Real bug caught while building this (would have fired phantom buys):
    with one indicator still warming up and another already computable and
    FALSE, the verdict must be None -- never False. A False here would make
    the sequence read False -> True the moment warm-up ends, firing a
    transition on a candle that was never actually evaluated."""
    candles = _series(n=60)
    # rsi(30) is still warming early on; mfi(5) is computable much sooner.
    conditions, error = sc.parse("rsi(30)<1,mfi(5)<99")
    assert error == ""
    verdicts = sc.evaluate(conditions, candles)
    rsi = rsi_series([c.close for c in candles], period=30)
    warming = next(i for i, v in enumerate(rsi) if v is None and i > 5)
    settled = next(i for i, v in enumerate(rsi) if v is not None)
    # While RSI warms up, the verdict must be None even though mfi<99 is
    # computable and rsi<1 would fail once it settles.
    assert verdicts[warming] is None
    assert verdicts[settled] is False  # both computable now, rsi<1 fails


def test_single_condition_spec_is_legal():
    candles = _series()
    conditions, error = sc.parse("rsi(14)<99")
    assert error == ""
    verdicts = sc.evaluate(conditions, candles)
    assert verdicts[-1] is True  # RSI is bounded below 99 on real data


def test_all_conditions_must_hold_on_the_same_candle():
    candles = _series()
    loose, error_loose = sc.parse("rsi(14)<99")
    impossible, error_impossible = sc.parse("rsi(14)<99,mfi(10)<1")
    assert error_loose == "" and error_impossible == ""
    assert sc.evaluate(loose, candles)[-1] is True
    # Adding a condition that cannot hold flips the same candle to False --
    # every condition gates the verdict, none is advisory.
    assert sc.evaluate(impossible, candles)[-1] is False


def test_min_candles_accounts_for_the_double_warmup_indicator():
    """vwapz needs 2x its period (its rolling VWAP must stabilize first) --
    a naive max(period) would under-warm it and read None forever."""
    conditions, _ = sc.parse("vwapz(20)<-2.5")
    assert sc.min_candles(conditions) == 41  # 20 * 2 + 1
    conditions, _ = sc.parse("rsi(20)<21")
    assert sc.min_candles(conditions) == 21


def test_evaluate_is_empty_safe():
    assert sc.evaluate([], _series(n=5)) == [None] * 5
    conditions, _ = sc.parse(sc.DEFAULT_SPEC)
    assert sc.evaluate(conditions, []) == []


# ── template surface ─────────────────────────────────────────────────────────

def test_template_indicators_expose_only_real_ones():
    rows = sc.as_template_indicators()
    assert {r["key"] for r in rows} == set(sc.INDICATORS)
    for row in rows:
        assert row["period_min"] <= row["default_period"] <= row["period_max"]
        assert row["threshold_min"] <= row["default_threshold"] <= row["threshold_max"]
        assert row["default_operator"] in ("<", ">")


def test_describe_names_every_condition():
    conditions, _ = sc.parse("rsi(18)<21,wick>0.3")
    text = sc.describe(conditions)
    assert "RSI" in text and "ET" in text and "0.3" in text


# ── fidélité aux conventions des plateformes de charting ─────────────────────

def test_stoch_rsi_is_smoothed_like_a_charting_platform():
    """07/08 -- checked against what a trader actually sees: the RAW
    Stochastic RSI pins to exactly 0 or 100 about a third of the time, so a
    threshold copied off a chart ("stochrsi < 20") would fire far more often
    here than there. The default now applies the standard %K=3 smoothing;
    smooth=1 still returns the raw series for anyone who wants it."""
    candles = _series(n=300, seed=4)
    smoothed = indicators.stoch_rsi_series(candles, period=14)
    raw = indicators.stoch_rsi_series(candles, period=14, smooth=1)

    def pinned(series):
        values = [v for v in series if v is not None]
        return sum(1 for v in values if v in (0.0, 100.0)) / len(values)

    assert pinned(raw) > 0.2           # the raw series really does pin
    assert pinned(smoothed) < pinned(raw) / 2  # smoothing materially fixes it
    assert all(0.0 <= v <= 100.0 for v in smoothed if v is not None)


def test_every_indicator_diverging_from_a_chart_says_so():
    """An indicator whose SCALE differs from what a charting platform
    displays must carry a scale_note -- otherwise an operator copies a
    threshold off a chart and discovers the mismatch by watching a pocket
    misbehave. These are the deliberate deviations, listed explicitly so
    adding a twelfth silently is impossible."""
    diverging = {k for k, spec in sc.INDICATORS.items() if spec.scale_note}
    assert diverging == {
        "awesome", "forceindex", "obvslope", "adslope", "pvtslope", "eom",
        "supertrend", "aroon", "vortex", "macdhist", "ultimate",
    }
    for key in diverging:
        assert len(sc.INDICATORS[key].scale_note) > 20  # a real sentence, not a marker


def test_scale_note_reaches_the_template_surface():
    rows = {r["key"]: r for r in sc.as_template_indicators()}
    assert rows["awesome"]["scale_note"]
    assert rows["rsi"]["scale_note"] == ""  # standard formula, transfers as-is
