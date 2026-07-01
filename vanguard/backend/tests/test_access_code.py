import pytest

from app.auth.access_code import create_session, init_auth_db, verify_session


@pytest.mark.asyncio
async def test_init_auth_db(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))
    await init_auth_db()
    assert db.exists()


@pytest.mark.asyncio
async def test_create_and_verify_session(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))

    token, _ = await create_session(ttl_hours=1)
    assert len(token) > 20
    assert await verify_session(token) is True
    assert await verify_session(None) is False
    assert await verify_session("invalid") is False