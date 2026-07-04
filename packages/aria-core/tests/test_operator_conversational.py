import pytest

from aria_core.operator_conversational import (
    operator_improvement_reply,
    wants_capability_improvement,
)
from aria_core.skills.acp_conversational import is_conversational_acp_question


def test_acp_plan_is_conversational():
    assert is_conversational_acp_question("tu a prevu de faire quoi sur acp ?")
    assert is_conversational_acp_question("et concernant acp ?")


def test_capability_improvement_detected():
    assert wants_capability_improvement("il te faut quoi pour ameliorer tes competence ?")


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