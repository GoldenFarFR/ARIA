import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.access_code import create_session, init_auth_db
from app.config import settings
from app.main import app


@pytest.fixture
async def gated_client(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))
    monkeypatch.setattr(settings, "access_code_enabled", True)
    await init_auth_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        yield client


@pytest.mark.asyncio
async def test_handoff_valid_token_sets_cookie_and_redirects(gated_client):
    token, _ = await create_session(ttl_hours=1)
    res = await gated_client.get(f"/api/auth/handoff?token={token}")
    assert res.status_code == 302
    assert "aria_token=" in res.headers["location"]
    assert res.cookies.get("aria_market_token") == token


@pytest.mark.asyncio
async def test_handoff_invalid_token_redirects_without_cookie(gated_client):
    res = await gated_client.get("/api/auth/handoff?token=not-a-real-session")
    assert res.status_code == 302
    assert res.headers["location"].endswith("/")
    assert "aria_market_token" not in res.cookies


@pytest.mark.asyncio
async def test_session_accepts_cookie(gated_client):
    token, _ = await create_session(ttl_hours=1)
    res = await gated_client.get("/api/auth/session", cookies={"aria_market_token": token})
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is True
    assert data["token"] == token