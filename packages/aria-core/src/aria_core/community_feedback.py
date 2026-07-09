"""Feedback communauté site — collecte, triage, délégation ouvrier si pertinent."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory
from aria_core.paths import data_dir
from aria_core.x_text import (
    FEEDBACK_SITE_MAX_CHARS,
    FEEDBACK_X_QUOTE_MAX_WEIGHT,
    FEEDBACK_X_QUOTE_THREAD_MAX_WEIGHT,
    X_TWEET_MAX_CHARS,
    feedback_x_min_tweet_weight,
    fit_x_tweet,
    tweet_fits,
    weighted_tweet_length,
)

logger = logging.getLogger(__name__)

FEEDBACK_JSONL = "community-feedback.jsonl"
X_QUEUE_FILE = "community-feedback-x-queue.json"
DEFAULT_QUEUE_SCORE = 55
SPAM_MAX_LEN = 8
X_TWEET_MIN_SCORE = 35
DEFAULT_X_COOLDOWN_HOURS = 4.0
_HOLDING_DOMAIN = "ariavanguardzhc.com"
_WORKER_OK = frozenset({"pushed", "local_only", "queued_local", "local_md"})

_PRODUCT_RE = re.compile(
    r"\b(?:site|page|vanguard|market|marché|bot|telegram|skill|ui|ux|bandeau|banner|"
    r"faq|nav|menu|bouton|button|feature|fonction|amelior|amélior|ajout|fix|bug|"
    r"communaut|community|aria|zhc|watchlist|alerte|chart|dark\s*mode)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(?:ajoute|ajouter|ameliore|améliorer|corrige|fixe|construi|implemente|implémenter|"
    r"add|improve|fix|build|would\s+like|j'?\s*aimerais|je\s+voudrais|il\s+faudrait|"
    r"manque|besoin|souhait|propos|suggest)\b",
    re.IGNORECASE,
)
_SPAM_SCAM_RE = re.compile(
    r"\b(?:free\s+money|crypto\s+giveaway|dm\s+me|airdrop\s+hunter)\b",
    re.IGNORECASE,
)
_EXTERNAL_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_COMPLIMENT_RE = re.compile(
    r"\b(?:bravo|joli|jolie|beau|belle|super|merci|génial|genial|love|nice|beautiful|magnifique)\b",
    re.IGNORECASE,
)
_CONTACT_RE = re.compile(
    r"\b(?:contact|reste en lien|message sur x|écris|ecris|dm|telegram|twitter)\b",
    re.IGNORECASE,
)
_ROADMAP_RE = re.compile(
    r"\b(?:futur|future|roadmap|suite|next\s+step|what'?s\s+next|partenariat|partnership|"
    r"partner|revenu|revenue|monétis|monetiz|business\s+model|generate|générer|"
    r"earn|profit|roadmap|plan\s+strat|strateg|stratég|prévu|prevu|bientôt|bientot)\b",
    re.IGNORECASE,
)


def is_roadmap_partnership_question(message: str) -> bool:
    from aria_core.operator_conversational import is_injected_factual_claim

    text = (message or "").strip()
    if is_injected_factual_claim(text):
        return False
    if re.search(r"\bplan\s+gratuit\b", text, re.I):
        return False

    # Never treat self-referential questions about humor, seriousness, tone, or personality
    # as roadmap/business questions — even if they contain "revenu" (as in "est revenu").
    if re.search(r"\b(humour|humour|sérieux|sérieuse|trop sérieux|ton|personnalité|mode)\b", text, re.I):
        return False

    # Direct clash / provocation at the operator on casual channel (e.g. "t'as 0 revenue et tu continues")
    # should not hijack into the roadmap template. Let the liberated casual path handle it.
    if re.search(r"\b(t'as|tu as|tu continues|tu sers|tu fais quoi|tu tournes|pas comme toi|0 revenue|zero revenue|tu es nulle|tu sers à rien)\b", text, re.I):
        return False

    return bool(_ROADMAP_RE.search(text))


def operator_roadmap_reply(*, lang: str = "fr") -> str:
    """Opérateur — politique ZHC locale, sans LLM probabiliste ni web."""
    pair = personal_reply_pair_on_feedback(
        "partenariats roadmap revenus futur?",
        lang=lang,
    )
    if lang == "fr":
        return (
            f"{pair.primary}\n\n{pair.followup}\n\n"
            "Aucun partenariat signé à annoncer — candidature Virtual Protocol en cours "
            "(voir JOURNAL.md). Pas de date marketing inventée."
        )
    return (
        f"{pair.primary}\n\n{pair.followup}\n\n"
        "No signed partnership to announce — Virtual Protocol application in progress "
        "(see JOURNAL.md). No invented launch dates."
    )
_INTERNAL_TEST_RE = re.compile(
    r"\b(?:test\s+diagnostic|diagnostic\s+test|ping\s+from\s+diagnostic)\b",
    re.IGNORECASE,
)
_IDEAS_LIST_RE = re.compile(r"\(\d+\)|\b\d+\)\s", re.IGNORECASE)
_REPLY_PAIR_SEP = "|||"


def _normalize_handle(handle: str) -> str:
    return (handle or "").strip().lstrip("@").lower()


def trusted_feedback_handles() -> frozenset[str]:
    """
    Handles de confiance (champ X sur le site) — pas de lien Telegram.
    Opérateur : tweets manuels via /x sur Telegram ou autre canal.
    """
    handles: set[str] = set()
    raw = os.getenv("COMMUNITY_FEEDBACK_TRUSTED_HANDLES", "GoldenFarFR")
    for part in raw.split(","):
        h = _normalize_handle(part)
        if h:
            handles.add(h)
    return frozenset(handles)


def is_trusted_feedback_handle(handle: str) -> bool:
    h = _normalize_handle(handle)
    return bool(h) and h in trusted_feedback_handles()


def trusted_instant_x_enabled() -> bool:
    """Handles de confiance (opérateur) — tweet X immédiat, sans file 4 h."""
    return os.getenv("COMMUNITY_FEEDBACK_TRUSTED_INSTANT_X", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def trusted_unrestricted_enabled() -> bool:
    """Opérateur (GoldenFarFR…) — pas de modération score/spam/file X."""
    return os.getenv("COMMUNITY_FEEDBACK_TRUSTED_UNRESTRICTED", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def is_trusted_operator_publish(handle: str) -> bool:
    """Handle de confiance avec publication libre (site + @Aria_ZHC)."""
    return is_trusted_feedback_handle(handle) and trusted_unrestricted_enabled()


def _feedback_has_spam_signal(text: str) -> bool:
    """Spam réel — pas les mentions du domaine holding."""
    t = (text or "").strip()
    if not t:
        return True
    lower = t.lower()
    if _SPAM_SCAM_RE.search(lower):
        return True
    allowed_hosts = (
        _HOLDING_DOMAIN,
        "x.com",
        "twitter.com",
        "t.me",
        "github.com/goldenfarfr",
    )
    for url in _EXTERNAL_URL_RE.findall(t):
        ul = url.lower()
        if not any(host in ul for host in allowed_hosts):
            return True
    if re.search(r"\.com\b", lower):
        compact = lower.replace(" ", "")
        if _HOLDING_DOMAIN.replace(".", "") not in compact.replace(".", ""):
            if _EXTERNAL_URL_RE.search(t):
                return True
            if len(t) < 80 and not _PRODUCT_RE.search(t):
                return True
    return False


def x_cooldown_hours() -> float:
    raw = os.getenv("COMMUNITY_FEEDBACK_X_COOLDOWN_HOURS", "").strip()
    if not raw:
        return DEFAULT_X_COOLDOWN_HOURS
    try:
        return max(0.5, min(24.0, float(raw)))
    except ValueError:
        return DEFAULT_X_COOLDOWN_HOURS


def x_tweet_min_score() -> int:
    """Seuil minimum pour publier un avis sur X (plus strict que le triage site)."""
    raw = os.getenv("COMMUNITY_FEEDBACK_X_MIN_SCORE", "").strip()
    if not raw:
        return X_TWEET_MIN_SCORE
    try:
        return max(20, min(80, int(raw)))
    except ValueError:
        return X_TWEET_MIN_SCORE


def assess_feedback_publishable_on_x(
    text: str,
    score: int,
    *,
    handle: str = "",
) -> tuple[bool, str]:
    """
    Filtre avant tweet @Aria_ZHC — pas de vulgarité, spam, ni avis vides.
    Handle de confiance (ex. GoldenFarFR) : assouplissement si contenu Vanguard.
    """
    t = (text or "").strip()
    if _INTERNAL_TEST_RE.search(t):
        return False, "internal_test"
    if is_trusted_operator_publish(handle) and t:
        return True, "ok_operator"
    trusted = is_trusted_feedback_handle(handle)
    min_score = 20 if trusted else x_tweet_min_score()
    if score < min_score and not (trusted and len(t) >= 40 and _PRODUCT_RE.search(t)):
        return False, "score_too_low"
    if _PROFANITY_RE.search(t):
        return False, "profanity"
    if _LOW_SUBSTANCE_RE.match(t):
        return False, "low_substance"
    if len(t) < _X_TWEET_MIN_LEN:
        return False, "too_short_for_x"
    if _feedback_has_spam_signal(t):
        return False, "spam_pattern"
    if (
        not trusted
        and score < 40
        and not _PRODUCT_RE.search(t)
        and not _ACTION_RE.search(t)
    ):
        return False, "too_generic_for_x"
    return True, "ok_trusted" if trusted else "ok"


def queue_score_threshold() -> int:
    """Seuil file ouvrier — monte via COMMUNITY_FEEDBACK_QUEUE_SCORE quand Vanguard grossit."""
    raw = os.getenv("COMMUNITY_FEEDBACK_QUEUE_SCORE", "").strip()
    if not raw:
        return DEFAULT_QUEUE_SCORE
    try:
        return max(20, min(95, int(raw)))
    except ValueError:
        return DEFAULT_QUEUE_SCORE


def _feedback_path() -> Path:
    path = data_dir() / FEEDBACK_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def score_feedback(text: str) -> int:
    """Score 0–100 — heuristique locale, pas de LLM obligatoire."""
    t = (text or "").strip()
    if len(t) < SPAM_MAX_LEN:
        return 0
    if _feedback_has_spam_signal(t):
        return 5
    score = min(35, len(t) // 4)
    if _PRODUCT_RE.search(t):
        score += 30
    if _ACTION_RE.search(t):
        score += 25
    if "?" in t and len(t) > 40:
        score += 5
    return min(100, score)


_FEEDBACK_WIDGET_RE = re.compile(
    r"\b(?:avis|feedback|commentaire|review|laisser un|feedback box|on-site feedback)\b",
    re.IGNORECASE,
)
_BUILD_TOGETHER_RE = re.compile(
    r"\b(?:ensemble|together|construi|build(?:ing)?|ship|zhc)\b",
    re.IGNORECASE,
)
_GENERIC_REPLY_RE = re.compile(
    r"\b(?:thanks for (?:sharing|your support)|thank you for your support|"
    r"love the energy|supporting our growth|we build in public for the community|"
    r"thanks for the feedback)\b",
    re.IGNORECASE,
)
_LOW_SUBSTANCE_RE = re.compile(
    r"^(?:thanks?|thank you|thx|lol|lmao|gm|gn|nice|cool|ok|okay|\+1|"
    r"yes|no|oui|non|great|awesome|bravo|super|merci|salut|hi|hello|"
    r"beau|joli|jolie|nul|debile|débile|stupid|useless|shitpost)[\s!.?❤️🔥👍🙏]*$",
    re.IGNORECASE,
)
_PROFANITY_RE = re.compile(
    r"\b(?:merde|putain|connard|salope|enculé|encule|fdp|ntm|pd|"
    r"fuck(?:ing)?|shit|bitch|asshole|bastard|cunt|dick|pussy|nigger|nigga|"
    r"faggot|retard|pute|batard|bâtard|con\b|conne\b|debile|débile)\b",
    re.IGNORECASE,
)
_X_TWEET_MIN_LEN = 15


@dataclass(frozen=True)
class FeedbackReplyPair:
    """Réponse fil X — 2 phrases (tweet reply sous la citation)."""

    primary: str
    followup: str


def personal_reply_pair_on_feedback(text: str, *, lang: str = "fr") -> FeedbackReplyPair:
    """Réponse 2 phrases — heuristique locale, ancrée sur le contenu."""
    t = (text or "").strip()
    if _ROADMAP_RE.search(t) or t.count("?") >= 2:
        if lang == "fr":
            return FeedbackReplyPair(
                primary="On prouve d'abord l'analyse — track-record public avant tout produit payant.",
                followup=(
                    "Partenariats et produits quand la preuve tient — "
                    "ton avis oriente la roadmap."
                ),
            )
        return FeedbackReplyPair(
            primary=(
                "Proving the analysis engine first — public track record before any paid "
                "product — is what we ship right now; your roadmap ask is exactly the kind "
                "of signal we prioritize."
            ),
            followup=(
                "Partnerships and revenue models follow once traction proves the model; "
                "notes like yours steer the next Vanguard iteration."
            ),
        )
    if _IDEAS_LIST_RE.search(t) or (_ACTION_RE.search(t) and len(t) > 120):
        if lang == "fr":
            return FeedbackReplyPair(
                primary="Tes idées produit sont notées — holding, Telegram, track-record public.",
                followup="On ship Vanguard brique par brique ; les retours comme le tien priorisent la suite.",
            )
        return FeedbackReplyPair(
            primary="Your product ideas are logged — holding page, Telegram, public track record.",
            followup="We ship Vanguard brick by brick; notes like yours steer what lands next.",
        )
    if _FEEDBACK_WIDGET_RE.search(t) and _BUILD_TOGETHER_RE.search(t):
        if lang == "fr":
            return FeedbackReplyPair(
                primary="Canal avis + construire ensemble — exactement l'usage prévu.",
                followup="Chaque note ZHC oriente la prochaine itération Vanguard.",
            )
        return FeedbackReplyPair(
            primary="Feedback lane + building together — exactly how we use this channel.",
            followup="Every ZHC note steers the next Vanguard iteration.",
        )
    if _FEEDBACK_WIDGET_RE.search(t):
        if lang == "fr":
            return FeedbackReplyPair(
                primary="Canal avis noté — ça priorise la roadmap ZHC.",
                followup="On continue d'itérer en public sur ariavanguardzhc.com.",
            )
        return FeedbackReplyPair(
            primary="Feedback lane noted — it prioritizes the ZHC roadmap.",
            followup="We keep iterating in public on ariavanguardzhc.com.",
        )
    if _ACTION_RE.search(t) or (_PRODUCT_RE.search(t) and len(t) > 40):
        if lang == "fr":
            return FeedbackReplyPair(
                primary="Piste produit claire — prochain cycle ZHC.",
                followup="Ton retour précis compte pour ce qu'on ship ensuite.",
            )
        return FeedbackReplyPair(
            primary="Clear product signal — next ZHC ship cycle.",
            followup="Your specific note counts toward what we ship next.",
        )
    if _CONTACT_RE.search(t):
        if lang == "fr":
            return FeedbackReplyPair(
                primary="On reste en contact — la commu ZHC avance avec des retours comme celui-ci.",
                followup="Suivi sur @Aria_ZHC et le site pour la suite.",
            )
        return FeedbackReplyPair(
            primary="We'll stay in touch — the ZHC community moves on notes like this.",
            followup="Follow @Aria_ZHC and the site for what ships next.",
        )
    if _COMPLIMENT_RE.search(t):
        if lang == "fr":
            return FeedbackReplyPair(
                primary="Merci — contenu utile pour la suite Vanguard.",
                followup="On continue d'itérer en public, brique par brique.",
            )
        return FeedbackReplyPair(
            primary="Thanks — genuinely useful for where Vanguard goes next.",
            followup="We keep shipping in public, brick by brick.",
        )
    if lang == "fr":
        return FeedbackReplyPair(
            primary="Ton retour oriente ce qu'on ship sur Vanguard.",
            followup="Chaque note sur le site compte pour la roadmap ZHC.",
        )
    return FeedbackReplyPair(
        primary="Your note steers what we ship on Vanguard.",
        followup="Every site note counts toward the ZHC roadmap.",
    )


def personal_take_on_feedback(text: str, *, lang: str = "fr") -> str:
    """Compat — première phrase de la paire."""
    return personal_reply_pair_on_feedback(text, lang=lang).primary


def _feedback_tweet_header(handle: str) -> str:
    h = (handle or "").strip().lstrip("@")
    return f"✦ @{h}" if h else "✦ Community note"


def feedback_x_thread_reply_enabled() -> bool:
    return os.getenv("COMMUNITY_FEEDBACK_X_THREAD_REPLY", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def _tweet_id_from_post_note(note: str) -> str | None:
    match = re.search(r"status/(\d+)", note or "")
    return match.group(1) if match else None


def _parse_reply_pair_llm(raw: str) -> FeedbackReplyPair | None:
    line = (raw or "").strip().strip('"').strip("'")
    if _REPLY_PAIR_SEP in line:
        a, b = line.split(_REPLY_PAIR_SEP, 1)
        a, b = a.strip(), b.strip()
        if len(a) >= 12 and len(b) >= 12:
            return FeedbackReplyPair(primary=a[:150], followup=b[:130])
    return None


async def compose_feedback_reply_pair(
    text_en: str,
    *,
    original: str = "",
) -> FeedbackReplyPair:
    """Réponse fil X — 2 phrases (LLM si dispo, sinon heuristique)."""
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.x_publication_policy import policy_rules_for_llm
    from aria_core.x_voice import human_voice_rules_for_llm, strip_obvious_ai_phrases

    source = (text_en or original or "").strip()
    orig = (original or text_en or "").strip()
    if not source:
        return personal_reply_pair_on_feedback(original, lang="en")

    simple_widget_only = (
        _FEEDBACK_WIDGET_RE.search(orig)
        and not _ROADMAP_RE.search(orig)
        and not _IDEAS_LIST_RE.search(orig)
        and len(orig) < 100
    )
    if simple_widget_only:
        return personal_reply_pair_on_feedback(orig, lang="en")

    if is_llm_configured():
        system = (
            "You reply on @Aria_ZHC in a THREAD under a site visitor quote (ariavanguardzhc.com).\n"
            "Write TWO warm, natural English sentences — conversational, not corporate.\n"
            "Sentence 1 (max 150 chars): answer their question OR name 2–3 specific ideas they raised.\n"
            "Sentence 2 (max 130 chars): concrete next step, roadmap hint, or human close.\n"
            "Use as much of the character budget as fits naturally — avoid one-liners.\n"
            "If they ask roadmap/revenue/partnerships: no paid product yet, proving the analysis "
            "track record first, built-in-public.\n"
            "If they list numbered ideas: acknowledge 2–3 by name.\n"
            "No generic thank-you. Forbidden: 'thanks for sharing', 'love the energy', "
            "'good to hear on the site itself', 'feedback box is for notes like this'.\n"
            f"{policy_rules_for_llm('en')}\n"
            f"{human_voice_rules_for_llm('en')}\n"
            f"Output format exactly: SENTENCE1{_REPLY_PAIR_SEP}SENTENCE2\n"
            "English only. No quotes. No @mentions."
        )
        try:
            composed = await chat_with_context(
                f"Community feedback to react to:\n{source[:600]}",
                system,
                temperature=0.45,
                max_tokens=180,
            )
            line = strip_obvious_ai_phrases((composed or "").strip())
            pair = _parse_reply_pair_llm(line)
            if pair and not _GENERIC_REPLY_RE.search(pair.primary):
                return FeedbackReplyPair(
                    primary=pair.primary[:150],
                    followup=pair.followup[:130],
                )
            if line and _REPLY_PAIR_SEP not in line:
                one = line.strip('"').strip("'")
                if len(one) >= 12 and not _GENERIC_REPLY_RE.search(one):
                    base = personal_reply_pair_on_feedback(original or text_en, lang="en")
                    return FeedbackReplyPair(primary=one[:150], followup=base.followup)
        except Exception as exc:
            logger.warning("feedback reply pair LLM failed: %s", exc)

    return personal_reply_pair_on_feedback(original or text_en, lang="en")


async def compose_personal_reply_to_feedback(
    text_en: str,
    *,
    original: str = "",
) -> str:
    """Compat — première phrase de la paire."""
    pair = await compose_feedback_reply_pair(text_en, original=original)
    return pair.primary


def _short_excerpt(text: str, max_len: int = 72) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


def _condense_quote_sync(text: str, max_weight: int) -> str:
    """Réduit un avis long pour X — phrases complètes avant troncature aveugle."""
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return ""
    if weighted_tweet_length(clean) <= max_weight:
        return clean

    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", clean) if s.strip()]
    if len(sentences) > 1:
        acc = sentences[0]
        for sentence in sentences[1:]:
            trial = f"{acc} {sentence}"
            if weighted_tweet_length(trial) <= max_weight:
                acc = trial
            else:
                break
        if weighted_tweet_length(acc) <= max_weight and len(acc) >= 16:
            return acc

    if weighted_tweet_length(clean) > max_weight + 24:
        excerpt = _short_excerpt(clean, max_len=min(120, max_weight))
        if weighted_tweet_length(excerpt) <= max_weight:
            return excerpt

    return fit_x_tweet(clean, max_chars=max_weight)


async def _llm_summarize_quote_for_x(text: str, max_weight: int) -> str | None:
    """Résumé LLM quand l'avis site (≤500 chars) dépasse le budget citation tweet."""
    from aria_core.llm import chat_with_context, is_llm_configured

    if not is_llm_configured():
        return None
    budget_chars = min(130, max(60, max_weight - 10))
    system = (
        f"Summarize this Vanguard site community feedback in ONE complete English sentence "
        f"(max {budget_chars} characters).\n"
        "Keep the author's main praise or request. No quotes, no @mentions, no ellipsis.\n"
        "Output ONLY the summary sentence."
    )
    try:
        out = await chat_with_context(text[:600], system, max_tokens=120, temperature=0.15)
        line = (out or "").strip().strip('"').strip("'")
        if line and len(line) >= 12 and weighted_tweet_length(line) <= max_weight:
            return line
        if line:
            return _condense_quote_sync(line, max_weight)
    except Exception as exc:
        logger.warning("feedback quote summarize failed: %s", exc)
    return None


_FR_SURFACE_RE = re.compile(
    r"\b(?:salut|bonjour|merci|bravo|joli|jolie|beau|belle|génial|genial|"
    r"ajouter|améliorer|ameliorer|communaut|filiale|pourquoi|comment)\b|"
    r"\b(?:c'est|j'aimerais|je voudrais|il faudrait)\b",
    re.IGNORECASE,
)


def _is_likely_english(text: str) -> bool:
    from aria_core.locale import LANG_EN, detect_lang

    if re.search(r"[éèêàùçîôâëïü]", text, re.IGNORECASE):
        return False
    if _FR_SURFACE_RE.search(text):
        return False
    if detect_lang(text) != LANG_EN:
        return False
    return True


async def _llm_polish_quote_for_x(text: str) -> str | None:
    """Traduction fidèle + correction orthographe/grammaire — sens inchangé."""
    from aria_core.llm import chat_with_context, is_llm_configured

    if not is_llm_configured():
        return None
    system = (
        "Prepare ONE Vanguard site community feedback quote for @Aria_ZHC on X.\n"
        "Rules:\n"
        "- Output in English.\n"
        "- Fix visitor spelling and grammar in any language (e.g. enssemble→together, "
        "generer→generate, partenariat→partnership) while translating to English.\n"
        "- Keep the EXACT meaning, tone, and details — do NOT summarize or genericize.\n"
        "- Do NOT add ideas, soften, or censor unless the input is already vulgar.\n"
        "- Keep first-person voice if present.\n"
        "Output ONLY the polished quote — no quotes, labels, or commentary."
    )
    out = await chat_with_context(text[:800], system, max_tokens=400, temperature=0.1)
    return out.strip().strip('"').strip("'") if out else None


async def _llm_translate_to_english(text: str) -> str | None:
    return await _llm_polish_quote_for_x(text)


async def _google_translate_to_english(text: str) -> str:
    import httpx
    from urllib.parse import quote

    q = quote(text[:500])
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=auto&tl=en&dt=t&q={q}"
    )
    async with httpx.AsyncClient(timeout=12.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    chunks = data[0] if isinstance(data, list) and data else []
    return "".join(part[0] for part in chunks if isinstance(part, list) and part[0]).strip()


async def _llm_fix_english_typos(text: str) -> str | None:
    from aria_core.llm import chat_with_context, is_llm_configured

    if not is_llm_configured():
        return None
    system = (
        "Fix spelling and grammar only in this English community feedback.\n"
        "Do NOT change meaning, tone, or length. Output ONLY the corrected text."
    )
    out = await chat_with_context(text[:800], system, max_tokens=400, temperature=0.05)
    return out.strip().strip('"').strip("'") if out else None


async def prepare_feedback_quote_for_x(text: str) -> tuple[str, bool]:
    """
    Citation tweet @Aria_ZHC — politique fixe :
    - toujours en anglais ;
    - fautes d'orthographe/grammaire du visiteur corrigées (sens inchangé).
    Returns (quote_en, was_transformed).
    """
    clean = (text or "").strip()
    if not clean:
        return "", False

    # Anglais déjà lisible — corriger typos seulement (évite résumés LLM type « User praises… »).
    if _is_likely_english(clean):
        fixed = await _llm_fix_english_typos(clean)
        if fixed and len(fixed) >= 3:
            return fixed[:800], fixed.strip() != clean
        return clean, False

    polished = await _llm_polish_quote_for_x(clean)
    if polished and len(polished) >= 3:
        return polished[:800], polished.strip() != clean

    for translator in (_google_translate_to_english,):
        try:
            result = await translator(clean)
            if result and len(result) >= 3:
                fixed = await _llm_fix_english_typos(result)
                out = fixed if fixed and len(fixed) >= 3 else result
                return out[:800], True
        except Exception as exc:
            logger.warning("feedback translate %s failed: %s", translator.__name__, exc)

    return clean, False


async def translate_to_english_for_x(text: str) -> tuple[str, bool]:
    """Alias — prépare la citation X (traduction + corrections)."""
    return await prepare_feedback_quote_for_x(text)


def _truncate_quote(text: str, max_weight: int) -> str:
    return _condense_quote_sync(text, max_weight)


def _x_queue_path() -> Path:
    path = data_dir() / X_QUEUE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_x_queue() -> dict[str, Any]:
    path = _x_queue_path()
    if not path.exists():
        return {"buckets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("buckets"), dict):
            return data
    except Exception:
        pass
    return {"buckets": {}}


def _save_x_queue(data: dict[str, Any]) -> None:
    _x_queue_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _queue_bucket_key(handle: str, visitor_id: str) -> str:
    h = _normalize_handle(handle)
    if h:
        return f"handle:{h}"
    vid = (visitor_id or "anon").strip()[:64] or "anon"
    return f"visitor:{vid}"


def _parse_iso(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _enqueue_x_feedback_item(
    *,
    handle: str,
    visitor_id: str,
    feedback_id: str,
    text: str,
    instant: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    flush_at = now if instant else now + timedelta(hours=x_cooldown_hours())
    data = _load_x_queue()
    buckets = data.setdefault("buckets", {})
    key = _queue_bucket_key(handle, visitor_id)
    bucket = buckets.get(key) or {
        "handle": (handle or "").strip().lstrip("@"),
        "visitor_id": (visitor_id or "").strip()[:64],
        "items": [],
        "flush_at": flush_at.isoformat(),
    }
    bucket["items"].append(
        {
            "id": feedback_id,
            "text": text[:FEEDBACK_SITE_MAX_CHARS],
            "at": now.isoformat(),
        }
    )
    bucket["flush_at"] = flush_at.isoformat()
    bucket["handle"] = (handle or bucket.get("handle") or "").strip().lstrip("@")
    buckets[key] = bucket
    data["buckets"] = buckets
    _save_x_queue(data)
    return {
        "flush_at": bucket["flush_at"],
        "pending_count": len(bucket["items"]),
        "bucket_key": key,
    }


def _clear_x_bucket(key: str) -> None:
    data = _load_x_queue()
    buckets = data.get("buckets") or {}
    if key in buckets:
        del buckets[key]
        data["buckets"] = buckets
        _save_x_queue(data)


def _due_bucket_keys(data: dict[str, Any]) -> list[str]:
    now = datetime.now(timezone.utc)
    due: list[str] = []
    for key, bucket in (data.get("buckets") or {}).items():
        items = bucket.get("items") or []
        if not items:
            continue
        flush_at = _parse_iso(str(bucket.get("flush_at") or ""))
        if flush_at and flush_at <= now:
            due.append(key)
    return due


async def _polish_merged_quotes(texts: list[str]) -> list[str]:
    out: list[str] = []
    for raw in texts:
        quote, _tr = await prepare_feedback_quote_for_x(raw)
        q = quote.strip()
        if not q:
            continue
        if weighted_tweet_length(q) > FEEDBACK_X_QUOTE_THREAD_MAX_WEIGHT:
            q = _condense_quote_sync(q, FEEDBACK_X_QUOTE_THREAD_MAX_WEIGHT)
        out.append(q)
    return out


def _quote_tweet_prefixes(handle: str) -> list[str]:
    h = (handle or "").strip().lstrip("@")
    if h:
        return [f"✦ @{h}\n\n", f"✦ @{h}\n"]
    return ["✦\n\n", "✦ "]


def _best_fill_tweet(candidates: list[str], *, min_weight: int | None = None) -> str:
    """Choisit le tweet le plus long qui tient, idéalement ≥ min_weight (70 % de 280)."""
    target = min_weight if min_weight is not None else feedback_x_min_tweet_weight()
    best = ""
    best_w = 0
    for tweet in candidates:
        if not tweet or not tweet_fits(tweet):
            continue
        w = weighted_tweet_length(tweet)
        if w >= target:
            return tweet
        if w > best_w:
            best = tweet
            best_w = w
    return best


def build_feedback_quote_tweet(text: str, *, handle: str = "") -> str:
    """Tweet 1 du fil — citation fidèle, sans lien site, remplissage ~70 %."""
    quote_full = re.sub(r"\s+", " ", (text or "").strip())
    if not quote_full:
        return "✦"

    min_w = feedback_x_min_tweet_weight()
    candidates: list[str] = []
    for prefix in _quote_tweet_prefixes(handle):
        shell_w = weighted_tweet_length(prefix) + 2
        max_quote_w = X_TWEET_MAX_CHARS - shell_w
        for quote_w in range(
            min(max_quote_w, FEEDBACK_X_QUOTE_THREAD_MAX_WEIGHT),
            48,
            -4,
        ):
            quote = _condense_quote_sync(quote_full, quote_w)
            if not quote:
                continue
            candidates.append(f'{prefix}"{quote}"')

    picked = _best_fill_tweet(candidates, min_weight=min_w)
    if picked:
        return picked

    quote = _condense_quote_sync(quote_full, 80)
    prefix = _quote_tweet_prefixes(handle)[0]
    return fit_x_tweet(f'{prefix}"{quote}"')


def build_feedback_followup_tweet(pair: FeedbackReplyPair) -> str:
    """Tweet 2 du fil — ton humain, 2 phrases aérées, ~70 % du budget."""
    primary = re.sub(r"\s+", " ", (pair.primary or "").strip())
    followup = re.sub(r"\s+", " ", (pair.followup or "").strip())
    if not primary:
        return fit_x_tweet(followup)
    min_w = feedback_x_min_tweet_weight()
    intros = (
        "Really appreciate you taking the time to write this —",
        "This is exactly the kind of note that helps us prioritize —",
        "Love the detail here —",
        "Good signal —",
    )
    candidates: list[str] = []
    for intro in intros:
        candidates.append(f"{intro}\n\n{primary}\n\n{followup}")
    candidates.append(f"{primary}\n\n{followup}")
    for intro in intros:
        candidates.append(f"{intro} {primary} {followup}")

    picked = _best_fill_tweet(candidates, min_weight=min_w)
    if picked:
        return picked
    return fit_x_tweet(f"{primary}\n\n{_truncate_quote(followup, 110)}")


def build_merged_feedback_tweet(
    quotes_en: list[str],
    *,
    handle: str = "",
    personal: str = "",
    reply_pair: FeedbackReplyPair | None = None,
) -> str:
    """Tweet citation — 1 avis ou fusion ; réponse en reply si fil actif."""
    if len(quotes_en) <= 1 and feedback_x_thread_reply_enabled():
        return build_feedback_quote_tweet(
            quotes_en[0] if quotes_en else "",
            handle=handle,
        )
    if len(quotes_en) <= 1:
        return build_feedback_thanks_tweet(
            quotes_en[0] if quotes_en else "",
            handle=handle,
            personal=personal or (reply_pair.primary if reply_pair else ""),
        )
    header = _feedback_tweet_header(handle)
    if quotes_en:
        header = f"{header} ({len(quotes_en)} notes)"
    merged = " / ".join(f'"{_truncate_quote(q, 90)}"' for q in quotes_en[:3])
    tweet = f"{header}\n\n{merged}"
    if tweet_fits(tweet):
        return tweet
    short = " / ".join(f'"{_truncate_quote(q, 50)}"' for q in quotes_en[:2])
    return fit_x_tweet(f"{header}\n\n{short}")


async def _flush_x_queue_bucket(key: str, bucket: dict[str, Any]) -> dict[str, Any] | None:
    items = bucket.get("items") or []
    if not items:
        _clear_x_bucket(key)
        return None

    texts = [str(it.get("text") or "") for it in items]
    quotes_en = await _polish_merged_quotes(texts)
    if not quotes_en:
        _clear_x_bucket(key)
        return {"status": "skipped", "reason": "empty_quote"}

    merged_original = "\n".join(texts)
    reply_pair = await compose_feedback_reply_pair(
        quotes_en[0],
        original=merged_original,
    )
    tweet = build_merged_feedback_tweet(
        quotes_en,
        handle=str(bucket.get("handle") or ""),
        personal=reply_pair.primary,
        reply_pair=reply_pair,
    )
    from aria_core.x_voice import strip_obvious_ai_phrases

    tweet = strip_obvious_ai_phrases(tweet)
    followup_tweet = strip_obvious_ai_phrases(build_feedback_followup_tweet(reply_pair))
    ids = ",".join(str(it.get("id") or "")[:12] for it in items[:3])

    from aria_core.gateway.x_twitter import post_tweet, reply_to_tweet

    operator_publish = is_trusted_operator_publish(str(bucket.get("handle") or ""))
    _exchange, note = await post_tweet(
        tweet,
        approval_id=f"community_fb_batch:{ids}",
        skip_rate_gap=True,
        force=operator_publish,
    )
    posted = "Publié sur X" in note or "x.com/" in note
    reply_note = ""
    thread_posted = False
    if posted and feedback_x_thread_reply_enabled():
        parent_id = _tweet_id_from_post_note(note)
        if parent_id:
            _reply_id, reply_note = await reply_to_tweet(
                followup_tweet,
                in_reply_to_tweet_id=parent_id,
                approval_id=f"community_fb_reply:{ids}",
                force=operator_publish,
            )
            thread_posted = "Reply publiée" in (reply_note or "")
    _clear_x_bucket(key)
    append_memory(
        "community",
        f"[feedback→x batch] {ids} posted={posted} thread={thread_posted} n={len(items)} {tweet[:100]}",
    )
    full_note = note
    if reply_note:
        full_note = f"{note}\n{reply_note}"
    return {
        "status": "posted" if posted else "blocked",
        "note": full_note,
        "draft": tweet,
        "followup_draft": followup_tweet,
        "thread_posted": thread_posted,
        "merged_count": len(items),
        "text_en": quotes_en[0] if len(quotes_en) == 1 else " / ".join(quotes_en[:2]),
    }


async def flush_due_community_x_tweets() -> list[dict[str, Any]]:
    """Publie les files d'attente X arrivées à échéance (cooldown 4 h)."""
    data = _load_x_queue()
    results: list[dict[str, Any]] = []
    for key in _due_bucket_keys(data):
        bucket = (data.get("buckets") or {}).get(key) or {}
        try:
            res = await _flush_x_queue_bucket(key, bucket)
            if res:
                results.append(res)
        except Exception as exc:
            logger.warning("community X queue flush failed %s: %s", key, exc)
    return results


def build_feedback_thanks_tweet(
    text: str,
    *,
    handle: str = "",
    personal: str = "",
) -> str:
    """Tweet commu — citation de l'avis + réponse concrète (sans template « → »)."""
    quote_full = _condense_quote_sync(
        re.sub(r"\s+", " ", (text or "").strip()),
        FEEDBACK_X_QUOTE_MAX_WEIGHT,
    )
    personal = re.sub(r"\s+", " ", (personal or "").strip())
    header = _feedback_tweet_header(handle)

    quote_start = min(weighted_tweet_length(quote_full), FEEDBACK_X_QUOTE_MAX_WEIGHT)
    quote_weights = list(range(max(quote_start, 48), 39, -8)) or [max(quote_start, 48)]
    for quote_weight in quote_weights:
        quote = _truncate_quote(quote_full, quote_weight)
        reply_start = min(weighted_tweet_length(personal), 110)
        reply_weights = list(range(max(reply_start, 40), 29, -10)) or [max(reply_start, 40)]
        for reply_weight in reply_weights:
            reply = _truncate_quote(personal, reply_weight)
            for tweet in (
                f'{header}:\n"{quote}"\n{reply}',
                f'{header} — "{quote}" — {reply}',
            ):
                if tweet_fits(tweet):
                    return tweet

    quote = _truncate_quote(quote_full, 72)
    reply = _truncate_quote(personal, 48)
    return fit_x_tweet(f'{header}:\n"{quote}"\n{reply}')


async def maybe_tweet_community_feedback(
    text: str,
    *,
    handle: str = "",
    visitor_id: str = "",
    feedback_id: str,
    score: int,
    lang: str = "fr",
) -> dict[str, Any]:
    """File X 4 h par handle — fusion des avis multiples, modération stricte."""
    publishable, block_reason = assess_feedback_publishable_on_x(
        text,
        score,
        handle=handle,
    )
    if not publishable:
        return {"status": "skipped", "reason": block_reason}

    enabled = os.getenv("COMMUNITY_FEEDBACK_X_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
    )
    if not enabled:
        return {"status": "skipped", "reason": "disabled"}

    # Publier d'abord les files arrivées à échéance (toute la queue).
    await flush_due_community_x_tweets()

    instant_x = is_trusted_operator_publish(handle) or (
        is_trusted_feedback_handle(handle) and trusted_instant_x_enabled()
    )
    q = _enqueue_x_feedback_item(
        handle=handle,
        visitor_id=visitor_id,
        feedback_id=feedback_id,
        text=text,
        instant=instant_x,
    )
    data = _load_x_queue()
    key = q["bucket_key"]
    bucket = (data.get("buckets") or {}).get(key) or {}
    flush_at = _parse_iso(str(bucket.get("flush_at") or q["flush_at"]))
    now = datetime.now(timezone.utc)
    if flush_at and flush_at > now:
        return {
            "status": "queued",
            "reason": "cooldown_4h",
            "flush_at": flush_at.isoformat(),
            "pending_count": q["pending_count"],
            "trusted": is_trusted_feedback_handle(handle),
        }

    try:
        res = await _flush_x_queue_bucket(key, bucket)
        if res:
            return res
    except Exception as exc:
        logger.warning("community feedback X tweet failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:200]}

    return {"status": "queued", "reason": "cooldown_4h", "flush_at": q["flush_at"]}


def _append_record(record: dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False)
    with _feedback_path().open("a", encoding="utf-8") as fh:
        if fh.tell() > 0:
            fh.write("\n")
        fh.write(line)


def format_public_reply(
    *,
    verdict: str,
    queued: bool,
    lang: str = "fr",
    x_status: str | None = None,
) -> str:
    x_bit_fr = ""
    x_bit_en = ""
    if x_status == "posted":
        x_bit_fr = " Je te remercie aussi sur @Aria_ZHC."
        x_bit_en = " I also thanked you on @Aria_ZHC."
    elif x_status == "queued":
        x_bit_fr = (
            " Ton avis partira sur @Aria_ZHC après le délai anti-spam (~4 h) ; "
            "plusieurs notes sont fusionnées en un seul tweet."
        )
        x_bit_en = (
            " Your note will be quoted on @Aria_ZHC after the anti-spam window (~4 h); "
            "multiple notes merge into one tweet."
        )

    if lang == "fr":
        if queued:
            return (
                "Merci — ton avis compte pour la commu ZHC. "
                "Je l'ai transmis à l'ouvrier Grok/Cursor : si ça renforce Vanguard, on prépare le workflow de ship."
                + x_bit_fr
            )
        if verdict == "spam":
            return "Message trop court ou hors périmètre produit — reformule une idée concrète pour Vanguard."
        return (
            "Merci — c'est noté côté ARIA. "
            "On priorise les idées qui améliorent Vanguard et le modèle ZHC ; je garde ton retour en mémoire."
            + x_bit_fr
        )
    if queued:
        return (
            "Thanks — your input matters. "
            "Forwarded to the Grok/Cursor worker: if it strengthens Vanguard, we'll prep the ship workflow."
            + x_bit_en
        )
    if verdict == "spam":
        return "Message too short or off-topic — share a concrete Vanguard product idea."
    return (
        "Thanks — noted on the ARIA side. We prioritize ideas that strengthen the ZHC stack."
        + x_bit_en
    )


async def submit_community_feedback(
    text: str,
    *,
    handle: str = "",
    visitor_id: str = "",
    source: str = "vanguard_site",
    lang: str = "fr",
    auto_queue: bool = True,
) -> dict[str, Any]:
    """
    Enregistre un avis public, trie, et file l'ouvrier si le score dépasse le seuil.
    """
    clean = (text or "").strip()[:FEEDBACK_SITE_MAX_CHARS]
    operator = is_trusted_operator_publish(handle)
    if not clean:
        return {
            "ok": False,
            "verdict": "spam",
            "queued": False,
            "reply": format_public_reply(verdict="spam", queued=False, lang=lang),
        }
    if not operator and len(clean) < SPAM_MAX_LEN:
        return {
            "ok": False,
            "verdict": "spam",
            "queued": False,
            "reply": format_public_reply(verdict="spam", queued=False, lang=lang),
        }

    score = score_feedback(clean)
    trusted = is_trusted_feedback_handle(handle)
    if operator:
        score = max(score, 80)
    elif trusted and len(clean) >= 20 and _PRODUCT_RE.search(clean) and score < 20:
        score = max(score, min(60, 25 + len(clean) // 8))
    threshold = queue_score_threshold()
    verdict = "noted"
    if operator:
        verdict = "noted"
    elif score < 20:
        verdict = "spam"
    elif score >= threshold:
        verdict = "queue"

    feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
    worker_task_id: str | None = None
    worker_status: str | None = None
    queued = False
    x_tweet: dict[str, Any] = {"status": "skipped"}

    if auto_queue and verdict == "queue" and not operator:
        from aria_core.aria_worker_queue import enqueue_worker_task, sync_pending_local_tasks_to_md

        sync_pending_local_tasks_to_md()

        slug = re.sub(r"[^a-z0-9]+", "-", clean.lower())[:24].strip("-") or "idea"
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        worker_task_id = f"community-fb-{slug}-{day}"
        handle_bit = f" (@{handle.strip().lstrip('@')})" if handle.strip() else ""
        problem = (
            f"Feedback communauté site{handle_bit} — score {score}/100\n\n{clean}"
        )
        try:
            wr = await enqueue_worker_task(
                task_id=worker_task_id,
                title=clean[:120],
                source="community_feedback",
                problem=problem,
                action=(
                    "Évaluer l'idée communauté ; si alignée vision ZHC/Vanguard, préparer workflow "
                    "ouvrier (spec courte, fichiers cibles, critères d'acceptation) puis implémenter."
                ),
                priority="normal",
                repos=("aria-vanguard", "aria-sandbox"),
                acceptance=(
                    "Décision documentée (ship / defer / decline)",
                    "Si ship : PR ou commit + JOURNAL.md",
                ),
                context=f"visitor={visitor_id or 'anon'} source={source}",
                lang=lang,
            )
            worker_status = wr.get("status")
            queued = wr.get("status") in _WORKER_OK
            append_memory(
                "community",
                f"[feedback→worker] {feedback_id} score={score} task={worker_task_id} status={worker_status}",
            )
        except Exception as exc:
            logger.warning("community feedback worker queue failed: %s", exc)
            verdict = "noted"
            queued = False

    if verdict != "spam":
        try:
            x_tweet = await maybe_tweet_community_feedback(
                clean,
                handle=handle,
                visitor_id=visitor_id,
                feedback_id=feedback_id,
                score=score,
                lang=lang,
            )
        except Exception as exc:
            logger.warning("community feedback X side-effect failed: %s", exc)
            x_tweet = {"status": "error", "reason": str(exc)[:120]}

    record = {
        "id": feedback_id,
        "text": clean,
        "handle": (handle or "").strip()[:64],
        "visitor_id": (visitor_id or "").strip()[:64],
        "source": source,
        "at": datetime.now(timezone.utc).isoformat(),
        "score": score,
        "verdict": verdict,
        "queued": queued,
        "worker_task_id": worker_task_id,
        "worker_status": worker_status,
        "x_tweet_status": x_tweet.get("status"),
        "queue_threshold": threshold,
    }
    _append_record(record)
    append_memory("community", f"[feedback] {feedback_id} score={score} verdict={verdict}")

    return {
        "ok": True,
        "id": feedback_id,
        "score": score,
        "verdict": verdict,
        "queued": queued,
        "worker_task_id": worker_task_id,
        "worker_status": worker_status,
        "x_tweet": x_tweet,
        "queue_threshold": threshold,
        "reply": format_public_reply(
            verdict=verdict,
            queued=queued,
            lang=lang,
            x_status=str(x_tweet.get("status") or ""),
        ),
    }