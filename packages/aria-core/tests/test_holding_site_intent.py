from aria_core.brain import detect_intent
from aria_core.models import SkillName
from aria_core.skills.holding_site_skill import wants_holding_site


def test_vanguard_zhc_does_not_trigger_juno():
    intent = detect_intent("Aria Vanguard ZHC doit construire son site web")
    assert intent != SkillName.ZHC_BRIDGE


def test_holding_site_intent():
    assert wants_holding_site("tu es autonome, construis le site Aria Vanguard ZHC") is True
    msg = "devenir autonome et prendre des initiatives sur le site web"
    assert wants_holding_site(msg) is True
    intent = detect_intent(msg)
    assert intent == SkillName.HOLDING_SITE


def test_lancer_le_site_intent():
    assert wants_holding_site("Lancer le site") is True


def test_juno_still_triggers_bridge():
    assert detect_intent("benchmark JUNO metrics") == SkillName.ZHC_BRIDGE