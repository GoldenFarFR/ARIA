from aria_core.gateway.x_twitter import (
    is_x_configured,
    is_x_post_configured,
    is_x_read_configured,
    is_x_reading_active,
    x_status,
)


def test_x_status_defaults():
    st = x_status()
    assert st["handle"] == "@Aria_ZHC"
    assert "read" in st
    assert "post" in st
    assert is_x_configured() == (is_x_read_configured() or is_x_post_configured())


def test_reading_active_false_without_bearer(test_settings):
    test_settings.x_bearer_token = ""
    test_settings.x_curiosity_enabled = True
    assert is_x_read_configured() is False
    assert is_x_reading_active() is False


def test_reading_active_false_when_bearer_configured_but_all_gates_off(test_settings):
    """#123 — bearer présent mais aucune tâche consommatrice active = pas de vraie lecture
    (cf. CLAUDE.md 11/07 : lecture X coupée délibérément pour maîtriser le coût)."""
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = False
    test_settings.x_allow_replies = False
    test_settings.x_mentions_learn_enabled = False
    assert is_x_read_configured() is True
    assert is_x_reading_active() is False
    assert x_status()["reading_active"] is False


def test_reading_active_true_when_curiosity_gate_on(test_settings):
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = True
    assert is_x_reading_active() is True
    assert x_status()["reading_active"] is True


def test_reading_active_true_when_replies_gate_on(test_settings):
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = False
    test_settings.x_allow_replies = True
    assert is_x_reading_active() is True


def test_reading_active_true_when_mentions_learn_gate_on(test_settings):
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = False
    test_settings.x_allow_replies = False
    test_settings.x_mentions_learn_enabled = True
    assert is_x_reading_active() is True
