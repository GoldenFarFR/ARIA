"""LLM depth — brief / standard / develop (token economy)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from aria_core.runtime import settings

_DEPTH_OVERRIDE = re.compile(
    r"(?:^|\s)/depth\s+(brief|standard|develop)\b",
    re.I,
)
_DEVELOP_HINT = re.compile(
    r"\b(?:d[eé]veloppe|develop|mode\s+d[eé]velopp[eé]|r[eé]ponse\s+compl[eè]te|"
    r"explique(?:\s+en)?\s+d[eé]tail|d[eé]taille|plan\s+d[eé]taill[eé]|architecture|"
    r"roadmap|analyse\s+compl[eè]te|full\s+analysis|deep\s+dive|"
    r"en\s+profondeur|write\s+a\s+long|r[eé]fl[eé]chis\s+longuement)\b",
    re.I,
)
_BRIEF_HINT = re.compile(
    r"^(?:ok|oui|yes|non|no|merci|thanks|bien\s+re[cç]u|compris|"
    r"c['']est\s+bon|parfait|go|done|vu)\b",
    re.I,
)
_STATUS_HINT = re.compile(
    r"\b(?:o[uù]\s+on\s+en\s+est|statut|status|r[eé]sum[eé]|"
    r"en\s+deux\s+mots|rapidement|vite)\b",
    re.I,
)


class LlmDepth(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    DEVELOP = "develop"


@dataclass(frozen=True)
class LlmEconomyBudget:
    depth: LlmDepth
    max_tokens: int
    context_max_chars: int
    history_turns: int
    history_msg_chars: int
    include_context_conversations: bool
    include_context_extras: bool
    collegue_max_chars: int
    model_override: str | None
    enhance_max_tokens: int


def _founder_mode() -> bool:
    return bool(getattr(settings, "aria_operator_founder_mode", False))


def _default_depth() -> LlmDepth:
    fallback = "standard" if _founder_mode() else "brief"
    raw = (getattr(settings, "aria_llm_depth_default", None) or fallback).strip().lower()
    if _founder_mode() and raw == "brief":
        raw = "standard"
    try:
        return LlmDepth(raw)
    except ValueError:
        return LlmDepth.STANDARD if _founder_mode() else LlmDepth.BRIEF


def _chiron_mode() -> bool:
    """Chiron profile — top model even on short messages (ARIA_SPARK_AGGRESSIVE + depth develop)."""
    if not _spark_active():
        return False
    if not _spark_aggressive():
        return False
    return _default_depth() == LlmDepth.DEVELOP


def detect_depth(message: str, *, default: LlmDepth | None = None) -> LlmDepth:
    """Detects brief / standard / develop from the user's message."""
    text = (message or "").strip()
    if not text:
        return default or _default_depth()

    override = _DEPTH_OVERRIDE.search(text)
    if override:
        try:
            return LlmDepth(override.group(1).lower())
        except ValueError:
            pass

    if _founder_mode() and _default_depth() != LlmDepth.DEVELOP:
        if _DEVELOP_HINT.search(text) or len(text) > 280:
            return LlmDepth.DEVELOP
        if _BRIEF_HINT.match(text):
            return LlmDepth.BRIEF
        return default or _default_depth()

    if _chiron_mode():
        if override and override.group(1).lower() == "brief":
            return LlmDepth.BRIEF
        return LlmDepth.DEVELOP

    if _founder_mode():
        if _DEVELOP_HINT.search(text) or len(text) > 280:
            return LlmDepth.DEVELOP
        if _BRIEF_HINT.match(text):
            return LlmDepth.BRIEF
        return default or _default_depth()

    if _DEVELOP_HINT.search(text) or len(text) > 420:
        return LlmDepth.DEVELOP
    if _BRIEF_HINT.match(text) or (len(text) < 48 and not _DEVELOP_HINT.search(text)):
        return LlmDepth.BRIEF
    if _STATUS_HINT.search(text) and len(text) < 120:
        return LlmDepth.BRIEF

    return default or LlmDepth.STANDARD


def depth_system_instruction(lang: str, depth: LlmDepth) -> str:
    if depth == LlmDepth.DEVELOP:
        if lang == "fr":
            return (
                "MODE DÉVELOPPÉ : verdict en tête, puis détail factuel. "
                "Sur salutation ou message court : prends les devants — état + "
                "« si on faisait X, je pourrais Y » (initiative fondateur). "
                "Chaque section a du contenu ou est omise — jamais de puces/titres vides, "
                "jamais de scorecard ou % sans source. Pas de remplissage."
            )
        return (
            "DEVELOP MODE: lead with verdict, then factual detail. "
            "Every section has content or is omitted — never empty bullets/headings, "
            "never scorecards or percentages without sources. No filler."
        )
    if lang == "fr":
        return (
            "CONCISION : verdict + 2–5 phrases utiles (pas de coquille vide). "
            "Tu peux répondre par une question si ça clarifie (style Socrate) — "
            "jusqu'à ce que l'opérateur dise « ok vazy » ou « si c'est bénéfique tu peux ». "
            "Ne développe pas sans demande explicite."
        )
    return (
        "BE CONCISE: short, relevant reply (2–5 sentences). "
        "Expand only if the question requires it or the user explicitly asks."
    )


def resolve_budget(
    depth: LlmDepth,
    *,
    public: bool = False,
    grounded: bool = False,
    self_context: bool = False,
) -> LlmEconomyBudget:
    if grounded:
        return LlmEconomyBudget(
            depth=depth,
            max_tokens=350,
            context_max_chars=2000,
            history_turns=0,
            history_msg_chars=0,
            include_context_conversations=False,
            include_context_extras=False,
            collegue_max_chars=0,
            model_override=None,
            enhance_max_tokens=300,
        )
    if self_context:
        return LlmEconomyBudget(
            depth=depth,
            max_tokens=480 if depth != LlmDepth.BRIEF else 220,
            context_max_chars=3000,
            history_turns=4 if depth == LlmDepth.DEVELOP else 2,
            history_msg_chars=350,
            include_context_conversations=False,
            include_context_extras=False,
            collegue_max_chars=0,
            model_override=_spark_model_for_depth(depth),
            enhance_max_tokens=300,
        )

    brief_ctx = int(getattr(settings, "aria_llm_context_max_brief", 3500) or 3500)
    std_ctx = int(getattr(settings, "aria_llm_context_max_standard", 5000) or 5000)
    dev_ctx = int(getattr(settings, "aria_llm_context_max_develop", 8000) or 8000)
    brief_tok = int(getattr(settings, "aria_llm_max_tokens_brief", 180) or 180)
    std_tok = int(getattr(settings, "aria_llm_max_tokens_standard", 400) or 400)
    dev_tok = int(getattr(settings, "aria_llm_max_tokens_develop", 900) or 900)

    if depth == LlmDepth.BRIEF:
        return LlmEconomyBudget(
            depth=depth,
            max_tokens=brief_tok if not public else min(brief_tok, 220),
            context_max_chars=brief_ctx,
            history_turns=3,
            history_msg_chars=200,
            include_context_conversations=False,
            include_context_extras=False,
            collegue_max_chars=900,
            model_override=_spark_model_for_depth(depth),
            enhance_max_tokens=280,
        )
    spark_boost = _spark_aggressive() or _founder_mode()
    std_model = _spark_model_for_depth(LlmDepth.STANDARD)
    dev_model = _spark_model_for_depth(LlmDepth.DEVELOP)
    if depth == LlmDepth.STANDARD:
        return LlmEconomyBudget(
            depth=depth,
            max_tokens=(std_tok * 2 if spark_boost else std_tok) if not public else min(std_tok, 350),
            context_max_chars=std_ctx * 2 if spark_boost else std_ctx,
            history_turns=8 if spark_boost else 6,
            history_msg_chars=450 if spark_boost else 350,
            include_context_conversations=spark_boost,
            include_context_extras=spark_boost,
            collegue_max_chars=4000 if spark_boost else 2500,
            model_override=std_model,
            enhance_max_tokens=600 if spark_boost else 400,
        )
    return LlmEconomyBudget(
        depth=depth,
        max_tokens=(dev_tok * 2 if spark_boost else dev_tok) if not public else min(dev_tok, 500),
        context_max_chars=dev_ctx * 2 if spark_boost else dev_ctx,
        history_turns=14 if spark_boost else 10,
        history_msg_chars=700 if spark_boost else 500,
        include_context_conversations=True,
        include_context_extras=True,
        collegue_max_chars=0,
        model_override=dev_model,
        # Real incident (12/07): 1200/800 was truncating "enhance" replies
        # (rephrasing a skill output) mid-word at develop depth — confirmed by
        # the logs (finish_reason=length, output_tokens=1200 right at the
        # cap), independent of ARIA_LLM_MAX_TOKENS_DEVELOP (literal here,
        # never configured via an environment variable).
        enhance_max_tokens=3000 if spark_boost else 2000,
    )


def _spark_active() -> bool:
    return (settings.llm_provider or "").strip().lower() == "virtuals"


def _virtuals_catalog_default(depth: LlmDepth) -> str:
    """SSOT spark_config — the 3 default values from the Virtuals catalog, never
    valid model IDs for a third-party provider (Groq, direct DeepSeek, xAI...)."""
    from aria_core.spark_config import (
        DEFAULT_MODEL_BRIEF,
        DEFAULT_MODEL_DEVELOP,
        DEFAULT_MODEL_STANDARD,
    )

    if depth == LlmDepth.DEVELOP:
        return DEFAULT_MODEL_DEVELOP
    if depth == LlmDepth.STANDARD:
        return DEFAULT_MODEL_STANDARD
    return DEFAULT_MODEL_BRIEF


def _spark_model_for_depth(depth: LlmDepth) -> str | None:
    """Model per depth (#201, 16/07) -- read independently of the active
    provider, not just on Virtuals: as soon as the Groq fallback or a direct
    provider (DeepSeek, xAI...) becomes the active provider,
    standard/develop/brief must still be able to differ if the operator
    configured them for THIS provider.

    Guard rail: if the value read is still the Virtuals catalog default
    (never overridden for the new provider) AND Virtuals isn't active, reject
    it rather than send it as-is to a real third-party API -- an ID like
    "x-ai-grok-4-3" only exists in the Spark catalog, never on
    api.groq.com/api.deepseek.com/api.x.ai."""
    if depth == LlmDepth.DEVELOP:
        raw = (getattr(settings, "aria_llm_model_develop", None) or "").strip()
    elif depth == LlmDepth.STANDARD:
        raw = (getattr(settings, "aria_llm_model_standard", None) or "").strip()
    else:
        raw = (getattr(settings, "aria_llm_model_brief", None) or "").strip()
    if not raw:
        return None
    if not _spark_active() and raw == _virtuals_catalog_default(depth):
        return None
    return raw


def _spark_aggressive() -> bool:
    return bool(getattr(settings, "aria_spark_aggressive", False)) and _spark_active()


def provider_display_name(provider: str | None = None) -> str:
    p = (provider if provider is not None else settings.llm_provider or "cloud").strip().lower()
    if p in ("grok", "xai"):
        return "Grok/xAI"
    if p == "groq":
        return "Groq"
    if p == "virtuals":
        return "Virtuals Spark"
    if p == "ollama":
        return "Ollama"
    return p or "cloud"


def fallback_notice_line(provider: str, *, lang: str = "fr") -> str:
    """Operator-only line (#135): flags that a chat turn went through the
    fallback route (Spark down), never shown outside the operator's chat --
    not subject to the "zero AI trace" doctrine (internal surface, not client)."""
    name = provider_display_name(provider)
    if lang == "fr":
        return f"Note : réponse générée via le fallback ({name}), Spark indisponible — relire avant de t'appuyer dessus pour une décision complexe."
    return f"Note: this reply was generated via the fallback provider ({name}), Spark unavailable — double-check before relying on it for a complex decision."


def calibrated_action_label(cal_data: dict, *, lang: str = "fr") -> str:
    if cal_data.get("web_verified") or cal_data.get("web_verify"):
        return "Actu web+LLM" if lang == "fr" else "Live web+LLM"
    if cal_data.get("groq_calibrated") or cal_data.get("llm_calibrated"):
        prov = provider_display_name()
        return f"LLM calibré ({prov})" if lang == "fr" else f"Calibrated LLM ({prov})"
    return "Policy/holding (static)"


def llm_unavailable_hint(lang: str) -> str:
    prov = provider_display_name()
    if lang == "fr":
        return (
            f"LLM cloud indisponible ({prov} — quota ou billing). "
            "Je peux quand même faire les analyses déterministes (scan on-chain, TA) sans le LLM."
        )
    return (
        f"Cloud LLM unavailable ({prov} — quota or billing). "
        "I can still run deterministic analyses (on-chain scan, TA) without the LLM."
    )


def skill_output_readable(skill_output: str) -> bool:
    text = (skill_output or "").strip()
    return 0 < len(text) < 500 and "\n\n\n" not in text