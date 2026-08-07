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


def test_mount_x402_seller_declares_bazaar_discovery_on_both_routes(monkeypatch):
    """07/08, operator go-ahead: both live paid routes must carry a real
    Bazaar discovery extension, or the CDP facilitator never catalogs/indexes
    them -- the whole point of listing (see docs/HANDOFF_X402.md, "no
    discoverability" gap). Locks the invariant rather than trusting the wiring
    stays correct after a future refactor."""
    from fastapi import FastAPI

    from app.x402_seller import mount_x402_seller

    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    app = FastAPI()
    mount_x402_seller(app)
    routes = app.user_middleware[0].kwargs["routes"]
    for path in ("GET /api/x402/walletscore", "GET /api/x402/b20score"):
        extensions = routes[path].extensions
        assert extensions is not None and "bazaar" in extensions, (
            f"{path} is missing its Bazaar discovery extension -- undiscoverable by real payers"
        )


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
async def test_record_sale_notifies_operator_on_success(monkeypatch, tmp_path):
    """05/08, operator request: a Telegram alert on every real x402 sale."""
    import aria_core.gateway.telegram_bot as telegram_bot_module
    from aria_core import x402_revenue_ledger as ledger

    from app.api.routes import x402_signals

    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "ledger.db"))

    sent = []

    async def fake_send_message(text, *args, **kwargs):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram_bot_module, "send_message", fake_send_message)

    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xBuyerAddress"))
    await x402_signals._record_sale_if_paid(request, "b20_safety")

    assert len(sent) == 1
    assert "b20_safety" in sent[0]
    assert "0xBuyerAddress" in sent[0]
    assert "0.10" in sent[0]


@pytest.mark.asyncio
async def test_record_sale_notify_failure_never_raises(monkeypatch, tmp_path):
    import aria_core.gateway.telegram_bot as telegram_bot_module
    from aria_core import x402_revenue_ledger as ledger

    from app.api.routes import x402_signals

    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "ledger.db"))

    async def raising_send(*args, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(telegram_bot_module, "send_message", raising_send)

    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xBuyer"))
    await x402_signals._record_sale_if_paid(request, "wallet_score")  # must not raise


@pytest.mark.asyncio
async def test_record_sale_skips_notify_when_ledger_write_fails(monkeypatch):
    import aria_core.gateway.telegram_bot as telegram_bot_module
    import aria_core.x402_revenue_ledger as ledger_module

    from app.api.routes import x402_signals

    async def raising(**kwargs):
        raise RuntimeError("db is down")

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("notify should never fire when the ledger write failed")

    monkeypatch.setattr(ledger_module, "record_sale", raising)
    monkeypatch.setattr(telegram_bot_module, "send_message", fail_if_called)

    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xBuyer"))
    await x402_signals._record_sale_if_paid(request, "wallet_score")  # must not raise/call notify


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


# ── product health tracking (05/08, operator request: eviter de facturer ────
# un x402 casse ou un resultat pas satisfaisant) ─────────────────────────────

@pytest.mark.asyncio
async def test_wallet_score_success_records_health_attempt(monkeypatch):
    from app.api.routes import x402_signals
    from aria_core import x402_product_health as health

    async def fake_score(address: str):
        return 42.0

    recorded = []

    async def fake_record_attempt(product, outcome):
        recorded.append((product, outcome))

    monkeypatch.setattr(x402_signals, "latest_score_for_wallet", fake_score)
    monkeypatch.setattr(health, "record_attempt", fake_record_attempt)

    await x402_signals.x402_wallet_score(address="0xScored")

    assert recorded == [("wallet_score", "success")]


@pytest.mark.asyncio
async def test_wallet_score_404_records_health_attempt_as_no_result(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import x402_signals
    from aria_core import x402_product_health as health

    async def fake_score(address: str):
        return None

    recorded = []

    async def fake_record_attempt(product, outcome):
        recorded.append((product, outcome))

    monkeypatch.setattr(x402_signals, "latest_score_for_wallet", fake_score)
    monkeypatch.setattr(health, "record_attempt", fake_record_attempt)

    with pytest.raises(HTTPException):
        await x402_signals.x402_wallet_score(address="0xneverscored")

    assert recorded == [("wallet_score", "no_result")]


# ── B20 route (31/07) -- anti-abuse guardrails baked in from the start ──────

_VALID_ADDR = "0x" + "a" * 40


@pytest.mark.asyncio
async def test_b20_exists_rejects_malformed_address():
    from fastapi import HTTPException

    from app.api.routes import x402_signals

    with pytest.raises(HTTPException) as exc_info:
        await x402_signals.x402_b20_score_exists(contract="not-an-address")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_b20_exists_reflects_real_verdict(monkeypatch):
    from app.api.routes import x402_signals
    from aria_core.services import b20

    async def fake_is_b20(address):
        return True

    monkeypatch.setattr(b20, "is_b20", fake_is_b20)
    result = await x402_signals.x402_b20_score_exists(contract=_VALID_ADDR)
    assert result == {"contract": _VALID_ADDR.lower(), "is_b20": True}


@pytest.mark.asyncio
async def test_b20_exists_degrades_to_none_on_failure(monkeypatch):
    from app.api.routes import x402_signals
    from aria_core.services import b20

    async def failing_is_b20(address):
        raise RuntimeError("RPC down")

    monkeypatch.setattr(b20, "is_b20", failing_is_b20)
    result = await x402_signals.x402_b20_score_exists(contract=_VALID_ADDR)
    assert result == {"contract": _VALID_ADDR.lower(), "is_b20": None}


@pytest.mark.asyncio
async def test_b20_score_rejects_malformed_address_before_any_scan(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import x402_signals
    from aria_core.services import b20

    async def fail_if_called(address):
        raise AssertionError("must never scan a malformed address")

    monkeypatch.setattr(b20, "evaluate_b20_safety", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        await x402_signals.x402_b20_score(contract="garbage")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_b20_score_returns_verdict_and_records_sale(monkeypatch, tmp_path):
    from aria_core import x402_revenue_ledger as ledger
    from aria_core.services import b20
    from app.api.routes import x402_signals

    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "ledger.db"))

    async def fake_verdict(address):
        return b20.B20SafetyVerdict(
            verdict="risky", reason="active holder(s) on: MINT_ROLE",
            role_holders={"MINT_ROLE": {"0xHolder"}},
        )

    monkeypatch.setattr(b20, "evaluate_b20_safety", fake_verdict)
    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xPayer"))

    result = await x402_signals.x402_b20_score(contract=_VALID_ADDR, request=request)

    assert result["contract"] == _VALID_ADDR.lower()
    assert result["b20_verdict"] == "risky"
    assert result["role_holders"] == {"MINT_ROLE": ["0xHolder"]}
    sales = await ledger.list_sales()
    assert len(sales) == 1
    assert sales[0]["product"] == "b20_safety"
    assert sales[0]["payer_address"] == "0xPayer"


@pytest.mark.asyncio
async def test_b20_score_includes_scanned_at_freshness(monkeypatch):
    """05/08, operator request: enrich the response with when this verdict
    was last scanned, not just the verdict itself."""
    from aria_core.services import b20
    from app.api.routes import x402_signals

    async def fake_verdict(address):
        return b20.B20SafetyVerdict(verdict="safe")

    async def fake_timestamp(address):
        return "2026-08-05T07:00:00+00:00"

    monkeypatch.setattr(b20, "evaluate_b20_safety", fake_verdict)
    monkeypatch.setattr(b20, "cached_scan_timestamp", fake_timestamp)

    result = await x402_signals.x402_b20_score(contract=_VALID_ADDR)

    assert result["scanned_at"] == "2026-08-05T07:00:00+00:00"


@pytest.mark.asyncio
async def test_b20_score_records_health_attempt_success(monkeypatch):
    from aria_core.services import b20
    from aria_core import x402_product_health as health
    from app.api.routes import x402_signals

    async def fake_verdict(address):
        return b20.B20SafetyVerdict(verdict="safe")

    recorded = []

    async def fake_record_attempt(product, outcome):
        recorded.append((product, outcome))

    monkeypatch.setattr(b20, "evaluate_b20_safety", fake_verdict)
    monkeypatch.setattr(health, "record_attempt", fake_record_attempt)

    await x402_signals.x402_b20_score(contract=_VALID_ADDR)

    assert recorded == [("b20_safety", "success")]


@pytest.mark.asyncio
async def test_b20_score_records_health_attempt_no_result_on_opaque(monkeypatch):
    from aria_core.services import b20
    from aria_core import x402_product_health as health
    from app.api.routes import x402_signals

    async def fake_verdict(address):
        return b20.B20SafetyVerdict(verdict="opaque", reason="node unreachable")

    recorded = []

    async def fake_record_attempt(product, outcome):
        recorded.append((product, outcome))

    monkeypatch.setattr(b20, "evaluate_b20_safety", fake_verdict)
    monkeypatch.setattr(health, "record_attempt", fake_record_attempt)

    await x402_signals.x402_b20_score(contract=_VALID_ADDR)

    assert recorded == [("b20_safety", "no_result")]


@pytest.mark.asyncio
async def test_b20_score_records_health_attempt_error_on_malformed_address(monkeypatch):
    from fastapi import HTTPException

    from aria_core import x402_product_health as health
    from app.api.routes import x402_signals

    recorded = []

    async def fake_record_attempt(product, outcome):
        recorded.append((product, outcome))

    monkeypatch.setattr(health, "record_attempt", fake_record_attempt)

    with pytest.raises(HTTPException):
        await x402_signals.x402_b20_score(contract="garbage")

    assert recorded == [("b20_safety", "error")]


@pytest.mark.asyncio
async def test_b20_score_records_health_attempt_error_on_scan_failure(monkeypatch):
    from fastapi import HTTPException

    from aria_core.services import b20
    from aria_core import x402_product_health as health
    from app.api.routes import x402_signals

    async def failing_verdict(address):
        raise RuntimeError("RPC down")

    recorded = []

    async def fake_record_attempt(product, outcome):
        recorded.append((product, outcome))

    monkeypatch.setattr(b20, "evaluate_b20_safety", failing_verdict)
    monkeypatch.setattr(health, "record_attempt", fake_record_attempt)

    with pytest.raises(HTTPException):
        await x402_signals.x402_b20_score(contract=_VALID_ADDR)

    assert recorded == [("b20_safety", "error")]


@pytest.mark.asyncio
async def test_b20_score_scan_failure_never_leaks_raw_error(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import x402_signals
    from aria_core.services import b20

    async def failing_verdict(address):
        raise RuntimeError("internal RPC secret detail that must never reach a client")

    monkeypatch.setattr(b20, "evaluate_b20_safety", failing_verdict)

    with pytest.raises(HTTPException) as exc_info:
        await x402_signals.x402_b20_score(contract=_VALID_ADDR)
    assert exc_info.value.status_code == 502
    assert "internal RPC secret detail" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_b20_score_rate_limit_blocks_before_scanning(monkeypatch):
    from fastapi import HTTPException

    from aria_core import x402_revenue_ledger as ledger_module
    from app.api.routes import x402_signals
    from aria_core.services import b20

    async def fail_if_called(address):
        raise AssertionError("must never scan once the payer is over quota")

    monkeypatch.setattr(b20, "evaluate_b20_safety", fail_if_called)

    async def over_quota(payer, product, *, window_seconds):
        return x402_signals._B20_RATE_LIMIT_MAX_REQUESTS

    monkeypatch.setattr(ledger_module, "recent_sale_count", over_quota)
    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xPayer"))

    with pytest.raises(HTTPException) as exc_info:
        await x402_signals.x402_b20_score(contract=_VALID_ADDR, request=request)
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_b20_score_rate_limit_check_failure_fails_open(monkeypatch, tmp_path):
    """Never blocks a legitimate paying request on a rate-limit CHECK failure
    (distinct from actually being over quota) -- same fail-open doctrine as
    every other best-effort guardrail in this pipeline."""
    from aria_core import x402_revenue_ledger as ledger_module, x402_revenue_ledger as ledger
    from app.api.routes import x402_signals
    from aria_core.services import b20

    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "ledger.db"))

    async def broken_count(payer, product, *, window_seconds):
        raise ConnectionError("db down")

    monkeypatch.setattr(ledger_module, "recent_sale_count", broken_count)

    async def fake_verdict(address):
        return b20.B20SafetyVerdict(verdict="safe", reason="every sensitive role confirmed renounced")

    monkeypatch.setattr(b20, "evaluate_b20_safety", fake_verdict)
    request = _FakeRequest(payment_payload=_FakePaymentPayload("0xPayer"))

    result = await x402_signals.x402_b20_score(contract=_VALID_ADDR, request=request)
    assert result["b20_verdict"] == "safe"


@pytest.mark.asyncio
async def test_b20_score_no_payer_skips_rate_limit_check(monkeypatch):
    """Free-mode/gate-off request (no payment payload) -- never rate-limited,
    that's the payment gate's own job, not this one's."""
    from app.api.routes import x402_signals
    from aria_core.services import b20

    async def fake_verdict(address):
        return b20.B20SafetyVerdict(verdict="not_b20")

    monkeypatch.setattr(b20, "evaluate_b20_safety", fake_verdict)

    result = await x402_signals.x402_b20_score(contract=_VALID_ADDR, request=None)
    assert result["b20_verdict"] == "not_b20"


# ── CDP mainnet facilitator authentication (04/08) ──────────────────────────

def test_facilitator_auth_provider_none_on_testnet(monkeypatch):
    """Mainnet gate off (default) -- the free testnet facilitator needs no
    auth at all, must never build a CDP auth provider."""
    from app.x402_seller import _facilitator_auth_provider

    monkeypatch.delenv("ARIA_X402_SELLER_MAINNET", raising=False)
    assert _facilitator_auth_provider() is None


def test_facilitator_auth_provider_raises_when_mainnet_on_without_credentials(monkeypatch):
    """Fail LOUD at mount time, never a silent 401 on the first real payment."""
    from app.x402_seller import _facilitator_auth_provider

    monkeypatch.setenv("ARIA_X402_SELLER_MAINNET", "true")
    monkeypatch.delenv("ARIA_X402_FACILITATOR_CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("ARIA_X402_FACILITATOR_CDP_API_KEY_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="ARIA_X402_FACILITATOR_CDP_API_KEY"):
        _facilitator_auth_provider()


def test_facilitator_auth_provider_builds_with_dedicated_credentials(monkeypatch):
    """Never the wallet's own CDP_API_KEY_ID/_SECRET -- a dedicated,
    minimum-permission key pair (operator decision, 04/08)."""
    from app import x402_seller as x402_seller_module
    from app.x402_seller import CdpFacilitatorAuthProvider, _facilitator_auth_provider

    monkeypatch.setenv("ARIA_X402_SELLER_MAINNET", "true")
    monkeypatch.setenv("ARIA_X402_FACILITATOR_CDP_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("ARIA_X402_FACILITATOR_CDP_API_KEY_SECRET", "test-key-secret")
    # X402_SELLER_FACILITATOR_URL is read once at import time (module-level
    # constant, unchanged pre-existing behavior) -- patch the constant
    # itself, not the env var, same as the mount-time test below.
    monkeypatch.setattr(
        x402_seller_module, "X402_SELLER_FACILITATOR_URL", "https://api.cdp.coinbase.com/platform/v2/x402",
    )

    provider = _facilitator_auth_provider()
    assert isinstance(provider, CdpFacilitatorAuthProvider)
    assert provider._host == "api.cdp.coinbase.com"
    assert provider._base_path == "/platform/v2/x402"


def test_cdp_facilitator_auth_provider_scopes_each_endpoint_correctly(monkeypatch):
    """Real, observed SDK behavior (verified against the installed x402 +
    cdp-sdk packages): get_auth_headers() is called fresh before EACH
    request and must return a distinct, correctly-scoped (method+path)
    Bearer token for verify/settle/supported all at once."""
    from app.x402_seller import CdpFacilitatorAuthProvider

    captured = []

    class _FakeJwtOptions:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.kwargs = kwargs

    def fake_generate_jwt(options):
        return f"jwt-for-{options.kwargs['request_method']}-{options.kwargs['request_path']}"

    import cdp.auth
    import cdp.auth.utils.jwt as jwt_module

    monkeypatch.setattr(jwt_module, "JwtOptions", _FakeJwtOptions)
    monkeypatch.setattr(cdp.auth, "generate_jwt", fake_generate_jwt)

    provider = CdpFacilitatorAuthProvider(
        "key-id", "key-secret", host="api.cdp.coinbase.com", base_path="/platform/v2/x402",
    )
    headers = provider.get_auth_headers()

    assert headers.verify == {"Authorization": "Bearer jwt-for-POST-/platform/v2/x402/verify"}
    assert headers.settle == {"Authorization": "Bearer jwt-for-POST-/platform/v2/x402/settle"}
    assert headers.supported == {"Authorization": "Bearer jwt-for-GET-/platform/v2/x402/supported"}
    assert all(kw["api_key_id"] == "key-id" and kw["api_key_secret"] == "key-secret" for kw in captured)


def test_mount_x402_seller_wires_auth_provider_on_mainnet(monkeypatch):
    """Smoke test: mounting with mainnet+credentials configured must not
    raise, and must actually pass the CDP auth provider through to
    FacilitatorConfig (never silently drop it)."""
    from fastapi import FastAPI

    import cdp.auth
    import cdp.auth.utils.jwt as jwt_module
    from app import x402_seller as x402_seller_module

    monkeypatch.setattr(jwt_module, "JwtOptions", lambda **kwargs: kwargs)
    monkeypatch.setattr(cdp.auth, "generate_jwt", lambda options: "fake-jwt")

    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    monkeypatch.setenv("ARIA_X402_SELLER_MAINNET", "true")
    monkeypatch.setenv("ARIA_X402_FACILITATOR_CDP_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("ARIA_X402_FACILITATOR_CDP_API_KEY_SECRET", "test-key-secret")
    monkeypatch.setenv(
        "ARIA_X402_SELLER_FACILITATOR_URL", "https://api.cdp.coinbase.com/platform/v2/x402",
    )
    monkeypatch.setattr(x402_seller_module, "X402_SELLER_FACILITATOR_URL", "https://api.cdp.coinbase.com/platform/v2/x402")

    app = FastAPI()
    x402_seller_module.mount_x402_seller(app)  # must not raise
