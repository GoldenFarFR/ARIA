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
    s.aria_llm_model_develop = "x-ai-grok-4-3"
    out = llm_routing_reply("fr", "/depth develop quel moteur")
    assert "virtuals" in out.lower()
    assert "grok" in out.lower()
    assert "compute.virtuals.io" in out
    assert "apache" not in out.lower() or "PAS Apache" in out