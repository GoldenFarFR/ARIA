import pytest


@pytest.mark.asyncio
async def test_sepolia_status_disabled_by_default(tmp_path, monkeypatch):
    from aria_core.onchain import sepolia_autonomous
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(sepolia_autonomous, "DB_PATH", str(tmp_path / "sepolia_auto.db"))
    monkeypatch.delenv("ARIA_SEPOLIA_AUTONOMOUS_ENABLED", raising=False)
    monkeypatch.delenv("ARIA_SEPOLIA_WALLET_ENABLED", raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/sepolia-status")

    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is False
    assert data["cycles_total"] == 0
    assert data["tx_count"] == 0
    assert data["last"] is None
    assert data["wallet_address"] is None
