import pytest


@pytest.mark.asyncio
async def test_track_record_exposes_pool_active_and_rejected(tmp_path, monkeypatch):
    from aria_core import screened_pool, vc_predictions
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(screened_pool, "DB_PATH", str(tmp_path / "pool.db"))
    monkeypatch.setattr(vc_predictions, "DB_PATH", str(tmp_path / "wallet.db"))

    await screened_pool.upsert_screened(
        contract="0xgood", symbol="GOOD", liquidity_usd=50_000.0,
        security_score=78, top_holder_pct=12.0, verdict="SAFE",
        pool_address="0xpool", screen_reason="screené",
    )
    await screened_pool.record_rejected(contract="0xrug", reason="honeypot", symbol="RUG")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/track-record")

    assert res.status_code == 200
    data = res.json()
    assert data["pool_active"] == 1
    assert data["pool_rejected"] == 1
