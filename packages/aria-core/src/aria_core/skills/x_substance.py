"""Signal "substance X" -- crédibilité réelle d'un compte X, réduite
HONNÊTEMENT à ce qu'ARIA peut RÉELLEMENT observer (23/07). Formule externe
proposée par l'opérateur évaluée AVANT tout code (norme du projet) : la
majorité de ses axes sont structurellement hors de portée aujourd'hui --

- Âge du compte / ratio following-followers / détection d'avatar par défaut :
  aucune source ne fournit un PROFIL (compteurs de followers/following, date
  d'inscription visible nulle part chez twit.sh -- vérifié réel, ses deux
  endpoints ne renvoient que des métriques PAR TWEET). Tavily ``extract`` sur
  la page profil (vérifié en réel sur @crynuxio, 23/07) rend bien le JS et
  expose "Joined October 2023" -- mais PAS les compteurs followers/following
  (absents du texte extrait, confirmé par grep sur le contenu réel).
- Ratio d'engagement normalisé par followers : impossible sans le nombre de
  followers (ci-dessus).
- Réseau/mentions de qualité : nécessiterait un graphe d'interactions complet,
  hors de portée sans un coût réseau disproportionné.

Ce qui RESTE mesurable et robuste, retenu ici : l'âge du compte SEUL, via
``extract`` sur la page profil ("Joined October 2023", confirmé exact contre
une capture réelle du profil @crynuxio -- 34 mois, cohérent). La régularité de
publication a été ÉVALUÉE puis ÉCARTÉE (23/07, testée deux fois en conditions
réelles, ``extract_depth`` basic ET advanced) : les liens de statut horodatés
visibles dans une page profil extraite par Tavily ne reflètent PAS le fil
chronologique récent -- ils couvrent Nov 2024 à Jan 2025, alors que le compte
a réellement posté le 21/07/2026 (confirmé via ``twitsh.fetch_user_tweets``,
qui renvoie lui un vrai fil chronologique daté). Tavily semble exposer un
sous-ensemble "highlights" (les tweets les plus vus/engagés : 21K/54K/81K vues
observées), pas les posts récents -- une régularité calculée dessus aurait
signalé un compte "inactif" alors qu'il postait la veille, un faux négatif
concret. La régularité de publication reste donc couverte par le mécanisme
EXISTANT de ``conviction_research.py`` (API X officielle -> repli twit.sh,
inchangé) ; le contenu précis de chaque tweet n'est pas parsé ici non plus
(même raison de fragilité du format brut scrapé) ; le buzz communautaire
(mentions par des tiers) reste couvert séparément par ``conviction_research.py``
via ``tavily_client.search``, qui a sa propre valeur (indexation large) mais
n'est PAS une source fiable pour des statistiques SPÉCIFIQUES à ce compte
(vérifié réel : une recherche ``from:handle`` mélange les propres posts du
compte ET les mentions par des tiers, sans les distinguer)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_JOIN_DATE_RE = re.compile(r"Joined\s+([A-Za-z]+\s+\d{4})")


@dataclass
class XSubstanceFacts:
    available: bool = False
    error: str | None = None
    account_age_days: int | None = None


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


async def _default_extract(handle: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.extract([f"https://x.com/{handle}"], caller="x_substance")


async def gather_x_substance_facts(
    x_handle: str | None, *, extract_fn=None, now: datetime | None = None,
) -> XSubstanceFacts:
    """Récolte best-effort, jamais bloquant. ``extract_fn`` injectable pour les
    tests (même patron que ``fetch=`` de ``github_substance.py``)."""
    handle = (x_handle or "").lstrip("@").strip()
    if not handle:
        return XSubstanceFacts(available=False, error="handle X manquant")

    now = now or datetime.now(timezone.utc)
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

    return XSubstanceFacts(available=True, account_age_days=(now - join_date).days)


def judge_x_substance(facts: XSubstanceFacts) -> XSubstanceVerdict:
    """Jugement pur, aucun appel réseau. 1 SEUL critère (âge du compte) --
    volontairement réduit (voir docstring du module) par rapport à la
    proposition externe à 6 critères : les 5 autres exigent des données
    (followers/following, avatar, régularité récente, réseau de mentions)
    qu'ARIA n'a pas de façon fiable aujourd'hui. Signal FAIBLE par construction
    (un seul axe) -- informationnel, jamais un véto."""
    if not facts.available or facts.account_age_days is None:
        return XSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "indisponible"])

    months = facts.account_age_days / 30.0
    if months >= 18:
        score = 100.0
    elif months >= 12:
        score = 70.0
    elif months >= 6:
        score = 40.0
    else:
        score = 0.0

    points = [
        f"substance {score:.1f}/100 -- compte âgé de {int(months)} mois "
        f"(seul axe mesurable de façon fiable aujourd'hui, cf. limites documentées)",
    ]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return XSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
