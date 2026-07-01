import pytest

from aria_core.brain import detect_intent
from aria_core.knowledge.base_launchpads import methodology_markdown
from aria_core.knowledge.contradiction import check_contradiction
from aria_core.models import SkillName
from aria_core.skills.launchpad_skill import execute_launchpad_select, wants_launchpad_methodology


def test_wants_launchpad_methodology_followup():
    msg = (
        "explique moi quelles sont tes sources sur chaque catégories, "
        "visibilité, volume, développeur"
    )
    assert wants_launchpad_methodology(msg) is True
    assert detect_intent(msg) == SkillName.LAUNCHPAD_SELECT


def test_developpeur_does_not_route_to_repertoire():
    msg = (
        "explique moi quelles sont tes sources sur chaque catégories, "
        "visibilité, volume, développeur"
    )
    assert detect_intent(msg) != SkillName.DEVELOP_REPERTOIRE


def test_develop_repertoire_still_works():
    msg = "développe le répertoire des filiales"
    assert detect_intent(msg) == SkillName.DEVELOP_REPERTOIRE


def test_methodology_markdown_covers_axes():
    body = methodology_markdown(lang="fr", holding_context=True)
    for token in ("Volume", "Builders", "Communauté", "Exposition", "Holding fit", "DeFiLlama"):
        assert token in body


@pytest.mark.asyncio
async def test_execute_launchpad_methodology_mode():
    msg = "quelle est ta méthodologie pour le score visibilité et volume ?"
    reply, data = await execute_launchpad_select(msg, "fr")
    assert data.get("mode") == "methodology"
    assert "Pondération" in reply or "pondération" in reply.lower()
    assert "Volume" in reply


def test_repertoire_filiale_not_false_contradiction():
    sample = "Ajouter une source de revenu à la filiale DEXPulse"
    conflict, _ = check_contradiction(sample, "fr")
    assert conflict is False


def test_wrong_holding_claim_still_flagged():
    conflict, msg = check_contradiction("DEXPulse est la holding mère", "fr")
    assert conflict is True
    assert "holding" in msg.lower() or "DEXPulse" in msg or "dexpulse" in msg.lower()