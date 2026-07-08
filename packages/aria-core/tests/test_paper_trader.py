"""Portefeuille papier 1 M$ (simulation) — moteur déterministe, DB temporaire isolée."""
from __future__ import annotations

import pytest

from aria_core import paper_trader as pt

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40
D = "0x" + "d" * 40


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    return tmp_path


@pytest.mark.asyncio
async def test_reset_and_starting(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    assert await pt.starting_capital() == 1_000_000.0
    assert await pt.cash_available() == 1_000_000.0


@pytest.mark.asyncio
async def test_open_deducts_cash_and_no_double(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 2.0, target_price=3.0, invalidation_price=1.5, alloc_usd=50_000)
    assert pos is not None
    assert pos["qty"] == 25_000  # 50000 / 2
    assert await pt.cash_available() == 950_000.0
    assert await pt.open_position(A, "AAA", 2.0, alloc_usd=10_000) is None  # déjà ouverte


@pytest.mark.asyncio
async def test_close_profit(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)
    closed = await pt.close_position(A, 4.0, reason="cible")
    assert closed["pnl_usd"] == 50_000
    assert round(closed["pnl_pct"], 1) == 100.0
    assert await pt.cash_available() == 1_050_000.0
    s = await pt.portfolio_summary()
    assert round(s["equity"]) == 1_050_000
    assert round(s["return_pct"], 1) == 5.0
    assert s["win_rate"] == 100.0


@pytest.mark.asyncio
async def test_close_loss(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(B, "BBB", 1.0, alloc_usd=100_000)
    closed = await pt.close_position(B, 0.5, reason="invalidation")
    assert closed["pnl_usd"] == -50_000
    assert await pt.cash_available() == 950_000.0


@pytest.mark.asyncio
async def test_summary_marks_to_market(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(C, "CCC", 1.0, alloc_usd=100_000)

    async def price_lookup(contract):
        return 1.5

    s = await pt.portfolio_summary(price_lookup=price_lookup)
    assert round(s["equity"]) == 1_050_000  # cash 900k + 100k*1.5
    assert round(s["unrealized_pnl"]) == 50_000


@pytest.mark.asyncio
async def test_run_cycle_opens_then_closes_on_target(tmp_db):
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "BUY", "symbol": "DDD", "price": 1.0, "target": 2.0, "invalidation": 0.5}

    prices = {"v": 1.0}

    async def price_lookup(contract):
        return prices["v"]

    alerts: list[str] = []

    async def notifier(msg):
        alerts.append(msg)

    act = await pt.run_paper_cycle(candidates=[D], analyzer=analyzer, price_lookup=price_lookup, notifier=notifier)
    assert len(act["opened"]) == 1
    assert await pt.has_open(D)
    assert any("ACHAT FICTIF" in a for a in alerts)

    prices["v"] = 2.5  # dépasse la cible
    act2 = await pt.run_paper_cycle(candidates=[D], analyzer=analyzer, price_lookup=price_lookup, notifier=notifier)
    assert len(act2["closed"]) == 1
    assert not await pt.has_open(D)
    assert any("VENTE FICTIVE" in a for a in alerts)


@pytest.mark.asyncio
async def test_cycle_ignores_non_buy(tmp_db):
    await pt.reset_portfolio(1_000_000.0)

    async def analyzer(contract):
        return {"action": "HOLD"}

    async def price_lookup(contract):
        return 1.0

    act = await pt.run_paper_cycle(candidates=[A], analyzer=analyzer, price_lookup=price_lookup)
    assert act["opened"] == []


@pytest.mark.asyncio
async def test_max_positions_capped(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    for i in range(pt.MAX_POSITIONS):
        c = "0x" + f"{i:040x}"
        assert await pt.open_position(c, f"T{i}", 1.0, alloc_usd=1_000) is not None
    # au-delà du plafond, refus
    assert await pt.open_position("0x" + "f" * 40, "OVER", 1.0, alloc_usd=1_000) is None


def test_alerts_labeled_simulation():
    buy = pt.format_buy_alert(
        {"symbol": "AAA", "contract": A, "entry_price": 2.0, "cost_usd": 50_000,
         "target_price": 3.0, "invalidation_price": 1.5}
    )
    assert "SIMULATION" in buy and "FICTIF" in buy
    sell = pt.format_sell_alert(
        {"symbol": "AAA", "contract": A, "exit_price": 3.0, "pnl_usd": 25_000,
         "pnl_pct": 50.0, "close_reason": "cible"}
    )
    assert "SIMULATION" in sell and "FICTIVE" in sell
