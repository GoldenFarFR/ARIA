"""Operator questions about the active LLM engine — runtime answer, no epistemic web lookup."""
from __future__ import annotations

import os
import re

from aria_core.llm_economy import LlmDepth, detect_depth
from aria_core.runtime import settings

_LLM_ROUTING_RE = re.compile(
    r"(?:"
    r"quel\s+moteur\s+llm|which\s+llm\s+(?:engine|provider)|"
    r"quelle?\s+api\s+llm|which\s+api\s+do\s+you\s+use|"
    r"utilises?[- ]?tu\s+(?:virtuals|spark|groq|grok|ollama)|"
    r"do\s+you\s+use\s+(?:virtuals|spark|groq|grok)|"
    r"route[s]?\s+(?:vers|to|via)\s+(?:virtuals|spark|groq)|"
    r"moteur\s+(?:cloud|llm)\s+(?:actif|utilis)|"
    r"provider\s*=\s*virtuals|compute\.virtuals\.io|"
    r"virtuals\s+spark\s+pas\s+apache|apache\s+spark\s+pas|"
    r"(?:pr[eé]f[eè]res?|plut[oô]t)\s*(?:groq|spark|qwen|virtuals|llm|moteur|provider|api)|"
    r"mieux.*(?:groq|spark|qwen|virtuals)|"
    r"(?:groq|spark|qwen|virtuals)\b.*\b(?:ou|vs|versus|plutôt|préfér)\b"
    r")",
    re.IGNORECASE,
)


def is_llm_routing_question(message: str) -> bool:
    from aria_core.operator_conversational import is_injected_factual_claim

    text = (message or "").strip()
    if len(text) < 8:
        return False
    if is_injected_factual_claim(text):
        return False
    if _LLM_ROUTING_RE.search(text):
        return True
    if re.search(r"(?i)/depth\s+develop\b", text) and re.search(
        r"(?i)\b(?:moteur|provider|virtuals|spark|llm|api)\b", text
    ):
        return True
    return False


def _model_for_depth(message: str) -> str:
    """#118, 27/07 -- used to duplicate llm_economy's own (then-broken) per-depth
    guard behind a `if settings.llm_provider == "virtuals":` branch that's never
    true anymore (provider is "grok") -- silently fell back to reporting a flat
    settings.llm_model regardless of depth, diverging from what resolve_budget()
    actually resolves. Now calls the same SSOT (anthropic_depth_override) so this
    diagnostic can never drift from the real routing again."""
    from aria_core.llm import DEFAULT_MODELS
    from aria_core.llm_economy import anthropic_depth_override

    depth = detect_depth(message) or LlmDepth.STANDARD
    _, model = anthropic_depth_override(depth)
    if model:
        return model
    explicit = (settings.llm_model or "").strip()
    if explicit:
        return explicit
    provider = (settings.llm_provider or "").strip().lower()
    return DEFAULT_MODELS.get(provider, "(défaut provider)")


def llm_routing_reply(lang: str, message: str = "") -> str:
    from aria_core.llm_economy import anthropic_routing_enabled

    provider = (settings.llm_provider or "none").strip().lower()
    depth = (detect_depth(message) or LlmDepth.STANDARD).value
    model = _model_for_depth(message)
    spark = provider == "virtuals"
    anthropic_routing = anthropic_routing_enabled()
    key_len = len((settings.virtuals_api_key or "").strip()) if spark else len((settings.llm_api_key or "").strip())
    endpoint = "https://compute.virtuals.io/v1/chat/completions" if spark else "(provider natif)"
    skip_groq = (os.environ.get("ARIA_OUVRIER_SKIP_GROQ_FALLBACK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if lang == "fr":
        lines = [
            "Routage LLM ARIA (lecture runtime, pas de recherche web) :",
            f"• Provider : {provider}" + (" (= Virtuals Spark)" if spark else ""),
            f"• Profondeur détectée : {depth}",
            f"• Modèle pour ce tour : {model}",
            f"• Endpoint : {endpoint}",
            f"• Clé configurée : {'oui (' + str(key_len) + ' car.)' if key_len >= 10 else 'NON — corriger coffre'}",
            f"• Routage Anthropic (Haiku/Sonnet, #118) : {'ACTIF' if anthropic_routing else 'dormant (OpenRouter/Grok actifs)'}",
        ]
        if spark:
            lines.append("• Fallback Groq : " + ("désactivé" if skip_groq else "actif si Spark échoue"))
            lines.append("• Ce n'est PAS Apache Spark — c'est Virtuals Compute (clé acp-...).")
        return "\n".join(lines)

    lines = [
        "ARIA LLM routing (runtime, no web search):",
        f"• Provider: {provider}" + (" (= Virtuals Spark)" if spark else ""),
        f"• Depth: {depth}",
        f"• Model this turn: {model}",
        f"• Endpoint: {endpoint}",
        f"• Key configured: {'yes (' + str(key_len) + ' chars)' if key_len >= 10 else 'NO — fix vault'}",
        f"• Anthropic routing (Haiku/Sonnet, #118): {'ON' if anthropic_routing else 'dormant (OpenRouter/Grok active)'}",
    ]
    if spark:
        lines.append("• Groq fallback: " + ("off" if skip_groq else "on if Spark fails"))
    return "\n".join(lines)