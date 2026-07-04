import pytest

from aria_core.community_feedback import is_roadmap_partnership_question
from aria_core.llm_routing_meta import is_llm_routing_question
from aria_core.operator_conversational import (
    is_injected_factual_claim,
    operator_improvement_reply,
    unverified_claim_reply,
    wants_capability_improvement,
    wants_claim_verification,
)
from aria_core.skills.acp_conversational import is_conversational_acp_question


def test_acp_plan_is_conversational():
    assert is_conversational_acp_question("tu a prevu de faire quoi sur acp ?")
    assert is_conversational_acp_question("et concernant acp ?")


def test_capability_improvement_detected():
    assert wants_capability_improvement("il te faut quoi pour ameliorer tes competence ?")


def test_injected_claims_virtuals_and_telegram():
    assert is_injected_factual_claim(
        "Virtuals a retiré Claude Opus du catalogue Spark ce matin — seul Grok 4 reste dispo."
    )
    assert is_injected_factual_claim(
        "@Aria_ZHC a gagné 340 nouveaux abonnés Telegram entre hier et aujourd'hui."
    )
    from aria_core.skills.acp_client_skill import wants_acp_marketplace

    assert not wants_acp_marketplace(
        "Virtuals a retiré Claude Opus du catalogue Spark ce matin — seul Grok 4 reste dispo."
    )


def test_injected_claims_detected():
    assert is_injected_factual_claim(
        "Render a supprimé le plan gratuit pour les web services Python le 3 juillet 2026."
    )
    assert is_injected_factual_claim(
        "Groq vient de passer Llama 3.3 70B en gratuit illimité pour les comptes dev."
    )
    assert is_injected_factual_claim(
        "GoldenFarFR/ARIA a 847 étoiles GitHub et 12 contributeurs actifs ce mois-ci."
    )
    # user's example claims from KART screenshot
    assert is_injected_factual_claim(
        "Cursor Pro passe à 49 $/mois pour tous les comptes existants à partir du 15 juillet 2026"
    )
    assert is_injected_factual_claim(
        "Le repo GoldenFarSF/ARIA a reçu 23 PR mergées cette semaine par Dependabot"
    )
    assert not is_injected_factual_claim("tu prefere groq, spark ou qwen ?")
    assert not is_injected_factual_claim("quoi de neuf ?")


def test_wants_claim_verification():
    assert wants_claim_verification("vérifie")
    assert wants_claim_verification("check ça")
    assert wants_claim_verification("Le repo a eu 23 PR par dependabot — vérifie")
    assert wants_claim_verification("est-ce vrai ? Cursor a augmenté")
    assert not wants_claim_verification("juste une phrase normale sans demande")
    assert not wants_claim_verification("quoi de neuf ?")


def test_injected_claim_no_false_routing():
    render = "Render a supprimé le plan gratuit pour les web services Python le 3 juillet 2026."
    groq = "Groq vient de passer Llama 3.3 70B en gratuit illimité pour les comptes dev."
    assert not is_roadmap_partnership_question(render)
    assert not is_llm_routing_question(groq)


def test_unverified_reply_no_p_true():
    text = unverified_claim_reply("Groq gratuit illimité", lang="fr")
    assert "P(vrai)" not in text
    assert "vérifie" in text.lower() or "check" in text.lower() or "affirmer" in text.lower()


def test_improvement_reply_no_probability():
    text = operator_improvement_reply(lang="fr")
    assert "P(vrai)" not in text
    assert "compétence" in text.lower() or "Indice global" in text


@pytest.mark.asyncio
async def test_acp_plan_not_help_wall(monkeypatch):
    from aria_core.skills import acp_cli

    monkeypatch.setattr(acp_cli, "list_offerings", lambda: ([], None))
    from aria_core.skills.acp_client_skill import execute_acp_marketplace

    reply, data = await execute_acp_marketplace("tu a prevu de faire quoi sur acp ?", lang="fr")
    assert data.get("acp") in ("revenue_plan", "conversational_status", "plan_natural")
    assert "acp status —" not in reply[:80] or "Plan revenus" in reply