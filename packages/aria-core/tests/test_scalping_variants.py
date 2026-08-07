"""Scalping variant engine tests (V8 only since the 06/08 v1-v7 retirement)
-- offline, no real network call. Indicator functions themselves are already
covered by test_indicators.py -- these tests mock them directly to exercise
the signal/gate logic in isolation, not the underlying math."""
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


@pytest.fixture(autouse=True)
def _v8_entries_unpaused(monkeypatch):
    """07/08 ~23h20 -- v8's real default flipped to _V8_ENTRY_PAUSED=True
    (both tiers proven 0 winners in 43 real trades, see its own comment).
    Every test below exercises the underlying gate logic that stays fully
    correct and worth locking in even while paused -- only
    test_v8_entry_paused_blocks_every_signal below tests the flag itself,
    so it opts back into the real default explicitly."""
    monkeypatch.setattr(scalping_variants, "_V8_ENTRY_PAUSED", False)


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


# ── VARIANT_ANALYZERS registry ───────────────────────────────────────────────

def test_variant_analyzers_registry_has_all_variants():
    # 06/08 -- v1-v7 retired (explicit operator decision, "supprimer toutes
    # les poches scalping sauf v8") -- deliberate invariant change, updated
    # in the same commit as the retirement.
    assert set(scalping_variants.VARIANT_ANALYZERS) == {"scalping_v8"}
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
async def test_v8_entry_paused_blocks_every_signal(monkeypatch):
    """07/08 ~23h20: real default is _V8_ENTRY_PAUSED=True (see its own
    comment -- 0 winners in 43 real closed trades across both tiers, same
    never-touches-positive signature on each). A perfect textbook signal
    (fresh divergence + strong wick, the exact setup that used to BUY) must
    still HOLD -- the pause is a hard stop on the whole pocket, not a gate
    tucked away in one code path that a future refactor could bypass."""
    monkeypatch.setattr(scalping_variants, "_V8_ENTRY_PAUSED", True)
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=True, bars_since=2)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "v8_entry_paused"


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


@pytest.mark.asyncio
async def test_v8_rejects_bounce_already_faded_below_signal_close(monkeypatch):
    """06/08 live diagnostic fix: 32/34 real v8 trades never traded a single
    tick above entry -- entry (live price) can lag the signal candle's close
    by up to a candle period, buying into a bounce that already gave up.
    Live price 2% below the signal candle's close clears even the ATR-scaled
    cap (1.5% on this fixture's synthetic, wide-range candles) -- must
    reject, symmetric with the anti-chase guard above."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(price=0.98), candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=True, bars_since=1)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "bounce_already_faded"


@pytest.mark.asyncio
async def test_v8_allows_small_giveback_within_tolerance(monkeypatch):
    """Live price 1% below the signal candle's close stays within this
    fixture's ATR-scaled cap (1.5%, clamped to _V8_MAX_GIVEBACK_PCT on the
    synthetic wide-range candles) -- normal execution noise, not a faded
    bounce."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(price=0.99), candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=True, bars_since=1)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "BUY"


@pytest.mark.asyncio
async def test_v8_giveback_gate_scales_with_atr_not_flat_pct(monkeypatch):
    """Devil's Advocate report 2db20159: a flat % threshold ignores the
    pair's own volatility regime -- everything else in v8's entry band
    (stop distance) scales with ATR, this gate now does too. A LOW-ATR
    series (tight candles, clamped to the _V8_MIN_GIVEBACK_PCT floor) must
    reject a 0.3% giveback that the default HIGH-ATR fixture (clamped to
    the ceiling, see test_v8_allows_small_giveback_within_tolerance above)
    would tolerate several times over -- proves the gate is ATR-adaptive,
    not a fixed number."""
    from aria_core.skills.ta_levels import Candle

    n = 60
    tight_candles = [
        Candle(ts=i, open=1.0, high=1.001, low=0.999, close=1.0, volume=1000.0) for i in range(n)
    ]
    tight_candles[n - 2] = Candle(ts=n - 2, open=1.0, high=1.003, low=0.995, close=1.0, volume=1500.0)
    _patch_gates_and_candles(monkeypatch, pair=_pair(price=0.997), candles=tight_candles)
    _mock_divergence(monkeypatch, present=True, bars_since=1)
    sig = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "bounce_already_faded"


@pytest.mark.asyncio
async def test_v8_exempt_from_volume_hard_gate(monkeypatch):
    """08/05 (first autonomous live-data decision) -- v8 opts out of the
    RVOL>=3x hard gate (empirically non-predictive, dominant starvation cause
    in its first live 40 min). The gate itself (enforce_volume_gate=True
    rejecting "not_confirmed") keeps its own dedicated test above."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(), candles=_v8_candles(signal_wick=True),
                             volume_status="not_confirmed")
    _mock_divergence(monkeypatch, present=True, bars_since=1)

    v8 = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)
    assert v8["action"] == "BUY"


@pytest.mark.asyncio
async def test_v8_buy_propagates_alignment_and_volume_fields(monkeypatch):
    """_buy_result (shared builder) must propagate align_score/align_*/
    volume_confirmed/rvol_multiple to risk_guard's conviction sizing --
    real bug found 08/02 on the former v1-v5 engines (signals computed then
    thrown away); coverage ported to v8 when v1-v7 were retired (06/08)."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(price=1.0),
                             candles=_v8_candles(signal_wick=True),
                             align_score=3, volume_status="confirmed")
    _mock_divergence(monkeypatch, present=True, bars_since=1)

    result = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)

    assert result["action"] == "BUY"
    assert result["align_score"] == 3
    assert result["align_ema"] is True
    assert result["align_macd"] is True
    assert result["align_pattern"] is True
    assert result["volume_confirmed"] is True
    assert result["rvol_multiple"] == pytest.approx(5.0)
    # 08/02 -- entry_atr_pct must be the real ratio (atr / entry), never None
    # (None silently disabled the ATR trailing stop on scalping-scale moves).
    assert result["entry_atr_pct"] is not None


@pytest.mark.asyncio
async def test_v8_buy_carries_entry_security_json(monkeypatch):
    """08/02, real bug found live (100% of prod positions had a NULL
    entry_security_json): the shared ``_buy_result`` builder never set the
    key. Populates the security cache the same way ``momentum_entry.
    _check_honeypot`` would in real production, so this test proves REAL
    data flows through end to end -- coverage ported from the retired v1
    engine (06/08)."""
    _patch_gates_and_candles(monkeypatch, pair=_pair(price=1.0),
                             candles=_v8_candles(signal_wick=True))
    _mock_divergence(monkeypatch, present=True, bars_since=1)

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
        result = await scalping_variants.evaluate_v8_wick_reversal(CONTRACT, CHAIN)

        assert result["action"] == "BUY"
        raw = result.get("entry_security_json")
        assert raw
        import json as _json

        parsed = _json.loads(raw)
        assert parsed["contract_verified"] is True
        assert parsed["owner_address"] == "0x" + "2" * 40
        assert parsed["is_honeypot"] is False
    finally:
        momentum_entry._security_cache.clear()
