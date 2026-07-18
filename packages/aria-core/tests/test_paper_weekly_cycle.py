"""Cycle d'entraînement hebdomadaire (18/07, remplace le protocole 30j/7j/14j) --
reset à 1M$ chaque semaine, objectif +10% validé chaque semaine, historique jamais
détruit (contrairement à ``reset_portfolio``)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import paper_trader as pt, risk_guard
from aria_core.paths import configure_data_dir

A = "0x" + "a" * 40
B = "0x" + "b" * 40


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    return tmp_path


async def _age_cycle(days: float) -> None:
    """Recule ``paper_state.created_at`` de ``days`` jours -- simule le temps écoulé
    sans dépendre d'un vrai sleep."""
    started = datetime.now(timezone.utc) - timedelta(days=days)
    async with aiosqlite.connect(pt.DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET created_at = ? WHERE id = 1", (started.isoformat(),),
        )
        await db.commit()


def test_weekly_target_equity_is_plus_10_pct():
    assert pt.weekly_target_equity(1_000_000.0) == 1_100_000.0


@pytest.mark.asyncio
async def test_weekly_cycle_not_due_when_fresh(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    assert await pt.weekly_cycle_due() is False


@pytest.mark.asyncio
async def test_weekly_cycle_due_after_7_days(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await _age_cycle(7.1)
    assert await pt.weekly_cycle_due() is True


@pytest.mark.asyncio
async def test_weekly_cycle_not_due_before_7_days(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await _age_cycle(6.9)
    assert await pt.weekly_cycle_due() is False


@pytest.mark.asyncio
async def test_run_weekly_reset_force_closes_open_position(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return 3.0  # +50 % latent au moment du reset

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["force_closed"] == 1
    assert (await pt.get_open_positions()) == []


@pytest.mark.asyncio
async def test_run_weekly_reset_validated_true_when_target_reached(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=500_000)

    async def price_lookup(contract):
        return 1.25  # 500k -> 625k, +125k latent -> équité 1 125 000 >= objectif 1,1M

    report = await pt.run_weekly_reset(price_lookup=price_lookup)
    assert report["validated"] is True
    assert round(report["end_equity"]) == 1_125_000


@pytest.mark.asyncio
async def test_run_weekly_reset_validated_false_when_below_target(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return 1.0  # flat -- équité reste 1M, sous l'objectif 1,1M

    report = await pt.run_weekly_reset(price_lookup=price_lookup)
    assert report["validated"] is False
    assert round(report["end_equity"]) == 1_000_000


@pytest.mark.asyncio
async def test_run_weekly_reset_price_unavailable_falls_back_to_entry_price(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return None  # prix indisponible -- jamais un prix inventé

    report = await pt.run_weekly_reset(price_lookup=price_lookup)
    # Fallback = entry_price -> pnl_usd == 0 exactement, équité inchangée.
    assert round(report["end_equity"]) == 1_000_000


@pytest.mark.asyncio
async def test_run_weekly_reset_never_destroys_history(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)
    await pt.close_position(A, 3.0, reason="cible")
    await pt.open_position(B, "BBB", 1.0, alloc_usd=20_000)

    async def price_lookup(contract):
        return 1.0

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    # Table live vidée...
    assert (await pt.get_open_positions()) == []
    assert (await pt.get_closed_positions()) == []

    # ...mais tout archivé sous le bon numéro de cycle, rien perdu.
    async with aiosqlite.connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT contract, cycle_number FROM paper_position_archive ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 2
    assert all(cycle == report["cycle_number"] for _, cycle in rows)
    assert {r[0] for r in rows} == {A, B}


@pytest.mark.asyncio
async def test_run_weekly_reset_resets_capital_and_increments_cycle(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    start_cycle = await pt.get_current_cycle_number()
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return 2.0

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["next_cycle_number"] == start_cycle + 1
    assert await pt.get_current_cycle_number() == start_cycle + 1
    assert await pt.starting_capital() == pt.STARTING_CAPITAL_USD
    assert await pt.cash_available() == pt.STARTING_CAPITAL_USD
    assert await pt.get_equity_high_water_mark() == pt.STARTING_CAPITAL_USD


@pytest.mark.asyncio
async def test_run_weekly_reset_records_permanent_cycle_row(tmp_db):
    await pt.reset_portfolio(1_000_000.0)

    async def price_lookup(contract):
        return 2.0

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    async with aiosqlite.connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT started_at, ended_at, target_equity, start_capital, end_equity, "
            "return_pct, validated, closed_trades, win_rate FROM paper_weekly_cycle "
            "WHERE cycle_number = ?",
            (report["cycle_number"],),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[2] == 1_100_000.0  # target_equity
    assert row[3] == 1_000_000.0  # start_capital
    assert row[6] == 0  # validated=False (flat, en dessous de l'objectif)


@pytest.mark.asyncio
async def test_run_weekly_reset_clears_circuit_breaker_for_fresh_week(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    risk_guard.block_new_entries("test -- palier dur simulé", by="test")
    blocked_before, _ = risk_guard.blocks_new_entries()
    assert blocked_before is True

    async def price_lookup(contract):
        return 2.0

    await pt.run_weekly_reset(price_lookup=price_lookup)

    blocked_after, reason_after = risk_guard.blocks_new_entries()
    assert blocked_after is False, reason_after


@pytest.mark.asyncio
async def test_run_weekly_reset_default_price_lookup_signature(tmp_db, monkeypatch):
    """Le price_lookup PAR DÉFAUT (_default_price_lookup) est appelé avec ``chain=`` --
    un price_lookup INJECTÉ garde le contrat d'appel historique à un seul argument (même
    convention que run_paper_cycle, cf. paper_trader.py)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000, chain="solana")

    calls = []

    async def fake_default(contract, *, chain="base"):
        calls.append((contract, chain))
        return 2.0

    monkeypatch.setattr(pt, "_default_price_lookup", fake_default)
    await pt.run_weekly_reset()

    assert calls == [(A, "solana")]


def test_format_weekly_cycle_report_shows_verdict():
    report = {
        "cycle_number": 3, "start_capital": 1_000_000.0, "target_equity": 1_100_000.0,
        "end_equity": 1_050_000.0, "return_pct": 5.0, "closed_trades": 4, "win_rate": 75.0,
        "validated": False, "force_closed": 2, "next_cycle_number": 4,
    }
    text = pt.format_weekly_cycle_report(report)
    assert "SIMULATION" in text
    assert "non atteint" in text
    assert "1 000 000" in text or "1,000,000" in text or "1000000" in text
    assert "cycle #4" in text or "#4" in text
    assert "Aucun argent réel" in text
