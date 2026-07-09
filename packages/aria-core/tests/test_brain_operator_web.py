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

    async def _fake_resolve(query, lang):
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

    async def _fake_resolve(query, lang):
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
