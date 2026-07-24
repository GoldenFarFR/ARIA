"""Refus déterministe d'une commande de fermeture manuelle de position (24/07).

Incident réel : l'opérateur a tapé "ferme la position autono" (AUTONO, +80% de
P&L non réalisé) dans Telegram. Le mot-clé "position" a été capté par le
routeur d'intent générique (``INTENT_PATTERNS``, ``ANALYZE_PORTFOLIO``), qui
scanne la WATCHLIST de découverte (candidats, jamais les positions du
portefeuille papier) -- réponse confuse mêlant "la watchlist est vide" et une
demande de clarification, sans jamais expliquer clairement la vraie doctrine :
ARIA ne ferme jamais une position papier sur commande manuelle (protocole
hebdomadaire, CLAUDE.md, "test pur, sans validation humaine").

Même patron que test_brain_trade_status.py -- checké AVANT le routage
générique, dans ``AriaBrain.process()``."""
from __future__ import annotations

import pytest

from aria_core.brain import AriaBrain


async def _noop_save(*a, **k):
    return None


async def _fake_repertoire_summary(lang):
    return "stub"


@pytest.mark.asyncio
async def test_manual_close_command_gets_a_clear_refusal_never_watchlist_confusion(monkeypatch):
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    async def _fail_if_called(lang):
        raise AssertionError("le scan watchlist ne doit jamais être atteint sur une commande de fermeture")

    monkeypatch.setattr("aria_core.brain.execute_portfolio_analysis", _fail_if_called)

    brain = AriaBrain()
    response = await brain.process("ferme la position autono", lang="fr", public_mode=False)

    assert response.data.get("manual_close_refusal") is True
    assert "watchlist" not in response.reply.lower()
    assert "jamais" in response.reply.lower()
    assert "/feedback" in response.reply or "/ledger" in response.reply


@pytest.mark.asyncio
async def test_manual_close_refusal_mentions_the_real_discipline_mechanisms(monkeypatch):
    """La réponse doit orienter vers les vrais mécanismes de correction (Devil's
    Advocate + suivi par lot de 10) plutôt qu'un simple refus sec sans suite."""
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process("vends AERO, clôture la position", lang="fr", public_mode=False)

    assert response.data.get("manual_close_refusal") is True
    assert "devil" in response.reply.lower()
    assert "lot de 10" in response.reply.lower()


@pytest.mark.asyncio
async def test_manual_close_refusal_replies_in_english_when_asked(monkeypatch):
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process("close this position now", lang="en", public_mode=False)

    assert response.data.get("manual_close_refusal") is True
    assert "never close" in response.reply.lower()


@pytest.mark.asyncio
async def test_trade_status_question_still_reaches_its_own_handler(monkeypatch):
    """Une vraie question de statut ("pourquoi as-tu vendu ?") ne doit jamais être
    interceptée par ce nouveau refus -- les deux chemins restent mutuellement
    exclusifs (commande vs question)."""
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: False)  # dégrade proprement, pas d'appel réseau

    brain = AriaBrain()
    response = await brain.process("pourquoi t'as vendu AERO ?", lang="fr", public_mode=False)

    assert response.data.get("manual_close_refusal") is not True


@pytest.mark.asyncio
async def test_manual_close_public_visitor_never_reaches_the_refusal_path(monkeypatch):
    """Doctrine admin-only (même que /feedback, /ledger, trade_status) : un visiteur
    PUBLIC ne doit jamais déclencher ce chemin, même avec la même formulation."""
    import aria_core.brain as brain_mod

    async def _fail_if_called(*a, **k):
        raise AssertionError("_try_manual_close_refusal_response ne doit jamais être atteint en mode public")

    monkeypatch.setattr(brain_mod.AriaBrain, "_try_manual_close_refusal_response", _fail_if_called)

    async def _fake_truth_ledger(*a, **k):
        return "", {}

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)
    monkeypatch.setattr(brain_mod, "truth_ledger_direct_answer", _fake_truth_ledger)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process("ferme la position autono", lang="fr", public_mode=True)
    assert response is not None


@pytest.mark.asyncio
async def test_unrelated_message_never_triggers_manual_close_refusal(monkeypatch):
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: False)

    brain = AriaBrain()
    response = await brain.process("salut, comment ça va ?", lang="fr", public_mode=False)
    assert response.data.get("manual_close_refusal") is not True
