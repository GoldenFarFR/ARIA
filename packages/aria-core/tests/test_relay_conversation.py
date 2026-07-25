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
async def test_answers_claude_message_even_if_other_messages_land_after_it(monkeypatch):
    """25/07, operator-found gap: an automatic bulletin (or an operator message)
    landing after Claude's question, before the cycle runs, used to make ARIA drop
    the question forever (only the LAST relay message was ever checked)."""
    await relay_chat.log_message("claude", "Tu vois une anomalie sur tes wallets ?")
    await relay_chat.log_message("aria", "🧪 SIMULATION — bilan paper-trading automatique")
    await relay_chat.log_message("operator", "salut ARIA")

    captured = {}

    async def fake_chat_with_context(user_message, system_context, history, **kw):
        captured["user_message"] = user_message
        return "Rien d'anormal de mon côté."

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat_with_context)

    sent = []

    async def fake_send_message(text):
        sent.append(text)
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", fake_send_message)

    result = await relay_conversation.run_relay_conversation_cycle()

    assert result == {"outcome": "ok"}
    assert sent == ["Rien d'anormal de mon côté."]
    assert "Tu vois une anomalie" in captured["user_message"]


@pytest.mark.asyncio
async def test_never_answers_the_same_claude_message_twice(monkeypatch):
    await relay_chat.log_message("claude", "Une question ?")

    async def fake_chat_with_context(*a, **kw):
        return "Une réponse."

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat_with_context)

    async def fake_send_message(text):
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", fake_send_message)

    first = await relay_conversation.run_relay_conversation_cycle()
    assert first == {"outcome": "ok"}

    # No new "claude" message since -- must not re-answer the same one, even
    # though other non-claude messages may have landed since (e.g. ARIA's own
    # reply is now the last message, same as the original rule already covered).
    second = await relay_conversation.run_relay_conversation_cycle()
    assert second == {"outcome": "nothing_to_answer"}


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


def test_system_context_forbids_generic_ai_cliches():
    assert "CLICHÉS DE REMPLISSAGE IA" in relay_conversation._SYSTEM_CONTEXT


def _fake_position(**overrides):
    base = {
        "symbol": "AUTONO", "contract": "0xb3d7e0c3c39a1d3f1b304663065a2f83ddf56d8e",
        "status": "open", "strategy": "momentum", "entry_price": 4.96e-06,
        "target_price": 1.2e-05, "invalidation_price": 3.8e-06, "rr": 2.4,
        "conviction_tier": "forte", "pnl_usd": 16415.0, "pnl_pct": 94.3,
        "thesis": None, "close_reason": None, "close_notes": None,
    }
    base.update(overrides)
    return base


def test_match_position_by_whole_word_symbol():
    positions = [_fake_position()]
    matched = relay_conversation._match_position(
        "Ta position AUTONO est a +94% de gain latent", positions,
    )
    assert matched is not None
    assert matched["symbol"] == "AUTONO"


def test_match_position_ignores_substring_not_whole_word():
    """25/07 -- a symbol embedded in a longer word (ex. 'AUTO' inside
    'AUTOMATIQUEMENT') must never match -- only a real, whole-word mention."""
    positions = [_fake_position(symbol="AUTO")]
    matched = relay_conversation._match_position(
        "Le bilan est genere automatiquement chaque jour", positions,
    )
    assert matched is None


def test_match_position_by_contract_prefix():
    positions = [_fake_position(symbol="AUTONO")]
    matched = relay_conversation._match_position(
        "Regarde 0xb3d7e0c3c39a1d3f1b304663065a2f83ddf56d8e stp", positions,
    )
    assert matched is not None


def test_match_position_returns_none_when_nothing_mentioned():
    positions = [_fake_position()]
    matched = relay_conversation._match_position("Comment tu te sens ?", positions)
    assert matched is None


def test_position_facts_block_cites_real_numbers_never_invents():
    pos = _fake_position(thesis="Golden pocket + divergence RSI, R/R 2.4 a l'entree.")
    block = relay_conversation._position_facts_block(pos)
    assert "AUTONO" in block
    assert "94.3" in block
    assert "2.4" in block
    assert "Golden pocket" in block
    assert "ne jamais en inventer" in block


def test_position_facts_block_flags_missing_thesis_explicitly():
    pos = _fake_position(thesis=None)
    block = relay_conversation._position_facts_block(pos)
    assert "Aucune these texte enregistree" in block


@pytest.mark.asyncio
async def test_position_context_for_message_prefers_open_over_closed(monkeypatch):
    open_pos = _fake_position(symbol="AUTONO", status="open")
    closed_pos = _fake_position(symbol="AUTONO", status="closed", close_reason="stop suiveur")

    async def fake_open():
        return [open_pos]

    async def fake_closed(limit=200):
        return [closed_pos]

    monkeypatch.setattr("aria_core.paper_trader.get_open_positions", fake_open)
    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", fake_closed)

    block = await relay_conversation._position_context_for_message("Et AUTONO alors ?")
    assert "ouverte" in block
    assert "stop suiveur" not in block


@pytest.mark.asyncio
async def test_position_context_for_message_falls_back_to_closed(monkeypatch):
    closed_pos = _fake_position(symbol="AERO", status="closed", close_reason="invalidation")

    async def fake_open():
        return []

    async def fake_closed(limit=200):
        return [closed_pos]

    monkeypatch.setattr("aria_core.paper_trader.get_open_positions", fake_open)
    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", fake_closed)

    block = await relay_conversation._position_context_for_message("Pourquoi AERO a ferme ?")
    assert block is not None
    assert "invalidation" in block


@pytest.mark.asyncio
async def test_position_context_for_message_none_when_no_match(monkeypatch):
    async def fake_open():
        return [_fake_position(symbol="AUTONO")]

    async def fake_closed(limit=200):
        return []

    monkeypatch.setattr("aria_core.paper_trader.get_open_positions", fake_open)
    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", fake_closed)

    block = await relay_conversation._position_context_for_message("Comment tu te sens ?")
    assert block is None


@pytest.mark.asyncio
async def test_cycle_injects_real_position_facts_into_system_context(monkeypatch):
    """25/07, operator-found gap: ARIA answered a question about AUTONO (+94%
    latent) by inventing a plausible-sounding but false story about her own
    decision process, instead of citing the real thesis/R-R already sitting in
    `paper_position`. The relay cycle must now hand her those real facts."""
    await relay_chat.log_message(
        "claude", "Ta position AUTONO est a +94%, tu as une vraie these dessus ?",
    )

    async def fake_open():
        return [_fake_position(symbol="AUTONO", thesis="Golden pocket confirme, R/R 2.4.")]

    async def fake_closed(limit=200):
        return []

    monkeypatch.setattr("aria_core.paper_trader.get_open_positions", fake_open)
    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", fake_closed)

    captured = {}

    async def fake_chat_with_context(user_message, system_context, history, **kw):
        captured["system_context"] = system_context
        return "Reponse ancree sur les vrais chiffres."

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat_with_context)

    async def fake_send_message(text):
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", fake_send_message)

    result = await relay_conversation.run_relay_conversation_cycle()

    assert result == {"outcome": "ok"}
    assert "DONNEES REELLES VERIFIEES" in captured["system_context"]
    assert "Golden pocket confirme" in captured["system_context"]


@pytest.mark.asyncio
async def test_cycle_leaves_system_context_untouched_without_a_token_match(monkeypatch):
    await relay_chat.log_message("claude", "Comment tu te sens par rapport au test ?")

    async def fake_open():
        return [_fake_position(symbol="AUTONO")]

    async def fake_closed(limit=200):
        return []

    monkeypatch.setattr("aria_core.paper_trader.get_open_positions", fake_open)
    monkeypatch.setattr("aria_core.paper_trader.get_closed_positions", fake_closed)

    captured = {}

    async def fake_chat_with_context(user_message, system_context, history, **kw):
        captured["system_context"] = system_context
        return "Reponse generale."

    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat_with_context)

    async def fake_send_message(text):
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", fake_send_message)

    result = await relay_conversation.run_relay_conversation_cycle()

    assert result == {"outcome": "ok"}
    assert captured["system_context"] == relay_conversation._SYSTEM_CONTEXT
