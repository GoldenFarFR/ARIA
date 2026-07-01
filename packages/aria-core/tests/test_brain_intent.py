from aria_core.brain import _is_strategic_conversation, detect_intent
from aria_core.models import SkillName


def test_strategic_github_question_skips_repertoire_skill():
    msg = (
        "aujourd'hui je développe tout depuis mon github personnel, "
        "souhaites-tu avoir le tien"
    )
    assert _is_strategic_conversation(msg)
    assert detect_intent(msg) is None


def test_develop_repertoire_still_detected_for_catalog_requests():
    msg = "développe le répertoire des filiales"
    assert not _is_strategic_conversation(msg)
    assert detect_intent(msg) == SkillName.DEVELOP_REPERTOIRE