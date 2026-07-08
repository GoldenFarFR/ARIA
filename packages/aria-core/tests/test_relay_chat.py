"""Relais de conversation à 3 (opérateur/ARIA/Claude) — hors-ligne, tout injecté."""
from __future__ import annotations

import pytest

from aria_core import relay_chat


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(relay_chat, "DB_PATH", str(tmp_path / "relay_test.db"))
    yield


def test_disabled_without_token(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    assert relay_chat.relay_enabled() is False


def test_enabled_with_token(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    assert relay_chat.relay_enabled() is True


def test_verify_relay_access_constant_time_match(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    assert relay_chat.verify_relay_access("secret123") is True
    assert relay_chat.verify_relay_access("wrong") is False
    assert relay_chat.verify_relay_access(None) is False
    assert relay_chat.verify_relay_access("") is False


def test_verify_relay_access_fails_when_not_configured(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    assert relay_chat.verify_relay_access("anything") is False


@pytest.mark.asyncio
async def test_log_message_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    await relay_chat.log_message("operator", "hello")
    assert await relay_chat.recent_messages() == []


@pytest.mark.asyncio
async def test_log_and_fetch_recent_messages(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    await relay_chat.log_message("operator", "Salut ARIA")
    await relay_chat.log_message("aria", "Bonjour !")
    await relay_chat.log_message("claude", "Je vous lis.")

    messages = await relay_chat.recent_messages()
    assert [m["sender"] for m in messages] == ["operator", "aria", "claude"]
    assert messages[0]["content"] == "Salut ARIA"


@pytest.mark.asyncio
async def test_recent_messages_since_id_filters(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    await relay_chat.log_message("operator", "un")
    await relay_chat.log_message("operator", "deux")
    first_batch = await relay_chat.recent_messages()
    last_id = first_batch[-1]["id"]

    await relay_chat.log_message("operator", "trois")
    only_new = await relay_chat.recent_messages(since_id=last_id)
    assert len(only_new) == 1
    assert only_new[0]["content"] == "trois"


@pytest.mark.asyncio
async def test_send_relay_reply_prefixes_and_logs(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    sent = []

    async def fake_sender(text):
        sent.append(text)
        return True

    ok = await relay_chat.send_relay_reply("Voici mon retour.", sender=fake_sender)
    assert ok is True
    assert sent == [f"{relay_chat.CLAUDE_PREFIX}Voici mon retour."]

    messages = await relay_chat.recent_messages()
    assert messages[-1]["sender"] == "claude"
    assert messages[-1]["content"] == "Voici mon retour."  # journalise SANS le prefixe


@pytest.mark.asyncio
async def test_send_relay_reply_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    called = []

    async def fake_sender(text):
        called.append(text)
        return True

    ok = await relay_chat.send_relay_reply("test", sender=fake_sender)
    assert ok is False
    assert called == []


@pytest.mark.asyncio
async def test_send_relay_reply_sender_exception_does_not_raise(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")

    async def broken_sender(text):
        raise RuntimeError("Telegram indisponible")

    ok = await relay_chat.send_relay_reply("test", sender=broken_sender)
    assert ok is False
