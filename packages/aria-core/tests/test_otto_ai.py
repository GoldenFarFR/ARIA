"""Client Otto AI (x402) -- aucun appel réseau réel, x402_executor.fetch_paid_resource
mocké directement (même patron que test_cybercentry.py)."""
from __future__ import annotations

import pytest

from aria_core.services import otto_ai


class _FakeResult:
    def __init__(self, *, status, body=b"", reason="", amount_usd=0.0):
        self.status = status
        self.body = body
        self.reason = reason
        self.amount_usd = amount_usd


def test_disabled_by_default():
    assert otto_ai.otto_ai_enabled() is False


def test_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("ARIA_OTTO_AI_ENABLED", "1")
    assert otto_ai.otto_ai_enabled() is True


@pytest.mark.asyncio
async def test_hyperliquid_gate_off_no_call(monkeypatch):
    monkeypatch.delenv("ARIA_OTTO_AI_ENABLED", raising=False)
    called = False

    async def fake_fetch(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeResult(status="ok")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await otto_ai.hyperliquid_market_data()

    assert result["available"] is False
    assert called is False


@pytest.mark.asyncio
async def test_hyperliquid_success_parses_body(monkeypatch):
    monkeypatch.setenv("ARIA_OTTO_AI_ENABLED", "1")

    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn):
        assert url == "https://x402.ottoai.services/hyperliquid-market"
        assert resource == "hyperliquid-market"
        assert provider == "otto_ai"
        return _FakeResult(status="ok", body=b'{"funding_rate": 0.01}', amount_usd=0.001)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await otto_ai.hyperliquid_market_data()

    assert result["available"] is True
    assert result["raw"] == {"funding_rate": 0.01}
    assert result["amount_usd"] == 0.001


@pytest.mark.asyncio
async def test_tradfi_success_parses_body(monkeypatch):
    monkeypatch.setenv("ARIA_OTTO_AI_ENABLED", "1")

    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn):
        assert url == "https://x402.ottoai.services/tradfi-data"
        assert resource == "tradfi-data"
        assert provider == "otto_ai"
        return _FakeResult(status="ok", body=b'{"vix": 18.2}', amount_usd=0.003)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await otto_ai.tradfi_macro_data()

    assert result["available"] is True
    assert result["raw"] == {"vix": 18.2}


@pytest.mark.asyncio
async def test_blocked_no_crash(monkeypatch):
    monkeypatch.setenv("ARIA_OTTO_AI_ENABLED", "1")

    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="blocked", reason="plafond hebdomadaire x402 dépassé")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await otto_ai.hyperliquid_market_data()

    assert result["available"] is False
    assert result["error"] == "plafond hebdomadaire x402 dépassé"


@pytest.mark.asyncio
async def test_unreadable_body_degrades_honestly(monkeypatch):
    monkeypatch.setenv("ARIA_OTTO_AI_ENABLED", "1")

    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="ok", body=b"not json", amount_usd=0.001)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await otto_ai.hyperliquid_market_data()

    assert result["available"] is False
    assert "illisible" in result["error"]
