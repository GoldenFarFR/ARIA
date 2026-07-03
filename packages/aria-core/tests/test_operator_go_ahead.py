from aria_core.operator_go_ahead import (
    wants_operator_deferred_go,
    _thread_goal_hint,
)


def test_deferred_go_ok_vazy():
    assert wants_operator_deferred_go("ok vazy")
    assert wants_operator_deferred_go("vas-y !")


def test_deferred_go_benefique_seulement():
    assert wants_operator_deferred_go("si c'est benefique pour toi seulement tu peux")


def test_not_deferred_ok_prevu():
    assert not wants_operator_deferred_go("ok prevu")


def test_thread_goal_hint():
    msgs = [
        {"role": "user", "content": "comment tu vois le site holding ?"},
        {"role": "agent", "content": "Je priorise aria-vanguard. Il manque le bandeau commu."},
        {"role": "user", "content": "ok vazy"},
    ]
    hint = _thread_goal_hint(msgs)
    assert "site holding" in hint or "aria-vanguard" in hint