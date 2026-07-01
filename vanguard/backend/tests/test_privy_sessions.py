import pytest

from app.auth.privy_sessions import login_with_privy, lookup_linked_handle


@pytest.mark.asyncio
async def test_privy_login_creates_member(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))
    monkeypatch.setattr("app.auth.privy_sessions.DB_PATH", str(db))

    token, _ = await login_with_privy(privy_did="did:privy:new", twitter_username="newuser")
    assert token

    handle = await lookup_linked_handle("did:privy:new")
    assert handle == "newuser"
    assert await lookup_linked_handle("did:privy:unknown") is None