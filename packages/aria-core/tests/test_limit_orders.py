"""Limit-order mechanism (07/23, operator-designed) -- a candidate whose price
drifted upward between signal detection and execution gets a limit order at
the original signal price instead of a plain reject, watched until the price
comes back down, the structure breaks, or it expires."""
from __future__ import annotations

import json

import pytest

from aria_core import limit_orders as lo
from aria_core import paper_trader, risk_guard


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "limit_orders_test.db")
    monkeypatch.setattr(lo, "DB_PATH", db_path)
    monkeypatch.setattr(paper_trader, "DB_PATH", db_path)
    yield


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


# ── process_active_orders orchestration ──────────────────────────────────────


def _fake_risk_state(*, blocked=False, alloc_multiplier=1.0, equity=1_000_000.0):
    return risk_guard.PortfolioRiskState(
        equity=equity, high_water_mark=equity, drawdown_pct=0.0, consecutive_losses=0,
        alloc_multiplier=alloc_multiplier, blocked=blocked,
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
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig(invalidation=0.03))
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
async def test_process_active_orders_watching_triggers_buy(monkeypatch):
    monkeypatch.setattr(risk_guard, "evaluate_portfolio_risk", _fake_evaluate_portfolio_risk)
    await paper_trader.reset_portfolio(1_000_000.0)
    order = await lo.create_pending_order("0xCHECK", "base", "CHECK", 0.038, _sig())
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


async def _fake_evaluate_portfolio_risk(*, price_lookup=None):
    return _fake_risk_state()


@pytest.mark.asyncio
async def test_process_active_orders_watching_trigger_skipped_if_portfolio_blocked(monkeypatch):
    async def _blocked(*, price_lookup=None):
        return _fake_risk_state(blocked=True)

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


# ── format helpers ────────────────────────────────────────────────────────────


def test_format_limit_order_placed_alert_contains_target_and_symbol():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_placed_alert(order)
    assert "CHECK" in text
    assert "0.038" in text


def test_format_limit_order_cancelled_alert_labels_known_reasons():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_cancelled_alert(order, "invalidation_crossed")
    assert "invalidation" in text.lower()
