"""Quick Intel client (x402, $0.03/scan) -- diligencé 30/07 après la
découverte d'un faux positif "Has blacklist: Yes" sur PONKE (aucune trace
dans le vrai code source vérifié). Même dôme que test_cybercentry.py/
test_otto_ai.py : aucun appel réseau réel, fetch_paid_resource mocké."""
from __future__ import annotations

import json

import pytest

from aria_core.services import quickintel


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("ARIA_QUICKINTEL_ENABLED", "true")
    yield


@pytest.mark.asyncio
async def test_scan_full_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_QUICKINTEL_ENABLED", raising=False)
    result = await quickintel.scan_full("base", "0xabc")
    assert result["available"] is False
    assert "désactivé" in result["error"]


@pytest.mark.asyncio
async def test_scan_full_empty_inputs_never_call_x402(monkeypatch):
    result = await quickintel.scan_full("", "0xabc")
    assert result["available"] is False
    result2 = await quickintel.scan_full("base", "")
    assert result2["available"] is False


@pytest.mark.asyncio
async def test_scan_full_ok(monkeypatch):
    class FakeResult:
        status = "ok"
        amount_usd = 0.03
        body = json.dumps({
            "tokenDetails": {"tokenName": "Ribbita by Virtuals", "tokenSymbol": "TIBBIR"},
            "tokenDynamicDetails": {"is_Honeypot": False, "buy_Tax": "0.0", "sell_Tax": "0.0"},
            "contractVerified": True,
            "quickiAudit": {"contract_Renounced": True, "can_Mint": False, "can_Blacklist": False},
        }).encode("utf-8")

    captured = {}

    async def fake_fetch_paid_resource(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResult()

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch_paid_resource)
    monkeypatch.setattr("aria_core.agent_wallet_cdp_adapter.usdc_balance_usd", lambda: None)
    monkeypatch.setattr("aria_core.x402_cdp_signer.build_x402_payment_header", lambda req: None)

    result = await quickintel.scan_full("base", "0xABC")
    assert result["available"] is True
    assert result["raw"]["tokenDetails"]["tokenSymbol"] == "TIBBIR"
    assert result["amount_usd"] == 0.03
    # chain/tokenAddress passés tels quels dans le corps JSON, chain normalisée
    assert captured["json_body"] == {"chain": "base", "tokenAddress": "0xABC"}
    assert captured["method"] == "POST"
    assert captured["contract"] == "0xABC"


@pytest.mark.asyncio
async def test_scan_full_blocked_never_raises(monkeypatch):
    class FakeResult:
        status = "blocked"
        amount_usd = 0.0
        body = b""
        reason = "plafond hebdomadaire x402 dépassé"

    async def fake_fetch_paid_resource(url, **kwargs):
        return FakeResult()

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch_paid_resource)
    monkeypatch.setattr("aria_core.agent_wallet_cdp_adapter.usdc_balance_usd", lambda: None)
    monkeypatch.setattr("aria_core.x402_cdp_signer.build_x402_payment_header", lambda req: None)

    result = await quickintel.scan_full("base", "0xabc")
    assert result["available"] is False
    assert "plafond" in result["error"]


@pytest.mark.asyncio
async def test_scan_full_unreadable_body_never_raises(monkeypatch):
    class FakeResult:
        status = "ok"
        amount_usd = 0.03
        body = b"not json"

    async def fake_fetch_paid_resource(url, **kwargs):
        return FakeResult()

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch_paid_resource)
    monkeypatch.setattr("aria_core.agent_wallet_cdp_adapter.usdc_balance_usd", lambda: None)
    monkeypatch.setattr("aria_core.x402_cdp_signer.build_x402_payment_header", lambda req: None)

    result = await quickintel.scan_full("base", "0xabc")
    assert result["available"] is False
    assert "illisible" in result["error"]
