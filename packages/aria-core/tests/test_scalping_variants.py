"""5 scalping variant engines (V1-V5, 08/01) -- offline, no real network call.
Indicator functions themselves are already covered by test_indicators.py --
these tests mock them directly to exercise each variant's SIGNAL/anti-dump/
stop-take-profit LOGIC in isolation, not the underlying math."""
from __future__ import annotations

import pytest

from aria_core import momentum_entry
from aria_core.skills import indicators, scalping_variants

CONTRACT = "0x" + "a" * 40
CHAIN = "base"


@pytest.fixture(autouse=True)
def _clear_gates_cache():
    """08/01 -- _gates_and_candles now caches per (contract, chain) across
    calls (real bug fix, see scalping_variants._gates_cache's own comment).
    Every test in this file reuses the SAME CONTRACT/CHAIN -- without
    clearing between tests, the first test's mocked result would silently
    leak into every later one instead of exercising its own mocks."""
    scalping_variants._gates_cache.clear()
    yield
    scalping_variants._gates_cache.clear()


def _pair(price=1.0, symbol="TOK", liquidity=100_000.0):
    return momentum_entry.PairSnapshot(
        pair_address="0xpool", price_usd=price, base_symbol=symbol, liquidity_usd=liquidity,
    )


def _candles(n=50):
    from aria_core.skills.ta_levels import Candle

    return [
        Candle(ts=i, open=1.0, high=1.05, low=0.95, close=1.0, volume=1000.0) for i in range(n)
    ]


def _patch_gates_and_candles(
    monkeypatch, *, pair=None, hold=None, candles=None, align_score=3, volume_status="confirmed",
):
    """``align_score`` (08/02, trend-alignment, informational/sizing only since
    the same-day RVOL fix below): defaults to 3/3 (strong alignment) so every
    EXISTING test exercising a variant's own signal/anti-dump/stop logic in
    isolation isn't incidentally affected. ``volume_status`` (08/02, real HARD
    GATE in _gates_and_candles_uncached since align_score turned out too slow
    for scalping's candle width, see that function's own comment): defaults
    to "confirmed" so no EXISTING test is incidentally blocked by this gate --
    its own behavior (rejecting "not_confirmed", never rejecting "unknown")
    is tested separately, with an explicit status passed here."""
    async def fake_hard_gates(contract, chain, *, mode="standard"):
        return (pair if hold is None else None), None, hold

    async def fake_fetch_candles(pool_address, chain, *, contract="", pair=None, mode="standard"):
        return candles if candles is not None else _candles()

    def fake_technical_alignment(candles):
        return align_score, [], {"ema_above": True, "macd_above": True, "bullish_pattern": True}

    def fake_volume_confirmation(candles):
        return volume_status, f"volume ({volume_status})", 5.0 if volume_status == "confirmed" else None

    monkeypatch.setattr(momentum_entry, "evaluate_hard_gates", fake_hard_gates)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", fake_fetch_candles)
    monkeypatch.setattr(momentum_entry, "_technical_alignment", fake_technical_alignment)
    monkeypatch.setattr(momentum_entry, "_check_volume_confirmation", fake_volume_confirmation)


# ── shared plumbing (_gates_and_candles) ────────────────────────────────────

@pytest.mark.asyncio
async def test_gates_and_candles_returns_hold_on_hard_gate_rejection(monkeypatch):
    hold = {"action": "HOLD", "hold_reason": "blacklisted", "reasons": ["listé noir"]}
    _patch_gates_and_candles(monkeypatch, hold=hold)
    pair, candles, result_hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    assert pair is None and candles == [] and result_hold == hold


@pytest.mark.asyncio
async def test_gates_and_candles_none_when_no_liquid_pair(monkeypatch):
    _patch_gates_and_candles(monkeypatch, pair=None, hold=None)
    pair, candles, result_hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    assert pair is None and candles == [] and result_hold is None


@pytest.mark.asyncio
async def test_gates_and_candles_hold_on_insufficient_history(monkeypatch):
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_candles(10))
    pair, candles, result_hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    assert pair is None and result_hold["hold_reason"] == "insufficient_candle_history"


@pytest.mark.asyncio
async def test_gates_and_candles_returns_pair_and_candles_on_success(monkeypatch):
    real_pair = _pair()
    real_candles = _candles()
    _patch_gates_and_candles(monkeypatch, pair=real_pair, candles=real_candles)
    pair, candles, result_hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    assert pair is real_pair and candles is real_candles and result_hold is None


@pytest.mark.asyncio
async def test_gates_and_candles_second_call_reuses_cache_no_network(monkeypatch):
    """08/01 real bug fix: the 5 variants used to each hit the network
    independently for the SAME candidate (5x the calls for identical data,
    root cause of a live GeckoTerminal rate-limit burst). The 2nd call for
    the same (contract, chain) must reuse the cached result -- the mocked
    hard-gates/fetch functions must NOT be invoked again."""
    calls = {"hard_gates": 0, "fetch_candles": 0}

    async def fake_hard_gates(contract, chain, *, mode="standard"):
        calls["hard_gates"] += 1
        return _pair(), None, None

    async def fake_fetch_candles(pool_address, chain, *, contract="", pair=None, mode="standard"):
        calls["fetch_candles"] += 1
        return _candles()

    monkeypatch.setattr(momentum_entry, "evaluate_hard_gates", fake_hard_gates)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", fake_fetch_candles)

    first = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    second = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)

    assert calls == {"hard_gates": 1, "fetch_candles": 1}
    assert first == second


@pytest.mark.asyncio
async def test_gates_and_candles_cache_scoped_per_contract_and_chain(monkeypatch):
    """A different contract (or a different chain for the same contract)
    must never reuse another candidate's cached result."""
    calls = {"hard_gates": 0}

    async def fake_hard_gates(contract, chain, *, mode="standard"):
        calls["hard_gates"] += 1
        return _pair(), None, None

    async def fake_fetch_candles(pool_address, chain, *, contract="", pair=None, mode="standard"):
        return _candles()

    monkeypatch.setattr(momentum_entry, "evaluate_hard_gates", fake_hard_gates)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", fake_fetch_candles)

    await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    await scalping_variants._gates_and_candles("0x" + "b" * 40, CHAIN)
    await scalping_variants._gates_and_candles(CONTRACT, "ethereum")

    assert calls["hard_gates"] == 3


# ── volume-confirmation gate (08/02, replaces the same-day trend-alignment
# gate) ───────────────────────────────────────────────────────────────────
# Real bug found live: 7/7 closed scalping trades lost, peak_gain_pct = 0.00%
# in EVERY case -- none of the 6 scalping entries (5 variants + legacy RSI-
# divergence) had any market-context filter, just "the oversold indicator
# just exited its zone". First fix (trend-alignment via
# momentum_entry._technical_alignment) was ITSELF a real bug, found by an
# adversarial cross-review workflow and confirmed live: EMA12>EMA26 is
# structurally too slow for scalping's candle width to ever confirm a bounce
# that JUST formed (2.1% pass rate in a 500-scenario simulation; real prod
# data showed scalping_v2/v4/v5 opened ZERO trades in 8h). Replaced same-day
# with relative volume (momentum_entry._check_volume_confirmation) -- the
# SAME gate the standard momentum pipeline already uses for this exact
# purpose, and one that can react on the very candle the bounce forms on.


@pytest.mark.asyncio
async def test_gates_and_candles_rejects_unconfirmed_volume(monkeypatch):
    pair = _pair()
    _patch_gates_and_candles(monkeypatch, pair=pair, volume_status="not_confirmed")

    result_pair, result_candles, hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)

    assert result_pair is None
    assert result_candles == []
    assert hold is not None
    assert hold["action"] == "HOLD"
    assert hold["hold_reason"] == "no_volume_confirmation"


@pytest.mark.asyncio
async def test_gates_and_candles_allows_confirmed_volume(monkeypatch):
    pair = _pair()
    _patch_gates_and_candles(monkeypatch, pair=pair, volume_status="confirmed")

    result_pair, result_candles, hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)

    assert result_pair is pair
    assert result_candles
    assert hold is None


@pytest.mark.asyncio
async def test_gates_and_candles_never_rejects_on_unknown_volume(monkeypatch):
    """Fail-open doctrine, same as everywhere else in this pipeline: a data
    source with no real per-candle volume (e.g. a synthesis fallback) must
    never be confused with "this signal is false"."""
    pair = _pair()
    _patch_gates_and_candles(monkeypatch, pair=pair, volume_status="unknown")

    result_pair, result_candles, hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)

    assert result_pair is pair
    assert result_candles
    assert hold is None


@pytest.mark.asyncio
async def test_v1_buy_propagates_alignment_and_volume_fields(monkeypatch):
    """align_score/align_* stay informational/sizing-only (risk_guard's
    conviction sizing) even though the gate above no longer hard-enforces
    align_score -- real bug found alongside entry_atr_pct's own: these
    signals were computed then thrown away, never reaching
    risk_guard.compute_entry_alloc's sizing. volume_confirmed/rvol_multiple
    (new, same-day RVOL fix) must reach the same sizing path too -- same
    "confirmed" -> True / else -> False mapping as the standard pipeline's
    own call site."""
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair, align_score=3, volume_status="confirmed")
    series = [None] * 48 + [-0.1, 0.2]
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: series)
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    assert result["align_score"] == 3
    assert result["align_ema"] is True
    assert result["align_macd"] is True
    assert result["align_pattern"] is True
    assert result["volume_confirmed"] is True
    assert result["rvol_multiple"] == pytest.approx(5.0)


# ── V1 -- Bollinger %B ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_v1_buys_on_confirmed_exit_from_oversold(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-0.1, 0.2]  # oversold then confirmed exit
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: series)
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    assert result["mode"] == "scalping"
    assert result["invalidation"] == pytest.approx(1.0 - 1.5 * 0.05)
    expected_stop = 1.0 - 1.5 * 0.05
    assert result["target"] == pytest.approx(1.0 + 2.0 * (1.0 - expected_stop))
    # 08/02 -- real bug found live: entry_atr_pct used to be hardcoded None,
    # silently disabling the ATR trailing stop (paper_trader._effective_
    # trail_pct fell back to the generic 15%, never activating on scalping-
    # scale moves) -- must now be the real ratio (atr / entry).
    assert result["entry_atr_pct"] == pytest.approx(0.05 / 1.0)


@pytest.mark.asyncio
async def test_v1_never_buys_mid_collapse_without_confirmation(monkeypatch):
    """Anti-dump: still IN the oversold zone on the LAST candle -- never a buy."""
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-0.1, -0.2]  # still oversold, no exit
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: series)

    result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "no_signal"


@pytest.mark.asyncio
async def test_v1_no_signal_without_a_prior_oversold_reading(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [0.5, 0.6]  # never was oversold
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: series)

    result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)
    assert result["action"] == "HOLD"


@pytest.mark.asyncio
async def test_v1_hold_on_missing_atr(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-0.1, 0.2]
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: series)
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 50)

    result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "indicator_unavailable"


@pytest.mark.asyncio
async def test_v1_returns_hold_dict_on_hard_gate_rejection(monkeypatch):
    hold = {"action": "HOLD", "hold_reason": "honeypot_rejected", "reasons": ["honeypot"]}
    _patch_gates_and_candles(monkeypatch, hold=hold)
    result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)
    assert result == hold


@pytest.mark.asyncio
async def test_v1_none_when_no_liquid_pair(monkeypatch):
    _patch_gates_and_candles(monkeypatch, pair=None, hold=None)
    result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)
    assert result is None


# ── V2 -- VWAP Z-score institutionnel ────────────────────────────────────────

@pytest.mark.asyncio
async def test_v2_buys_on_confirmed_exit_from_oversold(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-3.0, -2.0]  # oversold (<=-2.5) then confirmed exit
    monkeypatch.setattr(indicators, "vwap_zscore_series", lambda candles, **kw: series)
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    result = await scalping_variants.evaluate_v2_vwap_institutional(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    expected_stop = 1.0 - 1.5 * 0.05
    assert result["target"] == pytest.approx(1.0 + 1.5 * (1.0 - expected_stop))


@pytest.mark.asyncio
async def test_v2_no_buy_without_confirmation(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-3.0, -2.8]  # still oversold
    monkeypatch.setattr(indicators, "vwap_zscore_series", lambda candles, **kw: series)

    result = await scalping_variants.evaluate_v2_vwap_institutional(CONTRACT, CHAIN)
    assert result["action"] == "HOLD"


# ── V3 -- Stochastique %K ultra-réactif ──────────────────────────────────────

@pytest.mark.asyncio
async def test_v3_buys_on_confirmed_exit_with_structural_stop(monkeypatch):
    from aria_core.skills.ta_levels import Candle

    pair = _pair(price=1.0)
    candles = [
        Candle(ts=i, open=1.0, high=1.05, low=0.95, close=1.0, volume=1000.0) for i in range(48)
    ] + [
        Candle(ts=48, open=1.0, high=1.02, low=0.90, close=1.0, volume=1000.0),  # previous low = 0.90
        Candle(ts=49, open=1.0, high=1.02, low=0.98, close=1.0, volume=1000.0),
    ]
    _patch_gates_and_candles(monkeypatch, pair=pair, candles=candles)
    series = [None] * 48 + [10.0, 20.0]  # oversold (<=15) then confirmed exit (>15)
    monkeypatch.setattr(indicators, "stochastic_k_series", lambda candles, **kw: series)

    result = await scalping_variants.evaluate_v3_stochastic(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    expected_stop = 0.90 * (1.0 - 0.005)
    assert result["invalidation"] == pytest.approx(expected_stop)
    assert result["target"] == pytest.approx(1.0 + 2.0 * (1.0 - expected_stop))
    # 08/02 -- V3's own stop is structural (previous-candle-low), never ATR --
    # but entry_atr_pct must still be populated (computed uniformly in
    # _buy_result for all 5 variants) so the trailing stop isn't blind for V3
    # either, same real bug as V1/V2/V4/V5.
    assert result["entry_atr_pct"] is not None
    assert result["entry_atr_pct"] > 0


@pytest.mark.asyncio
async def test_v3_no_buy_without_confirmation(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [10.0, 12.0]  # still oversold
    monkeypatch.setattr(indicators, "stochastic_k_series", lambda candles, **kw: series)

    result = await scalping_variants.evaluate_v3_stochastic(CONTRACT, CHAIN)
    assert result["action"] == "HOLD"


# ── V4 -- Combo (Bollinger ET Stochastique) ──────────────────────────────────

@pytest.mark.asyncio
async def test_v4_requires_both_signals_confirmed(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: [None] * 48 + [-0.1, 0.2])
    monkeypatch.setattr(indicators, "stochastic_k_series", lambda candles, **kw: [None] * 48 + [10.0, 20.0])
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    result = await scalping_variants.evaluate_v4_combo(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    expected_stop = 1.0 - 2.0 * 0.05  # V4 uses a WIDER 2xATR stop
    assert result["invalidation"] == pytest.approx(expected_stop)
    assert result["target"] == pytest.approx(1.0 + 1.0 * (1.0 - expected_stop))  # 1:1 conservative TP


@pytest.mark.asyncio
async def test_v4_holds_when_only_one_signal_confirms(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    # Bollinger confirms exit, but Stochastic never was oversold -- combo fails.
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: [None] * 48 + [-0.1, 0.2])
    monkeypatch.setattr(indicators, "stochastic_k_series", lambda candles, **kw: [None] * 48 + [50.0, 55.0])

    result = await scalping_variants.evaluate_v4_combo(CONTRACT, CHAIN)
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "no_signal"


# ── V5 -- VWAP + trailing (pas de TP fixe) ───────────────────────────────────

@pytest.mark.asyncio
async def test_v5_buys_with_no_fixed_target(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-3.0, -2.0]
    monkeypatch.setattr(indicators, "vwap_zscore_series", lambda candles, **kw: series)
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    result = await scalping_variants.evaluate_v5_vwap_trailing(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    assert result["target"] is None  # no fixed TP -- generic trailing stop takes over
    assert result["invalidation"] == pytest.approx(1.0 - 1.5 * 0.05)
    # 08/02 -- real bug found (adversarial cross-review workflow): rr used to
    # be None here (no fixed target), which made risk_guard's conviction
    # sizing treat V5 as "no signal supplied at all" and always grant the
    # MAXIMUM allocation on every buy. Must now be a real, non-None value
    # (V2's own TP_RR_RATIO, same entry signal/stop width) so V5 stops being
    # exempt from risk-based sizing -- target itself stays None (no behavior
    # change to the actual exit, still pure ATR trailing stop).
    assert result["rr"] == pytest.approx(scalping_variants._V2_TP_RR_RATIO)
    assert result["rr"] == pytest.approx(1.5)


# ── VARIANT_ANALYZERS registry ───────────────────────────────────────────────

def test_variant_analyzers_registry_has_all_5_variants():
    assert set(scalping_variants.VARIANT_ANALYZERS) == {
        "scalping_v1", "scalping_v2", "scalping_v3", "scalping_v4", "scalping_v5",
    }
    for fn in scalping_variants.VARIANT_ANALYZERS.values():
        assert callable(fn)


def test_prune_gates_cache_removes_only_expired_entries_past_threshold():
    now = 1000.0
    scalping_variants._gates_cache["fresh"] = (now + 60.0, ("kept",))
    scalping_variants._gates_cache["stale"] = (now - 1.0, ("dropped",))
    for i in range(scalping_variants._GATES_CACHE_MAX_SIZE):
        scalping_variants._gates_cache[f"filler-{i}"] = (now - 1.0, ("dropped",))

    scalping_variants._prune_gates_cache(now)

    assert "fresh" in scalping_variants._gates_cache
    assert "stale" not in scalping_variants._gates_cache
    assert not any(k.startswith("filler-") for k in scalping_variants._gates_cache)


def test_prune_gates_cache_noop_under_threshold():
    scalping_variants._gates_cache["a"] = (1000.0 - 1.0, ("stale",))
    scalping_variants._prune_gates_cache(1000.0)
    # Below _GATES_CACHE_MAX_SIZE -- pruning is skipped entirely, even for an
    # expired entry (cheap by design, next insert over threshold catches it).
    assert "a" in scalping_variants._gates_cache
