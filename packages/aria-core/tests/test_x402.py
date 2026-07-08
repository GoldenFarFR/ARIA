"""Seam x402 — gaté OFF, fail-closed, aucune dépense autonome (dôme)."""
from __future__ import annotations

import pytest

from aria_core.services import x402


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_X402_ENABLED", raising=False)
    assert x402.x402_enabled() is False
    # Désactivé => aucune demande de paiement construite (dégradation gracieuse).
    assert x402.build_payment_requirement("premium/report", "10000") is None


def test_requirement_needs_enabled_and_pay_to(monkeypatch):
    monkeypatch.setenv("ARIA_X402_ENABLED", "1")
    monkeypatch.delenv("ARIA_X402_PAY_TO", raising=False)
    # Activé mais sans adresse d'encaissement => fail-closed (None).
    assert x402.build_payment_requirement("premium/report", "10000") is None

    monkeypatch.setenv("ARIA_X402_PAY_TO", "0xAriaBaseReceiver")
    req = x402.build_payment_requirement("premium/report", "10000", description="VC report")
    assert req is not None
    assert req.network == "base" and req.asset == "USDC"
    assert req.pay_to == "0xAriaBaseReceiver"
    assert req.as_dict()["payTo"] == "0xAriaBaseReceiver"


def test_payment_required_response_shape(monkeypatch):
    monkeypatch.setenv("ARIA_X402_ENABLED", "1")
    monkeypatch.setenv("ARIA_X402_PAY_TO", "0xAriaBaseReceiver")
    req = x402.build_payment_requirement("premium/report", "10000")
    resp = x402.payment_required_response(req)
    assert resp["status"] == 402
    assert resp["body"]["x402Version"] == 1
    assert resp["body"]["accepts"][0]["network"] == "base"
    # Sans demande => pas de gating (None), le flux ne casse pas.
    assert x402.payment_required_response(None) is None


@pytest.mark.asyncio
async def test_verify_disabled_is_invalid(monkeypatch):
    monkeypatch.delenv("ARIA_X402_ENABLED", raising=False)
    v = await x402.verify_settlement({"payment": "x"})
    assert v.valid is False and "disabled" in v.reason


@pytest.mark.asyncio
async def test_verify_no_facilitator_is_invalid(monkeypatch):
    monkeypatch.setenv("ARIA_X402_ENABLED", "1")
    monkeypatch.delenv("ARIA_X402_FACILITATOR_URL", raising=False)
    v = await x402.verify_settlement({"payment": "x"})
    assert v.valid is False and "facilitator" in v.reason


@pytest.mark.asyncio
async def test_verify_ok_via_facilitator(monkeypatch):
    monkeypatch.setenv("ARIA_X402_ENABLED", "1")
    monkeypatch.setenv("ARIA_X402_FACILITATOR_URL", "https://facilitator.example")

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"isValid": True, "txHash": "0xabc"}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): return FakeResp()

    monkeypatch.setattr(x402.httpx, "AsyncClient", FakeClient)
    v = await x402.verify_settlement({"payment": "proof"})
    assert v.valid is True and v.tx_hash == "0xabc"


@pytest.mark.asyncio
async def test_verify_facilitator_failure_is_invalid(monkeypatch):
    monkeypatch.setenv("ARIA_X402_ENABLED", "1")
    monkeypatch.setenv("ARIA_X402_FACILITATOR_URL", "https://facilitator.example")

    class BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): raise RuntimeError("down")

    monkeypatch.setattr(x402.httpx, "AsyncClient", BoomClient)
    v = await x402.verify_settlement({"payment": "proof"})
    # Dôme : une panne n'accorde jamais l'accès.
    assert v.valid is False and "unreachable" in v.reason


def test_propose_payment_never_executes():
    # Côté ARIA paie : proposition uniquement, validation humaine obligatoire, jamais exécutée.
    p = x402.propose_payment(amount="5000", to="0xVendor", resource="data/feed")
    assert p.requires_human is True
    assert p.status == "proposed"
    assert p.as_dict()["requires_human"] is True
