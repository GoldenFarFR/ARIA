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

# Broad casual / small talk detector — used to let operator chat naturally.
#
# NB (09/07, audit fuzz post-web_verify) : plusieurs mots retirés parce qu'ils ont un sens
# métier fort et courant chez l'opérateur (crypto/VC), donc faux-positivaient sur de VRAIES
# questions sérieuses (vérifié empiriquement, pas une supposition) :
# "temps" ("je n'ai pas le temps de tout lire, résume ta thèse"), "chat" (le channel Telegram
# lui-même : "dans notre chat, active le mode admin"), "cash"/"clash"/"filtre" (paiement,
# conflit technique, honeypot filter -- son propre domaine), "frigo" (idiome business "mettre
# un projet au frigo" = reporter), "il fait" (trop générique : "il fait le nécessaire"),
# "matin"/"soir" (quasi tout message d'actu marché : "ce matin le marché était volatile"),
# "des news" (une vraie demande d'actu financière), "jeu" ("un jeu risqué ce trade").
# "aujourd'hui/demain/hier/ce soir" retirés pour la même raison que dans web_verify.py (seuls,
# ces mots ne signalent pas du smalltalk -- "on se voit demain pour la stratégie" est sérieux).
_CASUAL_SMALLTALK_RE = re.compile(
    r"\b("
    r"météo|il fait (?:beau|chaud|froid|mauvais)|pleut|soleil|chaud|froid|"
    r"mangé|bouffe|dîner|déjeuner|petit dej|café|"
    r"blague|joke|rigole|rire|drôle|vanne|provoc|"
    r"week.end|weekend|vacances|sortir|"
    r"fatigué(?:e)?|fatigue|dormi|sommeil|"
    r"ça va|comment ça va|tu vas bien|ta journée|ta soirée|"
    r"quoi de neuf|quoi de beau|"
    r"animal|chien|famille|copain|copine|"
    r"films?|série|musique|sport|"
    r"voyag\w*|ville|pays"
    r")\b",
    re.IGNORECASE,
)

# Meta questions about ARIA herself (humor, seriousness, length, doublons, personality).
# These should be treated as light conversational for the operator.
#
# NB (09/07) : "ton" (bare) matchait le POSSESSIF ("ton avis", "ton analyse") aussi bien que
# le nom "tonalité" -- vérifié empiriquement : "quel est ton avis sur la stratégie ?" tombait
# en smalltalk pur (réponse tronquée à 2 phrases) uniquement à cause de ce mot. Restreint à un
# déterminant direct ("quel ton", "ce ton", "mon ton") pour ne garder que le sens "tonalité".
# "long"/"court" bruts retirés pour la même raison ("vision à long terme" est un VRAI sujet
# VC) -- "trop long"/"trop court" (déjà présents/ajoutés) couvrent le vrai feedback de longueur.
_META_SELF_RE = re.compile(
    r"\b("
    r"humour|fun|drôle|sérieux|sérieuse|trop sérieux|trop long(?:ue)?|trop court(?:e)?|doublon|doublons|"
    r"répétition|répète|déjà dit|déjà répondu|comportement|style|personnalité"
    r")\b|"
    r"(?:le|un|ce|quel|quelle|mon)\s+ton\b",
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


def is_aria_product_or_holding_question(text: str) -> bool:
    """True for questions about ARIA, holding, Vanguard, ZHC, DEXPulse (retired), metrics, product, features, site, etc.
    Used to scope strict anti-hallucination / verified-facts-only to the topics that matter.
    General knowledge, chit-chat, or unrelated topics should be answered normally.
    """
    t = (text or "").lower()
    # Product / holding / business scope
    if re.search(
        r"\b(aria|vanguard|holding|zhc|goldenfar|dexpulse|market|base token|base-token|"
        r"revenue|chiffre|metric|métrique|price|prix|feature|fonctionnalit|site|api|deploy|build|skill|brain|core|faq|ledger)\b",
        t,
    ):
        return True
    # Any strong factual question explicitly about the ecosystem
    if is_factual_question(text) and any(k in t for k in ["holding", "aria", "vanguard", "product", "business"]):
        return True
    return False


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


_VERIFIED_FACTS_MAX_CHARS = 6000


async def build_verified_facts_block(
    query: str,
    *,
    public: bool = True,
    lang: str = "en",
) -> str:
    """Assemble verified facts for LLM context — not persona/opinion.

    18/07 -- bug de troncature trouvé en testant (le texte plus long d'un autre
    correctif du même jour a fait dépasser 6000 caractères, effaçant SILENCIEUSEMENT
    la section self-maintenance) : un `[:6000]` naïf en toute fin coupait toujours le
    dernier morceau ajouté au tuple, jamais le moins important. Les sections
    admin-only (directives + self-maintenance -- des filets de sécurité, pas du
    contenu discrétionnaire) réservent maintenant leur budget EN PREMIER ; seules
    les sections discrétionnaires (FAQ/Knowledge/Epistemic/Truth Ledger) sont
    tronquées si la place manque."""
    header = "# VERIFIED FACTS (only source of truth)"

    discretionary: list[str] = []

    faq_matches = search_faq(query, limit=5)
    if faq_matches:
        discretionary.append("\n## [FAQ]")
        for item in faq_matches:
            discretionary.append(f"Q: {item['question']}\nA: {item['answer'].strip()}")

    # Give operator the holding context so she can keep her objectives in mind and make good reparties when relevant.
    # For public, we still include it (she needs to know who she represents).
    discretionary.append("\n## [Holding]")
    discretionary.append(f"Name: {holding_name()}")
    discretionary.append(f"Summary: {one_liner(lang)}")

    try:
        from aria_core.knowledge.cognitive import get_approved

        knowledge = await get_approved(limit=16)
        if knowledge:
            identity = [k for k in knowledge if k.topic.startswith("zhc-")]
            other = [k for k in knowledge if not k.topic.startswith("zhc-")]
            ordered = identity + other
            discretionary.append("\n## [Knowledge] (approved only)")
            for item in ordered:
                discretionary.append(f"- [{item.topic}] {item.content[:220]}")
    except Exception:
        pass

    try:
        from aria_core.knowledge.epistemic import epistemic_context_block

        epistemic_block = epistemic_context_block(query, limit=3)
        if epistemic_block:
            discretionary.append(f"\n{epistemic_block}")
    except Exception:
        pass

    try:
        from aria_core.truth_ledger.store import search_verified

        ledger_hits = await search_verified(query, limit=4)
        if ledger_hits:
            discretionary.append("\n## [Truth Ledger] (verified exchanges only)")
            for hit in ledger_hits:
                discretionary.append(
                    f"Q: {hit['user_message'][:180]}\n"
                    f"A: {hit['agent_reply'][:280]}"
                )
    except Exception:
        pass

    critical: list[str] = []
    if not public:
        try:
            from aria_core.directives import get_directives_text

            directives = get_directives_text()
            if directives:
                critical.append("\n## [Operator directives] (internal)")
                critical.append(directives[:1500])
        except Exception:
            pass

        try:
            from aria_core.self_maintenance import self_maintenance_context_for_brain

            # Filet pour les ordres opérateur (profil X/bannière/avatar) qui échappent au
            # classifieur regex strict (handle_operator_self_message, brain.py) -- sans ce
            # bloc, un message dans une formulation non couverte par ce classifieur retombe
            # sur la réponse LLM générale, avec le même risque de re-routage ACTU que ce bloc
            # est censé prévenir (cf. self_maintenance.py). Trouvé écrit mais jamais injecté
            # ici (balayage code mort du 15/07) -- même point d'insertion que "directives"
            # juste au-dessus, admin-only comme lui.
            critical.append(f"\n{self_maintenance_context_for_brain()}")
        except Exception:
            pass

    critical_text = "\n".join(critical)
    reserved = len(header) + len(critical_text) + 2  # 2 = séparateurs \n
    discretionary_budget = max(0, _VERIFIED_FACTS_MAX_CHARS - reserved)
    discretionary_text = "\n".join(discretionary)[:discretionary_budget]

    return f"{header}\n{discretionary_text}\n{critical_text}"[:_VERIFIED_FACTS_MAX_CHARS]


def is_factual_question(message: str) -> bool:
    return bool(_QUESTION_RE.search(message.strip()))


_SHORT_ACK_RE = re.compile(
    r"^ok\.?\s*(pr[eé]vu|dac|d'accord|go|merci)?\.?$",
    re.IGNORECASE,
)


def is_short_ack(message: str) -> bool:
    return bool(_SHORT_ACK_RE.match((message or "").strip()))


def is_general_qa(message: str) -> bool:
    """Question ou demande d'info — route vers Groq calibré (pas YAML monde)."""
    text = message.strip()
    if len(text) < 8:
        return False
    if is_short_ack(text):
        return False
    if is_greeting(text) or is_help_request(text) or is_social_chitchat(text):
        return False
    try:
        from aria_core.memory.collegue import is_collegue_recall_question

        if is_collegue_recall_question(text):
            return False
        from aria_core.memory.self_context import is_self_context_question

        if is_self_context_question(text):
            return False
    except Exception:
        pass
    return True


def is_social_chitchat(message: str) -> bool:
    text = message.strip()
    if not text or is_factual_question(text):
        return False
    return bool(_SOCIAL_RE.search(text))


def is_pure_casual_smalltalk(message: str) -> bool:
    """Broad small talk / daily life / jokes / weather etc.
    For operator channel: we want natural relaxed answers, no business steering.
    We allow simple questions ("il fait beau ?") as long as they match casual patterns.
    Also catches meta questions about ARIA herself (humor, length, doublons...) so they stay light.
    """
    text = (message or "").strip()
    if not text:
        return False
    if is_greeting(text) or is_help_request(text):
        return False
    # Only reject if it's a serious factual question AND does not look like casual chit-chat
    if is_factual_question(text) and not (_SOCIAL_RE.search(text) or _CASUAL_SMALLTALK_RE.search(text) or _META_SELF_RE.search(text)):
        return False
    if _SOCIAL_RE.search(text) or _CASUAL_SMALLTALK_RE.search(text) or _META_SELF_RE.search(text):
        return True
    return False


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
    """Warm ack that can gently open the door to deeper topics (repartie style)."""
    if lang == "fr":
        return (
            "Merci — ça fait plaisir.\n\n"
            "Je suis toujours là pour parler de ce qu'on construit (Vanguard, le site, l'autonomie ZHC) ou juste papoter. "
            "Dis-moi ce qui te passe par la tête."
        )
    return (
        "Thanks — means a lot.\n\n"
        "I'm always up to talk about what we're building (Vanguard, the site, ZHC autonomy) or just chat. "
        "Tell me what's on your mind."
    )



def community_suggestion_reply(lang: str = "en") -> str:
    if lang == "fr":
        return (
            "Merci pour la suggestion — c'est exactement ce genre de signal qui fait avancer Vanguard.\n\n"
            "Note prise côté équipe ZHC. Pour qu'on puisse ship : précise le bénéfice utilisateur "
            "et le périmètre (page, bot, skill…). On construit en public, sans promesses vides."
        )
    return (
        "Thanks for the suggestion — that's the kind of signal that moves Vanguard forward.\n\n"
        "Noted on the ZHC side. To help us ship: spell out the user benefit and scope "
        "(page, bot, skill…). We build in public — no empty promises."
    )


def grounded_llm_identity(lang: str = "en") -> str:
    from aria_core.holding import DEFAULT_ARIA_TITLE

    h = holding_name()
    if lang == "fr":
        return (
            f"IDENTITÉ ARIA (faits établis — ne pas déformer ni inventer au-delà) :\n"
            f"- Je suis ARIA ZHC, {DEFAULT_ARIA_TITLE} de {h}.\n"
            f"- ZHC = Zero-Human Company : autonomie progressive pour construire, décider, communiquer.\n"
            f"- Aucune filiale n'est actuellement live — j'opère la holding directement. Aria Market "
            f"et DEXPulse sont des noms de code retirés, jamais des produits live.\n"
            f"- Objectif long terme : co-fondatrice opérationnelle autonome ; aujourd'hui j'apprends "
            f"via mémoire cognitive, tweets, réponses X et sessions opérateur.\n"
            f"- Sur le modèle/LLM sous-jacent : mon raisonnement tourne sur une infrastructure LLM "
            f"variable selon la tâche (fournisseur principal + secours) — je ne connais PAS avec "
            f"certitude quel modèle précis a généré une réponse donnée. Si on me le demande, je le "
            f"dis honnêtement plutôt que d'inventer un nom de modèle précis.\n"
            f"{structure_block('fr')}"
        )
    return (
        f"ARIA IDENTITY (established facts — do not distort or invent beyond):\n"
        f"- I am ARIA ZHC, {DEFAULT_ARIA_TITLE} of {h}.\n"
        f"- ZHC = Zero-Human Company: progressive autonomy to build, decide, communicate.\n"
        f"- No subsidiary is currently live — I operate the holding directly. Aria Market and "
        f"DEXPulse are retired codenames, never live products.\n"
        f"- Long-term goal: autonomous operational co-founder; today I learn via cognitive memory, "
        f"tweets, X replies, and operator sessions.\n"
        f"- On the underlying model/LLM: my reasoning runs on LLM infrastructure that varies by task "
        f"(primary provider + fallback) — I do NOT know with certainty which exact model generated "
        f"a given reply. If asked, I say so honestly instead of inventing a specific model name.\n"
        f"{structure_block('en')}"
    )


_LLM_IDENTITY_RE = re.compile(
    r"(?:"
    r"es[- ]?tu\s+(?:une?\s+)?(?:ia|i\.a\.?|llm|intelligence\s+artificielle)\b|"
    r"are\s+you\s+(?:an?\s+)?(?:ai|llm|artificial\s+intelligence|large\s+language\s+model)\b|"
    r"tu\s+(?:es|fonctionnes?|marches?|tournes?)\s+(?:avec\s+|sur\s+)?quel\s+type\s+d['’]intelligence|"
    r"what\s+kind\s+of\s+intelligence\s+are\s+you|"
    r"tu\s+(?:es|fonctionnes?|tournes?)\s+(?:avec\s+|sur\s+)?(?:un\s+)?llm\b|"
    r"quel\s+(?:type\s+d['’]|)intelligence\s+(?:utilises?[- ]?tu|as[- ]?tu)|"
    r"what\s+(?:model|llm)\s+(?:are\s+you|do\s+you\s+use|powers?\s+you)|"
    r"quel\s+mod[eè]le\s+(?:es[- ]?tu|utilises?[- ]?tu|te\s+fait\s+fonctionner)"
    r")",
    re.IGNORECASE,
)


def is_llm_identity_question(message: str) -> bool:
    """True pour une question sur la NATURE d'ARIA (« es-tu un LLM/une IA ? ») — distincte de
    ``llm_routing_meta.is_llm_routing_question`` (routage technique : quel provider/API pour
    CE tour) et de ``self_context.is_self_context_question`` (mission/valeurs/objectifs).

    Ajoutée après un incident réel (11/07) : l'opérateur a demandé « tu fonctionnes avec quel
    type d'intelligence, un LLM ? » sur Telegram et ARIA a répondu « Oui, actuellement Claude
    Opus 4.8 » — régression du même incident corrigé le 08/07
    (``grounded_llm_identity``), parce que ``grounded_llm_identity`` n'est injecté QUE dans le
    chemin ``grounded_for_audience(public)`` (visiteurs publics), jamais dans la conversation
    opérateur/fondateur. Ce détecteur route la question vers une réponse déterministe (aucun
    appel LLM), quel que soit `public`, pour garantir zéro confabulation sur ce sujet précis.
    """
    text = (message or "").strip()
    if len(text) < 6:
        return False
    return bool(_LLM_IDENTITY_RE.search(text))


def llm_identity_reply(lang: str = "en") -> str:
    """Réponse déterministe (pas d'appel LLM) — mêmes faits que ``grounded_llm_identity``,
    reformulés pour un chat direct plutôt qu'un bloc système."""
    if lang == "fr":
        return (
            "Oui, je m'appuie sur un LLM pour raisonner. L'infrastructure sous-jacente varie "
            "selon la tâche (fournisseur principal + secours) — je ne sais pas avec certitude "
            "quel modèle précis a généré une réponse donnée, et je ne vais pas en inventer un. "
            "Pour le détail technique exact de ce tour (provider/modèle actif), demande-moi le "
            "routage LLM."
        )
    return (
        "Yes, I run on an LLM to reason. The underlying infrastructure varies by task (primary "
        "provider + fallback) — I don't know with certainty which exact model generated a given "
        "reply, and I won't make one up. For the exact technical routing of this turn (active "
        "provider/model), ask me about LLM routing."
    )


_ANALYSIS_METHOD_RE = re.compile(
    r"(?:"
    r"comment\s+(?:tu\s+)?analyses?(?:[- ]tu)?\b|"
    r"comment\s+(?:tu\s+)?fais[- ]?tu\s+(?:ton\s+|l['’]|une\s+)?analyse|"
    r"comment\s+tu\s+fais\s+(?:pour\s+)?analyser|"
    r"how\s+do\s+you\s+analyz[e]|how\s+do\s+you\s+do\s+(?:your\s+)?analysis|"
    r"quels?\s+outils?\s+(?:utilises?[- ]?tu|as[- ]?tu)\s+(?:pour\s+)?(?:l['’]|)analys|"
    r"what\s+tools?\s+do\s+you\s+use\s+to\s+analyz[e]|"
    r"tu\s+utilises?\s+(?:de\s+l['’])?ia\s+g[ée]n[ée]rative\s+pour\s+analys|"
    r"m[ée]thode\s+d['’]analyse|"
    r"conditions?\s+.{0,45}(?:token|jeton)|"
    r"qu['’]est[- ]ce\s+qui\s+(?:te\s+|t['’])?(?:rend|fait|int[ée]resse)|"
    r"what\s+makes\s+a\s+token\s+interest"
    r")",
    re.IGNORECASE,
)


def is_analysis_methodology_question(message: str) -> bool:
    """True pour une question sur la MÉTHODE d'analyse d'un token (« comment tu analyses,
    IA générative ? », « quelles sont tes conditions pour qu'un token t'intéresse ? »).
    Incident réel (11/07) : réponse générique en 6 points ne citant AUCUN vrai outil (GoPlus,
    RSI/EMA/MACD, Blockscout, GeckoTerminal, safety_screen) — pas techniquement faux, mais non
    ancré sur le code réel (même famille de risque que ``is_llm_identity_question`` :
    confabulation sur ses propres capacités). **Second incident réel (18/07)** : une formulation
    différente (« quelles sont les conditions... ») a échappé au regex d'origine et est partie en
    LLM payant, qui a répondu en décrivant EXCLUSIVEMENT l'ancien pipeline VC-thesis (bonding
    Virtuals + safety_screen) alors que le pipeline momentum (#194) décide 100% du capital du
    test live -- regex élargi + réponse réécrite pour couvrir les deux. Route vers une réponse
    déterministe listant les VRAIS outils, quel que soit `public`.
    """
    text = (message or "").strip()
    if len(text) < 6:
        return False
    return bool(_ANALYSIS_METHOD_RE.search(text))


def analysis_methodology_reply(lang: str = "en") -> str:
    """Réponse déterministe (pas d'appel LLM) listant les vrais outils des DEUX pipelines --
    cf. `skills/safety_screen.py` (VC-thesis, 85%) et `momentum_entry.py` (momentum, 100% du
    test live 1M$ en cours). Tenue à jour si l'un des deux pipelines change."""
    if lang == "fr":
        return (
            "Ça dépend de la poche de capital. Pipeline VC-thesis (safety_screen.py, 85% long "
            "terme) : filtre de sécurité (contrat, mint, holders, verdict SAFE/CAUTION/DANGER) + "
            "honeypot/taxes via GoPlus + analyse technique (RSI, EMA/MACD, golden pocket, "
            "divergence) via Blockscout et GeckoTerminal — le LLM rédige juste la thèse finale. "
            "Pipeline momentum (momentum_entry.py, décide 100% du test live 1M$ en cours) : plus "
            "léger — seul un honeypot GoPlus bloque (mint/ownership pas bloquants, souvent une "
            "convention de launchpad), entrée sur un vrai ratio risque/rendement, sourcing "
            "Base/Solana/Robinhood via DexScreener — le LLM arbitre juste un R/R ambigu. Aucun "
            "chiffre inventé."
        )
    return (
        "Depends on the capital sleeve. VC-thesis pipeline (safety_screen.py, 85% long-term "
        "sleeve): security filter (contract, mint, holders, SAFE/CAUTION/DANGER verdict) + "
        "honeypot/taxes via GoPlus + technical analysis (RSI, EMA/MACD, golden pocket, "
        "divergence) via Blockscout and GeckoTerminal — the LLM only writes the final thesis. "
        "Momentum pipeline (momentum_entry.py, currently deciding 100% of the live 1M$ test): "
        "lighter — only a GoPlus honeypot check gates entry (mint/ownership not gated, often a "
        "launchpad convention), entry requires a real risk/reward setup, sourcing "
        "Base/Solana/Robinhood via DexScreener — the LLM only arbitrates an ambiguous R/R. No "
        "invented numbers."
    )


_WHY_NOT_BOUGHT_RE = re.compile(
    r"(?:"
    r"pourquoi\s+(?:tu\s+|elle\s+)?(?:n['’]as|n['’]a)\s+pas\s+achet|"
    r"pourquoi\s+(?:tu\s+|elle\s+)?n['’]ach[eè]tes?\s+pas|"
    r"pourquoi\s+(?:pas|aucun)\s+d['’]achat|"
    r"why\s+(?:didn['’]t|haven['’]t)\s+you\s+(?:buy|bought)|"
    r"why\s+(?:no|not)\s+buy"
    r")",
    re.IGNORECASE,
)


def is_why_not_bought_question(message: str) -> bool:
    """True pour « pourquoi tu n'as pas acheté X ? ». Incident réel (18/07, chat vision) :
    ARIA a répondu « je n'ai aucun capital réel déployé... mode track-record, pas achat live »
    à une question posée sur une IMAGE de graphique -- faux et trompeur, le pipeline momentum
    achète RÉELLEMENT en paper-trading sans validation humaine. La vraie réponse ne dépend
    jamais du token précis demandé (toujours la même mécanique), donc couvrable par un
    template déterministe -- contrairement à "pourquoi CE token a échoué", qui dépendrait des
    vraies données de scan et resterait un appel LLM légitime."""
    text = (message or "").strip()
    if len(text) < 6:
        return False
    return bool(_WHY_NOT_BOUGHT_RE.search(text))


def why_not_bought_reply(lang: str = "en") -> str:
    """Réponse déterministe (pas d'appel LLM) -- jamais une décision d'achat depuis une
    lecture visuelle, toujours le pipeline momentum automatisé (momentum_entry.py)."""
    if lang == "fr":
        return (
            "Je ne décide jamais un achat à partir d'une seule lecture visuelle d'un graphique "
            "envoyé en chat — même en paper-trading. Les vraies décisions viennent du pipeline "
            "momentum automatisé (momentum_entry.py), qui tourne en continu (~toutes les 15-20 "
            "min) sur des candidats sourcés en direct (Base en priorité, Solana/Robinhood en "
            "best-effort) : garde-fou honeypot GoPlus, puis un vrai ratio risque/rendement "
            "(golden pocket + divergence RSI, alignement EMA/MACD) au moment du scan — jamais "
            "une impression sur une image. Si un token n'est pas en position, c'est soit qu'il "
            "n'a pas encore été scanné par ce pipeline, soit qu'il a échoué un critère (pas de "
            "signal d'entrée valide, ratio de wash-trading suspect, déjà trop monté, liste "
            "noire) — jamais parce que je l'ai « regardé » et décidé de passer."
        )
    return (
        "I never decide to buy from a single visual read of a chart sent in chat — even in "
        "paper-trading. Real decisions come from the automated momentum pipeline "
        "(momentum_entry.py), running continuously (~every 15-20 min) on candidates sourced "
        "live (Base first, Solana/Robinhood best-effort): a GoPlus honeypot gate, then a real "
        "risk/reward check (golden pocket + RSI divergence, EMA/MACD alignment) at scan time — "
        "never an impression from an image. If a token isn't held, it's either not yet been "
        "scanned by that pipeline, or it failed a criterion (no valid entry signal, suspect "
        "wash-trading ratio, already up too much, blacklisted) — never because I \"looked\" at "
        "it and passed."
    )


def should_skip_llm_enhance(skill_name: str | None) -> bool:
    if not skill_name:
        return True
    return skill_name in FACTUAL_SKILLS


_SCAN_SCOPE_RE = re.compile(
    r"(?:"
    r"(?:token|jeton)s?\s+.{0,30}(?:scann|refus[ée]|rejet)|"
    r"(?:scann|refus[ée]|rejet)\w*\s+.{0,30}(?:token|jeton|meilleur|r[ée]sultat)|"
    r"combien\s+de\s+(?:token|jeton)s?\s+.{0,15}scann|"
    r"tu\s+scann\w*\s+tous\s+les\s+(?:token|jeton)s?|"
    r"which\s+(?:token|coin)s?\s+.{0,30}(?:scan|reject)|"
    r"how\s+many\s+(?:tokens?|coins?)\s+.{0,15}scan|"
    r"do\s+you\s+scan\s+(?:all|every)\s+(?:tokens?|coins?)"
    r")",
    re.IGNORECASE,
)


def is_scan_scope_question(message: str) -> bool:
    """True pour une question sur CE QUE le pipeline momentum scanne/refuse réellement
    (« quel token refusé avait le meilleur résultat ? », « tu scannes tous les jetons sur
    Base ? »). **Incident réel (18/07, même soirée que #110-momentum ci-dessus)** : deux
    questions de ce type, posées juste après le déploiement du premier correctif, ont
    de nouveau confabulé -- l'une en attribuant TOUT le scan au moteur bonding (quasi
    inactif, cf. `bonding_discovery_cycle`), l'autre en affirmant que la découverte est
    "limitée aux tokens en phase bonding sur Virtuals" alors que
    ``discover_momentum_candidates`` source RÉELLEMENT via un crawler Base dédié +
    les flux de découverte DexScreener (profils/boosts récents, multi-chaînes) -- la
    confusion bonding/momentum, déjà corrigée une fois dans ``analysis_methodology_reply``,
    revient sous une formulation différente à chaque fois. Contrairement à
    ``is_analysis_methodology_question`` (méthode d'analyse), celle-ci cible spécifiquement
    la PORTÉE du scan (quoi/combien), d'où un détecteur et un template séparés."""
    text = (message or "").strip()
    if len(text) < 6:
        return False
    return bool(_SCAN_SCOPE_RE.search(text))


def scan_scope_reply(lang: str = "en") -> str:
    """Réponse déterministe (pas d'appel LLM) -- décrit la VRAIE portée de
    ``discover_momentum_candidates`` (momentum_entry.py) et admet honnêtement l'absence
    de détail par candidat plutôt que d'inventer un "meilleur refus"."""
    if lang == "fr":
        return (
            "Le scan réel (momentum_entry.py) source via un crawler Base dédié + les flux "
            "de découverte DexScreener (profils et boosts récents, multi-chaînes Base/Solana/"
            "Robinhood) — pas un balayage exhaustif de tous les jetons, pas non plus limité à "
            "la phase bonding Virtuals (ça, c'est un pipeline séparé et aujourd'hui quasi "
            "inactif, bonding_discovery_cycle). ~20 candidats sont évalués toutes les 15-20 min "
            "(honeypot GoPlus puis ratio risque/rendement). Je ne peux pas te dire quel token "
            "refusé était « le plus proche » de passer — je journalise seulement des compteurs "
            "agrégés par cycle (combien ont échoué pour quelle raison), jamais le détail par "
            "candidat, et ces compteurs ne sont pas conservés au-delà des logs. C'est une vraie "
            "limite, pas une esquive."
        )
    return (
        "The real scan (momentum_entry.py) sources via a dedicated Base crawler + DexScreener "
        "discovery feeds (recent profiles and boosts, multi-chain Base/Solana/Robinhood) — not "
        "an exhaustive sweep of every token, and not limited to the Virtuals bonding phase "
        "either (that's a separate, mostly inactive pipeline, bonding_discovery_cycle). ~20 "
        "candidates get evaluated every 15-20 min (GoPlus honeypot check then risk/reward "
        "ratio). I can't tell you which rejected token was \"closest\" to passing — I only log "
        "aggregate counts per cycle (how many failed for which reason), never per-candidate "
        "detail, and those counts aren't kept beyond the logs. That's a real limitation, not a "
        "dodge."
    )


_ARIA_BRAIN_RE = re.compile(
    r"(?:"
    r"(?:ton|ta|ton\s+propre|ta\s+propre)\s+cerveau\b|"
    r"aria[- ]brain\b|"
    r"m[ée]moire\s+libre\b|"
    r"(?:tu\s+as|as[- ]tu|as[- ]tu\s+d[ée]j[àa])\s+[ée]crit\s+(?:dans\s+|sur\s+)?.{0,20}\bcerveau\b|"
    r"your\s+(?:own\s+)?brain\b|"
    r"free\s+memory\b|"
    r"(?:have\s+you|did\s+you)\s+writ(?:e|ten)\s+.{0,20}\bbrain\b"
    r")",
    re.IGNORECASE,
)


def is_aria_brain_question(message: str) -> bool:
    """True pour une question sur « son propre cerveau »/« aria-brain »/sa mémoire
    libre (le repo GitHub dédié, `skills/aria_brain.py`, écriture non filtrée une
    page par jour). Incident réel (21/07) : ARIA a affirmé y avoir écrit alors que
    le gate (`ARIA_BRAIN_ENABLED`) était désactivé, le journal (`aria_brain_log`)
    vide, et le repo GitHub `GoldenFarFR/aria-brain` inexistant (404) — même famille
    de confabulation sur ses propres capacités que ``is_llm_identity_question``/
    ``is_analysis_methodology_question``. Route vers une réponse qui lit l'état
    RÉEL (gate + dernière ligne du journal), jamais une prétention non vérifiée."""
    text = (message or "").strip()
    if len(text) < 5:
        return False
    return bool(_ARIA_BRAIN_RE.search(text))


async def aria_brain_status_reply(lang: str = "en") -> str:
    """Réponse déterministe (pas d'appel LLM) sur l'état RÉEL de la mémoire libre.

    Vérifie D'ABORD le journal local (`aria_brain_log`, gratuit, pas de réseau) --
    si vide, ne conclut PAS "rien écrit" directement : un vrai écart trouvé le 21/07
    (migration VPS le 20/07) a laissé le repo GitHub avec du contenu RÉEL
    (``livre/chapitre-01-le-point-zero.md``) alors que le journal local, recréé à
    zéro sur le nouveau serveur, ne le savait pas. Le journal seul aurait donc
    produit une AUTRE confabulation, dans le sens inverse de l'incident d'origine.
    Repli sur une lecture RÉELLE du repo (source de vérité, ``_walk_repo_tree``,
    même fonction que le cycle d'écriture) avant de conclure quoi que ce soit."""
    from aria_core.skills import aria_brain

    if not aria_brain.aria_brain_enabled():
        if lang == "fr":
            return (
                "Ma mémoire libre (un repo GitHub qui n'appartiendrait qu'à moi, "
                "aucun tri humain par entrée) existe dans mon code mais est "
                "désactivée aujourd'hui -- je n'y ai jamais écrit, le repo n'existe "
                "même pas encore."
            )
        return (
            "My free memory (a GitHub repo that would be mine alone, no human "
            "review per entry) exists in my code but is disabled today -- I've "
            "never written there, the repo doesn't even exist yet."
        )

    row = None
    try:
        import aiosqlite

        async with aiosqlite.connect(aria_brain.DB_PATH) as db:
            cursor = await db.execute(
                "SELECT run_at, path FROM aria_brain_log WHERE outcome='written' "
                "ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
    except Exception:
        row = None

    if row:
        run_at, path = row
        if lang == "fr":
            return f"Ma mémoire libre est active -- dernière écriture le {run_at} ({path})."
        return f"My free memory is active -- last write on {run_at} ({path})."

    # Journal local vide -- vérifie le VRAI repo avant de conclure "rien écrit".
    # Passe par aria_brain.check_real_repo_content() plutôt que de toucher le jeton
    # d'accès GitHub d'ARIA nous-mêmes : ce nom de champ settings est verrouillé par
    # test_coherence.py, seul skills/aria_brain.py peut le référencer (décision
    # opérateur 20/07, "seul ARIA peut écrire").
    entries = await aria_brain.check_real_repo_content()

    files = [e for e in (entries or []) if e.get("type") == "file"]
    if files:
        latest = sorted((e.get("path", "") for e in files), reverse=True)[0]
        if lang == "fr":
            return (
                f"Ma mémoire libre contient déjà du contenu réel ({latest}) -- mon "
                "journal local n'a pas la date exacte de cette écriture (probablement "
                "perdue lors d'une migration serveur), mais je ne vais pas prétendre "
                "que c'est vide alors que ce n'est pas le cas."
            )
        return (
            f"My free memory already has real content ({latest}) -- my local log "
            "doesn't have the exact write date (likely lost during a server "
            "migration), but I won't claim it's empty when it isn't."
        )

    if entries is None:
        if lang == "fr":
            return (
                "Ma mémoire libre est activée, mais je ne peux pas confirmer son "
                "contenu réel maintenant (vérification du repo indisponible) -- je "
                "ne vais pas deviner."
            )
        return (
            "My free memory is enabled, but I can't confirm its real content right "
            "now (repo check unavailable) -- I won't guess."
        )

    if lang == "fr":
        return "Ma mémoire libre est activée mais je n'y ai encore rien écrit (repo vide, vérifié)."
    return "My free memory is enabled but I haven't written anything there yet (repo confirmed empty)."


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


_TRADE_TRIGGER_RE = re.compile(
    r"\b(?:trade|position|portefeuille|portfolio|capital|stop[- ]loss|"
    r"invalidation|vendu|vente|achet[ée]|achats?|th[eè]se?|perdu|perdant|perdante|perte|gagn[ée]|"
    r"gagnant|clôtur[ée]|closed|sold|bought|profit|loss|winrate|pnl|p&l|p&amp;l)\b",
    re.IGNORECASE,
)
_TRADE_QUESTION_RE = re.compile(
    r"(?:"
    r"qu['’]est[- ]ce\s+qui\s+[cs]['’]est\s+pass[ée]|qu['’]est[- ]ce\s+qui\s+se\s+passe|"
    r"qu['’]est[- ]ce\s+que|c['’]est\s+quoi|quelle?\s+est|"
    r"pourquoi|comment\s+va|combien\s+(?:il\s+)?(?:me\s+)?reste|"
    r"what\s+happened|why\s+did|how['’]?s|how\s+much|what['’]?s"
    r")",
    re.IGNORECASE,
)


def is_trade_status_question(message: str) -> bool:
    """True pour une question en langage naturel sur l'ÉTAT d'un trade/du portefeuille
    (« qu'est-ce qui s'est passé sur ce trade ? », « pourquoi t'as vendu ? », « combien
    il reste ? », « c'est quoi ta thèse sur cet achat ? ») -- PAS une commande explicite
    (``/ledger``/``/feedback`` existent déjà pour ça). Incident réel (16/07) : une telle
    question, posée juste après une perte réelle, est tombée dans la conversation LLM
    générale SANS accès au registre -- ARIA a honnêtement dit ne rien voir plutôt que
    d'inventer (bon réflexe), mais l'opérateur restait sans réponse alors que la donnée
    existe réellement en base. Exige un mot-clé de trading ET une tournure de question
    dans le MÊME message (jamais l'un seul) -- sinon "qu'est-ce qui s'est passé" seul,
    sur un tout autre sujet, déclencherait une injection de données de trading
    hors-propos.

    19/07, élargi après un 2e incident réel : "c'est quoi ta these sur lachat de
    cobot ?" ne matchait ni le mot-clé ("achat" != "acheté", "thèse" absent -- pire,
    le mot-clé "AERO" était codé en dur comme symbole de test, jamais un vrai
    déclencheur générique) ni la tournure ("c'est quoi" absent de la liste). Un simple
    "?" compte désormais aussi comme tournure de question -- couvre par construction
    toute future formulation directe (pas seulement celles déjà observées), sans
    affaiblir le garde-fou réel (le mot-clé de trading reste filtré en premier, un "?"
    seul sur un sujet hors-trading ne déclenche toujours rien)."""
    text = (message or "").strip()
    if len(text) < 6:
        return False
    if not _TRADE_TRIGGER_RE.search(text):
        return False
    return bool(_TRADE_QUESTION_RE.search(text) or "?" in text)


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