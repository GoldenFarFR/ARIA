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
async def test_latest_messages_returns_the_most_recent_not_the_oldest(monkeypatch):
    """25/07, operator-found gap: relay_conversation_cycle used recent_messages(limit=50)
    with no since_id -- once the relay logged more than 50 messages total (early July in
    prod), that call permanently returned the 50 OLDEST messages ever logged, never the
    current conversation, so the cycle could never see a fresh Claude message."""
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    for i in range(60):
        await relay_chat.log_message("operator", f"old message {i}")
    await relay_chat.log_message("claude", "recent question")

    latest = await relay_chat.latest_messages(limit=50)

    assert len(latest) == 50
    # Chronological order preserved (oldest of the returned batch first).
    assert latest[0]["content"] == "old message 11"
    # The freshest message (Claude's) is last, exactly what a conversation
    # history needs -- proven absent from the old recent_messages(limit=50) call.
    assert latest[-1]["sender"] == "claude"
    assert latest[-1]["content"] == "recent question"


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


@pytest.mark.asyncio
async def test_send_relay_reply_false_return_without_exception_is_not_a_success(monkeypatch):
    """25/07, operator-found gap: `telegram_bot.send_message` can return False
    WITHOUT raising (e.g. its bot application isn't initialized in the calling
    process -- true for any one-off script). The old code only caught
    exceptions, so a silent non-delivery was logged as a success ("ok" outcome,
    a message written to relay_message that was never actually seen on
    Telegram)."""
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")

    async def falsy_sender(text):
        return False  # no exception -- the old bug's exact shape

    ok = await relay_chat.send_relay_reply("test", sender=falsy_sender)
    assert ok is False

    messages = await relay_chat.recent_messages()
    assert messages == []  # never logged -- it was never really sent


@pytest.mark.asyncio
async def test_send_aria_relay_reply_false_return_without_exception_is_not_a_success(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    monkeypatch.setenv("ARIA_RELAY_AUTOREPLY_ENABLED", "true")

    async def falsy_sender(text):
        return False

    ok = await relay_chat.send_aria_relay_reply("test", sender=falsy_sender)
    assert ok is False

    messages = await relay_chat.recent_messages()
    assert messages == []


@pytest.mark.asyncio
async def test_send_relay_reply_respects_kill_switch(monkeypatch):
    """18/07 -- trouvé par audit de sécurité : ce chemin (POST /api/aria/relay/reply,
    hors heartbeat) ne vérifiait jamais outgoing_pause.is_paused() -- un appel
    authentifié par le token relay dédié pouvait donc poster sur Telegram même
    pendant un /stop."""
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    from aria_core import outgoing_pause

    monkeypatch.setattr(outgoing_pause, "is_paused", lambda **kw: True)
    called = []

    async def fake_sender(text):
        called.append(text)
        return True

    ok = await relay_chat.send_relay_reply("test", sender=fake_sender)
    assert ok is False
    assert called == []


def test_autoreply_disabled_without_token(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("ARIA_RELAY_AUTOREPLY_ENABLED", "true")
    assert relay_chat.relay_autoreply_enabled() is False


def test_autoreply_disabled_by_default_even_with_token(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    monkeypatch.delenv("ARIA_RELAY_AUTOREPLY_ENABLED", raising=False)
    assert relay_chat.relay_autoreply_enabled() is False


def test_autoreply_enabled_with_both_flags(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    monkeypatch.setenv("ARIA_RELAY_AUTOREPLY_ENABLED", "1")
    assert relay_chat.relay_autoreply_enabled() is True


@pytest.mark.asyncio
async def test_send_aria_relay_reply_no_prefix_and_logs(monkeypatch):
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    sent = []

    async def fake_sender(text):
        sent.append(text)
        return True

    ok = await relay_chat.send_aria_relay_reply("Salut Claude.", sender=fake_sender)
    assert ok is True
    assert sent == ["Salut Claude."]  # aucun prefixe -- c'est vraiment sa voix

    messages = await relay_chat.recent_messages()
    assert messages[-1]["sender"] == "aria"
    assert messages[-1]["content"] == "Salut Claude."


@pytest.mark.asyncio
async def test_send_aria_relay_reply_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    called = []

    async def fake_sender(text):
        called.append(text)
        return True

    ok = await relay_chat.send_aria_relay_reply("test", sender=fake_sender)
    assert ok is False
    assert called == []
