"""Correctif : la conversation OPÉRATEUR (public=False) doit atteindre le chemin web
(resolve_calibrated_answer -> web_first_answer -> Tavily) sur une question d'ACTU.

Avant le fix, ce chemin était gaté `public` uniquement : l'opérateur ne déclenchait
jamais la recherche web et ARIA répondait de mémoire. is_live_info_question exclut déjà
les sujets perso opérateur et produits ARIA, donc on n'ouvre que l'actu.
"""
from __future__ import annotations

import pytest

from aria_core.brain import AriaBrain


@pytest.mark.asyncio
async def test_operator_live_info_reaches_calibrated_web_path(monkeypatch):
    called = {"query": None}

    async def _fake_resolve(query, lang, **kwargs):
        called["query"] = query
        return ("NEWS_SENTINEL: Nvidia a annoncé X aujourd'hui.", {"web_verified": True})

    monkeypatch.setattr(
        "aria_core.knowledge.epistemic.resolve_calibrated_answer", _fake_resolve
    )

    brain = AriaBrain()
    reply, skill, labels, data, _ = await brain._general_response(
        "quelles sont les dernières news sur Nvidia ?", "fr", public=False,
    )

    # L'opérateur (public=False) atteint bien resolve_calibrated_answer sur une question d'actu.
    assert called["query"] is not None
    assert "NEWS_SENTINEL" in reply


def test_casual_operator_question_is_not_live_info():
    """La branche opérateur ne s'ouvre que sur is_live_info_question/is_explicit_web_request :
    une question d'opinion/casual ne doit PAS être considérée comme de l'actu (sinon on force
    le chemin web et on perd la voix fondateur)."""
    from aria_core.knowledge.web_verify import is_explicit_web_request, is_live_info_question

    assert is_live_info_question("quelles sont les dernières news sur Nvidia ?")
    assert not is_live_info_question("tu penses quoi de la vie en général ?")
    assert not is_live_info_question("explique-moi ta stratégie d'investissement")
    assert not is_explicit_web_request("tu penses quoi de la vie en général ?")


@pytest.mark.asyncio
async def test_operator_explicit_web_request_reaches_calibrated_web_path(monkeypatch):
    """Correctif 09/07 (2e round) : une demande explicite de vérification web ("vérifie sur
    le web...") ne matchait aucun mot-clé de is_live_info_question (actu/sport/prix) et ne
    déclenchait donc jamais Tavily/DDG pour l'opérateur -- même si elle est confirmée active.
    is_explicit_web_request comble ce trou indépendamment du sujet."""
    called = {"query": None}

    async def _fake_resolve(query, lang, **kwargs):
        called["query"] = query
        return ("WALLET_CHECK_SENTINEL: adresse confirmée Paradigm sur Etherscan.", {})

    monkeypatch.setattr(
        "aria_core.knowledge.epistemic.resolve_calibrated_answer", _fake_resolve
    )

    brain = AriaBrain()
    reply, *_ = await brain._general_response(
        "vérifie sur le web si cette adresse est bien Paradigm sur Etherscan",
        "fr",
        public=False,
    )

    assert called["query"] is not None
    assert "WALLET_CHECK_SENTINEL" in reply


@pytest.mark.asyncio
async def test_operator_public_flag_actually_threaded_to_resolve_calibrated_answer(monkeypatch):
    """Correctif 09/07 (3e round, incident réel) : public=False n'était même pas un paramètre
    de resolve_calibrated_answer -- brain.py appelait resolve_calibrated_answer(message,
    lang_key) sans le transmettre, donc should_use_web_verify() retombait sur is_public_mode()
    (réglage de déploiement global, toujours True en prod) au lieu du vrai statut opérateur.
    Résultat vécu : une question opérateur auto-réflexive ("remonte-moi tous les bugs
    détectés") a déclenché une recherche web hors-sujet présentée comme une actu vérifiée.
    Ce test verrouille que `public` arrive bien jusqu'à resolve_calibrated_answer."""
    received = {}

    async def _fake_resolve(query, lang, **kwargs):
        received.update(kwargs)
        return ("SENTINEL", {})

    monkeypatch.setattr(
        "aria_core.knowledge.epistemic.resolve_calibrated_answer", _fake_resolve
    )

    brain = AriaBrain()
    await brain._general_response(
        "vérifie sur le web si cette adresse est bien Paradigm sur Etherscan",
        "fr",
        public=False,
    )

    assert received.get("public") is False


@pytest.mark.asyncio
async def test_operator_vc_followup_uses_local_memory_not_web(monkeypatch):
    """Suivi /vc : +515 pourquoi ? doit s'ancrer sur le dernier rapport, pas le web."""
    web_called = {"n": 0}

    async def _fake_resolve(query, lang, **kwargs):
        web_called["n"] += 1
        return ("WEB_SHOULD_NOT_RUN", {})

    async def _fake_vc_block(*, lang="fr"):
        return "DERNIER RAPPORT /vc: +515% entrée 0.346 cible 2.13 AVOID"

    async def _fake_llm(self, message, lang, **kwargs):
        assert "DERNIER RAPPORT" in (kwargs.get("extra_system_context") or "")
        return "Le +515% vient de l'entrée 0.346 vers la cible 2.13."

    monkeypatch.setattr(
        "aria_core.knowledge.epistemic.resolve_calibrated_answer", _fake_resolve,
    )
    monkeypatch.setattr(
        "aria_core.skills.vc_session_context.get_followup_context_block", _fake_vc_block,
    )
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)

    brain = AriaBrain()
    reply, skill, labels, data, _ = await brain._general_response(
        "+515 pourquoi ?", "fr", public=False,
    )

    assert web_called["n"] == 0
    assert data.get("vc_followup") is True
    assert "515" in reply
    assert "0.346" in reply

