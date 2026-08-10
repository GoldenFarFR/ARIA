"""LLM depth — brief / standard / develop (token economy)."""
from __future__ import annotations

import contextvars
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
    model_provider_override: str | None = None


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
        self_provider, self_model = anthropic_depth_override(depth)
        return LlmEconomyBudget(
            depth=depth,
            max_tokens=480 if depth != LlmDepth.BRIEF else 220,
            context_max_chars=3000,
            history_turns=4 if depth == LlmDepth.DEVELOP else 2,
            history_msg_chars=350,
            include_context_conversations=False,
            include_context_extras=False,
            collegue_max_chars=0,
            model_override=self_model,
            enhance_max_tokens=300,
            model_provider_override=self_provider,
        )

    brief_ctx = int(getattr(settings, "aria_llm_context_max_brief", 3500) or 3500)
    std_ctx = int(getattr(settings, "aria_llm_context_max_standard", 5000) or 5000)
    dev_ctx = int(getattr(settings, "aria_llm_context_max_develop", 8000) or 8000)
    brief_tok = int(getattr(settings, "aria_llm_max_tokens_brief", 180) or 180)
    std_tok = int(getattr(settings, "aria_llm_max_tokens_standard", 400) or 400)
    dev_tok = int(getattr(settings, "aria_llm_max_tokens_develop", 900) or 900)

    brief_provider, brief_model = anthropic_depth_override(LlmDepth.BRIEF)
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
            model_override=brief_model,
            enhance_max_tokens=280,
            model_provider_override=brief_provider,
        )
    spark_boost = _spark_aggressive() or _founder_mode()
    std_provider, std_model = anthropic_depth_override(LlmDepth.STANDARD)
    dev_provider, dev_model = anthropic_depth_override(LlmDepth.DEVELOP)
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
            model_provider_override=std_provider,
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
        model_provider_override=dev_provider,
    )


def _spark_active() -> bool:
    return (settings.llm_provider or "").strip().lower() == "virtuals"


# Target end-state (#118, operator decision 27/07, "supprime tous et
# reconstruit avec haiku et sonnet en sommeil tant que openrouter et grok
# sont actifs") -- replaces the old ARIA_LLM_MODEL_<DEPTH> / Virtuals-catalog
# mechanism (_virtuals_catalog_default/_spark_model_for_depth, deleted),
# confirmed broken in prod (18/07 audit): its guard rejected any
# operator-configured value that numerically matched the old Virtuals-catalog
# default, even a real, routable Anthropic model ID (ARIA_LLM_MODEL_DEVELOP was
# silently inert for that reason). This new mechanism never reads a free-form
# provider string from .env -- it hardcodes the two real target models and
# stays fully dormant (returns (None, None), zero behavior change) until the
# operator flips ARIA_LLM_ANTHROPIC_ROUTING_ENABLED on.
_ANTHROPIC_MODEL_HAIKU = "claude-haiku-4-5-20251001"  # Haiku 4.5 -- trading + brief/standard
_ANTHROPIC_MODEL_SONNET = "claude-sonnet-5"  # Sonnet 5 -- develop depth


def anthropic_routing_enabled() -> bool:
    return bool(getattr(settings, "aria_llm_anthropic_routing_enabled", False))


# 10/08 -- kill switch for every LLM call reachable from the public site
# widget (/aria/chat), operator request after a real token-waste incident
# (the "grounded" branch of resolve_budget below silently bypassed the
# Anthropic routing gate and burned Grok/Groq calls at an 85% failure rate).
# Rather than gate each public-reachable chat_with_context call site
# individually (brain.py + knowledge/web_verify.py, several of them --
# missing just one would keep leaking tokens), this is enforced at the one
# choke point every path funnels through: llm.chat_with_context reads
# ``is_public_llm_disabled_now()`` and returns None immediately, before any
# network call. A contextvar (not a plain module global) so it's scoped to
# the current request/task only -- concurrent operator-chat or heartbeat
# calls in other tasks are never affected, and nothing leaks across requests.
_public_llm_disabled: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "public_llm_disabled", default=False,
)


def vitrine_llm_enabled() -> bool:
    return bool(getattr(settings, "aria_vitrine_llm_enabled", False))


def set_public_llm_context(public: bool) -> None:
    """Call once per request, as early as possible (brain.py._process_inner),
    with the REAL per-message public/operator flag -- never a deployment-wide
    default. Every chat_with_context call in this task (and anything it
    awaits) will short-circuit to None until the request ends, unless the
    operator has explicitly re-enabled vitrine LLM via ARIA_VITRINE_LLM_ENABLED."""
    _public_llm_disabled.set(public and not vitrine_llm_enabled())


def is_public_llm_disabled_now() -> bool:
    return _public_llm_disabled.get()


# 02/08 -- separate gate for the trading role specifically (momentum_entry.py's
# 3 entry-gate call sites), found necessary by an LLM architecture review
# workflow: the original single flag above was TOTAL -- flipping it would move
# conversation/VC/smart_money AND the trading gates all at once, with no way
# to sequence "non-trading first, observe, trading later" as the operator's
# own progressive-rollout doctrine (cadence d'observation accélérée) requires.
# Deliberately a SEPARATE bool, not a derived one -- so trading can stay OFF
# while the general flag is already ON (the actual planned sequence), and so
# a future flip of ONE never silently drags the other along.
def anthropic_routing_trading_enabled() -> bool:
    return bool(getattr(settings, "aria_llm_anthropic_routing_trading_enabled", False))


def anthropic_depth_override(
    depth: LlmDepth, *, trading: bool = False,
) -> tuple[str | None, str | None]:
    """(provider, model) override for this depth. Dormant by default -- see
    the module comment above for the rationale and the incident this replaces.

    ``trading=True`` (momentum_entry.py's 3 entry-gate call sites only) checks
    the SEPARATE ``anthropic_routing_trading_enabled`` gate instead of the
    general one -- see that function's docstring. Every other caller
    (conversation, /vc, smart_money, source_code_audit) keeps using the
    general gate, unaffected by this parameter's default."""
    enabled = anthropic_routing_trading_enabled() if trading else anthropic_routing_enabled()
    if not enabled:
        return (None, None)
    if depth == LlmDepth.DEVELOP:
        return ("anthropic", _ANTHROPIC_MODEL_SONNET)
    return ("anthropic", _ANTHROPIC_MODEL_HAIKU)


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
    if p == "anthropic":
        return "Anthropic"
    if p == "openrouter":
        return "OpenRouter"
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