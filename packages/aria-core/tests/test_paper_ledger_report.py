"""Registre paper-trading dissect thèse+winrate -- DB temporaire isolée, même patron
que test_paper_trader.py (aucune requête dupliquée, réutilise paper_trader tel quel)."""
from __future__ import annotations

import asyncio

import pytest

from aria_core import paper_ledger_report as report
from aria_core import paper_trader as pt

A = "0x" + "a" * 40
B = "0x" + "b" * 40


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    return tmp_path


@pytest.mark.asyncio
async def test_build_report_empty_portfolio_shows_zero_trades(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    text, machine = await report.build_report()
    assert "0 trade(s) clôturé(s)" in text
    assert "winrate: n/a" in text
    assert machine["winrate_stats"]["closed_trades"] == 0
    assert machine["winrate_stats"]["win_rate_pct"] is None
    assert machine["open_positions"] == []
    assert machine["closed_positions"] == []


@pytest.mark.asyncio
async def test_build_report_shows_open_position_with_thesis(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=1.5, invalidation_price=0.8,
        alloc_usd=50_000, thesis="Cassure de résistance confirmée par volume réel.",
    )
    text, machine = await report.build_report()
    assert "AAA" in text
    assert "OUVERTE" in text
    assert "Cassure de résistance confirmée par volume réel." in text
    assert "R:R visé" in text
    assert machine["summary"]["open_positions"] == 1


@pytest.mark.asyncio
async def test_build_report_missing_thesis_shows_honest_placeholder(tmp_db):
    """Position ouverte AVANT #197 (thesis jamais renseignée) -- jamais un texte inventé."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, target_price=1.5, invalidation_price=0.8, alloc_usd=50_000)
    text, _machine = await report.build_report()
    assert "aucune — position pré-#197 ou non renseignée" in text


@pytest.mark.asyncio
async def test_build_report_computes_winrate_and_expectancy_over_closed_trades(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, invalidation_price=0.5, alloc_usd=10_000)
    await pt.open_position(B, "BBB", 1.0, invalidation_price=0.5, alloc_usd=10_000)
    await pt.close_position(A, 1.5, reason="palier 3/3 (clôture)")  # +5000
    await pt.close_position(B, 0.5, reason="invalidation")  # -5000

    text, machine = await report.build_report()
    stats = machine["winrate_stats"]
    assert stats["closed_trades"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate_pct"] == 50.0
    assert stats["avg_win_usd"] == pytest.approx(5000.0, abs=1.0)
    assert stats["avg_loss_usd"] == pytest.approx(-5000.0, abs=1.0)
    assert "GAGNANTE" in text
    assert "PERDANTE" in text
    assert "raison de sortie : invalidation".lower() in text.lower()


@pytest.mark.asyncio
async def test_build_report_closed_limit_bounds_history_size(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    for i in range(3):
        contract = f"0x{i:040d}"
        await pt.open_position(contract, f"T{i}", 1.0, invalidation_price=0.5, alloc_usd=5_000)
        await pt.close_position(contract, 1.1, reason="manuel")
    _text, machine = await report.build_report(closed_limit=2)
    assert len(machine["closed_positions"]) == 2


@pytest.mark.asyncio
async def test_build_trade_status_context_labels_data_as_real_not_invented(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, target_price=1.5, invalidation_price=0.8, alloc_usd=50_000)
    context = await report.build_trade_status_context()
    assert "RÉEL" in context
    assert "AAA" in context
    assert "n'en invente aucun autre" in context


@pytest.mark.asyncio
async def test_build_trade_status_context_caps_closed_positions_at_five(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    for i in range(7):
        contract = f"0x{i:040d}"
        await pt.open_position(contract, f"T{i}", 1.0, invalidation_price=0.5, alloc_usd=5_000)
        await pt.close_position(contract, 1.1, reason="manuel")
    context = await report.build_trade_status_context()
    assert context.count("CLÔTURÉE") == 5
