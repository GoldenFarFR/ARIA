"""Signal "substance X" -- crédibilité réelle d'un compte X (23/07, enrichi le
même jour après avoir trouvé un fournisseur de profil complet).

Historique de la diligence (norme du projet : vérifier avant de coder) :
- Twit.sh (x402, déjà en prod, réservé à ``conviction_research.py`` pour la
  cadence de publication) ne renvoie que des métriques PAR TWEET, jamais de
  profil (pas de followers/following/date d'inscription).
- Tavily ``extract`` sur la page profil (vérifié réel sur @crynuxio) rend le
  JS et expose "Joined October 2023" -- mais PAS les compteurs
  followers/following (absents du texte extrait, confirmé par grep). La
  régularité de publication via Tavily a aussi été ÉVALUÉE puis ÉCARTÉE :
  les liens de statut horodatés d'une page profil extraite ne reflètent PAS
  le fil chronologique récent (tweets "highlights" les plus engagés, testé
  deux fois -- extract_depth basic ET advanced, même résultat).
- **TwitterAPI.io** (``services/twitterapi_io.py``, diligencé le 23/07 :
  ScamAdviser "legit and safe", Trustpilot positif, skill MCP officielle,
  0,18$/1000 profils) comble le trou du profil : un seul appel donne
  followers/following/date de création, vérifié en conditions réelles sur
  @crynuxio (3676 followers, 242 following, créé le 27/10/2023).
- **Activité/engagement** (même jour, demande opérateur explicite après un
  tableau confirmant que twit.sh les fournit AUSSI) : réutiliser twit.sh ici
  dupliquerait un appel payant déjà fait par ``conviction_research.py`` sur
  le MÊME compte -- TwitterAPI.io a un endpoint dédié équivalent
  (``/twitter/user/last_tweets``, vérifié réel : date + engagement par
  tweet), zéro nouveau fournisseur, zéro couplage entre les deux modules.
- **Xquik** (fournisseur x402 natif listé dans awesome-x402) évalué et
  ÉCARTÉ : son défi de paiement pointe vers le réseau "Tempo" (Stripe/
  Paradigm, chainId 4217), PAS Base/USDC -- incompatible avec l'infra CDP
  existante, chantier disproportionné pour ce signal consultatif.

Architecture en cascade : TwitterAPI.io est tenté EN PREMIER (si
``TWITTERAPI_IO_KEY`` configurée) -- profil PUIS derniers tweets (best-effort,
une panne sur les tweets seuls dégrade juste ces 2 axes, jamais tout le
signal). Si le profil est absent/indisponible, repli sur Tavily ``extract``
(âge du compte SEUL, comme avant ce correctif) -- dégradation douce, jamais
bloquant."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_JOIN_DATE_RE = re.compile(r"Joined\s+([A-Za-z]+\s+\d{4})")

# Seuils de ratio following/followers -- repris de la formule externe proposée
# par l'opérateur (indépendants des axes déjà écartés faute de données, donc
# réutilisables tels quels maintenant que le ratio est réellement mesurable).
_RATIO_EXCELLENT_MAX = 1.0
_RATIO_GOOD_MAX = 1.5
_RATIO_ACCEPTABLE_MAX = 3.0

# Fenêtre de régularité -- même esprit que le design initial pensé pour
# Tavily (jamais utilisé, la source était peu fiable) ; réutilisable
# maintenant que TwitterAPI.io donne un vrai fil chronologique daté.
_REGULARITY_LOOKBACK_DAYS = 90
_REGULARITY_LOOKBACK_WEEKS = _REGULARITY_LOOKBACK_DAYS // 7
_MIN_ACTIVE_WEEKS_FOR_FULL_SCORE = 8  # sur 12 semaines

# Seuils d'engagement normalisé (moyenne (likes+replies+retweets+quotes)/tweet
# / followers) -- calibrage approximatif, non issu d'une étude externe, à
# ajuster avec plus de données réelles dans le temps (cf. cas CNX observé :
# ~1,3-1,9% sur un compte déjà jugé positif par ailleurs).
_ENGAGEMENT_EXCELLENT_MIN = 0.01
_ENGAGEMENT_GOOD_MIN = 0.003
_ENGAGEMENT_WEAK_MIN = 0.0005


@dataclass
class XSubstanceFacts:
    available: bool = False
    error: str | None = None
    account_age_days: int | None = None
    followers: int | None = None
    following: int | None = None
    active_weeks_recent: int | None = None
    tweets_analyzed: int = 0
    avg_engagement_rate: float | None = None
    source: str = "none"  # "twitterapi_io" | "tavily_fallback"


@dataclass
class XSubstanceVerdict:
    signal: str  # "positive" | "neutral" | "weak" | "unknown"
    score: float | None
    points: list[str] = field(default_factory=list)


def _parse_join_date(text: str, *, now: datetime) -> datetime | None:
    m = _JOIN_DATE_RE.search(text or "")
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1).strip(), "%B %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt if dt <= now else None


async def _default_twitterapi_fetch(handle: str):
    from aria_core.services.twitterapi_io import fetch_user_profile

    return await fetch_user_profile(handle)


async def _default_twitterapi_tweets(handle: str):
    from aria_core.services.twitterapi_io import fetch_last_tweets

    return await fetch_last_tweets(handle)


async def _default_extract(handle: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.extract([f"https://x.com/{handle}"], caller="x_substance")


def _activity_from_tweets(tweets: list, *, followers: int, now: datetime) -> tuple[int, float | None]:
    """(semaines actives récentes, taux d'engagement moyen normalisé par
    followers). ``None`` pour le taux si followers<=0 (jamais une division
    par zéro ni un chiffre fabriqué)."""
    cutoff = now - timedelta(days=_REGULARITY_LOOKBACK_DAYS)
    recent_weeks = {(now - t.created_at).days // 7 for t in tweets if cutoff <= t.created_at <= now}

    if followers <= 0 or not tweets:
        return len(recent_weeks), None

    total_engagement = sum(t.like_count + t.reply_count + t.retweet_count + t.quote_count for t in tweets)
    avg_rate = (total_engagement / len(tweets)) / followers
    return len(recent_weeks), avg_rate


async def gather_x_substance_facts(
    x_handle: str | None, *, twitterapi_fn=None, tweets_fn=None, extract_fn=None, now: datetime | None = None,
) -> XSubstanceFacts:
    """Récolte best-effort, jamais bloquant. Fonctions injectables pour les
    tests (même patron que ``fetch=`` de ``github_substance.py``).
    TwitterAPI.io tenté EN PREMIER (profil puis tweets), Tavily en repli
    (âge seul, jamais d'activité/engagement sans compteurs de followers)."""
    handle = (x_handle or "").lstrip("@").strip()
    if not handle:
        return XSubstanceFacts(available=False, error="handle X manquant")

    now = now or datetime.now(timezone.utc)

    twitterapi_fn = twitterapi_fn or _default_twitterapi_fetch
    try:
        profile = await twitterapi_fn(handle)
    except Exception:  # noqa: BLE001 -- jamais bloquant
        profile = None

    if profile is not None:
        active_weeks: int | None = None
        avg_engagement: float | None = None
        tweets_analyzed = 0

        tweets_fn_ = tweets_fn or _default_twitterapi_tweets
        try:
            tweets = await tweets_fn_(handle)
        except Exception:  # noqa: BLE001 -- dégrade seulement ces 2 axes, jamais tout le signal
            tweets = None

        if tweets:
            tweets_analyzed = len(tweets)
            active_weeks, avg_engagement = _activity_from_tweets(
                tweets, followers=profile.followers, now=now,
            )

        return XSubstanceFacts(
            available=True,
            account_age_days=(now - profile.created_at).days,
            followers=profile.followers,
            following=profile.following,
            active_weeks_recent=active_weeks,
            tweets_analyzed=tweets_analyzed,
            avg_engagement_rate=avg_engagement,
            source="twitterapi_io",
        )

    extract_fn = extract_fn or _default_extract
    try:
        result = await extract_fn(handle)
    except Exception as exc:  # noqa: BLE001 -- jamais bloquant
        return XSubstanceFacts(available=False, error=str(exc))

    if not result.available or not result.pages:
        return XSubstanceFacts(available=False, error=result.error or "profil introuvable")

    text = result.pages[0].raw_content
    join_date = _parse_join_date(text, now=now)

    if join_date is None:
        return XSubstanceFacts(available=False, error="date d'inscription introuvable sur le profil")

    return XSubstanceFacts(
        available=True, account_age_days=(now - join_date).days, source="tavily_fallback",
    )


def _account_age_score(account_age_days: int) -> float:
    months = account_age_days / 30.0
    if months >= 18:
        return 100.0
    if months >= 12:
        return 70.0
    if months >= 6:
        return 40.0
    return 0.0


def _ratio_score(followers: int, following: int) -> float:
    if followers <= 0:
        return 0.0
    ratio = following / followers
    if ratio <= _RATIO_EXCELLENT_MAX:
        return 100.0
    if ratio <= _RATIO_GOOD_MAX:
        return 70.0
    if ratio <= _RATIO_ACCEPTABLE_MAX:
        return 30.0
    return 0.0


def _regularity_score(active_weeks: int) -> float:
    return min(1.0, active_weeks / _MIN_ACTIVE_WEEKS_FOR_FULL_SCORE) * 100.0


def _engagement_score(rate: float) -> float:
    if rate >= _ENGAGEMENT_EXCELLENT_MIN:
        return 100.0
    if rate >= _ENGAGEMENT_GOOD_MIN:
        return 70.0
    if rate >= _ENGAGEMENT_WEAK_MIN:
        return 40.0
    return 0.0


def judge_x_substance(facts: XSubstanceFacts) -> XSubstanceVerdict:
    """Jugement pur, aucun appel réseau. Jusqu'à 4 critères (âge, ratio,
    régularité, engagement) quand TwitterAPI.io a tout fourni ; dégrade
    honnêtement (poids redistribués) selon ce qui manque réellement -- jamais
    un axe fabriqué. Réseau/alignement thématique de la proposition externe
    restent hors de portée (analyse sémantique du fil réel)."""
    if not facts.available or facts.account_age_days is None:
        return XSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "indisponible"])

    age_score = _account_age_score(facts.account_age_days)
    months = int(facts.account_age_days / 30.0)

    has_ratio = facts.followers is not None and facts.following is not None
    has_regularity = facts.active_weeks_recent is not None
    has_engagement = facts.avg_engagement_rate is not None

    weighted = [(0.30 if (has_ratio or has_regularity or has_engagement) else 1.0, age_score)]
    detail_parts = [f"compte âgé de {months} mois"]

    if has_ratio:
        ratio_score = _ratio_score(facts.followers, facts.following)
        weighted.append((0.25, ratio_score))
        ratio_txt = f"{facts.following}/{facts.followers}" if facts.followers else "n/a"
        detail_parts.append(f"{facts.followers} abonnés / {facts.following} abonnements (ratio {ratio_txt})")

    if has_regularity:
        regularity_score = _regularity_score(facts.active_weeks_recent)
        weighted.append((0.25, regularity_score))
        detail_parts.append(
            f"actif {facts.active_weeks_recent}/{_REGULARITY_LOOKBACK_WEEKS} semaines récentes"
        )

    if has_engagement:
        engagement_score = _engagement_score(facts.avg_engagement_rate)
        weighted.append((0.20, engagement_score))
        detail_parts.append(f"engagement moyen {facts.avg_engagement_rate * 100:.2f}% des abonnés/tweet")

    total_weight = sum(w for w, _ in weighted)
    score = sum(w * s for w, s in weighted) / total_weight

    if not has_ratio and not has_regularity and not has_engagement:
        detail_parts.append("followers/following/activité indisponibles, repli sur l'âge seul")

    points = [f"substance {score:.1f}/100 -- " + ", ".join(detail_parts)]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return XSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
