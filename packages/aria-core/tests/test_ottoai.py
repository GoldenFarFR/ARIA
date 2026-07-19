"""Client Otto AI (x402) -- aucun appel réseau réel, x402_executor.fetch_paid_resource
mocké directement (même patron que test_cybercentry.py)."""
from __future__ import annotations

import pytest

from aria_core.services import ottoai


class _FakeResult:
    def __init__(self, *, status, body=b"", reason="", amount_usd=0.0):
        self.status = status
        self.body = body
        self.reason = reason
        self.amount_usd = amount_usd


_REAL_SHAPE_BODY = (
    b'{"status":"success","data":{"digest":"=== CRYPTO TWITTER DIGEST ===\\n'
    b'--- CRITICAL ALERTS ---\\n[ALERT] test alert","timestamp":"2026-07-19T14:15:32.560Z"}}'
)


@pytest.mark.asyncio
async def test_fetch_success_parses_real_response_shape(monkeypatch):
    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn):
        assert url == "https://x402.ottoai.services/twitter-summary"
        assert resource == "twitter-summary"
        assert provider == "ottoai"
        return _FakeResult(status="ok", body=_REAL_SHAPE_BODY, amount_usd=0.001)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await ottoai.fetch_twitter_digest()

    assert result.available is True
    assert "CRITICAL ALERTS" in result.digest_text
    assert result.timestamp == "2026-07-19T14:15:32.560Z"
    assert result.amount_usd == 0.001
    assert result.error is None


@pytest.mark.asyncio
async def test_fetch_blocked_no_crash(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="blocked", reason="plafond hebdomadaire x402 dépassé")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await ottoai.fetch_twitter_digest()

    assert result.available is False
    assert "plafond" in result.error
    assert result.digest_text == ""


@pytest.mark.asyncio
async def test_fetch_unreadable_body_no_crash(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="ok", body=b"not json")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await ottoai.fetch_twitter_digest()

    assert result.available is False
    assert "illisible" in result.error


@pytest.mark.asyncio
async def test_fetch_empty_digest_treated_as_unavailable(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="ok", body=b'{"status":"success","data":{"digest":""}}', amount_usd=0.001)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await ottoai.fetch_twitter_digest()

    assert result.available is False
    assert "vide" in result.error
    # Le montant payé reste rapporté même sur un digest vide -- jamais masqué.
    assert result.amount_usd == 0.001


@pytest.mark.asyncio
async def test_fetch_missing_data_key_no_crash(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="ok", body=b'{"status":"success"}', amount_usd=0.001)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await ottoai.fetch_twitter_digest()

    assert result.available is False


@pytest.mark.asyncio
async def test_fetch_failed_status_no_crash(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(status="failed", reason="requête initiale échouée : timeout")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await ottoai.fetch_twitter_digest()

    assert result.available is False
    assert "timeout" in result.error


@pytest.mark.asyncio
async def test_non_string_timestamp_ignored_never_crashes(monkeypatch):
    """Défensif : un timestamp mal typé (ex. un nombre) ne doit jamais faire
    planter le parsing -- dégradé à None plutôt qu'une valeur inventée/mal typée."""
    async def fake_fetch(*args, **kwargs):
        return _FakeResult(
            status="ok",
            body=b'{"status":"success","data":{"digest":"real content","timestamp":12345}}',
            amount_usd=0.001,
        )

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    result = await ottoai.fetch_twitter_digest()

    assert result.available is True
    assert result.timestamp is None
