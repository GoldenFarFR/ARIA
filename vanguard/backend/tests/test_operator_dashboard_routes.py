"""Operator trading dashboard (06/08): open positions + real OHLCV candles.
Reuses the SAME session auth as operator_mobile.py's /login (never the
diagnostics static token -- that one is meant for curl/scripts, unsafe to
ship in frontend JS). Same isolation doctrine as test_operator_mobile_routes.py."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from aria_core import kill_incident_log, paper_trader
from aria_core.admin_totp import generate_secret, totp_code
from aria_core.services import ohlcv as ohlcv_module
from aria_core.skills.ta_levels import Candle
from app.auth import operator_account as accounts
from app.auth import operator_auth_log as auth_log
from app.auth import operator_session as sessions
from app.auth import operator_totp_replay as totp_replay
from app.config import settings
from app.main import app

USERNAME = "operator"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(sessions, "DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(auth_log, "DB_PATH", str(tmp_path / "auth_log.db"))
    monkeypatch.setattr(totp_replay, "DB_PATH", str(tmp_path / "totp_replay.db"))
    monkeypatch.setattr(kill_incident_log, "DB_PATH", str(tmp_path / "aria.db"))
    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper.db"))
    yield


@pytest.fixture
async def totp_secret():
    return generate_secret()


@pytest.fixture(autouse=True)
async def _account(totp_secret, _isolated_db):
    await accounts.create_or_replace_account(
        username=USERNAME, password=PASSWORD, totp_secret=totp_secret,
    )
    yield


@pytest.fixture
async def client(tmp_path, monkeypatch):
    dexpulse_db = tmp_path / "dexpulse.db"
    monkeypatch.setattr("app.database.DB_PATH", str(dexpulse_db))
    monkeypatch.setattr(settings, "access_code_enabled", True)
    from app.database import init_db

    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _login_body(totp_secret):
    return {
        "username": USERNAME, "password": PASSWORD,
        "totp_code": totp_code(totp_secret), "installation_id": "test-device",
    }


async def _authed(client, totp_secret) -> dict:
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


# ── /positions ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_positions_requires_session(client):
    res = await client.get("/api/aria/ops/dashboard/positions")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_positions_returns_real_open_positions(client, totp_secret):
    headers = await _authed(client, totp_secret)
    await paper_trader.reset_portfolio(1_000_000.0, wallet="swing")
    await paper_trader.open_position(
        "0xaaa", "AAA", 1.0, target_price=2.0, invalidation_price=0.9, alloc_usd=10_000,
        pool_liquidity_usd=100_000.0, wallet="swing", thesis="momentum breakout",
    )
    res = await client.get("/api/aria/ops/dashboard/positions", headers=headers)
    assert res.status_code == 200
    positions = res.json()["positions"]
    assert len(positions) == 1
    assert positions[0]["contract"] == "0xaaa"
    assert positions[0]["thesis"] == "momentum breakout"
    assert positions[0]["target_price"] == 2.0
    assert positions[0]["invalidation_price"] == 0.9
    # internal-only telemetry never shipped to the frontend contract
    assert "align_ema" not in positions[0]
    assert "conviction_process_trail" not in positions[0]


@pytest.mark.asyncio
async def test_positions_empty_when_none_open(client, totp_secret):
    headers = await _authed(client, totp_secret)
    res = await client.get("/api/aria/ops/dashboard/positions", headers=headers)
    assert res.status_code == 200
    assert res.json()["positions"] == []


# ── /candles ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_candles_requires_session(client):
    res = await client.get("/api/aria/ops/dashboard/candles", params={"contract": "0xaaa"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_candles_uses_explicit_pool_address_when_given(client, totp_secret, monkeypatch):
    headers = await _authed(client, totp_secret)

    async def fake_get_ohlcv(self, pool_address, *, network="base", **kwargs):
        assert pool_address == "0xpool"
        return ohlcv_module.OHLCVResult(
            pool_address=pool_address, network=network,
            candles=[Candle(ts=1, open=1.0, high=1.1, low=0.9, close=1.05, volume=1000.0)],
            timeframe="1H", available=True,
        )

    monkeypatch.setattr(ohlcv_module.OHLCVClient, "get_ohlcv", fake_get_ohlcv)

    res = await client.get(
        "/api/aria/ops/dashboard/candles",
        params={"contract": "0xaaa", "pool_address": "0xpool"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    assert body["timeframe"] == "1H"
    assert body["candles"] == [{"ts": 1, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 1000.0}]


@pytest.mark.asyncio
async def test_candles_resolves_pool_via_dexscreener_when_absent(client, totp_secret, monkeypatch):
    headers = await _authed(client, totp_secret)

    class _FakePair:
        pair_address = "0xresolved"

    async def fake_pair_lookup(contract, *, chain="base"):
        return _FakePair()

    async def fake_get_ohlcv(self, pool_address, *, network="base", **kwargs):
        assert pool_address == "0xresolved"
        return ohlcv_module.OHLCVResult(pool_address=pool_address, network=network, available=True)

    monkeypatch.setattr(paper_trader, "_default_pair_lookup", fake_pair_lookup)
    monkeypatch.setattr(ohlcv_module.OHLCVClient, "get_ohlcv", fake_get_ohlcv)

    res = await client.get(
        "/api/aria/ops/dashboard/candles", params={"contract": "0xaaa"}, headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["pool_address"] == "0xresolved"


@pytest.mark.asyncio
async def test_candles_no_pool_found_returns_explicit_unavailable(client, totp_secret, monkeypatch):
    headers = await _authed(client, totp_secret)

    async def fake_pair_lookup(contract, *, chain="base"):
        return None

    monkeypatch.setattr(paper_trader, "_default_pair_lookup", fake_pair_lookup)

    res = await client.get(
        "/api/aria/ops/dashboard/candles", params={"contract": "0xaaa"}, headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is False
    assert body["candles"] == []
