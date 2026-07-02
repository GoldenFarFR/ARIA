from aria_core.grounding import is_general_qa
from aria_core.memory.collegue import is_collegue_recall_question


def test_collegue_recall_detected():
    msg = "que sais-tu de COLLEGUE.md et de mes préférences Excel ?"
    assert is_collegue_recall_question(msg)
    assert not is_general_qa(msg)


def test_random_question_still_general_qa():
    assert is_general_qa("quelle est la capitale du japon en 2026")