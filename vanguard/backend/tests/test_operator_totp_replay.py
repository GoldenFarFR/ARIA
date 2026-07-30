"""Operator mobile TOTP anti-replay (Item #201, Phase 3) -- one-time use of a code
on /stop and /resume, enforced by the UNIQUE(account_id, totp_code) constraint."""
import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from app.auth import operator_totp_replay as replay


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "DB_PATH", str(tmp_path / "auth_totp_replay_test.db"))
    yield


@pytest.mark.asyncio
async def test_first_claim_succeeds_second_is_refused():
    assert await replay.claim_code(account_id=1, totp_code="123456") is True
    assert await replay.claim_code(account_id=1, totp_code="123456") is False


@pytest.mark.asyncio
async def test_claims_are_scoped_per_account():
    """A future second operator (the `role` column the plan already put in place)
    must not burn another account's codes -- codes are only 6 digits, collisions
    between two accounts are routine."""
    assert await replay.claim_code(account_id=1, totp_code="123456") is True
    assert await replay.claim_code(account_id=2, totp_code="123456") is True


@pytest.mark.asyncio
async def test_different_codes_for_same_account_both_pass():
    assert await replay.claim_code(account_id=1, totp_code="111111") is True
    assert await replay.claim_code(account_id=1, totp_code="222222") is True


@pytest.mark.asyncio
async def test_concurrent_claims_of_the_same_code_yield_exactly_one_winner():
    """The whole reason for a UNIQUE constraint rather than a read-then-write
    counter: two simultaneous calls must never both be allowed through."""
    results = await asyncio.gather(
        *(replay.claim_code(account_id=1, totp_code="424242") for _ in range(8))
    )
    assert results.count(True) == 1
    assert results.count(False) == 7


@pytest.mark.asyncio
async def test_expired_rows_are_purged_and_stop_blocking():
    """Housekeeping only: past its window the code can no longer be accepted by
    verify_totp anyway, so keeping the row protects nothing and would grow the
    table forever."""
    await replay.claim_code(account_id=1, totp_code="999999")

    stale = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    async with aiosqlite.connect(replay.DB_PATH) as db:
        await db.execute("UPDATE operator_used_totp_codes SET expires_at = ?", (stale,))
        await db.commit()

    assert await replay.purge_expired() == 1
    assert await replay.claim_code(account_id=1, totp_code="999999") is True


@pytest.mark.asyncio
async def test_unexpired_rows_survive_a_purge():
    await replay.claim_code(account_id=1, totp_code="777777")
    assert await replay.purge_expired() == 0
    assert await replay.claim_code(account_id=1, totp_code="777777") is False
