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


def test_format_limit_order_cancelled_alert_labels_known_reasons():
    order = {"contract": "0xCHECK", "chain": "base", "symbol": "CHECK", "target_price": 0.038}
    text = lo.format_limit_order_cancelled_alert(order, "invalidation_crossed")
    assert "invalidation" in text.lower()
