import pytest

from aria_core.skills.comms_skill import _wants_faq_draft, execute_comms_draft


def test_operator_question_not_faq_draft():
    msg = "Es-ce que tu as des questions concernant ton travail comme PDG de vanguard ?"
    assert _wants_faq_draft(msg, msg.lower()) is False


@pytest.mark.asyncio
async def test_comms_delegates_role_coaching(monkeypatch):
    import aria_core.skills.comms_skill as mod

    async def fake_coaching(text: str) -> str:
        return f"COACHING:{text[:40]}"

    monkeypatch.setattr(
        "aria_core.tweet_compose_workflow.start_role_coaching_workflow",
        fake_coaching,
    )

    msg = "Concernant ton identité et ton travail comme zhc"
    out, data = await execute_comms_draft(msg, lang="fr")
    assert data.get("role_coaching") is True
    assert "COACHING:" in out
    assert "Brouillon" not in out