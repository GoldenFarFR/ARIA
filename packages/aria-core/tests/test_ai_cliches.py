from aria_core.ai_cliches import forbidden_cliches_prompt


def test_forbidden_cliches_prompt_fr_mentions_concrete_example():
    text = forbidden_cliches_prompt("fr")
    assert "processus complexe" in text
    assert "CLICHÉS DE REMPLISSAGE IA" in text


def test_forbidden_cliches_prompt_en_mentions_focal_words():
    text = forbidden_cliches_prompt("en")
    assert "delve" in text
    assert "tapestry" in text
    assert "AI FILLER CLICHÉS" in text
