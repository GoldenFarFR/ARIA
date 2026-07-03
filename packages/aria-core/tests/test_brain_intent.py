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


def test_comme_does_not_route_to_marketing_comms():
    msg = (
        "tranquille tu veut faire quoi aujourd'hui comme amelioration "
        "qui pourrai taider"
    )
    assert _is_strategic_conversation(msg)
    assert detect_intent(msg) is None


def test_comms_keyword_still_routes_to_marketing():
    assert detect_intent("rédige un comms pour le site") == SkillName.MARKETING_COMMS


def test_comment_does_not_route_to_marketing_comms():
    msg = "comment astu prevu d'attirer plus dacheteur ?"
    assert _is_strategic_conversation(msg)
    assert detect_intent(msg) is None


def test_financial_plan_question_is_strategic():
    msg = "astu un plan financier de prevu"
    assert _is_strategic_conversation(msg)
    assert detect_intent(msg) is None


def test_ok_prevu_is_not_strategic():
    assert not _is_strategic_conversation("ok prevu")