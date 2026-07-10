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


def parse_github_repo(url: str | None) -> tuple[str, str] | None:
    """(owner, repo) depuis une URL GitHub, ou None si ce n'en est pas une."""
    if not url:
        return None
    m = _GH_RE.search(str(url))
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if owner.lower() in {"", "sponsors", "orgs", "features", "about"}:
        return None
    return owner, repo


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


def github_url_from_links(links: list[dict] | None) -> str | None:
    """Trouve l'URL GitHub officielle dans les liens projet extraits au scan."""
    for link in links or []:
        url = str((link or {}).get("url") or "")
        if "github.com" in url.lower() and parse_github_repo(url):
            return url
    return None


# Domaines sociaux/dev connus -- jamais le "site" du projet lui-même, donc exclus
# de la recherche du site officiel ci-dessous.
_NON_WEBSITE_DOMAINS = (
    "github.com", "twitter.com", "x.com", "t.me", "telegram.me",
    "discord.gg", "discord.com", "medium.com", "youtube.com", "youtu.be",
    "instagram.com", "tiktok.com", "reddit.com", "warpcast.com", "farcaster.xyz",
)


def website_url_from_links(links: list[dict] | None) -> str | None:
    """Premier lien qui n'est PAS un réseau social/dev connu -- heuristique simple
    pour retrouver le site officiel du projet parmi les liens DexScreener
    (`info.websites` + `info.socials`, jamais distingués autrement en amont)."""
    for link in links or []:
        url = str((link or {}).get("url") or "").strip()
        if not url:
            continue
        low = url.lower()
        if any(domain in low for domain in _NON_WEBSITE_DOMAINS):
            continue
        return url
    return None


async def fetch_github_diligence_snapshot(
    repo_url: str | None, *, fetch=None, now: datetime | None = None
) -> dict | None:
    """Instantané GitHub pour la diligence produit pré-investissement (description,
    étoiles, issues ouvertes, fraîcheur) -- même client/doctrine que
    ``github_days_since_commit`` (best-effort, jamais bloquant, ``fetch`` injectable
    pour les tests). Distinct de ce dernier : lit `/repos/{owner}/{repo}` (métadonnées),
    pas `/commits` (historique)."""
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
    return {
        "description": str(data.get("description") or "")[:200],
        "stars": data.get("stargazers_count"),
        "open_issues": data.get("open_issues_count"),
        "days_since_push": _days_since(pushed_at, now=now) if pushed_at else None,
        "archived": bool(data.get("archived")),
        "fork": bool(data.get("fork")),
    }
