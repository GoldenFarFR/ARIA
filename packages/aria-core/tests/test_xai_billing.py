"""Client x.ai Management API (solde prépayé) — 100 % hors-ligne."""
from __future__ import annotations

import pytest

from aria_core.services.xai_billing import get_prepaid_balance, xai_billing_configured


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None):
        return self._response


def _patch_http(monkeypatch, response):
    monkeypatch.setattr(
        "aria_core.services.xai_billing.httpx.AsyncClient", lambda **kw: _FakeHttpClient(response),
    )


def test_not_configured_without_credentials(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "xai_management_key", "")
    monkeypatch.setattr(get_settings(), "xai_team_id", "")
    assert xai_billing_configured() is False


def test_configured_with_both_credentials(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "xai_management_key", "mgmt-key")
    monkeypatch.setattr(get_settings(), "xai_team_id", "team-123")
    assert xai_billing_configured() is True


@pytest.mark.asyncio
async def test_get_prepaid_balance_without_credentials_never_calls_network(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "xai_management_key", "")
    monkeypatch.setattr(get_settings(), "xai_team_id", "")
    result = await get_prepaid_balance()
    assert result.available is False
    assert result.balance_usd is None
    assert "missing" in result.error


@pytest.mark.asyncio
async def test_get_prepaid_balance_parses_negative_cents_as_positive_usd(monkeypatch):
    """Doc x.ai : "negative indicates credits available" -- un compte à -800 cents
    représente 8,00$ de crédit RÉELLEMENT disponible pour l'opérateur."""
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "xai_management_key", "mgmt-key")
    monkeypatch.setattr(get_settings(), "xai_team_id", "team-123")
    _patch_http(monkeypatch, _FakeResponse(200, {"total": {"val": "-800"}, "changes": []}))

    result = await get_prepaid_balance()

    assert result.available is True
    assert result.balance_usd == pytest.approx(8.0)
    assert result.error is None


@pytest.mark.asyncio
async def test_get_prepaid_balance_http_error(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "xai_management_key", "mgmt-key")
    monkeypatch.setattr(get_settings(), "xai_team_id", "team-123")
    _patch_http(monkeypatch, _FakeResponse(401, text="unauthorized"))

    result = await get_prepaid_balance()

    assert result.available is False
    assert "401" in result.error


@pytest.mark.asyncio
async def test_get_prepaid_balance_unexpected_response_shape(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "xai_management_key", "mgmt-key")
    monkeypatch.setattr(get_settings(), "xai_team_id", "team-123")
    _patch_http(monkeypatch, _FakeResponse(200, {"unexpected": "shape"}))

    result = await get_prepaid_balance()

    assert result.available is False
    assert result.balance_usd is None
