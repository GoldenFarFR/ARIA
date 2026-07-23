import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.middleware import PUBLIC_PREFIXES
from app.x402_seller import x402_seller_ready


def test_gate_off_by_default():
    """No env vars configured in the test environment -- fail-closed default."""
    assert x402_seller_ready() is False


def test_gate_requires_both_flag_and_address(monkeypatch):
    monkeypatch.setattr("app.x402_seller.X402_SELLER_ENABLED", True)
    monkeypatch.setattr("app.x402_seller.X402_SELLER_PAYTO_ADDRESS", "")
    assert x402_seller_ready() is False  # flag on, no address -> still closed

    monkeypatch.setattr("app.x402_seller.X402_SELLER_ENABLED", False)
    monkeypatch.setattr("app.x402_seller.X402_SELLER_PAYTO_ADDRESS", "0xabc")
    assert x402_seller_ready() is False  # address set, flag off -> still closed

    monkeypatch.setattr("app.x402_seller.X402_SELLER_ENABLED", True)
    monkeypatch.setattr("app.x402_seller.X402_SELLER_PAYTO_ADDRESS", "0xabc")
    assert x402_seller_ready() is True  # both set -> ready


def test_x402_prefix_exempted_from_privy_session_gate():
    """Machine-to-machine paid endpoints must never require a Privy operator/
    member session -- x402's own payment challenge is the access control."""
    assert "/api/x402/" in PUBLIC_PREFIXES


@pytest.mark.asyncio
async def test_route_not_mounted_when_gate_off():
    """End-to-end: with the gate off (default test env, no env vars set), the
    x402 router is never registered on the app -- the path simply doesn't
    exist, rather than existing-but-unprotected."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/x402/walletscore", params={"address": "0x" + "1" * 40})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_wallet_score_exists_reflects_scored_state(monkeypatch):
    from app.api.routes import x402_signals

    async def fake_score(address: str):
        return 82.5 if address == "0xscored" else None

    monkeypatch.setattr(x402_signals, "latest_score_for_wallet", fake_score)

    scored = await x402_signals.x402_wallet_score_exists(address="0xscored")
    assert scored == {"wallet": "0xscored", "scored": True}

    unscored = await x402_signals.x402_wallet_score_exists(address="0xneverscored")
    assert unscored == {"wallet": "0xneverscored", "scored": False}


@pytest.mark.asyncio
async def test_wallet_score_returns_404_when_never_scored(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import x402_signals

    async def fake_score(address: str):
        return None

    monkeypatch.setattr(x402_signals, "latest_score_for_wallet", fake_score)

    with pytest.raises(HTTPException) as exc_info:
        await x402_signals.x402_wallet_score(address="0xneverscored")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_wallet_score_returns_composite_percentile(monkeypatch):
    from app.api.routes import x402_signals

    async def fake_score(address: str):
        return 91.2

    monkeypatch.setattr(x402_signals, "latest_score_for_wallet", fake_score)

    result = await x402_signals.x402_wallet_score(address="0xSCORED")
    assert result == {"wallet": "0xscored", "composite_percentile": 91.2}
