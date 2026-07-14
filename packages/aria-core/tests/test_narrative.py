from aria_core.holding import GOVERNANCE_RULE, holding_name
from aria_core.narrative import (
    llm_system_block,
    no_unverified_search_state_claim_rule,
    one_liner,
    public_llm_system_block,
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


def test_no_unverified_search_state_claim_rule_forbids_crawl_fabrication():
    # Incident réel (14/07) : sur une question sans mot-clé d'actu reconnu (un
    # défilé civil), aucune recherche web n'était déclenchée, et le LLM a quand
    # même affirmé "mon crawl web est à sec, mes derniers passages reviennent
    # vides" -- une fabrication puisque rien n'avait été tenté ce tour-ci.
    fr = no_unverified_search_state_claim_rule("fr")
    assert "crawl est à sec" in fr
    assert "aucune recherche web n'a réellement eu lieu" in fr
    en = no_unverified_search_state_claim_rule("en")
    assert "my crawl is dry" in en
    assert "no web search actually happened" in en


def test_channel_rule_includes_no_unverified_search_state_claim():
    # channel_rule est construit en ligne dans _llm_response (pas de fonction
    # dédiée) -- vérifie que la règle y est bien appelée, pas seulement définie.
    import inspect

    from aria_core import brain

    source = inspect.getsource(brain.AriaBrain._llm_response)
    assert "no_unverified_search_state_claim_rule" in source


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


def test_llm_system_block_forbids_generic_ai_cliches():
    assert "CLICHÉS DE REMPLISSAGE IA" in llm_system_block("fr")
    assert "AI FILLER CLICHÉS" in llm_system_block("en")


def test_public_llm_system_block_forbids_generic_ai_cliches():
    assert "CLICHÉS DE REMPLISSAGE IA" in public_llm_system_block("fr")
    assert "AI FILLER CLICHÉS" in public_llm_system_block("en")
