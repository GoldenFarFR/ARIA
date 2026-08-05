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

    def fake_volume_confirmation(candles, *, mode=None):
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
async def test_gates_and_candles_trims_the_still_forming_last_candle(monkeypatch):
    """03/08 -- 9-pocket diagnostic (docs/HANDOFF_LLM.md): the OHLCV cascade's
    last candle is still forming, not closed -- every evaluate_vN must reason
    on candles that are ALL closed. Fetching exactly N candles must yield
    N-1 to the caller."""
    real_candles = _candles(50)
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=real_candles)
    _, candles, result_hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    assert result_hold is None
    assert len(candles) == len(real_candles) - 1


@pytest.mark.asyncio
async def test_gates_and_candles_exactly_at_threshold_before_trim_still_rejected(monkeypatch):
    """A batch that just barely clears _MIN_CANDLES_FOR_SIGNAL (45) on the
    fetched, UNTRIMMED count must be rejected once the still-forming candle
    is dropped (44 < 45) -- the threshold check must run AFTER trimming, not
    before (exactly the trap this test guards against regressing to)."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_candles(scalping_variants._MIN_CANDLES_FOR_SIGNAL))
    pair, candles, result_hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    assert pair is None and result_hold["hold_reason"] == "insufficient_candle_history"


@pytest.mark.asyncio
async def test_gates_and_candles_returns_pair_and_candles_on_success(monkeypatch):
    real_pair = _pair()
    real_candles = _candles()
    _patch_gates_and_candles(monkeypatch, pair=real_pair, candles=real_candles)
    pair, candles, result_hold = await scalping_variants._gates_and_candles(CONTRACT, CHAIN)
    # 03/08 -- the last fetched candle is trimmed (still forming, not closed
    # -- see _gates_and_candles_uncached's own comment), so `candles` is the
    # fetched list MINUS its last entry, never the same object anymore.
    assert pair is real_pair and candles == real_candles[:-1] and result_hold is None


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


@pytest.mark.asyncio
async def test_v1_buy_carries_entry_security_json(monkeypatch):
    """08/02, real bug found live (100% of prod positions had a NULL
    entry_security_json, diagnostic workflow): the 5 scalping variant
    engines (created 08/01, a day AFTER Item #234's 30/07 fix) share this
    ``_buy_result`` builder, which never set the key at all -- unlike
    momentum_entry.py's own BUY path. Populates the security cache the same
    way ``momentum_entry._check_honeypot`` would in real production (the
    honeypot hard gate this variant's own ``_gates_and_candles`` calls
    BEFORE this signal is ever evaluated), so this test proves REAL data
    flows through end to end, not just that the key exists."""
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-0.1, 0.2]
    monkeypatch.setattr(indicators, "bollinger_percent_b", lambda closes, **kw: series)
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    momentum_entry._security_cache.clear()

    class _FakeSecurity:
        is_honeypot = False
        cannot_sell_all = False
        hidden_owner = False
        can_take_back_ownership = False
        owner_change_balance = False
        is_open_source = True
        owner_address = "0x" + "2" * 40
        slippage_modifiable = False
        is_blacklisted = False
        transfer_pausable = False

    momentum_entry._cache_security(CHAIN, CONTRACT, _FakeSecurity())
    try:
        result = await scalping_variants.evaluate_v1_bollinger(CONTRACT, CHAIN)

        assert result["action"] == "BUY"
        raw = result.get("entry_security_json")
        assert raw  # previously the key was absent entirely -- ``.get`` would return None
        import json as _json

        parsed = _json.loads(raw)
        assert parsed["contract_verified"] is True
        assert parsed["owner_address"] == "0x" + "2" * 40
        assert parsed["is_honeypot"] is False
    finally:
        momentum_entry._security_cache.clear()


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
    # Item #65 (08/03), anti-chasing shadow filter: V1 (Bollinger) uses the
    # Bollinger/VWAP window (20), not the Stochastique one (14) -- see
    # chasing_filter_shadow.RECENT_LOW_WINDOW_BOLLINGER_VWAP. Actual min-low
    # math is covered exhaustively in test_chasing_filter_shadow.py; this
    # just proves the right window is wired for this variant.
    assert result["recent_low"] == pytest.approx(0.95)
    assert result["recent_low_window"] == 20


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
    series = [None] * 48 + [-2.5, -1.5]  # oversold (<=-2.0) then confirmed exit
    monkeypatch.setattr(indicators, "vwap_zscore_series", lambda candles, **kw: series)
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    result = await scalping_variants.evaluate_v2_vwap_institutional(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    expected_stop = 1.0 - 1.5 * 0.05
    assert result["target"] == pytest.approx(1.0 + 1.5 * (1.0 - expected_stop))
    # 08/02 -- key must be present on every variant sharing _buy_result, not
    # just V1 (see test_v1_buy_carries_entry_security_json's own comment for
    # the full incident).
    assert "entry_security_json" in result
    # Item #65 (08/03): V2 (VWAP) uses the Bollinger/VWAP window (20).
    assert result["recent_low"] == pytest.approx(0.95)
    assert result["recent_low_window"] == 20


@pytest.mark.asyncio
async def test_v2_no_buy_without_confirmation(monkeypatch):
    pair = _pair(price=1.0)
    _patch_gates_and_candles(monkeypatch, pair=pair)
    series = [None] * 48 + [-2.5, -2.3]  # still oversold
    monkeypatch.setattr(indicators, "vwap_zscore_series", lambda candles, **kw: series)

    result = await scalping_variants.evaluate_v2_vwap_institutional(CONTRACT, CHAIN)
    assert result["action"] == "HOLD"


# ── V3 -- Stochastique %K ultra-réactif ──────────────────────────────────────

@pytest.mark.asyncio
async def test_v3_buys_on_confirmed_exit_with_structural_stop(monkeypatch):
    from aria_core.skills.ta_levels import Candle

    pair = _pair(price=1.0)
    # 03/08 -- _gates_and_candles_uncached now trims the LAST candle (still
    # forming, not closed -- see that function's own comment), so the fed
    # list has 50 entries but the code under test only ever sees the first
    # 49. ts=49 (last) is the one trimmed away; ts=47 (candles_tronque[-2]
    # once trimmed) carries the distinctive low used as V3's structural stop.
    candles = [
        Candle(ts=i, open=1.0, high=1.05, low=0.95, close=1.0, volume=1000.0) for i in range(47)
    ] + [
        Candle(ts=47, open=1.0, high=1.02, low=0.90, close=1.0, volume=1000.0),  # previous low = 0.90
        Candle(ts=48, open=1.0, high=1.02, low=0.98, close=1.0, volume=1000.0),
        Candle(ts=49, open=1.0, high=1.02, low=0.99, close=1.0, volume=1000.0),  # trimmed away
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
    assert "entry_security_json" in result
    # Item #65 (08/03), anti-chasing shadow filter: V3 (Stochastique) uses
    # its own period (14, RECENT_LOW_WINDOW_STOCHASTIC) -- min(low) over the
    # last 14 of these 50 candles is 0.90 (index 48), inside that window.
    assert result["recent_low"] == pytest.approx(0.90)
    assert result["recent_low_window"] == 14


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
    monkeypatch.setattr(indicators, "stochastic_k_series", lambda candles, **kw: [None] * 48 + [15.0, 25.0])
    monkeypatch.setattr(indicators, "atr_series", lambda candles, **kw: [None] * 49 + [0.05])

    result = await scalping_variants.evaluate_v4_combo(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    expected_stop = 1.0 - 2.0 * 0.05  # V4 uses a WIDER 2xATR stop
    assert result["invalidation"] == pytest.approx(expected_stop)
    # 08/02 -- ratio relevé de 1.0 (1:1, mathématiquement invendable, voir
    # test_v4_tp_ratio_stays_strictly_above_the_price_impact_floor ci-dessous)
    # à 1.3.
    assert result["target"] == pytest.approx(1.0 + 1.3 * (1.0 - expected_stop))
    assert "entry_security_json" in result
    # Item #65 (08/03): V4 combines %B (period 20) and %K (period 14) --
    # uses the wider of the two windows (20), never a uniform N=14.
    assert result["recent_low"] == pytest.approx(0.95)
    assert result["recent_low_window"] == 20


def test_v4_tp_ratio_stays_strictly_above_the_price_impact_floor():
    """08/02 -- real critical bug found live (audit + adversarial verify
    workflow): _V4_TP_RR_RATIO used to be exactly 1.0, EXACTLY equal to
    risk_guard.PRICE_IMPACT_MIN_RR (1.0) -- an algebraic proof (and 7/7 real
    prod signals confirmed) showed cap_alloc_to_price_impact then ALWAYS
    returns 0.0 once the mandatory 1% scalping swap fee is applied,
    regardless of liquidity/volatility -- V4 could never open a single
    position. Must stay strictly above BOTH floors (the scalping-specific
    one introduced the same day, and the original default) with real margin,
    not just barely above."""
    from aria_core import risk_guard

    assert scalping_variants._V4_TP_RR_RATIO > risk_guard.PRICE_IMPACT_MIN_RR_SCALPING
    assert scalping_variants._V4_TP_RR_RATIO > risk_guard.PRICE_IMPACT_MIN_RR


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
    series = [None] * 48 + [-2.5, -1.5]
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
    # Item #65 (08/03): V5 (VWAP) uses the Bollinger/VWAP window (20).
    assert result["recent_low"] == pytest.approx(0.95)
    assert result["recent_low_window"] == 20
    assert "entry_security_json" in result


# ── VARIANT_ANALYZERS registry ───────────────────────────────────────────────

def test_variant_analyzers_registry_has_all_variants():
    # v8 added 08/05 (wick-confirmed reversal, operator carte blanche) --
    # deliberate invariant change, updated in the same commit as the engine.
    assert set(scalping_variants.VARIANT_ANALYZERS) == {
        "scalping_v1", "scalping_v2", "scalping_v3", "scalping_v4", "scalping_v5",
        "scalping_v8",
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


# ── V8 -- wick-confirmed reversal (08/05) ───────────────────────────────────
# The divergence engine has its own tests (test_entry_signals.py) -- mocked
# here to exercise V8's OWN gate logic in isolation, same doctrine as the
# V1-V5 tests above mocking their indicators.

def _v8_candles(*, signal_wick: bool, trough_age: int = 0, n: int = 60):
    """Flat series whose SIGNAL candle (the last one AFTER _gates_and_candles
    trims the still-forming tail, i.e. index -2 here) is shaped as a hammer
    or a monolithic no-wick candle. ``trough_age`` places the window's lowest
    low that many candles BEFORE the signal candle (0 = the signal candle
    itself is the trough)."""
    from aria_core.skills.ta_levels import Candle

    candles = [
        Candle(ts=i, open=1.0, high=1.05, low=0.95, close=1.0, volume=1000.0) for i in range(n)
    ]
    if signal_wick:
        # hammer: body pinned near the top, deep lower wick
        sig = Candle(ts=n - 2, open=1.0, high=1.02, low=0.90, close=1.0, volume=1500.0)
    else:
        # monolithic drop: body bottom == low, no rejection
        sig = Candle(ts=n - 2, open=1.0, high=1.02, low=0.94, close=0.94, volume=1500.0)
    candles[n - 2] = sig
    if trough_age > 0:
        # a DEEPER low further back (visible in the 10-candle bootstrap window)
        idx = n - 2 - trough_age
        candles[idx] = Candle(ts=idx, open=1.0, high=1.05, low=0.85, close=1.0, volume=1000.0)
    return candles


def _mock_divergence(monkeypatch, *, present: bool, bars_since: int | None = 1):
    from aria_core.skills import entry_signals

    def fake_detail(candles, *, lookback=40, period=14):
        if present:
            return entry_signals.RsiDivergenceDetail(
                True, "mock divergence", gap=10.0, span=8, bars_since_recent_pivot=bars_since,
            )
        return entry_signals.RsiDivergenceDetail(False, "")

    monkeypatch.setattr(entry_signals, "_bullish_rsi_divergence_detail", fake_detail)


@pytest.mark.asyncio
async def test_v8_registered_in_variant_analyzers():
    assert "scalping_v8" in scalping_variants.VARIANT_ANALYZERS
    assert scalping_variants.VARIANT_ANALYZERS["scalping_v8"] is scalping_variants.evaluate_v8_wick_reversal


@pytest.mark.asyncio
async def test_v8_buys_on_wick_with_fresh_divergence_standard_sizing(monkeypatch):
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=True, bars_since=2)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "BUY"
    assert sig["target"] is None  # no fixed TP by design (invalidated empirically)
    assert sig["rr"] == scalping_variants._V8_SIZING_RR_WITH_DIVERGENCE
    assert "divergence RSI confirmée" in sig["reasons"][0]


@pytest.mark.asyncio
async def test_v8_bootstrap_buys_on_wick_alone_defensive_sizing(monkeypatch):
    """Bootstrap mode: no divergence needed -- wick + fresh trough suffice,
    sized one tier more defensively, and the reason TRACES the sub-population
    (the free 8.1 experiment)."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=False)
    monkeypatch.setattr(scalping_variants, "_V8_BOOTSTRAP_MODE", True)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "BUY"
    assert sig["rr"] == scalping_variants._V8_SIZING_RR_WICK_ONLY
    assert "bootstrap" in sig["reasons"][0]


@pytest.mark.asyncio
async def test_v8_strict_mode_requires_divergence(monkeypatch):
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=False)
    monkeypatch.setattr(scalping_variants, "_V8_BOOTSTRAP_MODE", False)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "no_signal"


@pytest.mark.asyncio
async def test_v8_rejects_signal_candle_without_wick(monkeypatch):
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_v8_candles(signal_wick=False))
    _mock_divergence(monkeypatch, present=True, bars_since=1)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "no_wick_confirmation"


@pytest.mark.asyncio
async def test_v8_bootstrap_rejects_stale_trough(monkeypatch):
    """Bootstrap without divergence: a hammer printed 8 candles after the
    window's real low confirms nothing -- rejected as stale."""
    candles = _v8_candles(signal_wick=True, trough_age=8)
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=candles)
    _mock_divergence(monkeypatch, present=False)
    monkeypatch.setattr(scalping_variants, "_V8_BOOTSTRAP_MODE", True)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "stale_trough"


@pytest.mark.asyncio
async def test_v8_rejects_price_already_ran_away(monkeypatch):
    """Anti-chase: live price 5% above the signal candle's close -- the move
    this entry was meant to catch is already consumed (median max gain on the
    58 reconstructed real trades was +0.47%)."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(price=1.05), candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=True, bars_since=1)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "price_ran_away"
