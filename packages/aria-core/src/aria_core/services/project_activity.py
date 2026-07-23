"""Capteurs d'activité projet — « le projet livre-t-il ou stagne-t-il ? »

Après un investissement, ARIA surveille si le projet reste VIVANT : dernier commit
GitHub, dernier post social. Un projet qui livre soutient la thèse ; un projet qui
disparaît la casse (cf. thesis_journal.assess_project_activity qui juge les délais).

Lecture seule, réseau injectable (testable offline). Dégradation gracieuse : toute
indisponibilité -> ``None`` (délai inconnu), jamais d'exception, jamais un verdict inventé.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
# Extrait owner/repo d'une URL GitHub (https, avec ou sans .git, chemin en plus ignoré).
_GH_RE = re.compile(r"github\.com[/:]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#]|$)")
# URL d'ORGANISATION seule (aucun repo dans le chemin) -- ex. "github.com/crynux-network"
# sans second segment. Distinct de _GH_RE (qui exige owner ET repo).
_GH_ORG_ONLY_RE = re.compile(r"^https?://github\.com/([A-Za-z0-9_.-]+)/?(?:[?#]|$)")
_GH_RESERVED_NAMES = {"", "sponsors", "orgs", "features", "about"}


def parse_github_repo(url: str | None) -> tuple[str, str] | None:
    """(owner, repo) depuis une URL GitHub, ou None si ce n'en est pas une."""
    if not url:
        return None
    m = _GH_RE.search(str(url))
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if owner.lower() in _GH_RESERVED_NAMES:
        return None
    return owner, repo


async def _fetch_github_authenticated(path: str) -> object | None:
    """GET api.github.com AUTHENTIFIÉ (GITHUB_TOKEN, déjà existant ailleurs dans
    le projet -- vérifié capable de lire n'importe quel dépôt/organisation
    publique tiers, rate limit 5000/h). Réservé à `resolve_github_repo`
    (résolution d'organisation, un appel réseau de plus par lien non résolu
    directement) -- les fonctions historiques de ce module (`github_days_since_commit`/
    `fetch_github_diligence_snapshot`) restent volontairement en anonyme (60/h,
    gap de sobriété distinct, noté séparément, hors scope de ce correctif)."""
    import os

    import httpx

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{_GITHUB_API}{path}", headers=headers)
    if r.status_code != 200:
        return None
    return r.json()


def is_github_link(url: str | None) -> bool:
    """True si l'URL est un lien GitHub RECONNAISSABLE -- repo précis (`parse_github_repo`)
    OU organisation seule (`resolve_github_repo` saura la résoudre) -- utilisé pour
    FILTRER quel lien parmi plusieurs project_links est candidat, avant toute
    résolution réseau (synchrone, zéro coût)."""
    if not url:
        return False
    if parse_github_repo(url) is not None:
        return True
    return bool(_GH_ORG_ONLY_RE.match(str(url).strip()))


# Repos "spéciaux" d'organisation -- config/templates, jamais du développement
# réel (trouvé en vérifiant : `.github` remonte comme candidat sur un cas réel,
# sans rapport avec la substance du projet).
_GH_SPECIAL_REPO_NAMES = {".github", "profile"}
# Nombre de repos chargés pour choisir le PLUS POPULAIRE parmi eux -- l'API
# GitHub ne supporte PAS `sort=stars` sur cet endpoint (vérifié contre la doc
# officielle : seuls created/updated/pushed/full_name sont acceptés), donc le
# tri par popularité doit se faire CÔTÉ CLIENT sur un lot chargé par `pushed`
# (zéro appel réseau supplémentaire, un seul GET avec per_page plus large).
_ORG_REPOS_CANDIDATE_POOL = 20


async def resolve_github_repo(url: str | None, *, fetch=None) -> tuple[str, str] | None:
    """(owner, repo) -- résout AUSSI une URL d'ORGANISATION seule (pas de repo
    précis dans l'URL, pratique courante pour un projet multi-repos : contrat +
    frontend + doc + node dans des dépôts séparés d'une même organisation) vers
    son repo le plus PERTINENT, plutôt que d'échouer silencieusement comme
    `parse_github_repo` seul.

    Trouvé en vérifiant un cas RÉEL (23/07, CNX/crynux-network) : le projet
    déclare `github.com/crynux-network` (organisation) -- `parse_github_repo`
    renvoie `None` alors que cette organisation a un repo `crynux-node` à 272
    étoiles (développement réel et actif, jamais détecté avant ce correctif).

    Sélection en 2 temps, vérifiée nécessaire par un second test réel sur ce
    même cas (le tri naïf "juste le plus récemment poussé" ramenait un repo
    annexe à 1 étoile ; sur `crynux-network-dao`, il ramenait `.github`, un
    repo de config d'organisation, pas du développement) :
      1. Charge un lot de repos triés par `pushed` (activité récente réelle),
         exclut les FORKS (pas le code original du projet) et les repos
         spéciaux (`.github`/`profile`).
      2. Parmi les candidats restants, choisit celui avec le PLUS D'ÉTOILES
         (proxy de popularité/pertinence -- l'API ne supporte pas de tri
         par étoiles côté serveur, vérifié contre la doc officielle) ; en cas
         d'égalité, le plus récemment poussé (déjà en tête du tri).

    Le cas direct (repo précis dans l'URL) reste TOUJOURS essayé en premier,
    zéro coût réseau ni changement de comportement sur le cas dominant --
    l'appel organisation n'est tenté qu'en repli."""
    direct = parse_github_repo(url)
    if direct:
        return direct
    m = _GH_ORG_ONLY_RE.match(str(url or "").strip())
    if not m:
        return None
    org = m.group(1)
    if org.lower() in _GH_RESERVED_NAMES:
        return None
    fetch = fetch or _fetch_github_authenticated
    try:
        data = await fetch(f"/orgs/{org}/repos?sort=pushed&per_page={_ORG_REPOS_CANDIDATE_POOL}")
    except Exception as exc:  # noqa: BLE001 — best-effort, jamais bloquant
        logger.info("project_activity: résolution organisation %s échouée (%s)", org, exc)
        return None
    if not isinstance(data, list) or not data:
        return None

    candidates = [
        repo for repo in data
        if isinstance(repo, dict)
        and repo.get("name")
        and str(repo["name"]).lower() not in _GH_SPECIAL_REPO_NAMES
        and not repo.get("fork")
        and not repo.get("archived")
    ]
    if not candidates:
        return None
    # 23/07 -- garde-fou "plusieurs repos sans rapport les uns avec les autres"
    # (préoccupation opérateur explicite) : une organisation peut héberger des
    # projets réellement DISTINCTS (collectif, fondation multi-produits) où le
    # plus étoilé n'est pas forcément celui du projet CIBLÉ. Seul signal
    # disponible sans plomberie supplémentaire (aucun nom de projet/symbole
    # transmis à cette fonction) : le nom de l'organisation lui-même est
    # quasi toujours dérivé du nom du projet -- les repos qui en PARTAGENT une
    # racine (ex. org "crynux-network" -> "crynux") sont préférés aux repos
    # sans rapport apparent avant d'appliquer le tri par étoiles. Si AUCUN
    # candidat ne partage de racine (organisation au nommage incohérent), le
    # lot complet reste utilisé tel quel -- limite honnête documentée, jamais
    # un filtre qui viderait la sélection à tort.
    org_stems = {t for t in re.split(r"[^a-z0-9]+", org.lower()) if len(t) >= 3}
    on_theme = [
        repo for repo in candidates
        if org_stems and any(stem in str(repo["name"]).lower() for stem in org_stems)
    ]
    pool = on_theme or candidates
    # Stable sort (Python) : à égalité d'étoiles, l'ordre d'origine (par
    # `pushed`, le plus récent en tête) départage -- jamais un choix arbitraire.
    best = max(pool, key=lambda repo: repo.get("stargazers_count") or 0)
    return org, str(best["name"])


def _days_since(iso_ts: str, *, now: datetime | None = None) -> int | None:
    """Nombre de jours entiers depuis un timestamp ISO8601 (UTC). None si illisible."""
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return max(0, (ref - ts).days)


async def github_days_since_commit(
    repo_url: str | None, *, fetch=None, now: datetime | None = None
) -> int | None:
    """Jours depuis le dernier commit du dépôt (branche par défaut). None si indéterminable.

    ``fetch(path)`` (injectable) doit renvoyer le JSON de l'API GitHub. En prod, le défaut
    interroge ``api.github.com`` (public, sans clé — throttle modéré). Best-effort.
    """
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    fetch = fetch or _fetch_github
    try:
        data = await fetch(f"/repos/{owner}/{repo}/commits?per_page=1")
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        logger.info("project_activity: github %s/%s échoué (%s)", owner, repo, exc)
        return None
    # L'API renvoie une liste ; on lit commit.committer.date du plus récent.
    try:
        commit = data[0]["commit"]
        ts = commit.get("committer", {}).get("date") or commit.get("author", {}).get("date")
    except (KeyError, IndexError, TypeError):
        return None
    return _days_since(ts, now=now) if ts else None


async def _fetch_github(path: str) -> object | None:
    """GET api.github.com (dégradation gracieuse). Défaut prod (VPS, réseau autorisé)."""
    import httpx

    url = f"{_GITHUB_API}{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers={"Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        return None
    return r.json()


async def fetch_github_diligence_snapshot(
    repo_url: str | None, *, fetch=None, now: datetime | None = None
) -> dict | None:
    """Instantané GitHub pour la diligence produit pré-investissement (description,
    étoiles, issues ouvertes, fraîcheur, âge) -- même client/doctrine que
    ``github_days_since_commit`` (best-effort, jamais bloquant, ``fetch`` injectable
    pour les tests). Distinct de ce dernier : lit `/repos/{owner}/{repo}` (métadonnées),
    pas `/commits` (historique).

    19/07 -- source CANONIQUE unique pour le contenu GitHub, consommée par
    ``conviction_research.py`` (via ``_describe_other_known_link``) -- lui-même
    la source canonique unique de diligence de conviction pour LES DEUX
    pipelines (``vc_analysis.py``/`/vc` ET ``momentum_entry.py``, #134). Un
    doublon (``services/github_verify.py``) avait été construit par erreur le
    même soir avant que ce module pré-existant ne soit découvert ; retiré au
    profit de celui-ci plutôt que de laisser deux clients sur le même
    endpoint (doctrine « jamais dupliquer un client existant »)."""
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    fetch = fetch or _fetch_github
    try:
        data = await fetch(f"/repos/{owner}/{repo}")
    except Exception as exc:  # noqa: BLE001 -- jamais bloquant
        logger.info("project_activity: diligence github %s/%s échouée (%s)", owner, repo, exc)
        return None
    if not isinstance(data, dict) or "id" not in data:
        return None
    pushed_at = data.get("pushed_at") or data.get("updated_at")
    created_at = data.get("created_at")
    return {
        "description": str(data.get("description") or "")[:200],
        "stars": data.get("stargazers_count"),
        "open_issues": data.get("open_issues_count"),
        "days_since_push": _days_since(pushed_at, now=now) if pushed_at else None,
        "age_days": _days_since(created_at, now=now) if created_at else None,
        "archived": bool(data.get("archived")),
        "fork": bool(data.get("fork")),
    }


def format_github_diligence(snapshot: dict | None) -> str:
    """Ligne courte pour contexte LLM (19/07) -- jamais un fait fabriqué : dégrade
    honnêtement si ``snapshot`` est ``None`` (dépôt introuvable OU vérification
    indisponible -- ``fetch_github_diligence_snapshot`` ne distingue pas les deux,
    contrairement à l'ancien ``github_verify.py`` retiré -- accepté comme
    simplification mineure au profit d'un client unique déjà éprouvé/consommé par
    3 autres modules, plutôt que d'étendre un client partagé pour une seule nuance)."""
    if not snapshot:
        return "vérification indisponible ou dépôt introuvable"
    parts = []
    if snapshot.get("age_days") is not None:
        parts.append(f"créé il y a {snapshot['age_days']}j")
    if snapshot.get("days_since_push") is not None:
        parts.append(f"dernière activité il y a {snapshot['days_since_push']}j")
    if snapshot.get("stars") is not None:
        parts.append(f"{snapshot['stars']} étoiles")
    if snapshot.get("open_issues") is not None:
        parts.append(f"{snapshot['open_issues']} issues ouvertes")
    if snapshot.get("fork"):
        parts.append("fork (pas un dépôt original)")
    if snapshot.get("archived"):
        parts.append("ARCHIVÉ")
    return ", ".join(parts) if parts else "dépôt trouvé, détails indisponibles"
