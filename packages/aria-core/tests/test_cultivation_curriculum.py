from aria_core.knowledge.cultivation_curriculum import (
    generate_cultivation_message,
    mark_ship_completed,
)


def test_generate_cultivation_message_fr(monkeypatch, tmp_path):
    from aria_core.knowledge import cultivation_curriculum as cc

    state = tmp_path / "cultivation_curriculum_state.json"
    monkeypatch.setattr(cc, "_STATE_PATH", state)

    msg = generate_cultivation_message("fr")
    assert msg is not None
    assert "Culture large" in msg
    assert "Livrer" in msg
    assert "Play Store" in msg
    assert "25" in msg

    # Cooldown 24h — pas de spam après redeploy
    assert generate_cultivation_message("fr") is None


def test_mark_ship_completed(monkeypatch, tmp_path):
    from aria_core.knowledge import cultivation_curriculum as cc

    state = tmp_path / "cultivation_curriculum_state.json"
    monkeypatch.setattr(cc, "_STATE_PATH", state)

    generate_cultivation_message("en")
    loaded = cc._load_state()
    assert loaded.get("cycles_without_ship", 0) >= 1

    mark_ship_completed()
    loaded = cc._load_state()
    assert loaded.get("cycles_without_ship") == 0