import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.access_code import create_session, init_auth_db
from app.config import settings
from app.database import init_db
from app.main import app


@pytest.fixture
async def gated_client(tmp_path, monkeypatch):
    auth_db = tmp_path / "auth.db"
    dexpulse_db = tmp_path / "dexpulse.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", auth_db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(auth_db))
    monkeypatch.setattr("app.database.DB_PATH", str(dexpulse_db))
    monkeypatch.setattr(settings, "access_code_enabled", True)
    monkeypatch.setattr(settings, "aria_public_mode", False)
    await init_auth_db()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_api_requires_member_session(gated_client):
    res = await gated_client.get("/api/watchlist")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_api_accepts_bearer_session(gated_client):
    token, _ = await create_session(ttl_hours=1)
    res = await gated_client.get(
        "/api/watchlist",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Visitor-Id": "member-test-visitor",
        },
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_visitor_header_no_longer_bypasses_gate(gated_client):
    res = await gated_client.get(
        "/api/watchlist",
        headers={"X-Visitor-Id": "visitor-abcdef12"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_auth_required_endpoint_when_gated(gated_client, monkeypatch):
    monkeypatch.setattr(settings, "access_code_enabled", True)
    res = await gated_client.get("/api/auth/required")
    assert res.status_code == 200
    data = res.json()
    assert data["required"] is True