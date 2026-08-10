from aria_core.llm_routing_meta import is_llm_routing_question, llm_routing_reply
from aria_core.runtime import get_settings


def test_detect_llm_routing_question():
    assert is_llm_routing_question("/depth develop quel moteur LLM utilises-tu")
    assert is_llm_routing_question("route vers virtuals spark")
    assert not is_llm_routing_question("bonjour")


def test_llm_routing_reply_virtuals():
    s = get_settings()
    s.llm_provider = "virtuals"
    s.virtuals_api_key = "acp-" + "x" * 20
    out = llm_routing_reply("fr", "/depth develop quel moteur")
    assert "virtuals" in out.lower()
    assert "compute.virtuals.io" in out
    assert "apache" not in out.lower() or "PAS Apache" in out


def test_llm_routing_reply_anthropic_routing_dormant_by_default():
    # #118, 27/07 -- dormant while OpenRouter/Grok remain active (operator decision).
    s = get_settings()
    s.llm_provider = "grok"
    out = llm_routing_reply("fr", "/depth develop quel moteur")
    assert "dormant" in out.lower()
    assert "claude-sonnet-5" not in out


def test_llm_routing_reply_anthropic_routing_active_reports_haiku_for_develop():
    # 10/08, explicit operator decision: conversational depth (even develop)
    # never reaches Sonnet -- reserved for vc_judge.py's final verdict only.
    s = get_settings()
    s.llm_provider = "grok"
    s.aria_llm_anthropic_routing_enabled = True
    out = llm_routing_reply("fr", "/depth develop quel moteur")
    assert "claude-haiku-4-5-20251001" in out
    assert "ACTIF" in out


def test_llm_routing_reply_anthropic_routing_active_reports_haiku_for_brief():
    s = get_settings()
    s.llm_provider = "grok"
    s.aria_llm_anthropic_routing_enabled = True
    out = llm_routing_reply("fr", "/depth brief ok")
    assert "claude-haiku-4-5-20251001" in out