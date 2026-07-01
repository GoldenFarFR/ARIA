from aria_core.knowledge.exposure_curriculum import generate_curriculum_message


def test_generate_curriculum_message_fr(monkeypatch, tmp_path):
    from aria_core.knowledge import exposure_curriculum as ec

    state = tmp_path / "curriculum_state.json"
    monkeypatch.setattr(ec, "_STATE_PATH", state)

    msg = generate_curriculum_message("fr")
    assert msg is not None
    assert "Curriculum épistémique" in msg
    assert "Domaine" in msg

    assert generate_curriculum_message("fr") is None