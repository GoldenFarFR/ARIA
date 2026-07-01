from aria_core.x_voice import (
    has_ai_voice_markers,
    human_voice_rules_for_llm,
    looks_like_feature_roster,
    strip_obvious_ai_phrases,
)


def test_human_voice_rules_mention_forbidden_phrases():
    rules = human_voice_rules_for_llm("en")
    assert "autonomous agent" in rules.lower()
    assert "i'm aria" in rules.lower()


def test_detects_ai_roster():
    text = (
        "Built in public: autonomous ARIA CAO, aria-core, vector memory (Phases A-D), "
        "aria-core, multi-PC handoff, Cursor-ARIA 3-voice bridge, truth ledger."
    )
    assert looks_like_feature_roster(text) is True
    assert has_ai_voice_markers(text) is True


def test_strip_obvious_ai_phrases():
    raw = (
        "Built in public: autonomous stack ship. Operator in the loop. @GoldenFarFR"
    )
    out = strip_obvious_ai_phrases(raw)
    assert "built in public" not in out.lower()
    assert "operator in the loop" not in out.lower()
    assert "@GoldenFarFR" in out


def test_natural_sentence_not_roster():
    text = (
        "Heavy week — we landed aria-core updates and a cleaner handoff flow. "
        "Commit graph in the image. @GoldenFarFR"
    )
    assert looks_like_feature_roster(text) is False
    assert has_ai_voice_markers(text) is False