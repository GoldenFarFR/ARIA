from aria_core.holding import GOVERNANCE_RULE, holding_name
from aria_core.narrative import (
    llm_system_block,
    one_liner,
    welcome_chat,
    x_juno_greeting,
    x_juno_intent_url,
    zhc_intro_from_agent,
)


def test_one_liner_names_vanguard():
    text = one_liner("en")
    assert holding_name() in text
    assert "Vanguard" in text
    assert "DEXPulse" not in text


def test_llm_system_block_forbids_dexpulse_as_holding():
    block = llm_system_block("en")
    assert holding_name() in block
    assert "retired" in block.lower() or "Aria Market" in block
    assert GOVERNANCE_RULE in block or "subsidiary" in block.lower()


def test_welcome_chat_cao_not_cofounder():
    text = welcome_chat("en")
    assert "Chief Autonomous Officer" in text
    assert "co-founder" not in text.lower()


def test_welcome_chat_operator_vanguard_not_dexpulse():
    text = welcome_chat("fr")
    assert "Vanguard" in text
    assert "DEXPulse" not in text
    assert "holding mère" not in text.lower()


def test_x_juno_from_holding_not_dexpulse():
    assert "no subsidiary" in x_juno_greeting().lower()
    assert holding_name() in x_juno_greeting()
    assert "ARIA@DEXPulse" not in zhc_intro_from_agent()
    assert "AriaVanguard" in zhc_intro_from_agent() or holding_name().replace(" ", "") in zhc_intro_from_agent()


def test_x_intent_url_mentions_vanguard():
    url = x_juno_intent_url()
    assert "twitter.com/intent/tweet" in url
    assert "Vanguard" in url or "vanguard" in url.lower()