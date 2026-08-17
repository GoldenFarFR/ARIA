"""Auto-armed, auto-expiring GeckoTerminal outage suspension (17/08) -- see
the module's own docstring for the incident this addresses (605 consecutive
429s over 9h40+, confirmed via a live unauthenticated probe to be an
account/IP-level block, not a per-endpoint or per-caller issue). Mirrors
test_goplus_quota_suspension.py's test pattern exactly (same underlying
SingleRowStore plumbing, same exponential-backoff policy shape)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import geckoterminal_outage_suspension as gts


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(gts, "DB_PATH", str(tmp_path / "geckoterminal_outage_suspension_test.db"))


@pytest.mark.asyncio
async def test_not_suspended_initially():
    assert await gts.is_suspended() is False


@pytest.mark.asyncio
async def test_below_threshold_never_arms():
    for _ in range(gts._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES - 1):
        assert await gts.record_rate_limit_failure() is False
    assert await gts.is_suspended() is False


@pytest.mark.asyncio
async def test_reaching_threshold_arms_exactly_once():
    for _ in range(gts._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES - 1):
        assert await gts.record_rate_limit_failure() is False
    assert await gts.record_rate_limit_failure() is True  # crosses the threshold
    assert await gts.is_suspended() is True


@pytest.mark.asyncio
async def test_real_success_disarms_immediately_and_resets_backoff():
    for _ in range(gts._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES):
        await gts.record_rate_limit_failure()
    assert await gts.is_suspended() is True

    await gts.record_success()
    assert await gts.is_suspended() is False

    # Backoff reset -- a single subsequent failure must NOT re-arm on its own.
    assert await gts.record_rate_limit_failure() is False
    assert await gts.is_suspended() is False


@pytest.mark.asyncio
async def test_post_expiry_probe_failure_doubles_the_backoff():
    """Le blocage persiste au-delà de la première fenêtre -- la fenêtre
    suivante doit être PLUS LONGUE (jamais retenter à chaque appel un blocage
    qui peut rester actif des heures)."""
    import aiosqlite

    for _ in range(gts._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES):
        await gts.record_rate_limit_failure()

    async with aiosqlite.connect(gts.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT current_backoff_seconds FROM geckoterminal_outage_suspension_state WHERE id = 1",
            )
        ).fetchone()
    assert row[0] == gts._INITIAL_SUSPEND_SECONDS

    # Simule l'expiration de la première fenêtre.
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    async with aiosqlite.connect(gts.DB_PATH) as db:
        await db.execute(
            "UPDATE geckoterminal_outage_suspension_state SET suspended_until = ? WHERE id = 1", (past,),
        )
        await db.commit()
    assert await gts.is_suspended() is False

    # La sonde post-expiration échoue de nouveau -- backoff doublé.
    just_armed = await gts.record_rate_limit_failure()
    assert just_armed is False  # déjà armé une fois depuis le dernier succès
    assert await gts.is_suspended() is True

    async with aiosqlite.connect(gts.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT current_backoff_seconds FROM geckoterminal_outage_suspension_state WHERE id = 1",
            )
        ).fetchone()
    assert row[0] == gts._INITIAL_SUSPEND_SECONDS * 2


@pytest.mark.asyncio
async def test_backoff_never_exceeds_the_cap():
    """Le doublement doit s'arrêter au plafond, jamais grandir indéfiniment."""
    import aiosqlite

    for _ in range(gts._ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES):
        await gts.record_rate_limit_failure()

    # Force plusieurs cycles expiration -> échec pour dépasser le plafond.
    for _ in range(8):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        async with aiosqlite.connect(gts.DB_PATH) as db:
            await db.execute(
                "UPDATE geckoterminal_outage_suspension_state SET suspended_until = ? WHERE id = 1", (past,),
            )
            await db.commit()
        await gts.record_rate_limit_failure()

    async with aiosqlite.connect(gts.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT current_backoff_seconds FROM geckoterminal_outage_suspension_state WHERE id = 1",
            )
        ).fetchone()
    assert row[0] == gts._MAX_SUSPEND_SECONDS
