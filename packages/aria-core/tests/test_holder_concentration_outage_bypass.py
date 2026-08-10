"""Auto-armed, auto-expiring outage bypass (10/08) -- see the module's own
docstring for the real friction this replaces (manual .env edit + redeploy
every time the bypass needed (re)arming)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import holder_concentration_outage_bypass as bypass


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bypass, "DB_PATH", str(tmp_path / "outage_bypass_test.db"))


@pytest.mark.asyncio
async def test_not_armed_initially():
    assert await bypass.is_armed() is False


@pytest.mark.asyncio
async def test_below_threshold_never_arms():
    for _ in range(bypass._ARM_AFTER_CONSECUTIVE_FAILURES - 1):
        just_armed = await bypass.record_unavailable()
        assert just_armed is False
    assert await bypass.is_armed() is False


@pytest.mark.asyncio
async def test_reaching_threshold_arms_exactly_once():
    for _ in range(bypass._ARM_AFTER_CONSECUTIVE_FAILURES - 1):
        assert await bypass.record_unavailable() is False
    assert await bypass.record_unavailable() is True  # crosses the threshold
    assert await bypass.is_armed() is True
    # Further failures while already armed never re-fire the "just armed" signal.
    assert await bypass.record_unavailable() is False
    assert await bypass.record_unavailable() is False


@pytest.mark.asyncio
async def test_real_success_disarms_immediately_and_resets_streak():
    for _ in range(bypass._ARM_AFTER_CONSECUTIVE_FAILURES):
        await bypass.record_unavailable()
    assert await bypass.is_armed() is True

    await bypass.record_available()
    assert await bypass.is_armed() is False

    # Streak reset -- a single subsequent failure must NOT re-arm on its own.
    assert await bypass.record_unavailable() is False
    assert await bypass.is_armed() is False


@pytest.mark.asyncio
async def test_armed_window_expires_on_its_own(monkeypatch):
    """Self-expiry without any operator action -- the whole point of this
    module. Simulated by directly writing an already-past expiry."""
    import aiosqlite

    for _ in range(bypass._ARM_AFTER_CONSECUTIVE_FAILURES):
        await bypass.record_unavailable()
    assert await bypass.is_armed() is True

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    async with aiosqlite.connect(bypass.DB_PATH) as db:
        await db.execute(
            "UPDATE holder_concentration_outage_bypass_state SET armed_until = ? WHERE id = 1",
            (past,),
        )
        await db.commit()

    assert await bypass.is_armed() is False


@pytest.mark.asyncio
async def test_continued_failures_while_armed_extend_the_window():
    """A longer outage stays covered -- each further failure while already
    armed pushes the expiry forward again, rather than letting it lapse
    mid-outage."""
    for _ in range(bypass._ARM_AFTER_CONSECUTIVE_FAILURES):
        await bypass.record_unavailable()

    import aiosqlite

    async with aiosqlite.connect(bypass.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT armed_until FROM holder_concentration_outage_bypass_state WHERE id = 1",
            )
        ).fetchone()
    first_expiry = datetime.fromisoformat(row[0])

    await bypass.record_unavailable()

    async with aiosqlite.connect(bypass.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT armed_until FROM holder_concentration_outage_bypass_state WHERE id = 1",
            )
        ).fetchone()
    second_expiry = datetime.fromisoformat(row[0])

    assert second_expiry >= first_expiry
