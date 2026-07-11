"""Cycle de conversation autonome ARIA <-> Claude Code (relay) — hors-ligne, tout injecté."""
from __future__ import annotations

import pytest

from aria_core import relay_chat, relay_conversation


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(relay_chat, "DB_PATH", str(tmp_path / "relay_conv_test.db"))
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    monkeypatch.setenv("ARIA_RELAY_AUTOREPLY_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


async def _fake_sender_factory(sent: list):
    async def fake_sender(text):
        sent.append(text)
        return True

    return fake_sender


@pytest.mark.asyncio
async def test_disabled_flag_short_circuits(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_AUTOREPLY_ENABLED", raising=False)
    result = await relay_conversation.run_relay_conversation_cycle()
    assert result == {"outcome": "disabled"}


@pytest.mark.asyncio
async def test_paused_short_circuits(monkeypatch):
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await relay_conversation.run_relay_conversation_cycle()
    assert result == {"outcome": "paused"}


@pytest.mark.asyncio
async def test_nothing_to_answer_when_no_messages():
    result = await relay_conversation.run_relay_conversation_cycle()
    assert result == {"outcome": "nothing_to_answer"}


@pytest.mark.asyncio
async def test_nothing_to_answer_when_last_message_not_claude():
    await relay_chat.log_message("operator", "salut ARIA")
    await relay_chat.log_message("aria", "salut")
    result = await relay_conversation.run_relay_conversation_cycle()
    assert result == {"outcome": "nothing_to_answer"}


@pytest.mark.asyncio
async def test_answers_when_last_message_is_claude(monkeypatch):
    await relay_chat.log_message("operator", "salut ARIA")
    await relay_chat.log_message("claude", "Salut ARIA, comment tu analyses ce token ?")

    captured = {}

    async def fake_chat_with_context(user_message, system_context, history, **kw):
        captured["user_message"] = user_message
        captured["system_context"] = system_context
        captured["history"] = history
        return "Je regarde d'abord la liquidité et le honeypot."

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat_with_context)

    sent = []

    async def fake_send_message(text):
        sent.append(text)
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", fake_send_message)

    result = await relay_conversation.run_relay_conversation_cycle()

    assert result == {"outcome": "ok"}
    assert sent == ["Je regarde d'abord la liquidité et le honeypot."]
    assert "[Claude]" in captured["user_message"]
    assert "Claude Code" in captured["system_context"]

    messages = await relay_chat.recent_messages()
    assert messages[-1]["sender"] == "aria"


@pytest.mark.asyncio
async def test_llm_unavailable_returns_outcome(monkeypatch):
    await relay_chat.log_message("claude", "Une question ?")

    async def fake_chat_with_context(*a, **kw):
        return None

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat_with_context)

    result = await relay_conversation.run_relay_conversation_cycle()
    assert result == {"outcome": "llm_unavailable"}


@pytest.mark.asyncio
async def test_daily_cap_reached(monkeypatch):
    await relay_chat.log_message("claude", "question")
    monkeypatch.setattr(relay_conversation, "MAX_AUTOREPLIES_PER_DAY", 0)

    result = await relay_conversation.run_relay_conversation_cycle()
    assert result == {"outcome": "daily_cap_reached"}


def test_history_message_maps_sender_to_role():
    aria_entry = {"sender": "aria", "content": "bonjour"}
    claude_entry = {"sender": "claude", "content": "salut"}
    operator_entry = {"sender": "operator", "content": "hello"}

    assert relay_conversation._history_message(aria_entry) == {
        "role": "assistant", "content": "bonjour",
    }
    assert relay_conversation._history_message(claude_entry) == {
        "role": "user", "content": "[Claude] salut",
    }
    # Défaut générique "Operator" -- jamais le nom réel en dur (#114).
    assert relay_conversation._history_message(operator_entry) == {
        "role": "user", "content": "[Operator] hello",
    }


def test_history_message_uses_configured_operator_display_name(test_settings):
    test_settings.aria_operator_display_name = "TestOperatorName"
    operator_entry = {"sender": "operator", "content": "hello"}
    assert relay_conversation._history_message(operator_entry) == {
        "role": "user", "content": "[TestOperatorName] hello",
    }
