"""Questions opérateur sur le moteur LLM actif — réponse runtime, sans web épistémique."""
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
    r"pr[eé]f[eè]res?|plut[oô]t|mieux.*(?:groq|spark|qwen|virtuals)|"
    r"(?:groq|spark|qwen|virtuals)\b.*\b(?:ou|vs|versus)\b"
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
    depth = detect_depth(message) or LlmDepth.STANDARD
    if (settings.llm_provider or "").strip().lower() == "virtuals":
        if depth == LlmDepth.DEVELOP:
            return (getattr(settings, "aria_llm_model_develop", None) or "").strip() or "x-ai-grok-4-3"
        if depth == LlmDepth.BRIEF:
            return (getattr(settings, "aria_llm_model_brief", None) or "").strip() or "x-ai-grok-4-3"
        return (getattr(settings, "aria_llm_model_standard", None) or "").strip() or "x-ai-grok-4-3"
    return (settings.llm_model or "").strip() or "(défaut provider)"


def llm_routing_reply(lang: str, message: str = "") -> str:
    provider = (settings.llm_provider or "none").strip().lower()
    depth = (detect_depth(message) or LlmDepth.STANDARD).value
    model = _model_for_depth(message)
    spark = provider == "virtuals"
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
    ]
    if spark:
        lines.append("• Groq fallback: " + ("off" if skip_groq else "on if Spark fails"))
    return "\n".join(lines)