"""Plafond de dépense x402 -- 5$/semaine, décision opérateur explicite (16/07).
Vérifie le plafond dur, l'absence de throttle artificiel, et la fenêtre
glissante de 7 jours (calendaire jusqu'au 03/08, voir week_start's docstring)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import x402_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # 27/07: DB_PATH stopped being a module-level constant (real bug found --
    # it froze at import time, before per-test isolation ever ran) -- patch
    # the imported aria_db_path name instead, resolved dynamically now.
    monkeypatch.setattr(budget, "aria_db_path", lambda: tmp_path / "x402_budget_test.db")
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
async def test_weekly_reset_on_rolling_window():
    """03/08 -- renamed from test_weekly_reset_on_new_calendar_week: the
    window is now rolling (now - 7 days), not a calendar week. Unaffected
    by the change -- a spend 8 days old falls outside a 7-day window either
    way, this test still proves the same thing (an old spend no longer
    counts)."""
    last_week = datetime.now(timezone.utc) - timedelta(days=8)
    await budget.record_spend(resource="old", amount_usd=5.0, status="ok")
    # Force l'horodatage de la ligne insérée dans le passé (semaine précédente).
    import aiosqlite

    async with aiosqlite.connect(str(budget.aria_db_path())) as db:
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


@pytest.mark.asyncio
async def test_record_spend_persists_contract_and_token_symbol():
    """19/07, #143 -- trouvé en répondant à une question opérateur directe ("détaille
    chaque paiement, quel token") : sans ces deux champs, la seule façon de savoir quel
    token a motivé un paiement était de reconstruire la corrélation à la main via les
    horodatages -- fragile (un cas réel resté non identifiable)."""
    await budget.record_spend(
        resource="tweets-search", provider="twitsh", amount_usd=0.006, status="ok",
        contract="0x" + "a" * 40, token_symbol="GIZA",
    )
    rows = await budget.list_spends()
    assert rows[0]["contract"] == "0x" + "a" * 40
    assert rows[0]["token_symbol"] == "GIZA"


@pytest.mark.asyncio
async def test_record_spend_contract_defaults_empty_no_regression():
    """Un paiement non lié à un token précis (ex. Cybercentry wallet-verification)
    reste valide -- champs vides, jamais une valeur inventée."""
    await budget.record_spend(resource="wallet-verification", amount_usd=0.02, status="ok")
    rows = await budget.list_spends()
    assert rows[0]["contract"] == ""
    assert rows[0]["token_symbol"] == ""


# ── try_reserve/settle (03/08, atomic reserve-then-settle replacing the old
# can_spend/record_spend check-then-act pair for concurrent-safe callers) ────

@pytest.mark.asyncio
async def test_try_reserve_returns_id_and_counts_against_budget_before_settle():
    reservation_id = await budget.try_reserve(1.0, resource="r", provider="p")
    assert reservation_id is not None
    status = await budget.weekly_status()
    assert status["spent_usd"] == pytest.approx(1.0)
    assert status["remaining_usd"] == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_try_reserve_refuses_over_cap():
    reservation_id = await budget.try_reserve(5.01, resource="r", provider="p")
    assert reservation_id is None
    status = await budget.weekly_status()
    assert status["spent_usd"] == 0.0


@pytest.mark.asyncio
async def test_try_reserve_refuses_non_positive_amount():
    assert await budget.try_reserve(0.0, resource="r", provider="p") is None
    assert await budget.try_reserve(-1.0, resource="r", provider="p") is None


@pytest.mark.asyncio
async def test_settle_ok_persists_final_status_and_pay_to():
    reservation_id = await budget.try_reserve(1.0, resource="r", provider="p")
    await budget.settle(reservation_id, status="ok", pay_to="0xrecipient")
    rows = await budget.list_spends()
    assert rows[0]["status"] == "ok"
    assert rows[0]["pay_to"] == "0xrecipient"


@pytest.mark.asyncio
async def test_settle_failed_releases_the_reservation_but_stays_traced():
    """A 'failed' settlement releases the reserved budget (no money actually
    moved) -- same doctrine as record_spend's own 'failed' status never
    counting against the cap -- but the attempt stays traced, never a
    silently deleted row."""
    reservation_id = await budget.try_reserve(1.0, resource="r", provider="p")
    await budget.settle(reservation_id, status="failed", reason="solde insuffisant")
    status = await budget.weekly_status()
    assert status["spent_usd"] == 0.0
    assert status["remaining_usd"] == 5.0
    rows = await budget.list_spends()
    assert rows[0]["status"] == "failed"
    assert rows[0]["reason"] == "solde insuffisant"


@pytest.mark.asyncio
async def test_stale_pending_ages_out_of_the_budget():
    """A reservation that crashes before settle() must not freeze the
    budget forever -- it ages out after PENDING_TIMEOUT_MINUTES."""
    old = datetime.now(timezone.utc) - timedelta(minutes=budget.PENDING_TIMEOUT_MINUTES + 1)
    reservation_id = await budget.try_reserve(1.0, resource="r", provider="p")
    import aiosqlite

    async with aiosqlite.connect(str(budget.aria_db_path())) as db:
        await db.execute(
            "UPDATE x402_spend_log SET created_at = ? WHERE id = ?",
            (old.isoformat(), reservation_id),
        )
        await db.commit()

    status = await budget.weekly_status()
    assert status["spent_usd"] == 0.0
    assert status["remaining_usd"] == 5.0


@pytest.mark.asyncio
async def test_concurrent_reservations_never_both_succeed_past_the_cap():
    """03/08 -- reproduces the real incident: two amounts each individually
    affordable (3.0$ against a 5.0$ cap) but whose SUM exceeds it. Without
    real atomicity (BEGIN IMMEDIATE), both could read "budget still free"
    before either recorded its spend -- exactly what motivated this fix."""
    import asyncio

    results = await asyncio.gather(
        budget.try_reserve(3.0, resource="race1", provider="p1"),
        budget.try_reserve(3.0, resource="race2", provider="p2"),
    )
    accepted = [r for r in results if r is not None]
    assert len(accepted) == 1


# ── 10/08, real incident: known_pay_to_providers (automatic wallet naming) ──


@pytest.mark.asyncio
async def test_known_pay_to_providers_requires_minimum_ok_count():
    for _ in range(2):
        await budget.record_spend(resource="r", provider="twitsh", amount_usd=0.01, status="ok", pay_to="0xAAA")
    result = await budget.known_pay_to_providers(min_ok_count=3)
    assert result == {}


@pytest.mark.asyncio
async def test_known_pay_to_providers_derived_from_full_history():
    for _ in range(3):
        await budget.record_spend(resource="r", provider="twitsh", amount_usd=0.01, status="ok", pay_to="0x9dBA414637c611a16BEa6f0796BFcbcBdc410df8")
    result = await budget.known_pay_to_providers()
    assert result == {"0x9dba414637c611a16bea6f0796bfcbcbdc410df8": "twitsh"}


@pytest.mark.asyncio
async def test_known_pay_to_providers_ignores_failed_and_blocked_spends():
    for _ in range(5):
        await budget.record_spend(resource="r", provider="acme", amount_usd=0.01, status="failed", pay_to="0xAAA")
    result = await budget.known_pay_to_providers()
    assert result == {}


@pytest.mark.asyncio
async def test_known_pay_to_providers_keys_are_lowercase():
    for _ in range(3):
        await budget.record_spend(resource="r", provider="acme", amount_usd=0.01, status="ok", pay_to="0xAbCdEf")
    result = await budget.known_pay_to_providers()
    assert "0xabcdef" in result
    assert result["0xabcdef"] == "acme"
