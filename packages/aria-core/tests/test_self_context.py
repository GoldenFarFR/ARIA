from aria_core.grounding import is_factual_question, is_general_qa
from aria_core.memory.self_context import is_self_context_question


def test_self_context_objectifs_question():
    msg = "pourquoi existe tu et quel sont tes objectifs ?"
    assert is_self_context_question(msg)
    assert is_factual_question(msg)
    assert not is_general_qa(msg)


def test_self_context_identity_question():
    msg = "Salut aria, parle moi de toi que souhaite tu"
    assert is_self_context_question(msg)


def test_self_context_goldenfar():
    msg = "pourquoi as tu été programmée par GoldenFarFR ?"
    assert is_self_context_question(msg)


def test_external_career_not_self_context():
    msg = "quels sont tes objectifs de carrière en entretien ?"
    assert not is_self_context_question(msg)


def test_random_still_general_qa():
    assert is_general_qa("quelle est la capitale du japon en 2026")