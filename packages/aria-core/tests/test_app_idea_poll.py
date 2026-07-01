import pytest

from aria_core.knowledge.app_idea_poll import (
    format_poll_message,
    parse_app_vote,
    record_app_vote,
    run_app_idea_poll_cycle,
)


def test_parse_app_vote():
    assert parse_app_vote("app 1") == 1
    assert parse_app_vote("app 2") == 2
    assert parse_app_vote("APP 3") == 3
    assert parse_app_vote("oui") is None
    assert parse_app_vote("2") is None


@pytest.mark.asyncio
async def test_run_app_idea_poll_cycle_fallback(monkeypatch, tmp_path):
    from aria_core.knowledge import app_idea_poll as poll

    state = tmp_path / "app_idea_poll_state.json"
    monkeypatch.setattr(poll, "_STATE_PATH", state)
    monkeypatch.setattr(poll, "is_llm_configured", lambda: False)

    result = await run_app_idea_poll_cycle("fr")
    assert result["status"] == "ok"
    assert len(result["ideas"]) == 3
    assert "app 1" in result["message"].lower()
    assert "25" in result["message"]


def test_record_app_vote(monkeypatch, tmp_path):
    from aria_core.knowledge import app_idea_poll as poll
    from aria_core.knowledge import cultivation_curriculum as cc

    poll_state = tmp_path / "app_idea_poll_state.json"
    cult_state = tmp_path / "cultivation_curriculum_state.json"
    monkeypatch.setattr(poll, "_STATE_PATH", poll_state)
    monkeypatch.setattr(cc, "_STATE_PATH", cult_state)

    poll._save_state(
        {
            "ideas": [
                {"title": "App A", "pitch": "p1", "stack": "kotlin", "revenue": "2.99"},
                {"title": "App B", "pitch": "p2", "stack": "rn", "revenue": "ads"},
            ]
        }
    )

    reply = record_app_vote(2, lang="fr")
    assert "App B" in reply
    assert poll._load_state().get("selected_index") == 1


def test_format_poll_message_en():
    ideas = [{"title": "Test", "pitch": "x", "stack": "kotlin", "revenue": "iap"}]
    msg = format_poll_message(ideas, "en")
    assert "Kelly" in msg
    assert "Play Store" in msg