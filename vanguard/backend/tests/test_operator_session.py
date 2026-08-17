"""Operator mobile sessions (Item #201) -- Bearer token session_id.secret,
sliding-window expiration, revocation."""
from datetime import datetime, timedelta, timezone

import pytest

from app.auth import operator_session as osess


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "auth_operator_session_test.db"
    monkeypatch.setattr(osess, "DB_PATH", str(db))
    yield


@pytest.mark.asyncio
async def test_create_and_verify_session_roundtrip():
    token = await osess.create_operator_session(account_id=1, installation_id="dev-1", ip="1.2.3.4")
    assert "." in token
    session = await osess.verify_operator_session(token, ip="1.2.3.4")
    assert session is not None
    assert session["account_id"] == 1
    assert session["installation_id"] == "dev-1"


@pytest.mark.asyncio
async def test_verify_rejects_none_and_malformed_tokens():
    assert await osess.verify_operator_session(None) is None
    assert await osess.verify_operator_session("") is None
    assert await osess.verify_operator_session("no-dot-here") is None
    assert await osess.verify_operator_session(".missing-session-id") is None
    assert await osess.verify_operator_session("missing-secret.") is None


@pytest.mark.asyncio
async def test_verify_rejects_unknown_session_id():
    token = await osess.create_operator_session(account_id=1)
    session_id, _, _secret = token.partition(".")
    forged = f"{session_id}x.wrongsecret"
    assert await osess.verify_operator_session(forged) is None


@pytest.mark.asyncio
async def test_verify_rejects_wrong_secret_for_real_session_id():
    token = await osess.create_operator_session(account_id=1)
    session_id, _, _secret = token.partition(".")
    tampered = f"{session_id}.wrong-secret-value"
    assert await osess.verify_operator_session(tampered) is None


@pytest.mark.asyncio
async def test_revoke_operator_session_by_full_token():
    token = await osess.create_operator_session(account_id=1)
    assert await osess.verify_operator_session(token) is not None
    revoked = await osess.revoke_operator_session(token)
    assert revoked is True
    assert await osess.verify_operator_session(token) is None


@pytest.mark.asyncio
async def test_revoke_operator_session_by_bare_session_id():
    token = await osess.create_operator_session(account_id=1)
    session_id, _, _secret = token.partition(".")
    revoked = await osess.revoke_operator_session(session_id)
    assert revoked is True
    assert await osess.verify_operator_session(token) is None


@pytest.mark.asyncio
async def test_revoke_operator_session_unknown_returns_false():
    assert await osess.revoke_operator_session("00000000-0000-0000-0000-000000000000") is False


@pytest.mark.asyncio
async def test_revoke_all_operator_sessions_only_touches_that_account():
    token_a1 = await osess.create_operator_session(account_id=1)
    token_a2 = await osess.create_operator_session(account_id=1)
    token_b1 = await osess.create_operator_session(account_id=2)

    count = await osess.revoke_all_operator_sessions(1)
    assert count == 2
    assert await osess.verify_operator_session(token_a1) is None
    assert await osess.verify_operator_session(token_a2) is None
    assert await osess.verify_operator_session(token_b1) is not None


@pytest.mark.asyncio
async def test_expired_session_rejected(monkeypatch):
    token = await osess.create_operator_session(account_id=1)
    session_id, _, _secret = token.partition(".")

    # Force the row's expires_at into the past directly (bypassing the sliding
    # window logic entirely, to test the expiration check in isolation).
    import aiosqlite

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    async with aiosqlite.connect(osess.DB_PATH) as db:
        await db.execute(
            "UPDATE operator_sessions SET expires_at = ? WHERE session_id = ?", (past, session_id),
        )
        await db.commit()

    assert await osess.verify_operator_session(token) is None


@pytest.mark.asyncio
async def test_sliding_window_renews_only_when_close_to_expiry():
    import aiosqlite

    token = await osess.create_operator_session(account_id=1)
    session_id, _, _secret = token.partition(".")

    # Far from expiry (fresh session, ~7 days out) -- a verify must NOT rewrite
    # expires_at.
    async with aiosqlite.connect(osess.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT expires_at FROM operator_sessions WHERE session_id = ?", (session_id,),
        )
        before = (await cursor.fetchone())[0]

    await osess.verify_operator_session(token)

    async with aiosqlite.connect(osess.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT expires_at FROM operator_sessions WHERE session_id = ?", (session_id,),
        )
        after = (await cursor.fetchone())[0]
    assert before == after

    # Now push expires_at to within the 48h renewal threshold -- the next
    # verify MUST push it back out to a full new TTL.
    near_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    async with aiosqlite.connect(osess.DB_PATH) as db:
        await db.execute(
            "UPDATE operator_sessions SET expires_at = ? WHERE session_id = ?", (near_expiry, session_id),
        )
        await db.commit()

    session = await osess.verify_operator_session(token)
    assert session is not None
    new_expires = datetime.fromisoformat(session["expires_at"])
    assert new_expires - datetime.now(timezone.utc) > timedelta(days=6)


@pytest.mark.asyncio
async def test_verify_updates_last_seen_at_and_ip_every_time():
    token = await osess.create_operator_session(account_id=1, ip="1.1.1.1")
    session = await osess.verify_operator_session(token, ip="9.9.9.9")
    assert session["last_seen_ip"] == "9.9.9.9"


@pytest.mark.asyncio
async def test_purge_expired_removes_only_expired_rows():
    import aiosqlite

    live_token = await osess.create_operator_session(account_id=1)
    dead_token = await osess.create_operator_session(account_id=1)
    dead_session_id, _, _secret = dead_token.partition(".")

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    async with aiosqlite.connect(osess.DB_PATH) as db:
        await db.execute(
            "UPDATE operator_sessions SET expires_at = ? WHERE session_id = ?", (past, dead_session_id),
        )
        await db.commit()

    removed = await osess.purge_expired()
    assert removed == 1
    assert await osess.verify_operator_session(live_token) is not None
    assert await osess.verify_operator_session(dead_token) is None


# ── Session scope (17/08): read-only tokens for the operator's dashboard ──
# Operator decision, from an explicit threat model (a worm on his PC): a token
# lifted off that machine must not be able to drive the action routes.

@pytest.mark.asyncio
async def test_session_is_full_scope_by_default():
    token = await osess.create_operator_session(account_id=1)
    session = await osess.verify_operator_session(token)
    assert osess.is_read_only(session) is False


@pytest.mark.asyncio
async def test_read_only_scope_round_trips():
    token = await osess.create_operator_session(account_id=1, scope=osess.SCOPE_READ_ONLY)
    session = await osess.verify_operator_session(token)
    assert osess.is_read_only(session) is True


@pytest.mark.asyncio
async def test_legacy_session_without_scope_stays_full():
    """Every session minted before this migration has scope NULL. Treating
    those as read-only would silently break the operator's live phone session
    -- including the kill-switch."""
    assert osess.is_read_only({"scope": None}) is False
    assert osess.is_read_only({}) is False


@pytest.mark.asyncio
async def test_unrecognised_scope_fails_closed_to_read_only():
    """A tampered or corrupted column must cost read access, never grant
    write access."""
    assert osess.is_read_only({"scope": "administrator"}) is True
    assert osess.is_read_only({"scope": ""}) is True


@pytest.mark.asyncio
async def test_typo_scope_at_creation_is_stored_read_only():
    """A caller passing a bogus scope must not accidentally get write access."""
    token = await osess.create_operator_session(account_id=1, scope="ful")
    session = await osess.verify_operator_session(token)
    assert osess.is_read_only(session) is True


@pytest.mark.asyncio
async def test_read_only_scope_cannot_be_elevated_by_reverify():
    """The TOTP reverify path clears a time-based flag -- it must never widen
    a session's scope as a side effect."""
    token = await osess.create_operator_session(account_id=1, scope=osess.SCOPE_READ_ONLY)
    session = await osess.verify_operator_session(token)
    await osess.mark_totp_reverified(session["session_id"])
    assert osess.is_read_only(await osess.verify_operator_session(token)) is True
