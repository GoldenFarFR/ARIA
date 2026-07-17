"""Plafond de dépense x402 -- 5$/semaine, décision opérateur explicite (16/07).
Vérifie le plafond dur, l'absence de throttle artificiel, et la remise à zéro
hebdomadaire calendaire."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import x402_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "DB_PATH", str(tmp_path / "x402_budget_test.db"))
    yield


@pytest.mark.asyncio
async def test_empty_log_starts_with_full_budget():
    status = await budget.weekly_status()
    assert status["cap_usd"] == 5.0
    assert status["spent_usd"] == 0.0
    assert status["remaining_usd"] == 5.0


@pytest.mark.asyncio
async def test_can_spend_within_cap():
    assert await budget.can_spend(2.0) is True
    assert await budget.can_spend(5.0) is True
    assert await budget.can_spend(5.01) is False


@pytest.mark.asyncio
async def test_can_spend_rejects_non_positive_amounts():
    assert await budget.can_spend(0.0) is False
    assert await budget.can_spend(-1.0) is False


@pytest.mark.asyncio
async def test_recorded_spend_reduces_remaining_budget():
    await budget.record_spend(resource="x402stock/macro", provider="x402stock", amount_usd=1.5, status="ok")
    status = await budget.weekly_status()
    assert status["spent_usd"] == 1.5
    assert status["remaining_usd"] == 3.5
    assert await budget.can_spend(3.5) is True
    assert await budget.can_spend(3.51) is False


@pytest.mark.asyncio
async def test_blocked_and_failed_attempts_never_consume_budget():
    """Doctrine append-only : une tentative refusée/échouée reste tracée mais ne
    consomme jamais le plafond -- seul un paiement réellement réglé (status='ok')
    compte contre les 5$/semaine."""
    await budget.record_spend(resource="r1", amount_usd=4.9, status="blocked", reason="hors plafond")
    await budget.record_spend(resource="r2", amount_usd=1.0, status="failed", reason="facilitator down")
    status = await budget.weekly_status()
    assert status["spent_usd"] == 0.0
    assert status["remaining_usd"] == 5.0


@pytest.mark.asyncio
async def test_hard_cap_never_exceeded_across_multiple_spends():
    await budget.record_spend(resource="r1", amount_usd=3.0, status="ok")
    assert await budget.can_spend(2.0) is True
    await budget.record_spend(resource="r2", amount_usd=2.0, status="ok")
    # Plafond atteint pile -- plus aucune dépense possible cette semaine.
    assert await budget.can_spend(0.01) is False
    status = await budget.weekly_status()
    assert status["remaining_usd"] == 0.0


@pytest.mark.asyncio
async def test_no_artificial_daily_throttle_below_cap():
    """Consigne opérateur explicite (16/07) : aucun goutte-à-goutte quotidien --
    rien n'empêche de dépenser tout le budget hebdomadaire en une seule fois si
    des faits durables et distincts le justifient."""
    assert await budget.can_spend(5.0) is True
    await budget.record_spend(resource="r1", amount_usd=5.0, status="ok")
    assert await budget.can_spend(0.01) is False


@pytest.mark.asyncio
async def test_weekly_reset_on_new_calendar_week():
    last_week = datetime.now(timezone.utc) - timedelta(days=8)
    await budget.record_spend(resource="old", amount_usd=5.0, status="ok")
    # Force l'horodatage de la ligne insérée dans le passé (semaine précédente).
    import aiosqlite

    async with aiosqlite.connect(budget.DB_PATH) as db:
        await db.execute(
            "UPDATE x402_spend_log SET created_at = ? WHERE resource = 'old'",
            (last_week.isoformat(),),
        )
        await db.commit()

    status = await budget.weekly_status()
    assert status["spent_usd"] == 0.0
    assert status["remaining_usd"] == 5.0


@pytest.mark.asyncio
async def test_list_spends_order_most_recent_first():
    await budget.record_spend(resource="r1", amount_usd=0.5, status="ok")
    await budget.record_spend(resource="r2", amount_usd=0.5, status="ok")
    rows = await budget.list_spends()
    assert [r["resource"] for r in rows] == ["r2", "r1"]


@pytest.mark.asyncio
async def test_record_spend_persists_pay_to():
    """17/07 -- pay_to permet à agent_wallet_monitor.py de corréler un mouvement
    on-chain à ce paiement (cf. le faux positif réel qui a motivé cet ajout)."""
    await budget.record_spend(
        resource="wallet-verification", provider="cybercentry", amount_usd=0.02,
        status="ok", pay_to="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
    )
    rows = await budget.list_spends()
    assert rows[0]["pay_to"] == "0xfEE13309251B632317ea2d475d6ABa7E7E0219e6"


@pytest.mark.asyncio
async def test_record_spend_pay_to_defaults_empty_no_regression():
    """Les appelants existants (aucun pay_to fourni) ne cassent pas."""
    await budget.record_spend(resource="r1", amount_usd=0.5, status="ok")
    rows = await budget.list_spends()
    assert rows[0]["pay_to"] == ""
