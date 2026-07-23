"""Client TwitterAPI.io -- profil X complet (23/07, comble le trou X Substance).

Aucun réseau réel : httpx.AsyncClient monkeypatché. La clé n'est jamais écrite
en dur -- posée via monkeypatch.setenv."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core.services import twitterapi_io as tw


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeAsyncClient:
    _response = None
    _captured_headers = None
    _captured_params = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        type(self)._captured_params = params
        type(self)._captured_headers = headers
        return type(self)._response


@pytest.fixture
def _fresh(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test-key-sentinel")
    monkeypatch.setattr(tw, "_last_call_at", -10_000.0)  # jamais de sleep réel pendant les tests
    monkeypatch.setattr(tw.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient._response = None
    _FakeAsyncClient._captured_headers = None
    _FakeAsyncClient._captured_params = None


def test_is_configured(monkeypatch):
    monkeypatch.delenv("TWITTERAPI_IO_KEY", raising=False)
    assert tw.is_twitterapi_io_configured() is False
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "x")
    assert tw.is_twitterapi_io_configured() is True


@pytest.mark.asyncio
async def test_fetch_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("TWITTERAPI_IO_KEY", raising=False)
    result = await tw.fetch_user_profile("crynuxio")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_no_handle_returns_none(_fresh):
    assert await tw.fetch_user_profile("") is None
    assert await tw.fetch_user_profile(None) is None


@pytest.mark.asyncio
async def test_fetch_success_real_shape(_fresh):
    """Reproduit fidèlement la réponse réelle observée sur @crynuxio (23/07)."""
    _FakeAsyncClient._response = _FakeResponse(
        200,
        {
            "status": "success", "msg": "success",
            "data": {
                "id": "1717791336018120704", "userName": "crynuxio",
                "followers": 3676, "following": 242,
                "createdAt": "2023-10-27T06:32:29.000000Z",
            },
        },
    )
    result = await tw.fetch_user_profile("@crynuxio")
    assert result is not None
    assert result.followers == 3676
    assert result.following == 242
    assert result.created_at == datetime(2023, 10, 27, 6, 32, 29, tzinfo=timezone.utc)
    # header d'authentification confirmé réel, jamais la clé dans les query params
    assert _FakeAsyncClient._captured_headers == {"X-API-Key": "test-key-sentinel"}
    assert _FakeAsyncClient._captured_params == {"userName": "crynuxio"}  # @ retiré


@pytest.mark.asyncio
async def test_fetch_http_error_returns_none(_fresh):
    _FakeAsyncClient._response = _FakeResponse(401, {})
    assert await tw.fetch_user_profile("crynuxio") is None


@pytest.mark.asyncio
async def test_fetch_status_not_success_returns_none(_fresh):
    _FakeAsyncClient._response = _FakeResponse(200, {"status": "error", "msg": "not found"})
    assert await tw.fetch_user_profile("ghostaccount") is None


@pytest.mark.asyncio
async def test_fetch_missing_fields_returns_none(_fresh):
    _FakeAsyncClient._response = _FakeResponse(
        200, {"status": "success", "data": {"followers": 10}},  # following/createdAt manquants
    )
    assert await tw.fetch_user_profile("crynuxio") is None


@pytest.mark.asyncio
async def test_fetch_transport_error_returns_none(_fresh, monkeypatch):
    import httpx

    class _Boom(_FakeAsyncClient):
        async def get(self, url, params=None, headers=None):
            raise httpx.TransportError("panne réseau")

    monkeypatch.setattr(tw.httpx, "AsyncClient", _Boom)
    assert await tw.fetch_user_profile("crynuxio") is None
