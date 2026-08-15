"""Client Recall.ai (bot de réunion) -- 15/08, prototype voix/apparence ARIA.

Aucun réseau réel : httpx.AsyncClient est monkeypatché. Aucune clé réelle
n'existe encore (chantier en préparation, pas de compte créé) -- ces tests
posent RECALL_AI_API_KEY/RECALL_AI_API_BASE factices via monkeypatch.setenv.
"""
from __future__ import annotations

import httpx
import pytest

from aria_core.services import recall_meeting_bot


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeAsyncClient:
    _post_response = None
    _get_response = None
    _captured_post_payload = None
    _captured_headers = None
    _captured_url = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        type(self)._captured_url = url
        type(self)._captured_post_payload = json
        type(self)._captured_headers = headers
        return type(self)._post_response

    async def get(self, url, headers=None):
        type(self)._captured_url = url
        type(self)._captured_headers = headers
        return type(self)._get_response


@pytest.fixture
def _fresh_client(monkeypatch):
    monkeypatch.setenv("RECALL_AI_API_KEY", "test-key")
    monkeypatch.setenv("RECALL_AI_API_BASE", "https://us-east-1.recall.ai/api/v1")
    _FakeAsyncClient._post_response = None
    _FakeAsyncClient._get_response = None
    _FakeAsyncClient._captured_post_payload = None
    _FakeAsyncClient._captured_headers = None
    _FakeAsyncClient._captured_url = None
    monkeypatch.setattr(recall_meeting_bot.httpx, "AsyncClient", _FakeAsyncClient)
    return recall_meeting_bot.RecallMeetingBotClient()


def test_is_recall_configured(monkeypatch):
    monkeypatch.delenv("RECALL_AI_API_KEY", raising=False)
    monkeypatch.delenv("RECALL_AI_API_BASE", raising=False)
    assert recall_meeting_bot.is_recall_configured() is False
    monkeypatch.setenv("RECALL_AI_API_KEY", "k")
    assert recall_meeting_bot.is_recall_configured() is False  # base manquant
    monkeypatch.setenv("RECALL_AI_API_BASE", "https://us-east-1.recall.ai/api/v1")
    assert recall_meeting_bot.is_recall_configured() is True


@pytest.mark.asyncio
async def test_create_bot_without_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("RECALL_AI_API_KEY", raising=False)
    monkeypatch.delenv("RECALL_AI_API_BASE", raising=False)
    client = recall_meeting_bot.RecallMeetingBotClient()
    result = await client.create_bot(meeting_url="https://meet.google.com/abc-defg-hij")
    assert result.available is False
    assert "RECALL_AI_API_KEY" in (result.error or "")


@pytest.mark.asyncio
async def test_create_bot_empty_meeting_url_never_calls_network(monkeypatch):
    monkeypatch.setenv("RECALL_AI_API_KEY", "test-key")
    monkeypatch.setenv("RECALL_AI_API_BASE", "https://us-east-1.recall.ai/api/v1")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("ne doit jamais être appelé, meeting_url vide")

    monkeypatch.setattr(recall_meeting_bot.httpx, "AsyncClient", _fail_if_called)
    client = recall_meeting_bot.RecallMeetingBotClient()
    result = await client.create_bot(meeting_url="   ")
    assert result.available is False


@pytest.mark.asyncio
async def test_create_bot_success(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(
        201, {"id": "bot-123", "status": "joining_call"},
    )
    result = await _fresh_client.create_bot(meeting_url="https://meet.google.com/abc-defg-hij")
    assert result.available is True
    assert result.bot_id == "bot-123"
    assert result.status == "joining_call"


@pytest.mark.asyncio
async def test_create_bot_sends_bearer_auth_and_meeting_url(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(201, {"id": "bot-1"})
    await _fresh_client.create_bot(meeting_url="https://meet.google.com/xyz")
    assert _FakeAsyncClient._captured_headers["Authorization"] == "Bearer test-key"
    assert _FakeAsyncClient._captured_post_payload["meeting_url"] == "https://meet.google.com/xyz"


@pytest.mark.asyncio
async def test_create_bot_with_webhook_sets_realtime_endpoints(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(201, {"id": "bot-1"})
    await _fresh_client.create_bot(
        meeting_url="https://meet.google.com/xyz",
        webhook_url="https://aria.example.com/webhook/recall",
    )
    payload = _FakeAsyncClient._captured_post_payload
    endpoints = payload["recording_config"]["realtime_endpoints"]
    assert endpoints[0]["type"] == "webhook"
    assert endpoints[0]["url"] == "https://aria.example.com/webhook/recall"
    assert "transcript.data" in endpoints[0]["events"]


@pytest.mark.asyncio
async def test_create_bot_with_output_webpage_sets_output_media(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(201, {"id": "bot-1"})
    await _fresh_client.create_bot(
        meeting_url="https://meet.google.com/xyz",
        output_webpage_url="https://aria.example.com/avatar-frame",
    )
    payload = _FakeAsyncClient._captured_post_payload
    assert payload["output_media"]["camera"]["kind"] == "webpage"
    assert payload["output_media"]["camera"]["config"]["url"] == "https://aria.example.com/avatar-frame"


@pytest.mark.asyncio
async def test_create_bot_http_error_status_is_unavailable(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(401, {"detail": "invalid key"})
    result = await _fresh_client.create_bot(meeting_url="https://meet.google.com/xyz")
    assert result.available is False
    assert "401" in (result.error or "")


@pytest.mark.asyncio
async def test_create_bot_network_exception_never_raises(_fresh_client, monkeypatch):
    async def _broken_post(*args, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(_FakeAsyncClient, "post", _broken_post)
    result = await _fresh_client.create_bot(meeting_url="https://meet.google.com/xyz")
    assert result.available is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_get_bot_status_success(_fresh_client):
    _FakeAsyncClient._get_response = _FakeResponse(
        200, {"meeting_url": "https://meet.google.com/xyz", "status": "in_call_recording"},
    )
    result = await _fresh_client.get_bot_status("bot-123")
    assert result.available is True
    assert result.status == "in_call_recording"
    assert result.bot_id == "bot-123"


@pytest.mark.asyncio
async def test_get_bot_status_without_bot_id_is_unavailable(_fresh_client):
    result = await _fresh_client.get_bot_status("")
    assert result.available is False


@pytest.mark.asyncio
async def test_leave_call_success(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(200, {})
    ok = await _fresh_client.leave_call("bot-123")
    assert ok is True


@pytest.mark.asyncio
async def test_leave_call_without_config_returns_false(monkeypatch):
    monkeypatch.delenv("RECALL_AI_API_KEY", raising=False)
    monkeypatch.delenv("RECALL_AI_API_BASE", raising=False)
    client = recall_meeting_bot.RecallMeetingBotClient()
    ok = await client.leave_call("bot-123")
    assert ok is False


@pytest.mark.asyncio
async def test_leave_call_network_exception_never_raises(_fresh_client, monkeypatch):
    async def _broken_post(*args, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(_FakeAsyncClient, "post", _broken_post)
    ok = await _fresh_client.leave_call("bot-123")
    assert ok is False
