from aria_core.content.service import _score_faq, _load_faq
from aria_core.grounding import (
    FAQ_DIRECT_SCORE,
    anti_hallucination_rules,
    faq_direct_answer,
    format_greeting_reply,
    grounded_for_audience,
    is_greeting,
    is_social_chitchat,
    should_skip_llm_enhance,
    social_ack_reply,
    unknown_reply,
)


def test_grounded_for_audience_operator_bypass():
    assert grounded_for_audience(public=False) is False
    assert grounded_for_audience(public=True) is True


def test_faq_direct_answer_high_confidence():
    reply, data = faq_direct_answer("What is DEXPulse?")
    assert data["faq_direct"] is True
    assert "DEXPulse" in reply


def test_faq_direct_answer_low_confidence():
    reply, data = faq_direct_answer("xyzzy nonsense question 12345")
    assert data["faq_direct"] is False
    assert reply is None


def test_should_skip_llm_enhance_factual_skills():
    assert should_skip_llm_enhance("faq_content") is True
    assert should_skip_llm_enhance("marketing_comms") is False


def test_anti_hallucination_rules_mention_verified():
    rules = anti_hallucination_rules("en")
    assert "VERIFIED" in rules.upper() or "verified" in rules


def test_unknown_reply_no_invention():
    text = unknown_reply("en")
    assert "verified" in text.lower()


def test_faq_score_threshold():
    items = _load_faq()
    assert items
    score = _score_faq("What is ARIA?", items[0])
    assert score >= 0
    assert FAQ_DIRECT_SCORE >= 4


def test_greeting_detected_gm_hello():
    assert is_greeting("gm") is True
    assert is_greeting("GM!") is True
    assert is_greeting("hello") is True
    assert is_greeting("bonjour") is True
    assert is_greeting("what is dexpulse") is False


def test_operator_greeting_reply_french_bonjour():
    text = format_greeting_reply("hello", "en", public=False)
    assert "Bonjour" in text
    assert "Vanguard" in text
    assert "DEXPulse" not in text


def test_gm_greeting_prefix():
    text = format_greeting_reply("gm", "fr", public=False)
    assert text.startswith("GM !")
    assert "Bonjour" in text


def test_social_chitchat_detected():
    assert is_social_chitchat("Merci pour les félicitations !")
    assert is_social_chitchat("Bravo pour le succès, continue comme ça")
    assert not is_social_chitchat("Qu'est-ce que DEXPulse ?")


def test_social_ack_no_revenue_claims():
    text = social_ack_reply("fr")
    assert "revenus" in text.lower() or "vérifi" in text.lower()
    assert "équipe" not in text.lower() or "vérifi" in text.lower()