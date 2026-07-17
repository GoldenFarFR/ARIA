"""Client Cybercentry (x402) -- aucun appel réseau réel, x402_executor.fetch_paid_resource
mocké directement (même patron que test_x402_executor.py)."""
from __future__ import annotations

import pytest

from aria_core.services import cybercentry


class _FakeResult:
    def __init__(self, *, status, body=b"", reason="", amount_usd=0.0):
        self.status = status
        self.body = body
        self.reason = reason
        self.amount_usd = amount_usd


@pytest.mark.asyncio
async def test_verify_wallet_empty_address_no_call(monkeypatch):
    called = False

    async def fake_fetch(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeResult(status="ok")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await cybercentry.verify_wallet("")

    assert result["available"] is False
    assert called is False


@pytest.mark.asyncio
async def test_verify_wallet_success_parses_body(monkeypatch):
    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn):
        assert "0xabc" in url
        assert resource == "wallet-verification"
        assert provider == "cybercentry"
        return _FakeResult(status="ok", body=b'{"risk": "low"}', amount_usd=0.02)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await cybercentry.verify_wallet("0xabc")

    assert result["available"] is True
    assert result["raw"] == {"risk": "low"}
    assert result["amount_usd"] == 0.02
    assert result["error"] is None


@pytest.mark.asyncio
async def test_verify_wallet_blocked_no_crash(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="blocked", reason="plafond hebdomadaire x402 dépassé")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await cybercentry.verify_wallet("0xabc")

    assert result["available"] is False
    assert "plafond" in result["error"]


@pytest.mark.asyncio
async def test_verify_wallet_unreadable_body_no_crash(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="ok", body=b"not json")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await cybercentry.verify_wallet("0xabc")

    assert result["available"] is False
    assert "illisible" in result["error"]
