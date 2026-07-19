"""founder_ping ancré sur l'état réel (pool + track-record) + responsabilisation sur sa
dernière initiative — avant ce correctif, c'était du texte LLM pur, sans lien avec les
vraies données (candidat inventé possible, aucun suivi de promesse)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from aria_core import proactive
from aria_core.runtime import get_settings
from aria_core.skills.candidate_ranking import RankedCandidate


def _fake_candidate(symbol="GOOD", score=82.0, verdict="SAFE"):
    return RankedCandidate(
        contract="0x" + "a" * 40, symbol=symbol, rank_score=score,
        security_score=78, liquidity_usd=50_000.0, top_holder_pct=12.0, verdict=verdict,
    )


@pytest.mark.asyncio
async def test_real_state_snapshot_includes_pool_and_track_record(monkeypatch):
    async def fake_top_candidates(n):
        return [_fake_candidate()]

    async def fake_total_count():
        return 7

    async def fake_open_preds(limit=1000):
        return [{
            "id": 1, "strategy": "vc",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }]

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )

    snapshot = await proactive._real_state_snapshot()
    assert "GOOD" in snapshot
    assert "SAFE" in snapshot
    assert "7 pronostic" in snapshot
    # Ouvert mais pas encore à échéance (créé à l'instant, horizon vc=30j) --
    # ne doit JAMAIS inviter à "finaliser" un pronostic non résolvable (14/07).
    assert "AUCUN à échéance" in snapshot
    assert "rien à finaliser" in snapshot


@pytest.mark.asyncio
async def test_real_state_snapshot_empty_pool_says_so(monkeypatch):
    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 0

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )

    snapshot = await proactive._real_state_snapshot()
    assert "aucun candidat disponible" in snapshot
    assert "0 pronostic" in snapshot
    assert "aucun pronostic ouvert" in snapshot


@pytest.mark.asyncio
async def test_real_state_snapshot_degrades_gracefully_on_failure(monkeypatch):
    async def boom(n):
        raise RuntimeError("db down")

    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", boom)

    # Ne doit jamais lever -- best-effort, dégradation douce.
    snapshot = await proactive._real_state_snapshot()
    assert isinstance(snapshot, str)


def test_last_initiative_recap_returns_last_entry(monkeypatch):
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory",
        lambda category, limit: ["HH:MM:SS UTC]\nAncienne idée", "HH:MM:SS UTC]\nDernière idée"],
    )
    recap = proactive._last_initiative_recap()
    assert "Dernière idée" in recap


def test_last_initiative_recap_empty_when_no_history(monkeypatch):
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory", lambda category, limit: []
    )
    assert proactive._last_initiative_recap() == ""


@pytest.mark.asyncio
async def test_run_founder_ping_injects_real_state_and_accountability(monkeypatch):
    settings = get_settings()
    settings.aria_proactive_ideas = True
    settings.aria_llm_enabled = True
    settings.llm_api_key = "x"
    settings.llm_provider = "groq"
    settings.telegram_bot_token = "t"
    settings.telegram_admin_ids = "1"

    async def fake_top_candidates(n):
        return [_fake_candidate(symbol="REALTOKEN")]

    async def fake_total_count():
        return 3

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory",
        lambda category, limit: ["HH:MM:SS UTC]\nJ'ai promis un pronostic hier"],
    )

    captured = {}

    async def fake_chat(user, system, **kwargs):
        captured["system"] = system
        return "Verdict : ok."

    with patch("aria_core.proactive.build_llm_context", new=AsyncMock(return_value="contexte")):
        monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)
        reply = await proactive.run_founder_ping(lang="fr")

    assert reply == "Verdict : ok."
    system = captured["system"]
    assert "REALTOKEN" in system
    assert "3 pronostic" in system
    assert "J'ai promis un pronostic hier" in system
    assert "DERNIÈRE initiative" in system


@pytest.mark.asyncio
async def test_run_founder_ping_forbids_treating_candidates_as_people(monkeypatch):
    """Régression : Groq (fallback) a confondu un candidat token avec un prospect humain
    à "contacter" dans un test hors-prod -- le prompt doit interdire ça explicitement,
    quel que soit le provider derrière."""
    settings = get_settings()
    settings.aria_proactive_ideas = True
    settings.aria_llm_enabled = True
    settings.llm_api_key = "x"
    settings.llm_provider = "groq"
    settings.telegram_bot_token = "t"
    settings.telegram_admin_ids = "1"

    async def fake_top_candidates(n):
        return [_fake_candidate()]

    async def fake_total_count():
        return 1

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory", lambda category, limit: []
    )

    captured = {}

    async def fake_chat(user, system, **kwargs):
        captured["system"] = system
        return "Verdict : ok."

    with patch("aria_core.proactive.build_llm_context", new=AsyncMock(return_value="contexte")):
        monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)
        await proactive.run_founder_ping(lang="fr")

    system = captured["system"]
    assert "CONTRATS TOKEN" in system
    assert "jamais des" in system and "personnes" in system


@pytest.mark.asyncio
async def test_run_founder_ping_forbids_narrating_unexecuted_actions(monkeypatch):
    """#150 (13/07) : "Initiative ARIA" a annoncé un tweet ("shipping...") jamais posté --
    confirmé par l'opérateur en conditions réelles. Cette fonction n'exécute STRICTEMENT
    RIEN (pas de tweet, pas de commit), donc le prompt doit interdire toute formulation
    de fait accompli et exiger le conditionnel/proposition explicite."""
    settings = get_settings()
    settings.aria_proactive_ideas = True
    settings.aria_llm_enabled = True
    settings.llm_api_key = "x"
    settings.llm_provider = "groq"
    settings.telegram_bot_token = "t"
    settings.telegram_admin_ids = "1"

    async def fake_top_candidates(n):
        return [_fake_candidate()]

    async def fake_total_count():
        return 1

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory", lambda category, limit: []
    )

    captured = {}

    async def fake_chat(user, system, **kwargs):
        captured["system"] = system
        return "Verdict : ok."

    with patch("aria_core.proactive.build_llm_context", new=AsyncMock(return_value="contexte")):
        monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)
        await proactive.run_founder_ping(lang="fr")

    system = captured["system"]
    assert "n'exécute STRICTEMENT RIEN" in system
    assert "j'ai posté" in system.lower()
    assert "je propose de" in system.lower()


@pytest.mark.asyncio
async def test_run_founder_ping_no_last_initiative_no_accountability_block(monkeypatch):
    settings = get_settings()
    settings.aria_proactive_ideas = True
    settings.aria_llm_enabled = True
    settings.llm_api_key = "x"
    settings.llm_provider = "groq"
    settings.telegram_bot_token = "t"
    settings.telegram_admin_ids = "1"

    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 0

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory", lambda category, limit: []
    )

    captured = {}

    async def fake_chat(user, system, **kwargs):
        captured["system"] = system
        return "Verdict : ok."

    with patch("aria_core.proactive.build_llm_context", new=AsyncMock(return_value="contexte")):
        monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)
        await proactive.run_founder_ping(lang="fr")

    assert "DERNIÈRE initiative" not in captured["system"]


# ── Garde de qualité déterministe post-génération (#141, 19/07) ────────────────────
# Réponse au feedback opérateur direct sur une initiative confabulée ("si c'est des
# initiatives pourries autant qu'elle ne le dise pas") -- 2e récurrence du même bug
# de fond que le 14/07 (finaliser un pronostic non échu), cette fois sous la forme
# d'un "verdict chiffré sur la fiabilité" de pronostics tous non résolus, plus une
# initiative ACP relancée alors qu'ACP est abandonné par décision.

@pytest.mark.asyncio
async def test_real_state_snapshot_flags_zero_resolved_predictions(monkeypatch):
    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 14

    async def fake_open_preds(limit=1000):
        return []

    async def fake_metrics():
        return {"closed": 0, "open": 14}

    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates)
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", fake_total_count)
    monkeypatch.setattr("aria_core.vc_predictions.list_open_predictions", fake_open_preds)
    monkeypatch.setattr("aria_core.vc_predictions.metrics", fake_metrics)

    snapshot = await proactive._real_state_snapshot()
    assert "0 pronostic RÉSOLU" in snapshot
    assert "aucun taux de réussite/fiabilité" in snapshot


@pytest.mark.asyncio
async def test_real_state_snapshot_omits_zero_resolved_flag_once_some_resolve(monkeypatch):
    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 14

    async def fake_open_preds(limit=1000):
        return []

    async def fake_metrics():
        return {"closed": 3, "open": 11}

    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates)
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", fake_total_count)
    monkeypatch.setattr("aria_core.vc_predictions.list_open_predictions", fake_open_preds)
    monkeypatch.setattr("aria_core.vc_predictions.metrics", fake_metrics)

    snapshot = await proactive._real_state_snapshot()
    assert "0 pronostic RÉSOLU" not in snapshot


def test_quality_violation_blocks_reliability_claim_with_zero_resolved():
    reply = "Je propose un verdict chiffré sur la fiabilité de mes 14 derniers pronostics."
    violation = proactive._founder_ping_quality_violation(reply, resolved_count=0)
    assert violation is not None
    assert "fiabilit" in violation


def test_quality_violation_allows_reliability_claim_once_some_resolved():
    reply = "Je propose un verdict chiffré sur la fiabilité de mes 14 derniers pronostics."
    violation = proactive._founder_ping_quality_violation(reply, resolved_count=3)
    assert violation is None


def test_quality_violation_blocks_abandoned_acp_mention():
    reply = "Je propose de relancer l'initiative ACP marketplace, restée sans suite."
    violation = proactive._founder_ping_quality_violation(reply, resolved_count=0)
    assert violation is not None
    assert "abandonné" in violation


def test_quality_violation_blocks_dexpulse_mention_even_with_resolved_predictions():
    """Le sujet abandonné reste bloqué indépendamment du compte de pronostics résolus
    -- les deux règles sont indépendantes, pas un seul garde combiné."""
    reply = "On pourrait relancer DEXPulse comme produit phare."
    violation = proactive._founder_ping_quality_violation(reply, resolved_count=5)
    assert violation is not None


def test_quality_violation_passes_clean_reply():
    reply = "Je propose d'analyser le token 0xabc... via /vc, thèse basée sur la liquidité réelle."
    assert proactive._founder_ping_quality_violation(reply, resolved_count=0) is None


@pytest.mark.asyncio
async def test_run_founder_ping_suppresses_reply_that_violates_quality_gate(monkeypatch):
    """Bout en bout : si le LLM produit malgré tout un texte qui viole le garde
    (ici : relance ACP), run_founder_ping ne renvoie RIEN -- jamais envoyé à
    Telegram -- plutôt qu'un message avec un défaut connu.

    ``monkeypatch.setattr`` (jamais une mutation directe de ``get_settings()``,
    singleton partagé sans nettoyage entre tests -- cause probable des échecs
    déjà connus et indépendants de ce correctif sur les 4 autres tests
    ``run_founder_ping`` de ce fichier, visibles seulement en suite complète)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "aria_proactive_ideas", True)
    monkeypatch.setattr(settings, "aria_llm_enabled", True)
    monkeypatch.setattr(settings, "llm_api_key", "x")
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    monkeypatch.setattr(settings, "telegram_admin_ids", "1")

    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 0

    async def fake_open_preds(limit=1000):
        return []

    async def fake_metrics():
        return {"closed": 0}

    async def fake_build_context(public=False):
        return "contexte"

    async def fake_chat(user, system, **kwargs):
        return "Je propose de relancer l'initiative ACP marketplace, restée sans suite."

    logged = []

    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates)
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", fake_total_count)
    monkeypatch.setattr("aria_core.vc_predictions.list_open_predictions", fake_open_preds)
    monkeypatch.setattr("aria_core.vc_predictions.metrics", fake_metrics)
    monkeypatch.setattr("aria_core.proactive.read_recent_memory", lambda category, limit: [])
    monkeypatch.setattr("aria_core.proactive.build_llm_context", fake_build_context)
    monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)
    monkeypatch.setattr(
        "aria_core.proactive.append_memory",
        lambda category, text: logged.append(text),
    )

    reply = await proactive.run_founder_ping(lang="fr")

    assert reply is None
    assert any("[founder_ping][BLOQUÉ" in entry for entry in logged)


@pytest.mark.asyncio
async def test_run_founder_ping_allows_clean_reply_through(monkeypatch):
    """Contraste direct : un texte propre (aucune violation) passe normalement."""
    settings = get_settings()
    monkeypatch.setattr(settings, "aria_proactive_ideas", True)
    monkeypatch.setattr(settings, "aria_llm_enabled", True)
    monkeypatch.setattr(settings, "llm_api_key", "x")
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    monkeypatch.setattr(settings, "telegram_admin_ids", "1")

    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 0

    async def fake_open_preds(limit=1000):
        return []

    async def fake_metrics():
        return {"closed": 0}

    async def fake_build_context(public=False):
        return "contexte"

    async def fake_chat(user, system, **kwargs):
        return "Je propose d'analyser un nouveau candidat via /vc dès qu'un token émerge."

    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates)
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", fake_total_count)
    monkeypatch.setattr("aria_core.vc_predictions.list_open_predictions", fake_open_preds)
    monkeypatch.setattr("aria_core.vc_predictions.metrics", fake_metrics)
    monkeypatch.setattr("aria_core.proactive.read_recent_memory", lambda category, limit: [])
    monkeypatch.setattr("aria_core.proactive.build_llm_context", fake_build_context)
    monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)

    reply = await proactive.run_founder_ping(lang="fr")

    assert reply == "Je propose d'analyser un nouveau candidat via /vc dès qu'un token émerge."
