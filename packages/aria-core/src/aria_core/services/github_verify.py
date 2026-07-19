"""Client de lecture seule GitHub -- vérifie le CONTENU d'un dépôt déclaré par un
projet (19/07, retour opérateur), pas seulement son existence.

Contexte : `conviction_research.py` sait depuis peu qu'un lien GitHub est déclaré
(``known_links``, DexScreener) mais ne vérifiait jamais ce qu'il y a derrière --
un dépôt vide/gonflé de commits factices est indiscernable d'un vrai projet actif
tant qu'on ne regarde que l'URL. `api.github.com/repos/{owner}/{repo}` (vérifié en
direct, 19/07, aucune clé requise, schéma réel confirmé) donne un signal de
légitimité concret et gratuit : âge du dépôt, fraîcheur de la dernière activité,
étoiles, si c'est un fork/dépôt archivé.

Rate limit observé (en-têtes réels) : 60 requêtes/heure PAR IP sans authentification
-- suffisant pour ce cas d'usage (appelé uniquement post-gate, sur un achat déjà
confirmé, jamais sur la masse de candidats scannés). Volontairement PAS authentifié
avec le `GITHUB_TOKEN` existant (scopé `GoldenFarFR/ARIA` pour la gestion d'issues/PR,
#114 -- ne pas le détourner pour un usage sans rapport). À reconsidérer si ce plafond
s'avère insuffisant en pratique."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com/repos"
_REPO_URL_RE = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+)", re.IGNORECASE)
_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class GitHubRepoVerification:
    available: bool
    exists: bool | None = None  # None = jamais résolu (panne réseau, pas une vraie absence)
    age_days: int | None = None
    days_since_last_push: int | None = None
    stargazers: int | None = None
    is_fork: bool | None = None
    is_archived: bool | None = None
    error: str | None = None


def _parse_owner_repo(url: str) -> tuple[str, str] | None:
    m = _REPO_URL_RE.search(url or "")
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return owner, repo


def _days_since(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - dt).days)


async def verify_repo(url: str) -> GitHubRepoVerification:
    """Vérifie un dépôt GitHub déclaré. Jamais une exception qui remonte -- dégrade
    en ``available=False`` sur toute panne réseau (jamais confondu avec
    ``exists=False``, un vrai 404 confirmé)."""
    parsed = _parse_owner_repo(url)
    if parsed is None:
        return GitHubRepoVerification(available=False, error="URL GitHub illisible")
    owner, repo = parsed

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            res = await client.get(
                f"{_API_BASE}/{owner}/{repo}", headers={"Accept": "application/vnd.github+json"},
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("github_verify: requête échouée pour %s/%s (%s)", owner, repo, exc)
        return GitHubRepoVerification(available=False, error=f"requête échouée ({exc})")

    if res.status_code == 404:
        return GitHubRepoVerification(available=True, exists=False)
    if res.status_code != 200:
        return GitHubRepoVerification(available=False, error=f"HTTP {res.status_code}")

    try:
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        return GitHubRepoVerification(available=False, error=f"réponse illisible ({exc})")

    return GitHubRepoVerification(
        available=True, exists=True,
        age_days=_days_since(data.get("created_at")),
        days_since_last_push=_days_since(data.get("pushed_at")),
        stargazers=data.get("stargazers_count"),
        is_fork=bool(data.get("fork")),
        is_archived=bool(data.get("archived")),
    )


def format_repo_verification(v: GitHubRepoVerification) -> str:
    """Ligne courte destinée au contexte LLM -- jamais un fait fabriqué : dégrade
    honnêtement si indisponible/inexistant, jamais un chiffre inventé."""
    if not v.available:
        return "vérification indisponible"
    if v.exists is False:
        return "dépôt introuvable (lien mort ou jamais publié -- signal négatif)"
    parts = []
    if v.age_days is not None:
        parts.append(f"créé il y a {v.age_days}j")
    if v.days_since_last_push is not None:
        parts.append(f"dernière activité il y a {v.days_since_last_push}j")
    if v.stargazers is not None:
        parts.append(f"{v.stargazers} étoiles")
    if v.is_fork:
        parts.append("fork (pas un dépôt original)")
    if v.is_archived:
        parts.append("ARCHIVÉ")
    return ", ".join(parts) if parts else "dépôt trouvé, détails indisponibles"
