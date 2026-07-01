from aria_core.curiosity import parse_learn_approval


def test_learn_approval_oui():
    assert parse_learn_approval("oui") is True
    assert parse_learn_approval("Oui") is True
    assert parse_learn_approval("Oui!") is True


def test_learn_approval_non():
    assert parse_learn_approval("non") is False
    assert parse_learn_approval("Non.") is False


def test_learn_approval_english():
    assert parse_learn_approval("learn yes") is True
    assert parse_learn_approval("learn no") is False
    assert parse_learn_approval("yes") is True


def test_learn_approval_unrelated():
    assert parse_learn_approval("bonjour") is None
    assert parse_learn_approval("runbook") is None