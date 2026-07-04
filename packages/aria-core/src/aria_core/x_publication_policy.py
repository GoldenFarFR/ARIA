"""X publication policy — cost-aware conditions before any paid API write."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aria_core.runtime import settings
from aria_core.paths import memory_dir

POLICY_PATH = memory_dir() / "x_publication_policy.md"
LEDGER_PATH = memory_dir() / "x_api_ledger.json"

# Official X pay-per-use (USD) — docs.x.com 2026
X_COST_USD = {
    "tweet": 0.015,
    "tweet_with_url": 0.200,
    "reply": 0.015,
    "like": 0.015,
    "dm": 0.015,
    "follow": 0.015,
    "read_post": 0.005,
    "read_owned": 0.001,
}


def _load_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.exists():
        return {"posts": [], "estimated_spend_usd": 0.0}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"posts": [], "estimated_spend_usd": 0.0}


def _save_ledger(data: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _posts_today(ledger: dict[str, Any]) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    out = []
    for p in ledger.get("posts", []):
        try:
            if _parse_ts(p["at"]).date() == today:
                out.append(p)
        except Exception:
            continue
    return out


def _last_post_at(ledger: dict[str, Any]) -> datetime | None:
    times = []
    for p in ledger.get("posts", []):
        try:
            times.append(_parse_ts(p["at"]))
        except Exception:
            continue
    return max(times) if times else None


def _contains_url(text: str) -> bool:
    return bool(re.search(r"https?://|www\.", text, re.I))


# Contenu interdit — word boundaries pour éviter faux positifs (@GoldenFarFR ≠ nfa)
_CONTENT_VIOLATIONS: tuple[tuple[str, str], ...] = (
    (r"\$\d", "prix/hype ($ chiffre)"),
    (r"\bpump\b", "pump"),
    (r"\bmoon\b", "moon"),
    (r"\b100x\b", "100x"),
    (r"\bguaranteed\b", "guaranteed"),
    (r"financial advice", "conseil financier"),
    (r"buy now", "buy now"),
    (r"\bnfa\b", "NFA"),
)

_FR_TWEET_MARKERS = re.compile(
    r"[éèêàùçîôâëïü]|"
    r"\b(je suis|j'explore|qu'attendez|nouvelle agente|chez vanguard|"
    r"approfondir|brouillon|publier|merci|bonjour|salut)\b",
    re.IGNORECASE,
)


def _estimate_tweet_cost(text: str) -> float:
    return X_COST_USD["tweet_with_url"] if _contains_url(text) else X_COST_USD["tweet"]


def _monthly_spend_cap_usd() -> float:
    """Operational cap Aria enforces (min of spend cap and account budget)."""
    return min(settings.x_monthly_spend_cap_usd, settings.x_monthly_budget_usd)


def _daily_quota_active() -> bool:
    return settings.x_max_posts_per_day > 0


def _min_gap_active() -> bool:
    return settings.x_min_hours_between_posts > 0


def _format_daily_limit(lang: str = "fr") -> str:
    if not _daily_quota_active():
        return "illimité" if lang == "fr" else "unlimited"
    return str(settings.x_max_posts_per_day)


def _format_min_gap(lang: str = "fr") -> str:
    if not _min_gap_active():
        return "aucun" if lang == "fr" else "none"
    return f"{settings.x_min_hours_between_posts}h"


def _check_monthly_spend(ledger: dict[str, Any], cost: float, *, force: bool) -> tuple[bool, str]:
    cap = _monthly_spend_cap_usd()
    projected = float(ledger.get("estimated_spend_usd", 0)) + cost
    if projected > cap and not force:
        return (
            False,
            f"Plafond dépense mensuelle atteint ({cap:.2f} $ — "
            f"abonnement {settings.x_monthly_budget_usd:.2f} $, "
            f"cap Aria {settings.x_monthly_spend_cap_usd:.2f} $).",
        )
    return True, "OK"


def check_tweet_content(text: str, *, allow_urls: bool = False) -> tuple[bool, str]:
    """Règles contenu uniquement (pas quota / budget). Retourne (ok, raison)."""
    body = text.strip()
    if not body:
        return False, "tweet vide"
    if len(body) > 280:
        return False, f"trop long ({len(body)}/280)"
    if settings.x_block_urls_in_posts and _contains_url(body) and not allow_urls:
        return False, "URL interdite (~0,20 $/tweet — retire le lien)"
    hits: list[str] = []
    for pattern, label in _CONTENT_VIOLATIONS:
        if re.search(pattern, body, re.IGNORECASE):
            hits.append(label)
    if hits:
        return False, "hype prix / conseil financier interdit (" + ", ".join(hits) + ")"
    if tweet_has_french(body):
        return False, "français détecté — politique X : anglais uniquement sur @Aria_ZHC"
    return True, "OK"


def tweet_has_french(text: str) -> bool:
    return bool(_FR_TWEET_MARKERS.search(text.strip()))


def policy_rules_for_llm(lang: str = "en") -> str:
    """Bloc compact injecté dans les prompts tweet (compose, comms)."""
    if lang == "fr":
        return (
            "POLITIQUE X @Aria_ZHC (obligatoire — non négociable) :\n"
            "- Anglais uniquement sur X (pas de tweet français).\n"
            "- Pas d'URL (coût 0,20 $ vs 0,015 $).\n"
            "- Interdit : $X, pump, moon, 100x, buy now, NFA, conseil financier, hype prix.\n"
            "- Pas de quota journalier ni d'intervalle min (budget API seulement).\n"
            "- Ton : building in public, faits vérifiés, Vanguard ZHC — pas de shill.\n"
            "- @mentions : alias +veille / @holding OK (expansion auto)."
        )
    return (
        "X POLICY @Aria_ZHC (mandatory):\n"
        "- English only on X (no French tweets).\n"
        "- No URLs ($0.20 vs $0.015 per tweet).\n"
        "- Forbidden: $X, pump, moon, 100x, buy now, NFA, financial advice, price hype.\n"
        "- No daily quota or min gap (API spend cap only).\n"
        "- Tone: building in public, verified facts, Vanguard ZHC — no shill.\n"
        "- Voice: natural human prose — no AI/agent/CAO character (see x_voice rules).\n"
        "- @mentions: +veille / @holding aliases OK (auto-expanded)."
    )


def format_draft_policy_footer(text: str, lang: str = "fr") -> str:
    """Statut politique X pour brouillon opérateur (compose Telegram)."""
    content_ok, content_reason = check_tweet_content(text)
    if not content_ok:
        return f"⚠️ Politique X — bloqué : {content_reason}"
    allowed, rate_reason, cost = check_tweet_allowed(text)
    if allowed:
        return f"✅ Politique X — contenu OK · coût estimé ~{cost:.3f} $"
    if "Contenu bloqué" in rate_reason:
        return f"⚠️ Politique X — bloqué : {content_reason}"
    return f"✅ Contenu OK · publication : {rate_reason}"


def policy_summary(lang: str = "fr") -> str:
    from aria_core.identity import official_x_at

    handle = official_x_at()
    if lang == "fr":
        return (
            f"Politique X {handle} (pay-per-use)\n\n"
            f"- Tweets max / jour : {_format_daily_limit('fr')}\n"
            f"- Intervalle min : {_format_min_gap('fr')}\n"
            f"- Abonnement X : {settings.x_monthly_budget_usd:.2f} $/mois\n"
            f"- Plafond dépense Aria : {settings.x_monthly_spend_cap_usd:.2f} $/mois (actif)\n"
            f"- URLs dans tweets : {'bloquées' if settings.x_block_urls_in_posts else 'autorisées'} "
            f"(0,20 $ vs 0,015 $)\n"
            f"- Likes API : {'on' if settings.x_allow_likes else 'off'} (~0,015 $)\n"
            f"- Réponses API : {'on' if settings.x_allow_replies else 'off'} (~0,015 $)\n"
            f"- DM API : {'on' if settings.x_allow_dms else 'off'} (~0,015 $)\n"
            f"- Langue : anglais sur X · faits vérifiés · pas de hype prix\n"
            f"- Fichier : data/memory/x_publication_policy.md"
        )
    return (
        f"X policy {handle} — max {_format_daily_limit('en')}/day, "
        f"{_format_min_gap('en')} gap, "
        f"${settings.x_monthly_spend_cap_usd:.2f}/mo spend cap "
        f"(${settings.x_monthly_budget_usd:.2f} subscription)."
    )


def check_reply_allowed(text: str, *, force: bool = False) -> tuple[bool, str, float]:
    """Guard mention replies — content + spend cap; no min-hours gap (conversational)."""
    allowed, reason, cost = check_engagement_allowed("reply", force=force)
    if not allowed:
        return False, reason, cost
    if not settings.x_post_enabled and not force:
        return False, "Publication X désactivée (X_POST_ENABLED=false).", 0.0
    body = text.strip()
    if not body:
        return False, "Reply vide.", 0.0
    if len(body) > 280:
        return False, f"Reply trop longue ({len(body)}/280).", cost
    content_ok, content_reason = check_tweet_content(body)
    if not content_ok:
        return False, f"Contenu bloqué ({content_reason}).", cost
    ledger = _load_ledger()
    ok, spend_reason = _check_monthly_spend(ledger, cost, force=force)
    if not ok:
        return False, spend_reason, cost
    return True, "OK", cost


def check_engagement_allowed(action: str, *, force: bool = False) -> tuple[bool, str, float]:
    """Guard likes, replies, DMs — each ~0,015 $; disabled by default."""
    kind = action.strip().lower()
    flags = {
        "like": settings.x_allow_likes,
        "reply": settings.x_allow_replies,
        "dm": settings.x_allow_dms,
    }
    if kind not in flags:
        return False, f"Action inconnue : {action}", 0.0
    env_keys = {"like": "X_ALLOW_LIKES", "reply": "X_ALLOW_REPLIES", "dm": "X_ALLOW_DMS"}
    if not flags[kind] and not force:
        return (
            False,
            f"{kind.capitalize()} API désactivé ({env_keys[kind]}=false). Coût ~0,015 $ sans ROI clair.",
            X_COST_USD[kind],
        )
    cost = X_COST_USD[kind]
    ledger = _load_ledger()
    ok, reason = _check_monthly_spend(ledger, cost, force=force)
    if not ok:
        return False, reason, cost
    return True, "OK", cost


def record_engagement(action: str, *, target: str = "", cost_usd: float | None = None) -> None:
    kind = action.strip().lower()
    cost = cost_usd if cost_usd is not None else X_COST_USD.get(kind, 0.015)
    ledger = _load_ledger()
    ledger.setdefault("posts", []).append({
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "cost_usd": cost,
        "target": target[:80],
    })
    ledger["estimated_spend_usd"] = round(
        float(ledger.get("estimated_spend_usd", 0)) + cost, 4,
    )
    _save_ledger(ledger)


def check_tweet_allowed(
    text: str,
    *,
    force: bool = False,
    skip_rate_gap: bool = False,
) -> tuple[bool, str, float]:
    """Return (allowed, reason, estimated_cost_usd)."""
    if not settings.x_post_enabled and not force:
        return False, "Publication X désactivée (X_POST_ENABLED=false).", 0.0

    from aria_core.x_text import X_TWEET_MAX_CHARS, weighted_tweet_length

    body = text.strip()
    if not body:
        return False, "Tweet vide.", 0.0
    weight = weighted_tweet_length(body)
    if weight > X_TWEET_MAX_CHARS:
        return False, f"Tweet trop long ({weight}/{X_TWEET_MAX_CHARS} poids X).", 0.0

    cost = _estimate_tweet_cost(body)
    if settings.x_block_urls_in_posts and _contains_url(body):
        return (
            False,
            "URL détectée — coût ~0,20 $/tweet. Retire le lien ou désactive X_BLOCK_URLS_IN_POSTS.",
            cost,
        )

    content_ok, content_reason = check_tweet_content(body)
    if not content_ok:
        return False, f"Contenu bloqué ({content_reason}).", cost

    ledger = _load_ledger()
    today_posts = _posts_today(ledger)
    if _daily_quota_active() and len(today_posts) >= settings.x_max_posts_per_day and not force:
        return (
            False,
            f"Quota journalier atteint ({settings.x_max_posts_per_day} tweets/jour).",
            cost,
        )

    last = _last_post_at(ledger)
    if _min_gap_active() and last and not force and not skip_rate_gap:
        gap = datetime.now(timezone.utc) - last
        need = timedelta(hours=settings.x_min_hours_between_posts)
        if gap < need:
            wait = need - gap
            hrs = wait.total_seconds() / 3600
            return False, f"Attendre encore {hrs:.1f}h entre deux publications.", cost

    ok, reason = _check_monthly_spend(ledger, cost, force=force)
    if not ok:
        return False, reason, cost

    return True, "OK", cost


def list_published_tweets(*, limit: int = 20) -> list[dict[str, Any]]:
    """Tweets publiés via l'API X — texte complet quand disponible."""
    ledger = _load_ledger()
    posts = [p for p in ledger.get("posts", []) if p.get("kind") == "tweet"]
    posts.sort(key=lambda p: p.get("at", ""), reverse=True)
    out: list[dict[str, Any]] = []
    for p in posts[:limit]:
        text = (p.get("text") or p.get("preview") or "").strip()
        out.append({
            "at": p.get("at", ""),
            "text": text[:280],
            "tweet_id": p.get("tweet_id", ""),
            "preview": (p.get("preview") or text[:120]),
        })
    return out


def record_tweet_posted(text: str, *, tweet_id: str = "", cost_usd: float | None = None) -> None:
    ledger = _load_ledger()
    cost = cost_usd if cost_usd is not None else _estimate_tweet_cost(text)
    posted_at = datetime.now(timezone.utc).isoformat()
    ledger.setdefault("posts", []).append({
        "at": posted_at,
        "kind": "tweet",
        "cost_usd": cost,
        "chars": len(text),
        "has_url": _contains_url(text),
        "tweet_id": tweet_id,
        "text": text[:280],
        "preview": text[:120],
    })
    ledger["estimated_spend_usd"] = round(
        float(ledger.get("estimated_spend_usd", 0)) + cost, 4,
    )
    _save_ledger(ledger)
    try:
        from aria_core.tweet_compose_workflow import record_published_intel

        record_published_intel(text=text[:280], tweet_id=tweet_id, at=posted_at)
    except Exception:
        pass


def ledger_summary() -> dict[str, Any]:
    ledger = _load_ledger()
    return {
        "posts_today": len(_posts_today(ledger)),
        "estimated_spend_usd": ledger.get("estimated_spend_usd", 0),
        "spend_cap_usd": _monthly_spend_cap_usd(),
        "subscription_usd": settings.x_monthly_budget_usd,
        "aria_spend_cap_usd": settings.x_monthly_spend_cap_usd,
        "last_post": _last_post_at(ledger).isoformat() if _last_post_at(ledger) else None,
        "costs_usd": X_COST_USD,
    }


def ensure_policy_file() -> None:
    if POLICY_PATH.exists():
        return
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    POLICY_PATH.write_text(
        "# Politique de publication X — @Aria_ZHC\n\n"
        "## Coûts API (pay-per-use, juin 2026)\n"
        "| Action | Coût approx. |\n"
        "|--------|-------------|\n"
        "| Tweet (sans URL) | 0,015 $ |\n"
        "| Tweet avec URL | 0,20 $ |\n"
        "| Like | 0,015 $ |\n"
        "| Réponse | 0,015 $ |\n"
        "| DM | 0,015 $ |\n"
        "| Follow | 0,015 $ |\n"
        "| Lire un post (autrui) | 0,005 $ |\n"
        "| Lire tes propres posts | 0,001 $ |\n\n"
        "**Oui — likes, réponses et DM coûtent chacun ~0,015 $ via l'API.**\n"
        "Ils restent désactivés par défaut (pas de ROI vs tweets originaux).\n\n"
        "## Quotas & budget (.env)\n"
        "- `X_MAX_POSTS_PER_DAY=0` — 0 = illimité (pas de quota journalier)\n"
        "- `X_MIN_HOURS_BETWEEN_POSTS=0` — 0 = pas d'intervalle min\n"
        "- `X_MONTHLY_BUDGET_USD=5` — abonnement / crédits console X\n"
        "- `X_MONTHLY_SPEND_CAP_USD=1` — plafond dépense Aria (pour l'instant)\n"
        "- `X_BLOCK_URLS_IN_POSTS=true` — jamais d'URL dans tweets auto (0,20 $/tweet)\n"
        "- `X_ALLOW_LIKES/REPLIES/DMS=false`\n\n"
        "## Contenu autorisé\n"
        "- Anglais uniquement sur X\n"
        "- Building in public : Aria Vanguard ZHC, DEXPulse, autonomie Aria\n"
        "- Faits vérifiés, signaux watchlist, briefs entraînement (sans conseil financier)\n"
        "- Ton sobre, fondateur — pas de shill\n\n"
        "## Contenu interdit (bloqué auto)\n"
        "- Hype prix ($X, pump, moon, 100x, buy now)\n"
        "- Conseil financier ou promesses de gain\n"
        "- URLs dans tweets auto (tous workflows ACP inclus)\n\n"
        "## Commandes Telegram\n"
        "- `/x status` — connexion + dépense\n"
        "- `/x policy` — cette politique\n"
        "- `/x post <texte>` — publie si conditions OK\n",
        encoding="utf-8",
    )