"""Signal "substance X" -- crédibilité réelle d'un compte X (23/07, révisé le
même jour après avoir trouvé un fournisseur de profil complet).

Historique de la diligence (norme du projet : vérifier avant de coder) :
- Twit.sh (x402, déjà en prod) ne renvoie que des métriques PAR TWEET, jamais
  de profil (pas de followers/following/date d'inscription).
- Tavily ``extract`` sur la page profil (vérifié réel sur @crynuxio) rend le
  JS et expose "Joined October 2023" -- mais PAS les compteurs
  followers/following (absents du texte extrait, confirmé par grep).
- La régularité de publication via Tavily a été ÉVALUÉE puis ÉCARTÉE : les
  liens de statut horodatés d'une page profil extraite ne reflètent PAS le
  fil chronologique récent (tweets "highlights" les plus engagés, testé deux
  fois -- voir historique de ce module).
- **TwitterAPI.io** (``services/twitterapi_io.py``, diligencé le 23/07 :
  ScamAdviser "legit and safe", Trustpilot positif, skill MCP officielle,
  0,18$/1000 profils) comble enfin le vrai trou : un seul appel donne
  followers/following/date de création, vérifié en conditions réelles sur
  @crynuxio (3676 followers, 242 following, créé le 27/10/2023 -- cohérent
  avec Tavily et avec une capture réelle du profil).
- **Xquik** (fournisseur x402 natif listé dans awesome-x402) évalué et
  ÉCARTÉ : son défi de paiement pointe vers le réseau "Tempo" (Stripe/
  Paradigm, chainId 4217), PAS Base/USDC -- incompatible avec l'infra CDP
  existante (``x402_cdp_signer.py``), chantier disproportionné pour ce signal
  consultatif. Piste notée, pas construite.

Architecture en cascade : TwitterAPI.io est tenté EN PREMIER (si
``TWITTERAPI_IO_KEY`` configurée) -- un seul appel donne les 3 faits d'un
coup. Si absent/indisponible, repli sur Tavily ``extract`` (âge du compte
SEUL, comme avant ce correctif) -- dégradation douce, jamais bloquant."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_JOIN_DATE_RE = re.compile(r"Joined\s+([A-Za-z]+\s+\d{4})")

# Seuils de ratio following/followers -- repris de la formule externe proposée
# par l'opérateur (indépendants des axes déjà écartés faute de données, donc
# réutilisables tels quels maintenant que le ratio est réellement mesurable).
_RATIO_EXCELLENT_MAX = 1.0
_RATIO_GOOD_MAX = 1.5
_RATIO_ACCEPTABLE_MAX = 3.0


@dataclass
class XSubstanceFacts:
    available: bool = False
    error: str | None = None
    account_age_days: int | None = None
    followers: int | None = None
    following: int | None = None
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


async def _default_extract(handle: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.extract([f"https://x.com/{handle}"], caller="x_substance")


async def gather_x_substance_facts(
    x_handle: str | None, *, twitterapi_fn=None, extract_fn=None, now: datetime | None = None,
) -> XSubstanceFacts:
    """Récolte best-effort, jamais bloquant. ``twitterapi_fn``/``extract_fn``
    injectables pour les tests (même patron que ``fetch=`` de
    ``github_substance.py``). TwitterAPI.io tenté EN PREMIER (source la plus
    riche), Tavily en repli (âge seul)."""
    handle = (x_handle or "").lstrip("@").strip()
    if not handle:
        return XSubstanceFacts(available=False, error="handle X manquant")

    now = now or datetime.now(timezone.utc)

    twitterapi_fn = twitterapi_fn or _default_twitterapi_fetch
    try:
        profile = await twitterapi_fn(handle)
    except Exception as exc:  # noqa: BLE001 -- jamais bloquant
        profile = None

    if profile is not None:
        return XSubstanceFacts(
            available=True,
            account_age_days=(now - profile.created_at).days,
            followers=profile.followers,
            following=profile.following,
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


def judge_x_substance(facts: XSubstanceFacts) -> XSubstanceVerdict:
    """Jugement pur, aucun appel réseau. 2 critères (âge du compte + ratio
    following/followers) quand TwitterAPI.io a répondu ; 1 seul (âge) en repli
    Tavily -- toujours honnête sur ce qui manque plutôt que de fabriquer un
    axe. Les axes qualité de contenu/réseau/alignement de la proposition
    externe restent écartés (nécessiteraient une analyse sémantique du fil
    réel, hors de portée de ce signal déterministe)."""
    if not facts.available or facts.account_age_days is None:
        return XSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "indisponible"])

    age_score = _account_age_score(facts.account_age_days)
    months = int(facts.account_age_days / 30.0)

    if facts.followers is not None and facts.following is not None:
        ratio_score = _ratio_score(facts.followers, facts.following)
        score = 0.5 * age_score + 0.5 * ratio_score
        ratio_txt = f"{facts.following}/{facts.followers}" if facts.followers else "n/a"
        points = [
            f"substance {score:.1f}/100 -- compte âgé de {months} mois, "
            f"{facts.followers} abonnés / {facts.following} abonnements (ratio {ratio_txt})",
        ]
    else:
        score = age_score
        points = [
            f"substance {score:.1f}/100 -- compte âgé de {months} mois "
            f"(followers/following indisponibles, repli sur l'âge seul)",
        ]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return XSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
