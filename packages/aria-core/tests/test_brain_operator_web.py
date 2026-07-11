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
    """Suivi /vc : intercepté tôt dans process(), pas le web ni les skills launchpad."""
    web_called = {"n": 0}

    async def _fake_resolve(query, lang, **kwargs):
        web_called["n"] += 1
        return ("WEB_SHOULD_NOT_RUN", {})

    async def _fake_vc_block(*, lang="fr"):
        return "DERNIER RAPPORT /vc: +605% entrée 0.346 cible 2.13 AVOID"

    async def _fake_llm(self, message, lang, **kwargs):
        assert "DERNIER RAPPORT" in (kwargs.get("extra_system_context") or "")
        return "AVOID car whale 57% et liquidité mince — le +605% est mécanique (support→sommet)."

    monkeypatch.setattr(
        "aria_core.knowledge.epistemic.resolve_calibrated_answer", _fake_resolve,
    )
    monkeypatch.setattr(
        "aria_core.skills.vc_session_context.get_followup_context_block", _fake_vc_block,
    )
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)
    async def _noop_save(*a, **k):
        return None

    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process("pourquoi avoid ?", lang="fr", public_mode=False)

    assert web_called["n"] == 0
    assert response.data.get("vc_followup") is True
    assert "605" in response.reply or "AVOID" in response.reply
    assert "LAUNCHPAD" not in response.reply


@pytest.mark.asyncio
async def test_operator_llm_identity_question_never_reaches_free_llm(monkeypatch):
    """Régression réelle (11/07) : l'opérateur a demandé "tu fonctionnes avec quel type
    d'intelligence, un LLM ?" et ARIA a répondu "Opus 4.8" — grounded_llm_identity()
    n'est jamais injecté côté opérateur (grounded_for_audience(public) est toujours False
    pour l'opérateur). Ce test verrouille le nouveau chemin déterministe : _llm_response
    ne doit JAMAIS être appelé pour cette question, côté opérateur comme côté public."""
    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "ne devrait jamais être atteint"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)

    brain = AriaBrain()
    reply, skill, labels, data, _ = await brain._general_response(
        "tu fonctionnes avec quel type d'intelligence, un LLM ?", "fr", public=False,
    )

    assert llm_calls["n"] == 0
    assert data.get("llm_identity") is True
    assert "opus" not in reply.lower()
    assert "grok" not in reply.lower()

    # Même garantie côté visiteur public.
    reply_pub, _, _, data_pub, _ = await brain._general_response(
        "are you an LLM?", "en", public=True,
    )
    assert llm_calls["n"] == 0
    assert data_pub.get("llm_identity") is True
    assert "opus" not in reply_pub.lower()


@pytest.mark.asyncio
async def test_operator_analysis_methodology_question_never_reaches_free_llm(monkeypatch):
    """Régression réelle (11/07) : "comment tu analyses un token, IA générative ?" a reçu une
    réponse générique en 6 points ne citant aucun vrai outil. Ce test verrouille le nouveau
    chemin déterministe : _llm_response ne doit jamais être appelé, et la réponse doit citer
    les vrais outils du pipeline (pas une description générique)."""
    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "ne devrait jamais être atteint"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)

    brain = AriaBrain()
    reply, skill, labels, data, _ = await brain._general_response(
        "comment tu analyses un token, tu utilises de l'IA générative ?", "fr", public=False,
    )

    assert llm_calls["n"] == 0
    assert data.get("analysis_methodology") is True
    assert "goplus" in reply.lower()
    assert "blockscout" in reply.lower()


@pytest.mark.asyncio
async def test_analysis_methodology_question_wins_over_vc_followup_interceptor(monkeypatch):
    """Incident réel (11/07, post-déploiement 32e6b2f5) : le fix précédent verrouillait
    `_general_response`, mais `process()` appelle `_try_vc_followup_response` AVANT même
    d'atteindre `_general_response`. `vc_session_context.is_vc_followup_question` matche
    "comment tu analyses un token, tu utilises de l'IA générative ?" (son regex générique
    capture "comment" + "token") dès qu'un /vc récent traîne en mémoire courte (TTL 4h) —
    la question partait alors vers un vrai appel LLM (10923 tokens en prod réel), jamais vers
    le template déterministe. Reproduit les conditions réelles (un /vc récent en cache) via
    `process()` (pas `_general_response` directement) et verrouille que le chemin déterministe
    gagne quand même."""
    from aria_core.skills import vc_session_context

    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "ne devrait jamais être atteint"

    async def _fake_followup_block(*, lang="fr"):
        # Simule un /vc récent encore dans la fenêtre TTL de 4h.
        return "DERNIER RAPPORT /vc: WOJAK entrée 0.01 cible 0.05 BUY"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)
    monkeypatch.setattr(vc_session_context, "get_followup_context_block", _fake_followup_block)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)

    async def _noop_save(*a, **k):
        return None

    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process(
        "comment tu analyses un token, tu utilises de l'IA générative ?",
        lang="fr",
        public_mode=False,
    )

    assert llm_calls["n"] == 0
    assert response.data.get("analysis_methodology") is True
    assert response.data.get("vc_followup") is not True
    assert "goplus" in response.reply.lower()
    assert "blockscout" in response.reply.lower()


@pytest.mark.asyncio
async def test_llm_identity_question_wins_over_early_interceptors(monkeypatch):
    """Même garde-fou que ci-dessus, pour l'autre question (identité LLM) — verrouille qu'elle
    gagne aussi via `process()` (pas seulement `_general_response`), avant tout autre routage
    précoce (self-maintenance, operator readiness, etc.)."""
    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "ne devrait jamais être atteint"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)

    async def _noop_save(*a, **k):
        return None

    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process(
        "tu fonctionnes avec quel type d'intelligence, un LLM ?",
        lang="fr",
        public_mode=False,
    )

    assert llm_calls["n"] == 0
    assert response.data.get("llm_identity") is True
    assert "opus" not in response.reply.lower()

