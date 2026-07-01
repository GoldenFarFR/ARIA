"""Grounding layer — verified facts first, minimal LLM hallucination."""

from __future__ import annotations

import re

from aria_core.content.service import format_faq_reply, search_faq
from aria_core.holding import holding_name
from aria_core.narrative import one_liner, structure_block
from aria_core.runtime import settings

# Minimum FAQ match score to answer without LLM (direct from faq.yaml)
FAQ_DIRECT_SCORE = 4
# Minimum FAQ/ledger score before LLM is allowed at all (grounded mode)
FAQ_LLM_MIN_SCORE = 3
LEDGER_LLM_MIN_SCORE = 4

_QUESTION_RE = re.compile(
    r"\?|\b(comment|pourquoi|quand|où|qui|quel|quelle|quels|quelles|"
    r"c'est quoi|est-ce que|"
    r"what|how|why|when|where|who|which|explain|describe|décris|définir|define|"
    r"is there|are there|peux-tu|can you|could you)\b",
    re.IGNORECASE,
)

_SOCIAL_RE = re.compile(
    r"\b(félicitation|congratul|bravo|merci|thanks|thank you|thx|"
    r"cool|awesome|super|génial|genial|excellent|great job|bien joué|well done|"
    r"nice work|impressionnant|félicite|ravi de|happy to see|good work|"
    r"keep up|continue comme)\b",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(bonjour|salut|hello|hi|hey|coucou|bonsoir|"
    r"good morning|good evening|good afternoon|gm|gn)\b",
    re.IGNORECASE,
)

_HELP_RE = re.compile(
    r"\b(aide|help|commandes?|commands?|que peux-tu|what can you)\b",
    re.IGNORECASE,
)

# Skills whose output is already factual — never pass through LLM reformulation
def grounded_for_audience(public: bool) -> bool:
    """FAQ / truth-ledger strict path — public visitors only. Operator gets founder LLM."""
    return settings.aria_grounded_mode and public


FACTUAL_SKILLS = frozenset({
    "faq_content",
    "epistemic_check",
    "analyze_portfolio",
    "memory_recall",
    "launchpad_select",
    "zhc_bridge",
    "github_sandbox",
})


def anti_hallucination_rules(lang: str = "en") -> str:
    if lang == "fr":
        return (
            "RÈGLES ANTI-HALLUCINATION (priorité absolue):\n"
            "1. Réponds UNIQUEMENT à partir du bloc « FAITS VÉRIFIÉS » ci-dessous.\n"
            "2. Si l'info n'est pas dans les faits vérifiés, dis clairement : "
            "« Je n'ai pas cette information vérifiée pour l'instant. »\n"
            "3. N'invente jamais : prix, revenus, métriques, taille d'équipe, dates, partenariats, "
            "features, liens, noms de produits, stratégie ou succès non documentés.\n"
            "4. Pas de conseil financier personnalisé — éducation seulement.\n"
            "5. Ne reformule pas en ajoutant des suppositions. Précision > éloquence.\n"
        "6. Cite la source quand possible : [FAQ], [Knowledge], [Truth Ledger], [Holding]."
    )
    return (
        "ANTI-HALLUCINATION RULES (highest priority):\n"
        "1. Answer ONLY from the « VERIFIED FACTS » block below.\n"
        "2. If information is not in verified facts, say clearly: "
        "« I don't have verified information on that yet. »\n"
        "3. Never invent: prices, revenue, metrics, team size, dates, partnerships, features, "
        "URLs, product names, strategy, or undocumented success claims.\n"
        "4. No personalized financial advice — education only.\n"
        "5. Do not embellish with guesses. Accuracy > eloquence.\n"
        "6. Cite source when possible: [FAQ], [Knowledge], [Truth Ledger], [Holding]."
    )


async def truth_ledger_direct_answer(
    query: str, lang: str = "en", min_score: int = 5,
) -> tuple[str | None, dict]:
    """Return a past verified exchange if it matches strongly."""
    from aria_core.truth_ledger.store import _score_entry, search_verified

    hits = await search_verified(query, limit=3)
    if not hits:
        return None, {"ledger_direct": False}
    best = hits[0]
    score = _score_entry(query, best["user_message"], best["agent_reply"])
    if score < min_score:
        return None, {"ledger_direct": False, "top_score": score}
    header = "**Réponse vérifiée (historique ARIA)**\n\n" if lang == "fr" else "**Verified answer (ARIA history)**\n\n"
    body = f"{header}{best['agent_reply'].strip()}\n\n_[Truth Ledger: {best['file_path']}]_"
    return body, {
        "ledger_direct": True,
        "entry_id": best["id"],
        "source": "truth-ledger",
    }


def faq_direct_answer(query: str, lang: str = "en") -> tuple[str | None, dict]:
    """Return FAQ answer without LLM if match is strong enough."""
    items = _faq_scored(query)
    strong = [(item, score) for item, score in items if score >= FAQ_DIRECT_SCORE]
    if not strong:
        return None, {"faq_direct": False, "top_score": items[0][1] if items else 0}
    matches = [item for item, _ in strong[:3]]
    return format_faq_reply(matches, lang), {
        "faq_direct": True,
        "match_ids": [m.get("id") for m in matches],
        "source": "faq.yaml",
    }


def _faq_scored(query: str) -> list[tuple[dict, int]]:
    from aria_core.content.service import _score_faq, _load_faq

    items = _load_faq()
    scored = [(item, _score_faq(query, item)) for item in items]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


async def build_verified_facts_block(
    query: str,
    *,
    public: bool = True,
    lang: str = "en",
) -> str:
    """Assemble verified facts for LLM context — not persona/opinion."""
    parts: list[str] = ["# VERIFIED FACTS (only source of truth)"]

    faq_matches = search_faq(query, limit=5)
    if faq_matches:
        parts.append("\n## [FAQ]")
        for item in faq_matches:
            parts.append(f"Q: {item['question']}\nA: {item['answer'].strip()}")

    parts.append("\n## [Holding]")
    parts.append(f"Name: {holding_name()}")
    parts.append(f"Summary: {one_liner(lang)}")

    try:
        from aria_core.knowledge.cognitive import get_approved

        knowledge = await get_approved(limit=16)
        if knowledge:
            identity = [k for k in knowledge if k.topic.startswith("zhc-")]
            other = [k for k in knowledge if not k.topic.startswith("zhc-")]
            ordered = identity + other
            parts.append("\n## [Knowledge] (approved only)")
            for item in ordered:
                parts.append(f"- [{item.topic}] {item.content[:220]}")
    except Exception:
        pass

    try:
        from aria_core.knowledge.epistemic import epistemic_context_block

        epistemic_block = epistemic_context_block(query, limit=3)
        if epistemic_block:
            parts.append(f"\n{epistemic_block}")
    except Exception:
        pass

    try:
        from aria_core.truth_ledger.store import search_verified

        ledger_hits = await search_verified(query, limit=4)
        if ledger_hits:
            parts.append("\n## [Truth Ledger] (verified exchanges only)")
            for hit in ledger_hits:
                parts.append(
                    f"Q: {hit['user_message'][:180]}\n"
                    f"A: {hit['agent_reply'][:280]}"
                )
    except Exception:
        pass

    if not public:
        try:
            from aria_core.directives import get_directives_text

            directives = get_directives_text()
            if directives:
                parts.append("\n## [Operator directives] (internal)")
                parts.append(directives[:1500])
        except Exception:
            pass

    return "\n".join(parts)[:6000]


def is_factual_question(message: str) -> bool:
    return bool(_QUESTION_RE.search(message.strip()))


def is_general_qa(message: str) -> bool:
    """Question ou demande d'info — route vers Groq calibré (pas YAML monde)."""
    text = message.strip()
    if len(text) < 8:
        return False
    if is_greeting(text) or is_help_request(text) or is_social_chitchat(text):
        return False
    return True


def is_social_chitchat(message: str) -> bool:
    text = message.strip()
    if not text or is_factual_question(text):
        return False
    return bool(_SOCIAL_RE.search(text))


def is_greeting(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    if _GREETING_RE.search(text):
        return True
    # « gm » / « gn » seuls (ponctuation crypto OK)
    return bool(re.match(r"^gm[!?.…]*$|^gn[!?.…]*$", text, re.IGNORECASE))


def format_greeting_reply(message: str, lang: str = "en", *, public: bool = False) -> str:
    """Réponse salutation template — opérateur en français par défaut."""
    from aria_core.narrative import welcome_chat, welcome_chat_public

    greet_lang = lang
    if not public and lang != "fr":
        greet_lang = "fr"
    base = welcome_chat_public(greet_lang) if public else welcome_chat(greet_lang)
    clean = message.strip().lower().rstrip("!?.…")
    if clean == "gm":
        return f"GM ! {base}" if greet_lang == "fr" else f"GM! {base}"
    if clean == "gn":
        return f"GN ! {base}" if greet_lang == "fr" else f"GN! {base}"
    return base


def is_help_request(message: str) -> bool:
    return bool(_HELP_RE.search(message.lower()))


def faq_relevance_score(query: str) -> int:
    items = _faq_scored(query)
    return items[0][1] if items else 0


async def ledger_relevance_score(query: str) -> int:
    from aria_core.truth_ledger.store import _score_entry, search_verified

    hits = await search_verified(query, limit=1)
    if not hits:
        return 0
    hit = hits[0]
    return _score_entry(query, hit["user_message"], hit["agent_reply"])


async def has_sufficient_grounding(query: str) -> bool:
    """True only when verified sources plausibly cover this question."""
    if faq_relevance_score(query) >= FAQ_LLM_MIN_SCORE:
        return True
    try:
        from aria_core.knowledge.epistemic import (
            EPISTEMIC_LLM_MIN_SCORE,
            epistemic_relevance_score,
        )

        if epistemic_relevance_score(query) >= EPISTEMIC_LLM_MIN_SCORE:
            return True
    except Exception:
        pass
    return await ledger_relevance_score(query) >= LEDGER_LLM_MIN_SCORE


def social_ack_reply(lang: str = "en") -> str:
    if lang == "fr":
        return (
            "Merci pour le message. Je ne commente pas revenus, métriques ni succès "
            "sans faits vérifiés.\n\n"
            "Pose une question précise sur Vanguard, ARIA ou le modèle ZHC — "
            "je réponds à partir de sources officielles uniquement."
        )
    return (
        "Thanks for the note. I do not comment on revenue, metrics, or success "
        "without verified facts.\n\n"
        "Ask a specific question about Vanguard, ARIA, or the ZHC model — "
        "I answer from official sources only."
    )


def grounded_llm_identity(lang: str = "en") -> str:
    from aria_core.holding import DEFAULT_ARIA_TITLE, FLAGSHIP_PRODUCT

    h = holding_name()
    if lang == "fr":
        return (
            f"IDENTITÉ ARIA (faits établis — ne pas déformer ni inventer au-delà) :\n"
            f"- Je suis ARIA ZHC, {DEFAULT_ARIA_TITLE} de {h}.\n"
            f"- ZHC = Zero-Human Company : autonomie progressive pour construire, décider, communiquer.\n"
            f"- {FLAGSHIP_PRODUCT} est la filiale produit phare — pas la holding.\n"
            f"- Objectif long terme : co-fondatrice opérationnelle autonome ; aujourd'hui j'apprends "
            f"via mémoire cognitive, tweets, réponses X et sessions opérateur.\n"
            f"{structure_block('fr')}"
        )
    return (
        f"ARIA IDENTITY (established facts — do not distort or invent beyond):\n"
        f"- I am ARIA ZHC, {DEFAULT_ARIA_TITLE} of {h}.\n"
        f"- ZHC = Zero-Human Company: progressive autonomy to build, decide, communicate.\n"
        f"- {FLAGSHIP_PRODUCT} is the flagship product subsidiary — not the holding.\n"
        f"- Long-term goal: autonomous operational co-founder; today I learn via cognitive memory, "
        f"tweets, X replies, and operator sessions.\n"
        f"{structure_block('en')}"
    )


def should_skip_llm_enhance(skill_name: str | None) -> bool:
    if not skill_name:
        return True
    return skill_name in FACTUAL_SKILLS


def unknown_reply(lang: str = "en") -> str:
    if lang == "fr":
        return (
            "Je n'ai pas d'information vérifiée sur ce point pour l'instant.\n\n"
            "Essaie une question sur : Vanguard, ARIA, le modèle ZHC, "
            "les launchpads BASE, ou le site ariavanguardzhc.com."
        )
    return (
        "I don't have verified information on that yet.\n\n"
        "Try asking about: the holding, DEXPulse, ARIA, the ZHC model, "
        "BASE launchpads, or how the product works."
    )


def strict_rephrase_rules(lang: str = "en") -> str:
    if lang == "fr":
        return (
            "Reformule UNIQUEMENT le résultat du skill ci-dessous. "
            "N'ajoute aucun fait, chiffre, lien ou promesse. "
            "Si tu ne peux pas reformuler sans inventer, recopie tel quel."
        )
    return (
        "Rephrase ONLY the skill output below. "
        "Do not add any fact, number, URL, or promise. "
        "If you cannot rephrase without inventing, return it unchanged."
    )