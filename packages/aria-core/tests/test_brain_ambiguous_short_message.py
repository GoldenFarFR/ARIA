"""Garde-fou anti-gaspillage (08/08) -- un message opérateur trop court/vague
("feddback", faute de frappe pour "feedback") ne doit jamais atteindre un
appel LLM payant juste pour recevoir "précise ta demande" en retour. Même
patron que test_brain_trade_status.py (LLM mocké pour confirmer qu'il n'est
JAMAIS appelé sur ce chemin, sauf quand ARIA vient de poser une question)."""
from __future__ import annotations

import pytest

from aria_core.brain import AriaBrain


async def _noop_save(*a, **k):
    return None


async def _fake_repertoire_summary(lang):
    return "stub"


async def _false():
    return False


async def _true():
    return True


def _mock_common(monkeypatch, llm_calls):
    async def _fake_llm(self, message, lang, *, public=False, visitor_id="", **k):
        llm_calls["n"] += 1
        return "réponse LLM (ne devrait jamais être atteinte)"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)
    # brain.py imports is_llm_configured directly at module scope (line 37) --
    # patching aria_core.llm.is_llm_configured alone does NOT affect that
    # already-bound reference, must patch it where it's actually used.
    monkeypatch.setattr("aria_core.brain.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)
    import aria_core.brain as brain_mod

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)


@pytest.mark.asyncio
async def test_ambiguous_operator_message_skips_the_llm_entirely(monkeypatch):
    llm_calls = {"n": 0}
    _mock_common(monkeypatch, llm_calls)
    monkeypatch.setattr(
        "aria_core.brain.AriaBrain._last_agent_turn_asked_a_question",
        lambda self: _false(),
    )

    brain = AriaBrain()
    response = await brain.process("feddback", lang="fr", public_mode=False)

    assert llm_calls["n"] == 0
    assert response.data.get("ambiguous_short_message") is True
    assert "précise" in response.reply.lower()


@pytest.mark.asyncio
async def test_guard_stands_down_when_aria_just_asked_a_question(monkeypatch):
    """Un court "ouais" en réponse à une question qu'ARIA vient de poser ne
    doit jamais être avalé par le garde-fou -- il peut retomber sur n'importe
    quelle autre route légitime (LLM ou pas), l'important est que CE garde-fou
    précis ne s'en mêle pas."""
    llm_calls = {"n": 0}
    _mock_common(monkeypatch, llm_calls)
    monkeypatch.setattr(
        "aria_core.brain.AriaBrain._last_agent_turn_asked_a_question",
        lambda self: _true(),
    )

    brain = AriaBrain()
    response = await brain.process("ouais", lang="fr", public_mode=False)

    assert response.data.get("ambiguous_short_message") is not True


@pytest.mark.asyncio
async def test_real_question_never_gets_swallowed_even_if_short(monkeypatch):
    llm_calls = {"n": 0}
    _mock_common(monkeypatch, llm_calls)
    monkeypatch.setattr(
        "aria_core.brain.AriaBrain._last_agent_turn_asked_a_question",
        lambda self: _false(),
    )

    brain = AriaBrain()
    response = await brain.process("prix?", lang="fr", public_mode=False)

    assert response.data.get("ambiguous_short_message") is not True


@pytest.mark.asyncio
async def test_public_visitor_never_gets_the_guard(monkeypatch):
    """Doctrine admin-only : ce garde-fou cible le coût réel du chat opérateur,
    jamais l'expérience d'un visiteur public (surface client, jamais dégradée)."""
    llm_calls = {"n": 0}
    _mock_common(monkeypatch, llm_calls)

    brain = AriaBrain()
    response = await brain.process("feddback", lang="fr", public_mode=True)

    assert response.data.get("ambiguous_short_message") is not True


@pytest.mark.asyncio
async def test_longer_legitimate_message_never_hits_the_guard(monkeypatch):
    llm_calls = {"n": 0}
    _mock_common(monkeypatch, llm_calls)
    monkeypatch.setattr(
        "aria_core.brain.AriaBrain._last_agent_turn_asked_a_question",
        lambda self: _false(),
    )

    brain = AriaBrain()
    response = await brain.process(
        "peux-tu me faire un point sur le portfolio v8", lang="fr", public_mode=False,
    )

    assert response.data.get("ambiguous_short_message") is not True


@pytest.mark.asyncio
async def test_last_agent_turn_asked_a_question_reads_real_history(monkeypatch):
    """_last_agent_turn_asked_a_question elle-même (pas mockée cette fois) --
    doit lire le vrai dernier message role='agent' et ignorer le tour user
    qu'on vient de sauvegarder juste avant."""
    async def _fake_get_messages(limit=50, visitor_id=None):
        return [
            {"role": "user", "content": "ouais"},
            {"role": "agent", "content": "Tu veux que je lance le scan maintenant ?"},
            {"role": "user", "content": "précédent message"},
        ]

    monkeypatch.setattr("aria_core.repertoire_db.get_messages", _fake_get_messages)
    brain = AriaBrain()
    assert await brain._last_agent_turn_asked_a_question() is True


@pytest.mark.asyncio
async def test_last_agent_turn_asked_a_question_false_when_no_question(monkeypatch):
    async def _fake_get_messages(limit=50, visitor_id=None):
        return [
            {"role": "user", "content": "ouais"},
            {"role": "agent", "content": "C'est fait."},
        ]

    monkeypatch.setattr("aria_core.repertoire_db.get_messages", _fake_get_messages)
    brain = AriaBrain()
    assert await brain._last_agent_turn_asked_a_question() is False


@pytest.mark.asyncio
async def test_last_agent_turn_asked_a_question_degrades_silently_on_db_failure(monkeypatch):
    async def _broken(limit=50, visitor_id=None):
        raise RuntimeError("DB indisponible")

    monkeypatch.setattr("aria_core.repertoire_db.get_messages", _broken)
    brain = AriaBrain()
    assert await brain._last_agent_turn_asked_a_question() is False
