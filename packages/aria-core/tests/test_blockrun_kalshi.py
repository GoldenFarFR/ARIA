"""Client BlockRun.AI Kalshi (x402) -- aucun appel réseau réel,
x402_executor.fetch_paid_resource mocké directement (même patron que
test_cybercentry.py/test_otto_ai.py)."""
from __future__ import annotations

import pytest

from aria_core.services import blockrun_kalshi


class _FakeResult:
    def __init__(self, *, status, body=b"", reason="", amount_usd=0.0):
        self.status = status
        self.body = body
        self.reason = reason
        self.amount_usd = amount_usd


def test_disabled_by_default():
    assert blockrun_kalshi.blockrun_kalshi_enabled() is False


def test_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("ARIA_BLOCKRUN_KALSHI_ENABLED", "1")
    assert blockrun_kalshi.blockrun_kalshi_enabled() is True


@pytest.mark.asyncio
async def test_gate_off_no_call(monkeypatch):
    monkeypatch.delenv("ARIA_BLOCKRUN_KALSHI_ENABLED", raising=False)
    called = False

    async def fake_fetch(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeResult(status="ok")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await blockrun_kalshi.kalshi_markets()

    assert result["available"] is False
    assert called is False


@pytest.mark.asyncio
async def test_success_parses_body(monkeypatch):
    monkeypatch.setenv("ARIA_BLOCKRUN_KALSHI_ENABLED", "1")

    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn):
        assert url == "https://blockrun.ai/api/v1/pm/kalshi/markets"
        assert resource == "kalshi-markets"
        assert provider == "blockrun_ai"
        return _FakeResult(status="ok", body=b'{"markets": []}', amount_usd=0.0095)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await blockrun_kalshi.kalshi_markets()

    assert result["available"] is True
    assert result["raw"] == {"markets": []}
    assert result["amount_usd"] == 0.0095


@pytest.mark.asyncio
async def test_blocked_no_crash(monkeypatch):
    monkeypatch.setenv("ARIA_BLOCKRUN_KALSHI_ENABLED", "1")

    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="blocked", reason="plafond hebdomadaire x402 dépassé")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await blockrun_kalshi.kalshi_markets()

    assert result["available"] is False
    assert result["error"] == "plafond hebdomadaire x402 dépassé"


@pytest.mark.asyncio
async def test_unreadable_body_degrades_honestly(monkeypatch):
    monkeypatch.setenv("ARIA_BLOCKRUN_KALSHI_ENABLED", "1")

    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="ok", body=b"not json", amount_usd=0.0095)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await blockrun_kalshi.kalshi_markets()

    assert result["available"] is False
    assert "illisible" in result["error"]
