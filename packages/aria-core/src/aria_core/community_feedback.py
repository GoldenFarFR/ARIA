"""Feedback communauté site — collecte, triage, délégation ouvrier si pertinent."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory
from aria_core.paths import data_dir

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


def personal_take_on_feedback(text: str, *, lang: str = "fr") -> str:
    """Réponse courte à l'avis — heuristique locale, ancrée sur le contenu."""
    t = (text or "").strip()
    if _FEEDBACK_WIDGET_RE.search(t) and _BUILD_TOGETHER_RE.search(t):
        if lang == "fr":
            return (
                "La boîte à avis sur le site sert exactement à ça — "
                "on priorise Vanguard avec des retours comme le tien."
            )
        return (
            "The on-site feedback box is for notes like this — "
            "we'll keep shaping Vanguard from what you wrote."
        )
    if _FEEDBACK_WIDGET_RE.search(t):
        if lang == "fr":
            return "Content que tu utilises déjà le canal avis — ça guide la roadmap Vanguard."
        return "Glad you're using the feedback lane on the site — it steers what we ship next."
    if _ACTION_RE.search(t) or (_PRODUCT_RE.search(t) and len(t) > 40):
        if lang == "fr":
            return "Piste produit claire — je la garde pour le prochain cycle ZHC."
        return "Clear product signal — queued for the next ZHC ship cycle."
    if _CONTACT_RE.search(t):
        if lang == "fr":
            return "On reste en contact — la commu ZHC avance avec des retours comme celui-ci."
        return "We'll stay in touch — the ZHC community moves on notes like this."
    if _COMPLIMENT_RE.search(t) and _PRODUCT_RE.search(t):
        if lang == "fr":
            return "Content du retour sur le site — on continue d'itérer Vanguard en public."
        return "Good to hear on the site itself — we'll keep iterating Vanguard in public."
    if lang == "fr":
        return "Ton retour précis oriente ce qu'on ship sur Vanguard."
    return "Your specific note steers what we ship on Vanguard."


async def compose_personal_reply_to_feedback(
    text_en: str,
    *,
    original: str = "",
) -> str:
    """Réponse ARIA ciblée sur l'avis (LLM si dispo, sinon heuristique)."""
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.x_publication_policy import policy_rules_for_llm
    from aria_core.x_voice import human_voice_rules_for_llm, strip_obvious_ai_phrases

    source = (text_en or original or "").strip()
    orig = (original or text_en or "").strip()
    if not source:
        return personal_take_on_feedback(original, lang="en")

    # Réponses déterministes quand l'avis mentionne le canal feedback (évite LLM générique).
    if _FEEDBACK_WIDGET_RE.search(orig):
        return personal_take_on_feedback(orig, lang="en")

    if is_llm_configured():
        system = (
            "You reply on @Aria_ZHC to ONE community feedback left on the Vanguard site.\n"
            "Write ONE short sentence (max 110 chars) reacting to THEIR exact words.\n"
            "Reference what they actually said (feedback form, site, building together, "
            "feature idea, compliment, contact request).\n"
            "No generic thank-you. Forbidden: 'thanks for sharing', 'love the energy', "
            "'thank you for your support', 'supporting our growth'.\n"
            f"{policy_rules_for_llm('en')}\n"
            f"{human_voice_rules_for_llm('en')}\n"
            "English only. No quotes. No @mentions."
        )
        try:
            composed = await chat_with_context(
                f"Community feedback to react to:\n{source[:600]}",
                system,
                temperature=0.45,
                max_tokens=90,
            )
            line = strip_obvious_ai_phrases((composed or "").strip().strip('"').strip("'"))
            if line and len(line) >= 12 and not _GENERIC_REPLY_RE.search(line):
                return line[:110]
        except Exception as exc:
            logger.warning("feedback personal reply LLM failed: %s", exc)

    return personal_take_on_feedback(original or text_en, lang="en")


def _short_excerpt(text: str, max_len: int = 72) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


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
        "- Fix spelling and grammar only (e.g. enssemble→ensemble, typos).\n"
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
    Citation tweet : sens exact, orthographe corrigée, anglais pour @Aria_ZHC.
    Returns (quote_en, was_transformed).
    """
    clean = (text or "").strip()
    if not clean:
        return "", False

    polished = await _llm_polish_quote_for_x(clean)
    if polished and len(polished) >= 3:
        return polished[:800], polished.strip() != clean

    if _is_likely_english(clean):
        fixed = await _llm_fix_english_typos(clean)
        if fixed and len(fixed) >= 3:
            return fixed[:800], fixed.strip() != clean
        return clean, False

    for translator in (_google_translate_to_english,):
        try:
            result = await translator(clean)
            if result and len(result) >= 3:
                return result[:800], True
        except Exception as exc:
            logger.warning("feedback translate %s failed: %s", translator.__name__, exc)

    return clean, False


async def translate_to_english_for_x(text: str) -> tuple[str, bool]:
    """Alias — prépare la citation X (traduction + corrections)."""
    return await prepare_feedback_quote_for_x(text)


def _truncate_quote(text: str, max_len: int) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


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
            "text": text[:2000],
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
        if quote.strip():
            out.append(quote.strip())
    return out


def build_merged_feedback_tweet(
    quotes_en: list[str],
    *,
    handle: str = "",
    personal: str = "",
) -> str:
    """Tweet unique — plusieurs avis fusionnés."""
    if len(quotes_en) <= 1:
        return build_feedback_thanks_tweet(
            quotes_en[0] if quotes_en else "",
            handle=handle,
            personal=personal,
        )
    h = (handle or "").strip().lstrip("@")
    header = f"@{h} · Vanguard ({len(quotes_en)} notes)" if h else f"Vanguard feedback ({len(quotes_en)} notes)"
    merged = " / ".join(f'"{_truncate_quote(q, 90)}"' for q in quotes_en[:3])
    personal = re.sub(r"\s+", " ", (personal or "").strip())
    tweet = f"{header}\n\n{merged}\n\n→ {personal}"
    if len(tweet) <= 280:
        return tweet
    short = " / ".join(f'"{_truncate_quote(q, 50)}"' for q in quotes_en[:2])
    return f"{header}\n\n{short}\n\n→ {_truncate_quote(personal, 55)}"[:280]


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
    personal = await compose_personal_reply_to_feedback(
        quotes_en[0],
        original=merged_original,
    )
    tweet = build_merged_feedback_tweet(
        quotes_en,
        handle=str(bucket.get("handle") or ""),
        personal=personal,
    )
    from aria_core.x_voice import strip_obvious_ai_phrases

    tweet = strip_obvious_ai_phrases(tweet)
    ids = ",".join(str(it.get("id") or "")[:12] for it in items[:3])

    from aria_core.gateway.x_twitter import post_tweet

    operator_publish = is_trusted_operator_publish(str(bucket.get("handle") or ""))
    _exchange, note = await post_tweet(
        tweet,
        approval_id=f"community_fb_batch:{ids}",
        skip_rate_gap=True,
        force=operator_publish,
    )
    posted = "Publié sur X" in note or "x.com/" in note
    _clear_x_bucket(key)
    append_memory(
        "community",
        f"[feedback→x batch] {ids} posted={posted} n={len(items)} {tweet[:100]}",
    )
    return {
        "status": "posted" if posted else "blocked",
        "note": note,
        "draft": tweet,
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
    """Tweet commu — citation fidèle de l'avis + réponse ARIA ciblée."""
    quote_full = re.sub(r"\s+", " ", (text or "").strip())
    personal = re.sub(r"\s+", " ", (personal or "").strip())
    h = (handle or "").strip().lstrip("@")
    header = f"@{h} · Vanguard site" if h else "Vanguard site feedback"

    for quote_len in range(len(quote_full), 48, -8):
        quote = _truncate_quote(quote_full, quote_len)
        for reply_len in range(len(personal), 40, -10):
            reply = _truncate_quote(personal, reply_len)
            tweet = f'{header}\n\n"{quote}"\n\n→ {reply}'
            if len(tweet) <= 280:
                return tweet

    quote = _truncate_quote(quote_full, 80)
    reply = _truncate_quote(personal, 60)
    return f'{header}\n\n"{quote}"\n\n→ {reply}'[:280]


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
    clean = (text or "").strip()[:2000]
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