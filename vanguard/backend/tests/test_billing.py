import pytest

from app.billing.subscriptions import (
    is_pro_active,
    stripe_configured,
    upsert_subscription,
)


@pytest.mark.asyncio
async def test_subscription_active(tmp_path, monkeypatch):
    from app.auth import access_code as ac
    from app.billing import subscriptions as sub

    db = tmp_path / "auth.db"
    monkeypatch.setattr(ac, "DB_PATH", str(db))
    monkeypatch.setattr(sub, "DB_PATH", str(db))

    await upsert_subscription(privy_did="did:privy:test", status="active")
    assert await is_pro_active("did:privy:test") is True
    await upsert_subscription(privy_did="did:privy:test", status="canceled")
    assert await is_pro_active("did:privy:test") is False


def test_stripe_configured_false(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "")
    monkeypatch.setattr(settings, "stripe_price_id", "")
    assert stripe_configured() is False


@pytest.mark.asyncio
async def test_billing_plan_public():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/billing/plan")
    assert res.status_code == 200
    data = res.json()
    assert data["plan_id"] == "dexpulse_pro"
    assert data["name"] == "Aria Market Pro"