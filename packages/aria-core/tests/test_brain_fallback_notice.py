"""#135 : quand un appel LLM du tour est passé par la route de secours (Spark down),
AriaBrain.process() doit le noter dans ChatResponse.data -- c'est le seul point de passage
obligé (begin/clear_chat_usage_tracking) commun à tous les appels internes de
chat_with_context (_enhance_with_llm, _general_response, _llm_response), donc indépendant
de la branche interne réellement empruntée pour un message donné.
"""
from __future__ import annotations

import pytest

from aria_core.brain import AriaBrain
from aria_core.models import ChatResponse


@pytest.mark.asyncio
async def test_process_marks_fallback_used_in_data(monkeypatch):
    from aria_core import llm_usage

    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        # Simule un chat_with_context interne qui a dû basculer sur le fallback.
        llm_usage.mark_fallback_used("groq")
        return ChatResponse(reply="réponse générée malgré tout", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    response = await brain.process("question test", "fr")

    assert response.data.get("llm_fallback_used") is True
    assert response.data.get("llm_fallback_provider") == "groq"


@pytest.mark.asyncio
async def test_process_silent_when_primary_succeeds(monkeypatch):
    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        return ChatResponse(reply="réponse normale", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    response = await brain.process("question test", "fr")

    # Silence total : aucune clé de fallback ajoutée si le tour n'en a pas eu besoin.
    assert "llm_fallback_used" not in response.data
    assert "llm_fallback_provider" not in response.data


@pytest.mark.asyncio
async def test_process_fallback_state_does_not_leak_across_calls(monkeypatch):
    """Le contextvar est réinitialisé à chaque process() -- un fallback sur un tour ne doit
    jamais contaminer le tour suivant."""
    from aria_core import llm_usage

    calls = {"n": 0}

    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        calls["n"] += 1
        if calls["n"] == 1:
            llm_usage.mark_fallback_used("groq")
        return ChatResponse(reply=f"réponse {calls['n']}", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    first = await brain.process("question 1", "fr")
    second = await brain.process("question 2", "fr")

    assert first.data.get("llm_fallback_used") is True
    assert "llm_fallback_used" not in second.data
