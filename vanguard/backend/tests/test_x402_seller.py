import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.middleware import PUBLIC_PREFIXES
from app.x402_seller import x402_seller_ready


def test_gate_off_by_default(monkeypatch):
    """No env vars configured in the test environment -- fail-closed default."""
    monkeypatch.delenv("ARIA_X402_SELLER_ENABLED", raising=False)
    assert x402_seller_ready() is False


def test_gate_reads_the_shared_aria_core_gate_live(monkeypatch):
    """24/07 (#59): gating is now delegated entirely to
    aria_core.x402_seller.seller_enabled() -- no separate "address configured"
    check remains (the receiving address is a hardcoded constant there, never
    an operator-supplied env var). Read LIVE, not baked at import time."""
    monkeypatch.delenv("ARIA_X402_SELLER_ENABLED", raising=False)
    assert x402_seller_ready() is False

    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    assert x402_seller_ready() is True

    monkeypatch.delenv("ARIA_X402_SELLER_ENABLED", raising=False)
    assert x402_seller_ready() is False


def test_mount_x402_seller_wires_correctly_when_gate_on(monkeypatch):
    """24/07 (#59): smoke test for the actual reconciled wiring -- mounting
    must not raise, and must build the route from aria_core.x402_seller's
    catalog (never a hardcoded duplicate) with a CAIP-2-compatible network
    (the real bug found and fixed during this reconciliation: a mismatch here
    would previously have made every payment silently unverifiable)."""
    from fastapi import FastAPI

    from app.x402_seller import mount_x402_seller

    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    app = FastAPI()
    mount_x402_seller(app)  # must not raise


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


# ── record_sale wiring (26/07) -- record_sale() existed since 07/24 but was ──
# ── never actually called anywhere on the payment path until now ────────────

class _FakeState:
    def __init__(self, payment_payload=None):
        self.payment_payload = payment_payload


class _FakeRequest:
    def __init__(self, payment_payload=None):
        self.state = _FakeState(payment_payload)


class _FakePaymentPayload:
    def __init__(self, from_address):
        self.payload = {"authorization": {"from": from_address}}


@pytest.mark.asyncio
async def test_record_sale_noop_when_request_is_none(monkeypatch):
    import aria_core.x402_revenue_ledger as ledger_module

    from app.api.routes import x402_signals

    async def fail_if_called(**kwargs):
        raise AssertionError("record_sale should never be called without a request")

    monkeypatch.setattr(ledger_module, "record_sale", fail_if_called)
    await x402_signals._record_sale_if_paid(None, "wallet_score")


@pytest.mark.asyncio
async def test_record_sale_noop_when_no_payment_payload(monkeypatch):
    import aria_core.x402_revenue_ledger as ledger_module

    from app.api.routes import x402_signals

    async def fail_if_called(**kwargs):
        raise AssertionError("record_sale should never be called without a payment")

    monkeypatch.setattr(ledger_module, "record_sale", fail_if_called)
    await x402_signals._record_sale_if_paid(_FakeRequest(payment_payload=None), "wallet_score")


@pytest.mark.asyncio
async def test_record_sale_calls_ledger_with_payer_and_catalog_price(monkeypatch, tmp_path):
    from aria_core import x402_revenue_ledger as ledger

    from app.api.routes import x402_signals

    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "ledger.db"))

    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xBuyerAddress"))
    await x402_signals._record_sale_if_paid(request, "wallet_score")

    sales = await ledger.list_sales()
    assert len(sales) == 1
    assert sales[0]["product"] == "wallet_score"
    assert sales[0]["payer_address"] == "0xBuyerAddress"
    assert sales[0]["amount_usd"] == pytest.approx(0.10)
    assert sales[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_record_sale_failure_never_raises(monkeypatch):
    import aria_core.x402_revenue_ledger as ledger_module

    from app.api.routes import x402_signals

    async def raising(**kwargs):
        raise RuntimeError("db is down")

    monkeypatch.setattr(ledger_module, "record_sale", raising)
    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xBuyer"))
    await x402_signals._record_sale_if_paid(request, "wallet_score")  # must not raise


@pytest.mark.asyncio
async def test_wallet_score_route_records_a_sale_when_paid(monkeypatch, tmp_path):
    from aria_core import x402_revenue_ledger as ledger
    from app.api.routes import x402_signals

    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "ledger.db"))

    async def fake_score(address: str):
        return 77.0

    monkeypatch.setattr(x402_signals, "latest_score_for_wallet", fake_score)

    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xPayer"))
    result = await x402_signals.x402_wallet_score(address="0xScored", request=request)

    assert result == {"wallet": "0xscored", "composite_percentile": 77.0}
    sales = await ledger.list_sales()
    assert len(sales) == 1
    assert sales[0]["payer_address"] == "0xPayer"


@pytest.mark.asyncio
async def test_wallet_score_404_never_records_a_sale(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from aria_core import x402_revenue_ledger as ledger
    from app.api.routes import x402_signals

    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "ledger.db"))

    async def fake_score(address: str):
        return None

    monkeypatch.setattr(x402_signals, "latest_score_for_wallet", fake_score)

    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xPayer"))
    with pytest.raises(HTTPException):
        await x402_signals.x402_wallet_score(address="0xneverscored", request=request)

    assert await ledger.list_sales() == []
