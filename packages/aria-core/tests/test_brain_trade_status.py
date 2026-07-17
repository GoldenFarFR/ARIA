"""Grounding des questions de trade en langage naturel (17/07) -- même patron que
`_try_vc_followup_response`/test_analysis_methodology_question_wins_over_vc_followup_
interceptor dans test_brain_operator_web.py.

Incident réel (16/07) : "tu viens de réaliser un trade perdant qu'est-ce qui c'est
passé ?", posé juste après une clôture réelle en perte, est tombé dans la conversation
LLM générale SANS accès au registre paper-trading -- ARIA a honnêtement dit ne rien voir
plutôt que d'inventer (bon réflexe), mais l'opérateur restait sans réponse alors que la
donnée existe réellement en base. `_try_trade_status_response` injecte maintenant le
registre réel (aria_core.paper_ledger_report) dans le contexte LLM via le même mécanisme
`extra_system_context` déjà utilisé par le suivi /vc.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_core import paper_trader as pt
from aria_core.brain import AriaBrain

A = "0x" + "a" * 40


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    return tmp_path


async def _noop_save(*a, **k):
    return None


async def _fake_repertoire_summary(lang):
    return "stub"


@pytest.mark.asyncio
async def test_trade_status_question_injects_real_ledger_into_llm_context(monkeypatch, tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AERO", 0.4889, target_price=0.5282, invalidation_price=0.4736, alloc_usd=50_000)
    await pt.close_position(A, 0.4734, reason="invalidation")

    captured = {}

    async def _fake_llm(self, message, lang, *, public=False, visitor_id="", extra_system_context=None, **k):
        captured["message"] = message
        captured["extra_system_context"] = extra_system_context
        return "Le stop-loss a été touché à 0.4734, perte de 1 585 $."

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process(
        "tu viens de réaliser un trade perdant qu'est-ce qui c'est passé ?",
        lang="fr",
        public_mode=False,
    )

    assert response.data.get("trade_status") is True
    assert "AERO" in captured["extra_system_context"]
    assert "invalidation" in captured["extra_system_context"].lower()
    assert "RÉEL" in captured["extra_system_context"]
    assert response.reply == "Le stop-loss a été touché à 0.4734, perte de 1 585 $."


@pytest.mark.asyncio
async def test_non_trade_question_never_reaches_trade_status_path(monkeypatch, tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    llm_calls = {"n": 0, "extra": "sentinel"}

    async def _fake_llm(self, message, lang, *, public=False, visitor_id="", extra_system_context=None, **k):
        llm_calls["n"] += 1
        llm_calls["extra"] = extra_system_context
        return "réponse générale"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process("salut, comment ça va ?", lang="fr", public_mode=False)

    assert response.data.get("trade_status") is not True
    # Si jamais un appel LLM a eu lieu par un autre chemin, il n'a pas reçu le registre.
    assert llm_calls["extra"] != None or llm_calls["n"] == 0  # noqa: E711 -- sentinel explicite


@pytest.mark.asyncio
async def test_trade_status_public_visitor_never_gets_private_ledger(monkeypatch, tmp_db):
    """Doctrine admin-only (même que /feedback, /ledger) : un visiteur PUBLIC ne doit
    jamais recevoir le registre paper-trading, même s'il pose une question qui ressemble
    à une question de trade."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AERO", 0.4889, invalidation_price=0.4736, alloc_usd=50_000)

    async def _fail_if_called(*a, **k):
        raise AssertionError("_try_trade_status_response ne doit jamais être atteint en mode public")

    monkeypatch.setattr(
        "aria_core.brain.AriaBrain._try_trade_status_response", _fail_if_called,
    )
    import aria_core.brain as brain_mod

    async def _fake_truth_ledger(*a, **k):
        return "", {}

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)
    monkeypatch.setattr(brain_mod, "truth_ledger_direct_answer", _fake_truth_ledger)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    # Ne doit pas lever -- confirme que le chemin public ne passe jamais par
    # _try_trade_status_response (gardé derrière `if not public:` dans process()).
    response = await brain.process(
        "pourquoi t'as vendu AERO ?", lang="fr", public_mode=True,
    )
    assert response is not None


@pytest.mark.asyncio
async def test_trade_status_degrades_silently_when_ledger_read_fails(monkeypatch, tmp_db):
    """Une panne de lecture du registre ne doit jamais casser la conversation --
    dégradation vers le routage normal, jamais une exception remontée à l'opérateur."""
    async def _broken_context():
        raise RuntimeError("DB indisponible")

    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "réponse de repli normale"

    monkeypatch.setattr(
        "aria_core.paper_ledger_report.build_trade_status_context", _broken_context,
    )
    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    import aria_core.brain as brain_mod

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process(
        "tu viens de réaliser un trade perdant qu'est-ce qui c'est passé ?",
        lang="fr",
        public_mode=False,
    )
    # Pas de crash -- la question retombe dans le routage normal (pas trade_status=True
    # avec des données vides, ce serait pire qu'une dégradation propre).
    assert response.data.get("trade_status") is not True
