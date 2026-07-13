import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


async def _client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_bonding_pool_requires_operator(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    async with await _client() as client:
        res = await client.get("/api/aria/bonding-pool")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_bonding_pool_exposes_bonding_network_only(tmp_path, monkeypatch):
    from aria_core import screened_pool
    from app.config import settings

    monkeypatch.setattr(screened_pool, "DB_PATH", str(tmp_path / "pool.db"))
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)

    await screened_pool.upsert_screened(
        contract="0xbonding", symbol="BOND", verdict="SAFE",
        network="base-bonding", screen_reason="bonding_progress=0.4",
    )
    await screened_pool.upsert_screened(
        contract="0xstandard", symbol="STD", verdict="SAFE",
        network="base", screen_reason="screené",
    )

    async with await _client() as client:
        res = await client.get(
            "/api/aria/bonding-pool",
            headers={"X-Admin-Secret": "s3cr3t"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["items"][0]["contract"] == "0xbonding"


@pytest.mark.asyncio
async def test_bonding_pool_trade_log_requires_operator(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    async with await _client() as client:
        res = await client.post(
            "/api/aria/bonding-pool/trade-log",
            json={"contract": "0xabc", "side": "buy", "status": "ok"},
        )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_bonding_pool_trade_log_records_entry(tmp_path, monkeypatch):
    from aria_core import bonding_trade_log
    from app.config import settings

    monkeypatch.setattr(bonding_trade_log, "DB_PATH", str(tmp_path / "trades.db"))
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)

    async with await _client() as client:
        res = await client.post(
            "/api/aria/bonding-pool/trade-log",
            headers={"X-Admin-Secret": "s3cr3t"},
            json={
                "contract": "0xabc",
                "symbol": "ABC",
                "side": "buy",
                "amount_usdc": 25.0,
                "min_out_wei": "123456",
                "slippage_bps": 500,
                "status": "ok",
            },
        )
    assert res.status_code == 200
    assert res.json() == {"recorded": True}

    trades = await bonding_trade_log.list_trades()
    assert len(trades) == 1
    assert trades[0]["contract"] == "0xabc"
    assert trades[0]["slippage_bps"] == 500


@pytest.mark.asyncio
async def test_bonding_pool_trade_log_rejects_bad_side(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)

    async with await _client() as client:
        res = await client.post(
            "/api/aria/bonding-pool/trade-log",
            headers={"X-Admin-Secret": "s3cr3t"},
            json={"contract": "0xabc", "side": "hold", "status": "ok"},
        )
    assert res.status_code == 422
