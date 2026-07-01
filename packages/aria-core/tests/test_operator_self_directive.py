from aria_core.operator_self_directive import (
    SelfMaintenanceAction,
    OperatorMessageKind,
    classify_operator_message,
    is_operator_self_directive,
    parse_self_maintenance_action,
)


def test_banner_directive_french():
    msg = "Tu va mettre a jour ta banniere sur X ?"
    assert classify_operator_message(msg) == OperatorMessageKind.SELF_DIRECTIVE
    assert is_operator_self_directive(msg)
    assert parse_self_maintenance_action(msg) == SelfMaintenanceAction.UPDATE_X_BANNER


def test_general_howto_not_self():
    msg = "Comment changer une banniere Twitter en general ?"
    assert classify_operator_message(msg) == OperatorMessageKind.GENERAL_INFO
    assert parse_self_maintenance_action(msg) is None


def test_curiosity_gap():
    msg = "Je vois une belle banniere sur X, tu n as pas la tienne ?"
    assert classify_operator_message(msg) == OperatorMessageKind.CURIOSITY_GAP
    assert parse_self_maintenance_action(msg) == SelfMaintenanceAction.CURIOSITY_X_BANNER


def test_avatar_directive():
    msg = "Tu dois changer ta photo de profil sur X"
    assert classify_operator_message(msg) == OperatorMessageKind.SELF_DIRECTIVE
    assert parse_self_maintenance_action(msg) == SelfMaintenanceAction.UPDATE_X_AVATAR