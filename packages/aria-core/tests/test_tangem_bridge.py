"""Python client for the local Tangem WalletConnect bridge (tangem_bridge.py).

No real network/process: httpx.AsyncClient is monkeypatched, and the Node.js
bridge service itself is never started. Focuses on the client's own contract
-- never raise on an expected failure (bridge unreachable, rejected/timed-out
connection), always degrade to available=False + error, never invent a
result."""
from __future__ import annotations

import httpx
import pytest

from aria_core import tangem_bridge


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Replaces httpx.AsyncClient -- returns a programmed response, or raises
    a connection error if configured to simulate an unreachable bridge."""

    _response = None
    _raise_error = None
    _captured_calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def _maybe_raise_or_respond(self, method, url, **kwargs):
        type(self)._captured_calls.append((method, url, kwargs))
        if type(self)._raise_error is not None:
            raise type(self)._raise_error
        return type(self)._response

    async def get(self, url, params=None):
        return await self._maybe_raise_or_respond("GET", url, params=params)

    async def post(self, url, json=None):
        return await self._maybe_raise_or_respond("POST", url, json=json)


@pytest.fixture
def _fresh_client(monkeypatch):
    _FakeAsyncClient._response = None
    _FakeAsyncClient._raise_error = None
    _FakeAsyncClient._captured_calls = []
    monkeypatch.setattr(tangem_bridge.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.delenv("TANGEM_BRIDGE_URL", raising=False)
    return _FakeAsyncClient


def test_bridge_url_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("TANGEM_BRIDGE_URL", raising=False)
    url = tangem_bridge.bridge_url()
    assert url.startswith("http://127.0.0.1") or url.startswith("http://localhost")


def test_bridge_url_respects_override(monkeypatch):
    monkeypatch.setenv("TANGEM_BRIDGE_URL", "http://127.0.0.1:9999/")
    assert tangem_bridge.bridge_url() == "http://127.0.0.1:9999"


# ── start_connection ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_connection_success(_fresh_client):
    _fresh_client._response = _FakeResponse(200, {"uri": "wc:abc123@2", "connectionId": "conn_1"})
    result = await tangem_bridge.start_connection()
    assert result.available is True
    assert result.uri == "wc:abc123@2"
    assert result.connection_id == "conn_1"
    assert result.error is None


@pytest.mark.asyncio
async def test_start_connection_non_200(_fresh_client):
    _fresh_client._response = _FakeResponse(500, {"error": "boom"})
    result = await tangem_bridge.start_connection()
    assert result.available is False
    assert "500" in result.error


@pytest.mark.asyncio
async def test_start_connection_bridge_unreachable(_fresh_client):
    _fresh_client._raise_error = httpx.ConnectError("connection refused")
    result = await tangem_bridge.start_connection()
    assert result.available is False
    assert "unreachable" in result.error


# ── poll_status / wait_for_connection ────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_status_connected(_fresh_client):
    _fresh_client._response = _FakeResponse(200, {"status": "connected", "address": "0xabc"})
    result = await tangem_bridge.poll_status("conn_1")
    assert result.available is True
    assert result.status == "connected"
    assert result.address == "0xabc"


@pytest.mark.asyncio
async def test_poll_status_bridge_unreachable(_fresh_client):
    _fresh_client._raise_error = httpx.ReadTimeout("timed out")
    result = await tangem_bridge.poll_status("conn_1")
    assert result.available is False
    assert "unreachable" in result.error


@pytest.mark.asyncio
async def test_wait_for_connection_returns_as_soon_as_connected(_fresh_client):
    _fresh_client._response = _FakeResponse(200, {"status": "connected", "address": "0xabc"})
    result = await tangem_bridge.wait_for_connection("conn_1", timeout_seconds=5, poll_interval_seconds=0.01)
    assert result.status == "connected"
    assert result.address == "0xabc"


@pytest.mark.asyncio
async def test_wait_for_connection_returns_on_error_status(_fresh_client):
    _fresh_client._response = _FakeResponse(200, {"status": "error", "error": "rejected by operator"})
    result = await tangem_bridge.wait_for_connection("conn_1", timeout_seconds=5, poll_interval_seconds=0.01)
    assert result.status == "error"
    assert result.error == "rejected by operator"


@pytest.mark.asyncio
async def test_wait_for_connection_times_out_without_inventing_success(_fresh_client):
    _fresh_client._response = _FakeResponse(200, {"status": "pending"})
    result = await tangem_bridge.wait_for_connection("conn_1", timeout_seconds=0.05, poll_interval_seconds=0.02)
    # Never claims "connected" just because time ran out -- last real status wins.
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_wait_for_connection_stops_polling_on_bridge_unreachable(_fresh_client):
    _fresh_client._raise_error = httpx.ConnectError("connection refused")
    result = await tangem_bridge.wait_for_connection("conn_1", timeout_seconds=5, poll_interval_seconds=0.01)
    assert result.available is False
    # Only one poll attempt -- an unreachable bridge is a hard stop, not
    # something to hammer with repeated retries for the full timeout window.
    assert len(_fresh_client._captured_calls) == 1


# ── request_signature ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_signature_success(_fresh_client):
    _fresh_client._response = _FakeResponse(200, {"result": "0xsignedtxhash"})
    result = await tangem_bridge.request_signature("conn_1", "eth_sendTransaction", [{"to": "0xdead"}])
    assert result.available is True
    assert result.result == "0xsignedtxhash"


@pytest.mark.asyncio
async def test_request_signature_rejected_by_operator(_fresh_client):
    _fresh_client._response = _FakeResponse(502, {"error": "user rejected"})
    result = await tangem_bridge.request_signature("conn_1", "eth_sendTransaction", [{"to": "0xdead"}])
    assert result.available is False
    assert result.error == "user rejected"


@pytest.mark.asyncio
async def test_request_signature_bridge_unreachable(_fresh_client):
    _fresh_client._raise_error = httpx.ConnectError("connection refused")
    result = await tangem_bridge.request_signature("conn_1", "eth_sendTransaction", [{"to": "0xdead"}])
    assert result.available is False
    assert "unreachable" in result.error


@pytest.mark.asyncio
async def test_request_signature_passes_chain_id_and_method(_fresh_client):
    _fresh_client._response = _FakeResponse(200, {"result": "0xsigned"})
    await tangem_bridge.request_signature("conn_1", "personal_sign", ["hello", "0xabc"], chain_id="eip155:84532")
    _, _, kwargs = _fresh_client._captured_calls[0]
    assert kwargs["json"]["method"] == "personal_sign"
    assert kwargs["json"]["chainId"] == "eip155:84532"
    assert kwargs["json"]["connectionId"] == "conn_1"
