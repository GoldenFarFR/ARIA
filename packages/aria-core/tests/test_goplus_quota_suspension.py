"""Auto-armed, auto-expiring GoPlus quota suspension (10/08) -- see the
module's own docstring for the friction this replaces (a hardcoded date
constant requiring a code edit + commit + deploy every time it needed
correcting)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import goplus_quota_suspension as gqs


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(gqs, "DB_PATH", str(tmp_path / "goplus_quota_suspension_test.db"))


@pytest.mark.asyncio
async def test_not_suspended_initially():
    assert await gqs.is_suspended() is False


@pytest.mark.asyncio
async def test_below_threshold_never_arms():
    for _ in range(gqs._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES - 1):
        assert await gqs.record_rate_limit_failure() is False
    assert await gqs.is_suspended() is False


@pytest.mark.asyncio
async def test_reaching_threshold_arms_exactly_once():
    for _ in range(gqs._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES - 1):
        assert await gqs.record_rate_limit_failure() is False
    assert await gqs.record_rate_limit_failure() is True  # crosses the threshold
    assert await gqs.is_suspended() is True


@pytest.mark.asyncio
async def test_real_success_disarms_immediately_and_resets_backoff():
    for _ in range(gqs._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES):
        await gqs.record_rate_limit_failure()
    assert await gqs.is_suspended() is True

    await gqs.record_success()
    assert await gqs.is_suspended() is False

    # Backoff reset -- a single subsequent failure must NOT re-arm on its own.
    assert await gqs.record_rate_limit_failure() is False
    assert await gqs.is_suspended() is False


@pytest.mark.asyncio
async def test_post_expiry_probe_failure_doubles_the_backoff(monkeypatch):
    """Le quota reste mort au-delà de la première fenêtre -- la fenêtre
    suivante doit être PLUS LONGUE (jamais retenter à chaque appel un quota
    mensuel qui peut rester mort des jours)."""
    import aiosqlite

    for _ in range(gqs._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES):
        await gqs.record_rate_limit_failure()

    async with aiosqlite.connect(gqs.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT current_backoff_seconds FROM goplus_quota_suspension_state WHERE id = 1",
            )
        ).fetchone()
    assert row[0] == gqs._INITIAL_SUSPEND_SECONDS

    # Simule l'expiration de la première fenêtre.
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    async with aiosqlite.connect(gqs.DB_PATH) as db:
        await db.execute(
            "UPDATE goplus_quota_suspension_state SET suspended_until = ? WHERE id = 1", (past,),
        )
        await db.commit()
    assert await gqs.is_suspended() is False

    # La sonde post-expiration échoue de nouveau -- backoff doublé.
    just_armed = await gqs.record_rate_limit_failure()
    assert just_armed is False  # déjà armé une fois depuis le dernier succès
    assert await gqs.is_suspended() is True

    async with aiosqlite.connect(gqs.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT current_backoff_seconds FROM goplus_quota_suspension_state WHERE id = 1",
            )
        ).fetchone()
    assert row[0] == gqs._INITIAL_SUSPEND_SECONDS * 2


@pytest.mark.asyncio
async def test_backoff_never_exceeds_the_cap(monkeypatch):
    """Le doublement doit s'arrêter au plafond, jamais grandir indéfiniment."""
    import aiosqlite

    for _ in range(gqs._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES):
        await gqs.record_rate_limit_failure()

    # Force plusieurs cycles expiration -> échec pour dépasser le plafond.
    for _ in range(6):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        async with aiosqlite.connect(gqs.DB_PATH) as db:
            await db.execute(
                "UPDATE goplus_quota_suspension_state SET suspended_until = ? WHERE id = 1", (past,),
            )
            await db.commit()
        await gqs.record_rate_limit_failure()

    async with aiosqlite.connect(gqs.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT current_backoff_seconds FROM goplus_quota_suspension_state WHERE id = 1",
            )
        ).fetchone()
    assert row[0] == gqs._MAX_SUSPEND_SECONDS
