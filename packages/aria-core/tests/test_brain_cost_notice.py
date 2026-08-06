"""06/08 -- operator request: a short "XXX$ dépensé" line on every paid
Telegram reply (Haiku/Sonnet cost tracking). AriaBrain.process() must surface
the turn's real cost in ChatResponse.data, same choke point and same
doctrine as the fallback notice (test_brain_fallback_notice.py): the
begin/clear_chat_usage_tracking pair wraps every internal chat_with_context
call regardless of which branch actually fired."""
from __future__ import annotations

import pytest

from aria_core.brain import AriaBrain
from aria_core.models import ChatResponse


@pytest.mark.asyncio
async def test_process_surfaces_real_cost_in_data(monkeypatch):
    from aria_core import llm_usage

    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        llm_usage.record_llm_usage(
            provider="anthropic", model="claude-haiku-4-5-20251001",
            input_tokens=100_000, output_tokens=10_000,
        )
        return ChatResponse(reply="réponse", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    response = await brain.process("question test", "fr")

    expected = (100_000 / 1_000_000) * 1.0 + (10_000 / 1_000_000) * 5.0
    assert response.data.get("llm_turn_cost_usd") == pytest.approx(expected)
    assert response.data.get("llm_turn_cost_unknown") is False


@pytest.mark.asyncio
async def test_process_flags_unknown_cost_without_a_dollar_figure(monkeypatch):
    from aria_core import llm_usage

    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        llm_usage.record_llm_usage(provider="grok", model="x-ai-grok-4-3", input_tokens=1000, output_tokens=100)
        return ChatResponse(reply="réponse", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    response = await brain.process("question test", "fr")

    assert response.data.get("llm_turn_cost_usd") == 0.0
    assert response.data.get("llm_turn_cost_unknown") is True


@pytest.mark.asyncio
async def test_process_silent_when_no_llm_call_happened(monkeypatch):
    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        return ChatResponse(reply="réponse déterministe, sans LLM", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    response = await brain.process("question test", "fr")

    assert "llm_turn_cost_usd" not in response.data
    assert "llm_turn_cost_unknown" not in response.data


@pytest.mark.asyncio
async def test_process_cost_does_not_leak_across_calls(monkeypatch):
    from aria_core import llm_usage

    calls = {"n": 0}

    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        calls["n"] += 1
        if calls["n"] == 1:
            llm_usage.record_llm_usage(
                provider="anthropic", model="claude-haiku-4-5-20251001",
                input_tokens=1_000_000, output_tokens=0,
            )
        return ChatResponse(reply=f"réponse {calls['n']}", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    first = await brain.process("question 1", "fr")
    second = await brain.process("question 2", "fr")

    assert first.data.get("llm_turn_cost_usd") == pytest.approx(1.0)
    assert "llm_turn_cost_usd" not in second.data
