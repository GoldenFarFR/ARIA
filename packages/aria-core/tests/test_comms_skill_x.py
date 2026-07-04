import pytest

from aria_core.skills.comms_skill import (
    _wants_immediate_x_publish,
    compose_x_tweet,
    execute_comms_draft,
    extract_explicit_x_post_body,
)


def test_extract_explicit_x_post_only_after_command():
    assert extract_explicit_x_post_body("publie sur x: Hello world") == "Hello world"
    assert extract_explicit_x_post_body(
        "Je veux publier un tweet sur X pour apprendre : tu souhaiterais apprendre quoi ?"
    ) is None


@pytest.mark.asyncio
async def test_comms_x_draft_when_not_configured(monkeypatch):
    import aria_core.skills.comms_skill as mod

    async def fake_post(_text, approval_id=""):
        return None, "Tweet préparé — X pas connecté.\n\nTexte:\nHello"

    async def fake_rep(_lang):
        return "portfolio"

    async def fake_compose(_msg, _lang="en"):
        return "Composed learning tweet for ARIA."

    monkeypatch.setattr("aria_core.gateway.x_twitter.is_x_post_configured", lambda: False)
    monkeypatch.setattr("aria_core.gateway.x_twitter.post_tweet", fake_post)
    monkeypatch.setattr(mod, "get_repertoire_summary", fake_rep)
    monkeypatch.setattr(mod, "compose_x_tweet", fake_compose)

    out, data = await execute_comms_draft("publie sur x: Hello", lang="fr")
    assert data.get("posted") is False
    assert data.get("draft_only") is True
    assert data.get("tweet_text") == "Hello"
    assert data.get("composed") is False


def test_proposal_does_not_trigger_immediate_publish():
    msg = (
        "propose moi un tweet a publier qui t'aiderai a mieux comprendre tes objectif"
    )
    assert _wants_immediate_x_publish(msg) is False


def test_explicit_imperative_triggers_publish():
    assert _wants_immediate_x_publish("publie sur x: Hello world") is True
    assert _wants_immediate_x_publish("publie maintenant sur x ce tweet") is True


@pytest.mark.asyncio
async def test_comms_x_draft_on_proposal_not_publish(monkeypatch):
    import aria_core.skills.comms_skill as mod

    async def fake_post(text, approval_id=""):
        raise AssertionError(f"should not post: {text}")

    async def fake_rep(_lang):
        return "portfolio"

    async def fake_compose(_msg, _lang="fr"):
        return "Question tweet brouillon ARIA."

    monkeypatch.setattr("aria_core.gateway.x_twitter.is_x_post_configured", lambda: True)
    monkeypatch.setattr("aria_core.gateway.x_twitter.post_tweet", fake_post)
    monkeypatch.setattr(mod, "get_repertoire_summary", fake_rep)
    monkeypatch.setattr(mod, "compose_x_tweet", fake_compose)

    msg = (
        "propose moi un tweet a publier qui t'aiderai a mieux comprendre tes objectif"
    )
    out, data = await execute_comms_draft(msg, lang="fr")
    assert data.get("posted") is False
    assert data.get("draft_only") is True
    assert "non publié" in out.lower()
    assert "/x compose" in out


@pytest.mark.asyncio
async def test_comms_x_composes_when_explicit_publish(monkeypatch):
    import aria_core.skills.comms_skill as mod

    posted_text = ""

    async def fake_post(text, approval_id=""):
        nonlocal posted_text
        posted_text = text
        return None, "Publié sur X\nhttps://x.com/Aria_ZHC/status/123"

    async def fake_rep(_lang):
        return "portfolio"

    async def fake_compose(_msg, _lang="fr"):
        return (
            "Learning sprint #1 — what topic should ARIA prioritize: signals, agents, or structure? "
            "Education only."
        )

    monkeypatch.setattr("aria_core.gateway.x_twitter.is_x_post_configured", lambda: True)
    monkeypatch.setattr("aria_core.gateway.x_twitter.post_tweet", fake_post)
    monkeypatch.setattr(mod, "get_repertoire_summary", fake_rep)
    monkeypatch.setattr(mod, "compose_x_tweet", fake_compose)

    msg = "publie sur x: Learning sprint — what topic should ARIA prioritize next?"
    out, data = await execute_comms_draft(msg, lang="fr")
    assert data.get("composed") is False
    assert "Learning sprint" in posted_text
    assert "tu souhaiterais" not in posted_text.lower()
    assert "Rédaction" in out
    assert data.get("posted") is True


@pytest.mark.asyncio
async def test_compose_x_tweet_fallback_learning(monkeypatch):
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: False)
    text = await compose_x_tweet(
        "publie un tweet pour apprendre ta première leçon",
        lang="fr",
    )
    assert "learn" in text.lower() or "study" in text.lower()
    assert "dexpulse" not in text.lower()
    assert len(text) <= 280