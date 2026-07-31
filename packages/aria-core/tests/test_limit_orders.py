"""Limit-order mechanism (07/23, operator-designed) -- a candidate whose price
drifted upward between signal detection and execution gets a limit order at
the original signal price instead of a plain reject, watched until the price
comes back down, the structure breaks, or it expires."""
from __future__ import annotations

import json

import pytest

from aria_core import limit_orders as lo
from aria_core import paper_trader, risk_guard
from aria_core.services.dexscreener import PairSnapshot


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "limit_orders_test.db")
    monkeypatch.setattr(lo, "DB_PATH", db_path)
    monkeypatch.setattr(paper_trader, "DB_PATH", db_path)
    yield


@pytest.fixture(autouse=True)
def _bypass_btc_cycles_network_call(monkeypatch):
    """Item #165, 28/07: a bonding-tagged trigger now calls btc_cycles.
    fetch_current_macro_phase() (real network call) -- neutral mock by
    default so no test here hits the real network. Tests dedicated to Item
    #165 itself override this locally."""
    from aria_core.skills import btc_cycles

    async def _fake_fetch_current_macro_phase(*, client=None, force_refresh=False):
        return None

    monkeypatch.setattr(btc_cycles, "fetch_current_macro_phase", _fake_fetch_current_macro_phase)


def _sig(**overrides) -> dict:
    base = {
        "price": 0.038, "target": 0.06, "invalidation": 0.03, "rr": 3.9,
        "align_score": 3, "symbol": "CHECK", "chain": "base",
        "liquidity_usd": 100_000.0, "category": "", "entry_security_json": "",
        "reasons": ["golden pocket + divergence RSI"], "entry_atr_pct": 0.15,
        "strategy": "momentum", "dev_sold_pct": None, "rvol_multiple": 5.0,
        "conviction_process_trail": None, "conviction_website_corroborated": None,
        "conviction_posting_cadence": None, "potential_score": None, "volume_confirmed": True,
        "regime": None,
    }
    base.update(overrides)
    return base


# ── pure decision functions ──────────────────────────────────────────────────


def test_should_place_limit_order_case_b_drifted_upward_structure_intact():
    # signal at 0.038, execution price drifted to 0.044 (the real CHECK case),
    # invalidation at 0.03 -- structure still intact, worth a limit order.
    assert lo.should_place_limit_order(0.038, 0.044, 0.03) is True


def test_should_place_limit_order_case_a_structure_broken_never_a_limit_order():
    # price fell THROUGH the invalidation -- dead setup, reject outright.
    assert lo.should_place_limit_order(0.038, 0.025, 0.03) is False


def test_should_place_limit_order_price_at_invalidation_exactly_rejects():
    assert lo.should_place_limit_order(0.038, 0.03, 0.03) is False


def test_should_place_limit_order_price_moved_down_not_up_no_limit_order():
    # fresh price BELOW the signal price but still above invalidation --
    # this is a favorable move, not the "got more expensive" case; the
    # existing _execution_rr_still_valid path already handles it upstream.
    assert lo.should_place_limit_order(0.038, 0.035, 0.03) is False


@pytest.mark.parametrize("signal_price,fresh_price,invalidation", [
    (None, 0.044, 0.03), (0.038, None, 0.03), (0.038, 0.044, None),
])
def test_should_place_limit_order_missing_data_fails_closed(signal_price, fresh_price, invalidation):
    assert lo.should_place_limit_order(signal_price, fresh_price, invalidation) is False


def test_should_enter_watching_within_trigger_mult():
    target = 0.038
    assert lo.should_enter_watching(target, target * 1.10) is True  # exactly at the boundary
    assert lo.should_enter_watching(target, target * 1.09) is True
    assert lo.should_enter_watching(target, target * 1.11) is False


def test_should_enter_watching_missing_price():
    assert lo.should_enter_watching(0.038, None) is False
    assert lo.should_enter_watching(0.038, 0.0) is False


def test_check_watching_order_trigger():
    assert lo.check_watching_order(0.038, 0.03, 0.037) == "trigger"
    assert lo.check_watching_order(0.038, 0.03, 0.038) == "trigger"  # exactly at target


def test_check_watching_order_cancel_invalidation_crossed():
    assert lo.check_watching_order(0.038, 0.03, 0.029) == "cancel"
    assert lo.check_watching_order(0.038, 0.03, 0.03) == "cancel"  # exactly at invalidation


def test_check_watching_order_wait():
    assert lo.check_watching_order(0.038, 0.03, 0.040) == "wait"


def test_check_watching_order_missing_price_waits():
    assert lo.check_watching_order(0.038, 0.03, None) == "wait"


# ── Item #158, 28/07: bonding-specific market-cap-proxy floor ───────────────


def test_should_place_limit_order_bonding_below_floor_rejects():
    from aria_core.bonding_entry import CHAIN_MARKER

    assert lo.should_place_limit_order(
        0.038, 0.044, 0.03, chain=CHAIN_MARKER, liquidity_usd=10_000.0,
    ) is False


def test_should_place_limit_order_bonding_above_floor_allows():
    from aria_core.bonding_entry import CHAIN_MARKER

    assert lo.should_place_limit_order(
        0.038, 0.044, 0.03, chain=CHAIN_MARKER, liquidity_usd=25_000.0,
    ) is True


def test_should_place_limit_order_bonding_unknown_liquidity_fails_closed():
    from aria_core.bonding_entry import CHAIN_MARKER

    assert lo.should_place_limit_order(
        0.038, 0.044, 0.03, chain=CHAIN_MARKER, liquidity_usd=None,
    ) is False


def test_should_place_limit_order_non_bonding_ignores_the_bonding_floor():
    # A non-bonding candidate is never subject to the bonding-only floor,
    # even with a low liquidity_usd value.
    assert lo.should_place_limit_order(
        0.038, 0.044, 0.03, chain="base", liquidity_usd=1_000.0,
    ) is True


# ── DB CRUD ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_pending_order_and_has_active_order():
    sig = _sig()
    assert await lo.has_active_order("0xCHECK", "base") is False
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, sig)
    assert order["state"] == "pending"
    assert order["target_price"] == pytest.approx(0.038)
    assert await lo.has_active_order("0xCHECK", "base") is True


@pytest.mark.asyncio
async def test_get_active_orders_excludes_resolved():
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    active = await lo.get_active_orders()
    assert len(active) == 1
    await lo.mark_cancelled(order["id"], "invalidation_crossed")
    assert await lo.get_active_orders() == []


@pytest.mark.asyncio
async def test_transition_to_watching_sets_timestamp():
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    await lo.transition_to_watching(order["id"])
    active = await lo.get_active_orders()
    assert active[0]["state"] == "watching"
    assert active[0]["watch_entered_at"] is not None


@pytest.mark.asyncio
async def test_mark_triggered_removes_from_active():
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    await lo.mark_triggered(order["id"])
    assert await lo.get_active_orders() == []


@pytest.mark.asyncio
async def test_sweep_expired(monkeypatch):
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    # Force it into the past directly via SQL (create_pending_order always
    # computes a future expires_at -- this simulates time having passed).
    import aiosqlite

    async with aiosqlite.connect(lo.DB_PATH) as db:
        await db.execute(
            "UPDATE pending_limit_order SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (order["id"],),
        )
        await db.commit()

    expired = await lo.sweep_expired()
    assert len(expired) == 1
    assert expired[0]["id"] == order["id"]
    assert await lo.get_active_orders() == []


@pytest.mark.asyncio
async def test_sweep_expired_never_touches_still_valid_orders():
    await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    expired = await lo.sweep_expired()
    assert expired == []
    assert len(await lo.get_active_orders()) == 1


# ── reanalyze_for_watching ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reanalyze_for_watching_honeypot_clear(monkeypatch):
    from aria_core import momentum_entry

    async def _clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _clear)
    order = {"contract": "0xCHECK", "chain": "base"}
    assert await lo.reanalyze_for_watching(order) is True


@pytest.mark.asyncio
async def test_reanalyze_for_watching_honeypot_confirmed_cancels(monkeypatch):
    from aria_core import momentum_entry

    async def _honeypot(contract, chain):
        return False, "honeypot confirmé (GoPlus)", "honeypot_rejected"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot)
    order = {"contract": "0xCHECK", "chain": "base"}
    assert await lo.reanalyze_for_watching(order) is False


@pytest.mark.asyncio
async def test_reanalyze_for_watching_network_failure_fails_closed(monkeypatch):
    from aria_core import momentum_entry

    async def _boom(contract, chain):
        raise RuntimeError("network down")

    monkeypatch.setattr(momentum_entry, "check_honeypot", _boom)
    order = {"contract": "0xCHECK", "chain": "base"}
    assert await lo.reanalyze_for_watching(order) is False


# ── Item #158, 28/07: bonding-native re-analysis (never GoPlus honeypot) ─────


def _fake_bonding_token(**overrides):
    from aria_core.services.virtuals import VirtualToken

    defaults = dict(
        name="Bonding Token", symbol="BOND", status="UNDERGRAD", chain="BASE",
        pre_token_address="0xPRE0000000000000000000000000000000000abcd",
        dev_holding_pct=0.5, liquidity_usd=25_000.0,
    )
    defaults.update(overrides)
    return VirtualToken(**defaults)


@pytest.mark.asyncio
async def test_reanalyze_for_watching_bonding_routes_away_from_goplus(monkeypatch):
    """A bonding order must NEVER call GoPlus (momentum_entry.check_honeypot)
    -- structurally inapplicable, see bonding_entry.py's own docstring."""
    from aria_core import momentum_entry
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.services import virtuals

    called = {"goplus": False}

    async def _boom_if_called(contract, chain):
        called["goplus"] = True
        return True, "should never be reached", "honeypot_clear"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _boom_if_called)

    class _FakeClient:
        async def fetch_by_address(self, token_address, chain="BASE"):
            return _fake_bonding_token()

    monkeypatch.setattr(virtuals, "virtuals_client", _FakeClient())

    order = {"contract": "0xBOND", "chain": CHAIN_MARKER}
    assert await lo.reanalyze_for_watching(order) is True
    assert called["goplus"] is False


@pytest.mark.asyncio
async def test_reanalyze_for_watching_bonding_dev_holding_too_high_cancels(monkeypatch):
    from aria_core import bonding_entry
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.services import virtuals

    class _FakeClient:
        async def fetch_by_address(self, token_address, chain="BASE"):
            return _fake_bonding_token(dev_holding_pct=bonding_entry._MAX_DEV_HOLDING_PCT + 1.0)

    monkeypatch.setattr(virtuals, "virtuals_client", _FakeClient())

    order = {"contract": "0xBOND", "chain": CHAIN_MARKER}
    assert await lo.reanalyze_for_watching(order) is False


@pytest.mark.asyncio
async def test_reanalyze_for_watching_bonding_liquidity_drained_cancels(monkeypatch):
    from aria_core import bonding_entry
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.services import virtuals

    class _FakeClient:
        async def fetch_by_address(self, token_address, chain="BASE"):
            return _fake_bonding_token(liquidity_usd=bonding_entry._MIN_LIQUIDITY_USD - 1.0)

    monkeypatch.setattr(virtuals, "virtuals_client", _FakeClient())

    order = {"contract": "0xBOND", "chain": CHAIN_MARKER}
    assert await lo.reanalyze_for_watching(order) is False


@pytest.mark.asyncio
async def test_reanalyze_for_watching_bonding_token_unresolved_fails_closed(monkeypatch):
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.services import virtuals

    class _FakeClient:
        async def fetch_by_address(self, token_address, chain="BASE"):
            return None

    monkeypatch.setattr(virtuals, "virtuals_client", _FakeClient())

    order = {"contract": "0xBOND", "chain": CHAIN_MARKER}
    assert await lo.reanalyze_for_watching(order) is False


@pytest.mark.asyncio
async def test_reanalyze_for_watching_bonding_network_failure_fails_closed(monkeypatch):
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.services import virtuals

    class _FakeClient:
        async def fetch_by_address(self, token_address, chain="BASE"):
            raise RuntimeError("network down")

    monkeypatch.setattr(virtuals, "virtuals_client", _FakeClient())

    order = {"contract": "0xBOND", "chain": CHAIN_MARKER}
    assert await lo.reanalyze_for_watching(order) is False


# ── Item #182, 28/07: golden-pocket liberation re-analysis (DEX quality, not
#    an already-confirmed golden pocket) ─────────────────────────────────────


def _golden_pocket_pending_order():
    sig = {"limit_order_reason": "golden_pocket_pending"}
    return {"contract": "0xCHECK", "chain": "base", "signal_json": json.dumps(sig)}


@pytest.mark.asyncio
async def test_reanalyze_for_watching_routes_golden_pocket_pending_to_dex_quality(monkeypatch):
    """A golden_pocket_pending order must be routed to the DEX-quality
    re-check, never the plain honeypot-only path -- verified by making the
    plain path explode if it were ever reached."""
    from aria_core import momentum_entry, risk_guard

    async def _boom_if_plain_path_reached(contract, chain):
        raise AssertionError("plain reanalyze_for_watching path must not run")

    async def _honeypot_clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    async def _fresh_score(contract, chain):
        from aria_core import dex_composite_score as dcs

        return dcs.DexSecurityScore(score=risk_guard.DEX_QUALITY_WATCH_THRESHOLD + 5.0)

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot_clear)
    monkeypatch.setattr(momentum_entry, "refresh_dex_composite_score", _fresh_score)
    order = _golden_pocket_pending_order()
    assert await lo.reanalyze_for_watching(order) is True


@pytest.mark.asyncio
async def test_reanalyze_dex_quality_cancels_on_honeypot_failure(monkeypatch):
    """The one hard guardrail this pipeline always enforces -- never skipped
    for a golden-pocket-pending order either."""
    from aria_core import momentum_entry

    called = {"score": False}

    async def _honeypot_confirmed(contract, chain):
        return False, "honeypot confirmé (GoPlus)", "honeypot_rejected"

    async def _fresh_score(contract, chain):
        called["score"] = True
        from aria_core import dex_composite_score as dcs

        return dcs.DexSecurityScore(score=100.0)

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot_confirmed)
    monkeypatch.setattr(momentum_entry, "refresh_dex_composite_score", _fresh_score)
    order = _golden_pocket_pending_order()
    assert await lo.reanalyze_for_watching(order) is False
    assert called["score"] is False  # short-circuits before spending the extra call


@pytest.mark.asyncio
async def test_reanalyze_dex_quality_cancels_when_score_degraded(monkeypatch):
    """The order's whole premise was the DEX composite score -- if it no
    longer clears the bar, cancel, even though honeypot is still clear."""
    from aria_core import momentum_entry, risk_guard

    async def _honeypot_clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    async def _degraded_score(contract, chain):
        from aria_core import dex_composite_score as dcs

        return dcs.DexSecurityScore(score=risk_guard.DEX_QUALITY_WATCH_THRESHOLD - 1.0)

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot_clear)
    monkeypatch.setattr(momentum_entry, "refresh_dex_composite_score", _degraded_score)
    order = _golden_pocket_pending_order()
    assert await lo.reanalyze_for_watching(order) is False


@pytest.mark.asyncio
async def test_reanalyze_dex_quality_fails_closed_when_score_unresolved(monkeypatch):
    """Unlike the additive-signal doctrine elsewhere (unresolved never
    rejects a BUY), THIS order's only reason to exist is the score -- an
    unresolved re-check must cancel, never default to watching blind."""
    from aria_core import momentum_entry

    async def _honeypot_clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    async def _unresolved(contract, chain):
        return None

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot_clear)
    monkeypatch.setattr(momentum_entry, "refresh_dex_composite_score", _unresolved)
    order = _golden_pocket_pending_order()
    assert await lo.reanalyze_for_watching(order) is False


@pytest.mark.asyncio
async def test_reanalyze_dex_quality_fails_closed_on_network_error(monkeypatch):
    from aria_core import momentum_entry

    async def _honeypot_clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    async def _boom(contract, chain):
        raise RuntimeError("network down")

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot_clear)
    monkeypatch.setattr(momentum_entry, "refresh_dex_composite_score", _boom)
    order = _golden_pocket_pending_order()
    assert await lo.reanalyze_for_watching(order) is False


@pytest.mark.asyncio
async def test_reanalyze_for_watching_plain_order_unaffected_by_golden_pocket_routing(monkeypatch):
    """A standard order (no limit_order_reason, or signal_json missing
    entirely) keeps the exact pre-#182 behavior -- honeypot-only re-check."""
    from aria_core import momentum_entry

    async def _honeypot_clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot_clear)
    order = {"contract": "0xCHECK", "chain": "base", "signal_json": json.dumps({"target": 1.0})}
    assert await lo.reanalyze_for_watching(order) is True


# ── check_rsi_divergence_watching_order (Item #183, 28/07) ──────────────────


def _rsi_watch_order(**overrides):
    sig = {
        "limit_order_reason": "rsi_divergence_pending", "invalidation": 1.19,
        "last_candle_ts": 5 * 3600,
    }
    sig.update(overrides.pop("sig_overrides", {}))
    base = {"contract": "0xCHECK", "chain": "base", "target_price": 1.5, "signal_json": json.dumps(sig)}
    base.update(overrides)
    return base, sig


def _rsi_pair(price_usd=1.5):
    return PairSnapshot(pair_address="0xpool", base_address="0xcheck", price_usd=price_usd)


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_triggers_on_span_within_window(monkeypatch):
    from aria_core import momentum_entry
    from aria_core.skills import entry_signals

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, **kw):
        from aria_core.skills.ta_levels import Candle

        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]  # <= last_candle_ts, never expires

    def _fake_detail(candles, **kw):
        return entry_signals.RsiDivergenceDetail(True, "divergence", gap=5.0, span=18)

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(entry_signals, "_bullish_rsi_divergence_detail", _fake_detail)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "trigger"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_replaces_stale_reasons_on_trigger(monkeypatch):
    """29/07 -- real bug found via operator screenshot comparison (chart vs.
    buy thesis): ``sig["reasons"]`` used to keep the ORIGINAL watch-creation
    wording ("divergence RSI pas encore confirmée"), persisted as-is into the
    executed BUY's thesis (_execute_trigger) -- the opposite of what just
    happened. Must be replaced with the CONFIRMED divergence detail (span/
    gap), never left stale, never merely appended (which would read as a
    self-contradicting mix of stale + fresh)."""
    from aria_core import momentum_entry
    from aria_core.skills import entry_signals

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, **kw):
        from aria_core.skills.ta_levels import Candle

        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]

    def _fake_detail(candles, **kw):
        return entry_signals.RsiDivergenceDetail(True, "divergence", gap=5.0, span=18)

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(entry_signals, "_bullish_rsi_divergence_detail", _fake_detail)

    order, sig = _rsi_watch_order(
        sig_overrides={"reasons": ["prix déjà dans la golden pocket mais divergence RSI pas encore confirmée"]},
    )
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "trigger"

    assert len(sig["reasons"]) == 1


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_sets_gap_span_on_trigger(monkeypatch):
    """Item #247 (30/07): the CONFIRMED divergence's own gap/span (re-checked
    on fresh candles, never the stale watch-creation values) must reach
    ``sig`` so ``process_active_orders`` can log this trigger's real
    "steepness" without re-deriving it."""
    from aria_core import momentum_entry
    from aria_core.skills import entry_signals

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, **kw):
        from aria_core.skills.ta_levels import Candle

        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]

    def _fake_detail(candles, **kw):
        return entry_signals.RsiDivergenceDetail(True, "divergence", gap=7.25, span=16)

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(entry_signals, "_bullish_rsi_divergence_detail", _fake_detail)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "trigger"

    assert sig["rsi_gap"] == pytest.approx(7.25)
    assert sig["rsi_span"] == 16


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_refetches_with_scalping_mode_for_scalping_wallet(monkeypatch):
    """Item #199 (29/07): the re-check must fetch candles at the SAME
    timeframe the order was placed under, derived from `order["wallet"]`
    (already stored on every pending order) -- never the standard-mode
    default, which would silently corrupt the divergence detection's own
    timeframe for a scalping order."""
    from aria_core import momentum_entry

    captured_mode = {}

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, mode="standard", **kw):
        from aria_core.skills.ta_levels import Candle

        captured_mode["mode"] = mode
        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)

    order, sig = _rsi_watch_order(wallet="scalping")
    await lo.check_rsi_divergence_watching_order(order, sig)

    assert captured_mode["mode"] == "scalping"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_refetches_with_scalping_mode_for_swing_wallet(monkeypatch):
    """31/07, explicit operator decision: swing's own WATCH phase (once an
    order reaches its target zone) now uses fine-grained (15-30min) candles,
    same as scalping -- swing finds the setup on big timeframes, then this
    fine-grained lens confirms the precise entry timing. Was "standard" mode
    before this change (see git history for the prior version of this test)."""
    from aria_core import momentum_entry

    captured_mode = {}

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, mode="standard", **kw):
        from aria_core.skills.ta_levels import Candle

        captured_mode["mode"] = mode
        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)

    order, sig = _rsi_watch_order(wallet="swing")
    await lo.check_rsi_divergence_watching_order(order, sig)

    assert captured_mode["mode"] == "scalping"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_refetches_with_standard_mode_for_vc_wallet(monkeypatch):
    """VC unaffected by the 31/07 swing change -- still standard mode."""
    from aria_core import momentum_entry

    captured_mode = {}

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, mode="standard", **kw):
        from aria_core.skills.ta_levels import Candle

        captured_mode["mode"] = mode
        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)

    order, sig = _rsi_watch_order(wallet="vc")
    await lo.check_rsi_divergence_watching_order(order, sig)

    assert captured_mode["mode"] == "standard"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_waits_on_span_outside_window(monkeypatch):
    """A divergence CAN be present but with too short/too long a span --
    never triggers on a looser span than the operator-validated window."""
    from aria_core import momentum_entry
    from aria_core.skills import entry_signals

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, **kw):
        from aria_core.skills.ta_levels import Candle

        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]

    def _fake_detail(candles, **kw):
        return entry_signals.RsiDivergenceDetail(True, "divergence", gap=5.0, span=5)

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(entry_signals, "_bullish_rsi_divergence_detail", _fake_detail)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "wait"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_waits_when_no_divergence_yet(monkeypatch):
    from aria_core import momentum_entry
    from aria_core.skills import entry_signals

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, **kw):
        from aria_core.skills.ta_levels import Candle

        return [Candle(ts=i * 3600, open=1, high=1, low=1, close=1) for i in range(6)]

    def _fake_detail(candles, **kw):
        return entry_signals.RsiDivergenceDetail(False, "")

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(entry_signals, "_bullish_rsi_divergence_detail", _fake_detail)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "wait"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_cancels_on_invalidation_crossed(monkeypatch):
    """The golden pocket setup itself died while waiting -- cancel, never
    trigger on a broken structure, even if a divergence somehow reads True."""
    from aria_core import momentum_entry

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair(price_usd=1.0)]  # below invalidation (1.19)

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "cancel"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_expires_past_candle_horizon(monkeypatch):
    """More new candles observed since creation than RSI_WATCH_MAX_HORIZON_
    CANDLES, with no qualifying divergence -- silent expiry, never a cancel
    Telegram alert (same doctrine as sweep_expired)."""
    from aria_core import momentum_entry
    from aria_core.skills.ta_levels import Candle

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _fake_fetch_candles(pool_address, chain, *, contract="", pair=None, **kw):
        # last_candle_ts=5*3600 (18000) -- 25 new hourly candles after it,
        # well past the 20-candle horizon.
        return [Candle(ts=(6 + i) * 3600, open=1, high=1, low=1, close=1) for i in range(25)]

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _fake_fetch_candles)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "expire"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_waits_on_pair_lookup_failure(monkeypatch):
    from aria_core import momentum_entry

    async def _boom(contract, *, chain="base"):
        raise RuntimeError("network down")

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _boom)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "wait"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_waits_on_no_pair(monkeypatch):
    from aria_core import momentum_entry

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return []

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "wait"


@pytest.mark.asyncio
async def test_check_rsi_divergence_watching_waits_on_candle_fetch_failure(monkeypatch):
    from aria_core import momentum_entry

    async def _fake_fetch_pairs(contract, *, chain="base"):
        return [_rsi_pair()]

    async def _boom(pool_address, chain, *, contract="", pair=None, **kw):
        raise RuntimeError("candles down")

    monkeypatch.setattr(momentum_entry, "fetch_token_pairs", _fake_fetch_pairs)
    monkeypatch.setattr(momentum_entry, "_fetch_candles", _boom)

    order, sig = _rsi_watch_order()
    assert await lo.check_rsi_divergence_watching_order(order, sig) == "wait"


@pytest.mark.asyncio
async def test_process_active_orders_routes_rsi_divergence_pending_to_dedicated_check(monkeypatch):
    """process_active_orders must dispatch a rsi_divergence_pending watching
    order to check_rsi_divergence_watching_order, never the plain price
    comparison (check_watching_order) -- verified by making the plain path
    unreachable (a stale target_price that would otherwise spuriously trigger)."""
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order(
        "0xCHECK", "base", "CHECK", 1.5,
        {"limit_order_reason": "rsi_divergence_pending", "invalidation": 1.19, "last_candle_ts": 0},
    )
    await lo.transition_to_watching(order["id"])

    called = {"dedicated": False}

    async def _fake_dedicated(order_arg, sig_arg):
        called["dedicated"] = True
        return "wait"

    async def _price(contract, *, chain="base"):
        return 1.5

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_dedicated)
    await lo.process_active_orders(_price)
    assert called["dedicated"] is True


# ── rsi_divergence_log wiring (Item #247, 30/07) ─────────────────────────────


@pytest.mark.asyncio
async def test_process_active_orders_logs_expired_rsi_divergence(monkeypatch):
    from aria_core import rsi_divergence_log

    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order(
        "0xCHECK", "base", "CHECK", 1.5,
        {"limit_order_reason": "rsi_divergence_pending", "invalidation": 1.19, "last_candle_ts": 0},
    )
    await lo.transition_to_watching(order["id"])

    async def _fake_dedicated(order_arg, sig_arg):
        return "expire"

    async def _price(contract, *, chain="base"):
        return 1.5

    calls = []

    async def _fake_record(contract, chain, **kw):
        calls.append({"contract": contract, "chain": chain, **kw})

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_dedicated)
    monkeypatch.setattr(rsi_divergence_log, "record_divergence", _fake_record)
    await lo.process_active_orders(_price)

    assert len(calls) == 1
    assert calls[0]["outcome"] == "expired_unconfirmed"


@pytest.mark.asyncio
async def test_process_active_orders_logs_cancelled_rsi_divergence(monkeypatch):
    from aria_core import rsi_divergence_log

    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order(
        "0xCHECK", "base", "CHECK", 1.5,
        {"limit_order_reason": "rsi_divergence_pending", "invalidation": 1.19, "last_candle_ts": 0},
    )
    await lo.transition_to_watching(order["id"])

    async def _fake_dedicated(order_arg, sig_arg):
        return "cancel"

    async def _price(contract, *, chain="base"):
        return 1.5

    calls = []

    async def _fake_record(contract, chain, **kw):
        calls.append({"contract": contract, "chain": chain, **kw})

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_dedicated)
    monkeypatch.setattr(rsi_divergence_log, "record_divergence", _fake_record)
    await lo.process_active_orders(_price)

    assert len(calls) == 1
    assert calls[0]["outcome"] == "cancelled_unconfirmed"


@pytest.mark.asyncio
async def test_process_active_orders_never_logs_non_divergence_cancel(monkeypatch):
    """A plain price-drift/golden-pocket order's invalidation-crossed cancel
    has nothing to do with divergence "steepness" -- must never be logged
    into the divergence log's cancelled_unconfirmed bucket."""
    from aria_core import rsi_divergence_log

    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(invalidation=0.03))
    await lo.transition_to_watching(order["id"])

    calls = []

    async def _fake_record(contract, chain, **kw):
        calls.append({"contract": contract, "chain": chain, **kw})

    async def _price(contract, *, chain="base"):
        return 0.029  # below invalidation

    monkeypatch.setattr(rsi_divergence_log, "record_divergence", _fake_record)
    await lo.process_active_orders(_price)

    assert calls == []


@pytest.mark.asyncio
async def test_process_active_orders_logs_triggered_rsi_divergence_with_gap_span(monkeypatch):
    from aria_core import rsi_divergence_log

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order(
        "0xCHECK", "base", "CHECK", 1.5,
        {
            "limit_order_reason": "rsi_divergence_pending", "invalidation": 1.19, "last_candle_ts": 0,
            "target": 2.5, "rr": 2.5,
        },
    )
    await lo.transition_to_watching(order["id"])

    async def _fake_dedicated(order_arg, sig_arg):
        # Same mutate-in-place contract as the real function.
        sig_arg["rsi_gap"] = 9.5
        sig_arg["rsi_span"] = 17
        return "trigger"

    async def _price(contract, *, chain="base"):
        return 1.5

    calls = []

    async def _fake_record(contract, chain, **kw):
        calls.append({"contract": contract, "chain": chain, **kw})

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_dedicated)
    monkeypatch.setattr(rsi_divergence_log, "record_divergence", _fake_record)
    actions = await lo.process_active_orders(_price)

    assert len(actions["triggered"]) == 1
    assert len(calls) == 1
    assert calls[0]["outcome"] == "bought_via_limit_order"
    assert calls[0]["gap"] == pytest.approx(9.5)
    assert calls[0]["span"] == 17


# ── process_active_orders orchestration ──────────────────────────────────────


def _fake_risk_state(*, wallet="swing", blocked=False, alloc_multiplier=1.0, equity=1_000_000.0):
    return risk_guard.PortfolioRiskState(
        wallet=wallet, equity=equity, high_water_mark=equity, drawdown_pct=0.0,
        consecutive_losses=0, alloc_multiplier=alloc_multiplier, blocked=blocked,
    )


@pytest.mark.asyncio
async def test_process_active_orders_pending_stays_pending_far_from_target(monkeypatch):
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())

    async def _price(contract, *, chain="base"):
        return 0.10  # far above target * 1.10 -- stays pending

    await lo.process_active_orders(_price)
    active = await lo.get_active_orders()
    assert len(active) == 1
    assert active[0]["state"] == "pending"


@pytest.mark.asyncio
async def test_process_active_orders_pending_to_watching_on_reanalysis_pass(monkeypatch):
    from aria_core import momentum_entry

    async def _clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _clear)
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())

    async def _price(contract, *, chain="base"):
        return 0.040  # within target * 1.10 (0.0418)

    await lo.process_active_orders(_price)
    active = await lo.get_active_orders()
    assert active[0]["state"] == "watching"


@pytest.mark.asyncio
async def test_process_active_orders_notifies_on_entering_watching(monkeypatch):
    """29/07 -- operator question ("plus le temps d'expiration est petit plus
    on est proche du point d'achat ?"): expiry never signals proximity --
    this pending->watching transition is the real one, now notified."""
    from aria_core import momentum_entry

    async def _clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _clear)
    await paper_trader.reset_portfolio(1_000_000.0)
    await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="swing")

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    async def _price(contract, *, chain="base"):
        return 0.040  # within target * 1.10

    await lo.process_active_orders(_price, notifier=_notifier)
    assert len(notified) == 1
    assert "se rapproche" in notified[0]
    assert "SWING" in notified[0]
    assert "0.04" in notified[0]


@pytest.mark.asyncio
async def test_process_active_orders_skips_watching_notification_for_rsi_divergence(monkeypatch):
    """The rsi_divergence_pending path enters "watching" almost instantly
    (target_price == price at creation) -- a notification here would just
    duplicate the "ORDRE LIMITE POSÉ" alert."""
    from aria_core import momentum_entry

    async def _clear(contract, chain):
        return True, "honeypot clear (GoPlus)", "honeypot_clear"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _clear)
    await paper_trader.reset_portfolio(1_000_000.0)
    await lo.create_pending_order(
        "0xCHECK", "base", "CHECK", 0.038, _sig(limit_order_reason="rsi_divergence_pending"), wallet="scalping",
    )

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    async def _price(contract, *, chain="base"):
        return 0.038

    await lo.process_active_orders(_price, notifier=_notifier)
    assert notified == []


@pytest.mark.asyncio
async def test_process_active_orders_pending_to_cancelled_on_reanalysis_fail(monkeypatch):
    from aria_core import momentum_entry

    async def _honeypot(contract, chain):
        return False, "honeypot confirmé (GoPlus)", "honeypot_rejected"

    monkeypatch.setattr(momentum_entry, "check_honeypot", _honeypot)
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    async def _price(contract, *, chain="base"):
        return 0.040

    await lo.process_active_orders(_price, notifier=_notifier)
    assert await lo.get_active_orders() == []
    assert len(notified) == 1
    assert "honeypot" in notified[0].lower() or "sécurité" in notified[0].lower()


@pytest.mark.asyncio
async def test_process_active_orders_watching_cancelled_on_invalidation_crossed(monkeypatch):
    """31/07 -- wallet="vc" pinned explicitly: swing's own watching orders now
    resolve via check_rsi_divergence_watching_order (a real network lookup),
    see test_process_active_orders_swing_watching_cancelled_on_invalidation
    below for that path's own cancel test."""
    await paper_trader.reset_portfolio(1_000_000.0, wallet="vc")
    order = await lo.create_pending_order(
        "0xCHECK", "base", "CHECK", 0.038, _sig(invalidation=0.03), wallet="vc",
    )
    await lo.transition_to_watching(order["id"])

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    async def _price(contract, *, chain="base"):
        return 0.029  # below invalidation

    await lo.process_active_orders(_price, notifier=_notifier)
    assert await lo.get_active_orders() == []
    assert len(notified) == 1


@pytest.mark.asyncio
async def test_process_active_orders_swing_watching_cancelled_on_invalidation(monkeypatch):
    """31/07 -- swing's own cancel path, routed through check_rsi_divergence_
    watching_order (mocked here -- its own real invalidation-crossing logic
    is covered by check_rsi_divergence_watching_order's dedicated tests)."""
    await paper_trader.reset_portfolio(1_000_000.0, wallet="swing")
    order = await lo.create_pending_order(
        "0xCHECK", "base", "CHECK", 0.038, _sig(invalidation=0.03), wallet="swing",
    )
    await lo.transition_to_watching(order["id"])

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    async def _fake_check(order_arg, sig_arg):
        return "cancel"

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_check)

    async def _price(contract, *, chain="base"):
        return 0.029

    await lo.process_active_orders(_price, notifier=_notifier)
    assert await lo.get_active_orders() == []
    assert len(notified) == 1


@pytest.mark.asyncio
async def test_process_active_orders_watching_triggers_buy(monkeypatch):
    """31/07 -- wallet="vc" pinned explicitly: swing's own watching orders no
    longer trigger on price alone (they now wait for a fresh RSI divergence,
    see test_process_active_orders_swing_watching_triggers_on_fresh_
    divergence below) -- VC keeps this plain price-comparison mechanism
    unchanged."""
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="vc")
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="vc")
    await lo.transition_to_watching(order["id"])

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    async def _price(contract, *, chain="base"):
        return 0.037  # at/below target -- triggers

    actions = await lo.process_active_orders(_price, notifier=_notifier)
    assert len(actions["triggered"]) == 1
    assert await lo.get_active_orders() == []
    pos = await paper_trader._get_open("0xCHECK")
    assert pos is not None
    assert pos["discovery_channel"] == "limit_order"
    assert pos["rr"] == pytest.approx(3.9)
    assert len(notified) == 1  # format_buy_alert


async def _fake_evaluate_portfolio_risk(wallet="swing", *, price_lookup=None):
    return _fake_risk_state(wallet=wallet)


@pytest.mark.asyncio
async def test_process_active_orders_watching_trigger_skipped_if_portfolio_blocked(monkeypatch):
    async def _blocked(wallet="swing", *, price_lookup=None):
        return _fake_risk_state(wallet=wallet, blocked=True)

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _blocked)
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    await lo.transition_to_watching(order["id"])

    async def _price(contract, *, chain="base"):
        return 0.037

    await lo.process_active_orders(_price)
    # never opened, order stays in "watching" -- may still fill on a later pass
    assert await paper_trader.has_open("0xCHECK") is False
    active = await lo.get_active_orders()
    assert len(active) == 1
    assert active[0]["state"] == "watching"


# ``is_market_dead`` (30/07) cancelled a ``watching`` order once its pool's
# hourly volume dried up. REMOVED 30/07, Item #251 -- operator's explicit
# call (screenshot of a real "marché devenu illiquide" cancellation,
# believed already gone along with the #246 24h volume floor). Its 3
# dedicated unit tests and the dead-market cancellation test above are gone
# along with it -- the surviving test below now covers a ``pair_lookup``-
# provided trigger regardless of volume, which is all that mechanism does
# now.

@pytest.mark.asyncio
async def test_process_active_orders_watching_alive_market_still_triggers(monkeypatch):
    """31/07 -- wallet="vc" pinned explicitly, same rationale as
    test_process_active_orders_watching_triggers_buy above."""
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="vc")
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="vc")
    await lo.transition_to_watching(order["id"])

    async def _pair(contract, *, chain="base"):
        return PairSnapshot(pair_address="0xpool", price_usd=0.037, volume_h1_usd=1_000.0)

    actions = await lo.process_active_orders(None, pair_lookup=_pair)
    assert len(actions["triggered"]) == 1
    pos = await paper_trader._get_open("0xCHECK")
    assert pos is not None


@pytest.mark.asyncio
async def test_process_active_orders_swing_watching_triggers_on_fresh_divergence(monkeypatch):
    """31/07, explicit operator decision: a swing order in "watching" now
    resolves via a fresh RSI-divergence re-check (fine-grained candles),
    never a plain price comparison -- even for reasons other than
    rsi_divergence_pending (e.g. this one, a plain price-drift order,
    limit_order_reason=None)."""
    from aria_core import momentum_entry

    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="swing")
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="swing")
    await lo.transition_to_watching(order["id"])

    async def _fake_check(order_arg, sig_arg):
        return "trigger"

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_check)

    async def _price(contract, *, chain="base"):
        return 0.037

    actions = await lo.process_active_orders(_price)
    assert len(actions["triggered"]) == 1
    pos = await paper_trader._get_open("0xCHECK", wallet="swing")
    assert pos is not None


@pytest.mark.asyncio
async def test_process_active_orders_swing_watching_waits_without_fresh_divergence(monkeypatch):
    """Mirror of the above: price alone reaching the target is NOT enough
    for a swing order anymore -- without a confirmed divergence, it stays in
    "watching"."""
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="swing")
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="swing")
    await lo.transition_to_watching(order["id"])

    async def _fake_check(order_arg, sig_arg):
        return "wait"

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_check)

    async def _price(contract, *, chain="base"):
        return 0.037  # would have triggered under the old plain-price mechanism

    actions = await lo.process_active_orders(_price)
    assert len(actions["triggered"]) == 0
    active = await lo.get_active_orders()
    assert len(active) == 1
    assert active[0]["state"] == "watching"


# ── historical_trigger_rate / reset_historical_trigger_rate (Item #227/#250)

@pytest.mark.asyncio
async def test_historical_trigger_rate_below_min_sample_returns_none():
    await lo._ensure_table()
    for i in range(5):
        order = await lo.create_pending_order(
            f"0x{i}", "base", "T", 1.0, {"limit_order_reason": "rsi_divergence_pending"},
        )
        await lo.mark_triggered(order["id"])
    rate, total = await lo.historical_trigger_rate("rsi_divergence_pending")
    assert rate is None
    assert total == 5


@pytest.mark.asyncio
async def test_historical_trigger_rate_computes_ratio_for_matching_reason_only():
    await lo._ensure_table()
    for i in range(7):
        order = await lo.create_pending_order(
            f"0xT{i}", "base", "T", 1.0, {"limit_order_reason": "rsi_divergence_pending"},
        )
        await lo.mark_triggered(order["id"])
    for i in range(3):
        order = await lo.create_pending_order(
            f"0xC{i}", "base", "T", 1.0, {"limit_order_reason": "rsi_divergence_pending"},
        )
        await lo.mark_cancelled(order["id"], "invalidation_crossed")
    # A different reason must never pollute this reason's own rate.
    other = await lo.create_pending_order(
        "0xOTHER", "base", "T", 1.0, {"limit_order_reason": "golden_pocket_pending"},
    )
    await lo.mark_triggered(other["id"])

    rate, total = await lo.historical_trigger_rate("rsi_divergence_pending")
    assert total == 10
    assert rate == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_historical_trigger_rate_ignores_still_active_orders():
    await lo._ensure_table()
    for i in range(10):
        order = await lo.create_pending_order(
            f"0x{i}", "base", "T", 1.0, {"limit_order_reason": "rsi_divergence_pending"},
        )
        await lo.mark_triggered(order["id"])
    # A pending order of the SAME reason must never count -- still undecided.
    await lo.create_pending_order(
        "0xPENDING", "base", "T", 1.0, {"limit_order_reason": "rsi_divergence_pending"},
    )

    rate, total = await lo.historical_trigger_rate("rsi_divergence_pending")
    assert total == 10
    assert rate == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_reset_historical_trigger_rate_excludes_orders_resolved_before_reset():
    """Item #250 (30/07), operator request ("reset les taux de déclenchement
    historique") after several same-day pipeline changes made the displayed
    rate a mix of regimes. Pre-reset history is preserved on disk (never
    deleted, see reset_historical_trigger_rate's own docstring) but excluded
    from the displayed rate going forward."""
    import aiosqlite as _aiosqlite

    await lo._ensure_table()
    ids = []
    for i in range(10):
        order = await lo.create_pending_order(
            f"0xOLD{i}", "base", "T", 1.0, {"limit_order_reason": "rsi_divergence_pending"},
        )
        await lo.mark_cancelled(order["id"], "invalidation_crossed")
        ids.append(order["id"])

    # Backdate resolved_at well before the reset -- avoids any clock-
    # resolution ambiguity with reset_historical_trigger_rate()'s own
    # timestamp; the real-world case (orders resolved days/weeks earlier)
    # always has this much separation anyway.
    async with _aiosqlite.connect(lo.DB_PATH) as db:
        await db.executemany(
            "UPDATE pending_limit_order SET resolved_at = ? WHERE id = ?",
            [("2000-01-01T00:00:00+00:00", i) for i in ids],
        )
        await db.commit()

    rate_before, total_before = await lo.historical_trigger_rate("rsi_divergence_pending")
    assert total_before == 10
    assert rate_before == pytest.approx(0.0)

    await lo.reset_historical_trigger_rate()

    rate_after_reset, total_after_reset = await lo.historical_trigger_rate("rsi_divergence_pending")
    assert total_after_reset == 0
    assert rate_after_reset is None

    for i in range(10):
        order = await lo.create_pending_order(
            f"0xNEW{i}", "base", "T", 1.0, {"limit_order_reason": "rsi_divergence_pending"},
        )
        await lo.mark_triggered(order["id"])

    rate_after, total_after = await lo.historical_trigger_rate("rsi_divergence_pending")
    assert total_after == 10
    assert rate_after == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_process_active_orders_sweeps_expired_orders_silently(monkeypatch):
    import aiosqlite

    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    async with aiosqlite.connect(lo.DB_PATH) as db:
        await db.execute(
            "UPDATE pending_limit_order SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (order["id"],),
        )
        await db.commit()

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    async def _price(contract, *, chain="base"):
        return 0.10

    actions = await lo.process_active_orders(_price, notifier=_notifier)
    assert actions["expired"] == 1
    assert notified == []  # silent by design, never a Telegram alert


@pytest.mark.asyncio
async def test_process_active_orders_price_lookup_failure_never_raises(monkeypatch):
    await paper_trader.reset_portfolio(1_000_000.0)
    await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())

    async def _boom(contract, *, chain="base"):
        raise RuntimeError("network down")

    actions = await lo.process_active_orders(_boom)  # must not raise
    assert actions["triggered"] == []


# ── 3-pocket architecture plan, Phase 2 (27/07) ──────────────────────────────
# Mirrors paper_trader.py's own multi_pocket_sourcing_enabled() gate: OFF
# (default) keeps every order implicitly "swing" (byte-for-byte unchanged),
# ON lets an order remember and execute into a specific pocket
# (scalping/swing/vc), never blocking or counting against a DIFFERENT pocket
# holding the same contract.


@pytest.mark.asyncio
async def test_migration_adds_wallet_column_defaults_to_swing(monkeypatch, tmp_path):
    """A DB created BEFORE the ``wallet`` column existed must migrate without
    crashing and without losing an already-pending order -- same hot-migration
    contract as paper_trader.py's own test_migration_adds_position_management_columns."""
    import aiosqlite

    db_path = str(tmp_path / "legacy_limit_orders.db")
    monkeypatch.setattr(lo, "DB_PATH", db_path)

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE pending_limit_order (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                symbol TEXT,
                target_price REAL NOT NULL,
                signal_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                watch_entered_at TEXT,
                resolved_at TEXT,
                cancel_reason TEXT
            )
            """
        )
        await db.execute(
            "INSERT INTO pending_limit_order (contract, chain, symbol, target_price, signal_json, "
            "state, created_at, expires_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            ("0xLEGACY", "base", "LEGACY", 0.038, "{}", "2026-01-01T00:00:00+00:00",
             "2026-01-01T03:00:00+00:00"),
        )
        await db.commit()

    await lo._ensure_table()  # must never crash, on a fresh OR a pre-existing DB

    active = await lo.get_active_orders()
    assert len(active) == 1
    assert active[0]["wallet"] == "swing"


@pytest.mark.asyncio
async def test_create_pending_order_defaults_wallet_to_swing():
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
    assert order["wallet"] == "swing"
    active = await lo.get_active_orders()
    assert active[0]["wallet"] == "swing"


@pytest.mark.asyncio
async def test_create_pending_order_respects_explicit_wallet():
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="vc")
    assert order["wallet"] == "vc"
    active = await lo.get_active_orders()
    assert active[0]["wallet"] == "vc"


@pytest.mark.asyncio
async def test_has_active_order_scoped_per_wallet_isolates_pockets():
    """The whole point of 3 concurrent pockets: a pending order already placed
    by ONE pocket must never be visible to a DIFFERENT pocket checking the
    SAME contract."""
    await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="scalping")

    assert await lo.has_active_order("0xCHECK", "base", wallet="scalping") is True
    assert await lo.has_active_order("0xCHECK", "base", wallet="swing") is False
    assert await lo.has_active_order("0xCHECK", "base", wallet="vc") is False
    # default (no wallet=) implicitly means "swing" -- unchanged behavior.
    assert await lo.has_active_order("0xCHECK", "base") is False


@pytest.mark.asyncio
async def test_wallet_position_cap_gate_off_always_legacy_max_positions(monkeypatch):
    monkeypatch.delenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", raising=False)
    assert lo._wallet_position_cap(paper_trader, "vc") == paper_trader.MAX_POSITIONS
    assert lo._wallet_position_cap(paper_trader, "swing") == paper_trader.MAX_POSITIONS
    assert lo._wallet_position_cap(paper_trader, "scalping") == paper_trader.MAX_POSITIONS


@pytest.mark.asyncio
async def test_wallet_position_cap_gate_on_maps_to_real_per_pocket_caps(monkeypatch):
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    assert lo._wallet_position_cap(paper_trader, "vc") == paper_trader.MAX_POSITIONS_VC == 5
    assert lo._wallet_position_cap(paper_trader, "swing") == paper_trader.MAX_POSITIONS_SWING == 15
    assert lo._wallet_position_cap(paper_trader, "scalping") == paper_trader.MAX_POSITIONS_SCALPING is None


@pytest.mark.asyncio
async def test_execute_trigger_books_into_the_orders_own_wallet(monkeypatch):
    """Gate ON: a triggered limit order must book into the SAME pocket it was
    placed for, never a hardcoded "swing"."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="vc")
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="vc")
    await lo.transition_to_watching(order["id"])

    async def _price(contract, *, chain="base"):
        return 0.037  # at/below target -- triggers

    actions = await lo.process_active_orders(_price)
    assert len(actions["triggered"]) == 1
    assert actions["triggered"][0]["wallet"] == "vc"
    assert await paper_trader.has_open("0xCHECK", wallet="vc") is True
    assert await paper_trader.has_open("0xCHECK", wallet="swing") is False


@pytest.mark.asyncio
async def test_execute_trigger_scalping_pocket_sets_scalping_mode(monkeypatch):
    """29/07 -- real bug found via a live position (wstETH, id 11): a
    scalping-pocket order used to trigger into open_position's default
    mode="standard", silently governed by the wrong exit discipline (no
    scalping bearish-RSI-divergence exit, #105). ``mode`` must mirror
    ``wallet`` exactly like the direct-buy 3-pocket loop already does."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="scalping")
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="scalping")
    await lo.transition_to_watching(order["id"])

    async def _price(contract, *, chain="base"):
        return 0.037

    actions = await lo.process_active_orders(_price)
    assert len(actions["triggered"]) == 1
    assert actions["triggered"][0]["wallet"] == "scalping"
    assert actions["triggered"][0]["mode"] == "scalping"


@pytest.mark.asyncio
async def test_execute_trigger_swing_pocket_keeps_standard_mode(monkeypatch):
    """31/07 -- swing's WATCH mechanism now needs a confirmed fresh divergence
    to trigger (check_rsi_divergence_watching_order mocked here), but the
    resulting POSITION's own trading mode (exit discipline: stop/TP, distinct
    from the watch candle granularity) stays "standard" for swing, unchanged."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="swing")
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="swing")
    await lo.transition_to_watching(order["id"])

    async def _fake_check(order_arg, sig_arg):
        return "trigger"

    monkeypatch.setattr(lo, "check_rsi_divergence_watching_order", _fake_check)

    async def _price(contract, *, chain="base"):
        return 0.037

    actions = await lo.process_active_orders(_price)
    assert len(actions["triggered"]) == 1
    assert actions["triggered"][0]["mode"] == "standard"


@pytest.mark.asyncio
async def test_execute_trigger_different_wallet_holding_same_contract_never_blocks(monkeypatch):
    """A "swing" position already open on a contract must never block a
    "scalping" pocket's own limit-order trigger on that SAME contract."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="swing")
    await paper_trader.reset_portfolio(1_000_000.0, wallet="scalping")
    assert await paper_trader.open_position(
        "0xCHECK", "CHECK", 1.0, alloc_usd=10_000, wallet="swing",
    ) is not None

    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="scalping")
    await lo.transition_to_watching(order["id"])

    async def _price(contract, *, chain="base"):
        return 0.037

    actions = await lo.process_active_orders(_price)
    assert len(actions["triggered"]) == 1
    assert actions["triggered"][0]["wallet"] == "scalping"
    assert await paper_trader.has_open("0xCHECK", wallet="swing") is True
    assert await paper_trader.has_open("0xCHECK", wallet="scalping") is True


@pytest.mark.asyncio
async def test_execute_trigger_gate_on_stops_at_the_real_per_pocket_cap(monkeypatch):
    """Gate ON: a "vc" order must not trigger once the REAL per-pocket cap
    (MAX_POSITIONS_VC = 5) is already reached -- unlike gate OFF, which would
    still allow it under the legacy flat MAX_POSITIONS (30)."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="vc")
    for i in range(paper_trader.MAX_POSITIONS_VC):
        c = "0x" + f"{i:040x}"
        assert await paper_trader.open_position(c, f"T{i}", 1.0, alloc_usd=1_000, wallet="vc") is not None
    assert len(await paper_trader.get_open_positions(wallet="vc")) == paper_trader.MAX_POSITIONS_VC

    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="vc")
    await lo.transition_to_watching(order["id"])

    async def _price(contract, *, chain="base"):
        return 0.037

    actions = await lo.process_active_orders(_price)
    assert actions["triggered"] == []  # cap reached -- never opened
    assert await paper_trader.has_open("0xCHECK", wallet="vc") is False
    # never lost -- stays "watching", may still fill on a later pass once room frees up.
    active = await lo.get_active_orders()
    assert len(active) == 1
    assert active[0]["state"] == "watching"


@pytest.mark.asyncio
async def test_execute_trigger_gate_off_ignores_per_pocket_cap_uses_legacy_value(monkeypatch):
    """Gate OFF: the legacy flat MAX_POSITIONS (30) governs regardless of
    ``wallet`` -- an order tagged "vc" (e.g. left over from a prior gate-ON
    session) is NOT held to the tighter real per-pocket cap (5) while the
    gate is off."""
    monkeypatch.delenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", raising=False)
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="vc")
    # past the REAL per-pocket VC cap (5), still well under the legacy flat one (30).
    for i in range(paper_trader.MAX_POSITIONS_VC + 2):
        c = "0x" + f"{i:040x}"
        assert await paper_trader.open_position(c, f"T{i}", 1.0, alloc_usd=1_000, wallet="vc") is not None

    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(), wallet="vc")
    await lo.transition_to_watching(order["id"])

    async def _price(contract, *, chain="base"):
        return 0.037

    actions = await lo.process_active_orders(_price)
    assert len(actions["triggered"]) == 1
    assert await paper_trader.has_open("0xCHECK", wallet="vc") is True


# ── format helpers ────────────────────────────────────────────────────────────


def test_format_limit_order_placed_alert_contains_target_and_symbol():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_placed_alert(order)
    assert "CHECK" in text
    assert "0.038" in text


def test_format_limit_order_placed_alert_shows_current_price_and_gap():
    """29/07 -- operator feedback: the alert was missing the current price."""
    import json as _json

    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038,
        "signal_json": _json.dumps({"price_at_order_placed": 0.044, "invalidation": 0.030, "rr": 3.9}),
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "0.044" in text
    assert "0.03" in text  # ``.6g`` formatting drops the trailing zero
    assert "3.9" in text
    # (0.044/0.038 - 1) * 100 = ~15.8%
    assert "15.8%" in text


def test_format_limit_order_placed_alert_omits_missing_fields_gracefully():
    """No signal_json at all (e.g. an order created before this fix) --
    never a crash, never an invented number."""
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_placed_alert(order)
    assert "Prix actuel" not in text
    assert "Invalidation" not in text
    assert "R/R" not in text


def test_format_limit_order_placed_alert_ignores_non_numeric_signal_fields():
    import json as _json

    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038,
        "signal_json": _json.dumps({"price_at_order_placed": None, "invalidation": "n/a", "rr": None}),
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "Prix actuel" not in text
    assert "Invalidation" not in text
    assert "R/R" not in text


def test_format_limit_order_placed_alert_shows_pocket_label():
    """29/07, second pass -- operator feedback: the alert never showed which
    pocket (swing/scalping/vc) placed the order, indistinguishable from any
    other pocket's order in Telegram."""
    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038,
        "wallet": "scalping",
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "SCALPING" in text


def test_format_limit_order_placed_alert_defaults_pocket_label_to_swing():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_placed_alert(order)
    assert "SWING" in text


def test_format_limit_order_placed_alert_rsi_divergence_wording_and_real_expiry():
    """29/07, second pass -- operator feedback ("elle cible le prix actuel,
    étrange"): a rsi_divergence_pending order's target_price literally equals
    the price at detection time (no pullback to describe) -- the generic
    "cible X, expire si le prix ne redescend jamais" wording is wrong here.
    Also its real expiry (created_at -> expires_at) is a candle-count horizon,
    often far from the flat LIMIT_ORDER_EXPIRY_HOURS (3h) this alert used to
    hardcode regardless of the order's actual expires_at."""
    import json as _json

    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.1087,
        "wallet": "scalping",
        "created_at": "2026-07-29T19:17:55.338409+00:00",
        "expires_at": "2026-07-30T10:17:55.338409+00:00",  # 15h, NOT the flat 3h
        "signal_json": _json.dumps({
            "limit_order_reason": "rsi_divergence_pending", "invalidation": 0.09, "rr": 2.5,
        }),
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "cible" not in text.lower()
    assert "golden pocket" in text.lower()
    assert "divergence" in text.lower()
    assert "Expire dans 15h" in text
    assert "Invalidation" in text
    assert "2.5" in text


def test_format_limit_order_placed_alert_no_longer_shows_zone_a_tenir():
    """Item #234 (30/07) added a "Zone à tenir pendant la formation" line
    (gp_low/gp_high, the golden-pocket range to hold while the divergence
    forms). REMOVED 30/07, Item #249 -- operator's explicit call after
    seeing it in a real alert screenshot and understanding what it meant
    ("j'ai compris supprime la zone a tenir"). Checked both with and
    without gp_low/gp_high present on the signal -- the line must never
    reappear either way."""
    import json as _json

    order_with_range = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.1087,
        "wallet": "swing",
        "signal_json": _json.dumps({
            "limit_order_reason": "rsi_divergence_pending", "gp_low": 0.0637954, "gp_high": 0.0776245,
        }),
    }
    order_without_range = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.1087,
        "wallet": "swing",
        "signal_json": _json.dumps({"limit_order_reason": "rsi_divergence_pending"}),
    }
    for order in (order_with_range, order_without_range):
        text = lo.format_limit_order_placed_alert(order)
        assert "tenir" not in text.lower()
        assert "0.0637954" not in text


def test_format_limit_order_placed_alert_shows_timeframe_per_pocket():
    """29/07 -- operator request: the alert never stated which candle
    timeframe the setup was analyzed on."""
    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038,
        "wallet": "scalping",
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "15-30min" in text
    assert "scalping" in text.lower()

    order["wallet"] = "swing"
    text = lo.format_limit_order_placed_alert(order)
    assert "1h+" in text


def test_format_limit_order_placed_alert_shows_estimated_size():
    """29/07 -- operator feedback ("ordre limite ne montre pas la taille de
    la future position"): an ESTIMATE only, clearly labeled as recomputed at
    trigger time (paper_trader.compute_entry_alloc, fresh context)."""
    import json as _json

    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038,
        "wallet": "swing",
        "signal_json": _json.dumps({"estimated_alloc_usd": 50_000.0, "estimated_alloc_pct": 5.0}),
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "Taille estimée : 50,000 $" in text
    assert "5.0%" in text
    assert "recalculée au déclenchement" in text


def test_format_limit_order_placed_alert_omits_estimated_size_when_absent():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_placed_alert(order)
    assert "Taille estimée" not in text


def test_format_limit_order_placed_alert_shows_sell_target_and_gain_pct():
    """29/07 -- operator feedback ("la cible doit apparaître aussi sur
    l'ordre limite et rajouter le pourcentage en face du prix en usdc"):
    ``target_price`` is the BUY trigger, never the real profit target
    (``sig["target"]``) -- the latter was never shown at all."""
    import json as _json

    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038,
        "signal_json": _json.dumps({"target": 0.076}),  # +100% from the buy trigger
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "Cible de vente : 0.076" in text
    assert "+100.0%" in text


def test_format_limit_order_placed_alert_shows_sell_target_for_rsi_divergence_too():
    import json as _json

    order = {
        "contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.05,
        "signal_json": _json.dumps({"limit_order_reason": "rsi_divergence_pending", "target": 0.06}),
    }
    text = lo.format_limit_order_placed_alert(order)
    assert "Cible de vente : 0.06" in text
    assert "+20.0%" in text


def test_format_limit_order_placed_alert_omits_sell_target_when_absent():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_placed_alert(order)
    assert "Cible de vente" not in text


def test_format_limit_order_placed_alert_bolds_the_title_line():
    """29/07 -- operator request: highlight buy/sell/limit-order alerts so
    they stand out in a busy feed."""
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038, "wallet": "swing"}
    text = lo.format_limit_order_placed_alert(order)
    assert "<b>🎯 ORDRE LIMITE POSÉ (SWING, portefeuille papier, aucun argent réel)</b>" in text


def test_format_limit_order_placed_alert_escapes_html_special_chars_in_symbol():
    """A token symbol is on-chain metadata an attacker can set freely -- an
    unescaped ``<``/``>``/``&`` would break Telegram's HTML parser for the
    WHOLE message once this alert opts into parse_mode="HTML"."""
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "<script>", "target_price": 0.038}
    text = lo.format_limit_order_placed_alert(order)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_limit_order_cancelled_alert_labels_known_reasons():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_cancelled_alert(order, "invalidation_crossed")
    assert "invalidation" in text.lower()


# Item #231 (30/07)'s R/R floor on limit-order candidates -- removed, then
# restored same day (Items #245/#248), then removed AGAIN 31/07 (Item #252,
# operator's explicit call after a live case, DRV at R/R 0.066, ran to
# +18.3% past its original technical target). Test coverage removed along
# with it -- see limit_orders.py's own comment where the floor used to live
# for the full context and the disclosed, accepted tradeoff.
