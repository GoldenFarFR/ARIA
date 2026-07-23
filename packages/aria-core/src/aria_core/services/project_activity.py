"""Project activity sensors — "is the project shipping or stalling?"

After an investment, ARIA monitors whether the project stays ALIVE: last
GitHub commit, last social post. A project that ships supports the thesis; a
project that vanishes breaks it (see thesis_journal.assess_project_activity,
which judges the delays).

Read-only, injectable network (testable offline). Graceful degradation: any
unavailability -> ``None`` (unknown delay), never an exception, never a
made-up verdict.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
# Extracts owner/repo from a GitHub URL (https, with or without .git, extra path ignored).
_GH_RE = re.compile(r"github\.com[/:]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#]|$)")
# ORGANIZATION-only URL (no repo in the path) -- e.g. "github.com/crynux-network"
# with no second segment. Distinct from _GH_RE (which requires owner AND repo).
_GH_ORG_ONLY_RE = re.compile(r"^https?://github\.com/([A-Za-z0-9_.-]+)/?(?:[?#]|$)")
_GH_RESERVED_NAMES = {"", "sponsors", "orgs", "features", "about"}


def parse_github_repo(url: str | None) -> tuple[str, str] | None:
    """(owner, repo) from a GitHub URL, or None if it isn't one."""
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
    """AUTHENTICATED GET to api.github.com (GITHUB_TOKEN, already existing
    elsewhere in the project -- verified able to read any third-party public
    repo/organization, 5000/h rate limit). Reserved for `resolve_github_repo`
    (organization resolution, one more network call per link not resolved
    directly) -- this module's historical functions
    (`github_days_since_commit`/`fetch_github_diligence_snapshot`) deliberately
    stay anonymous (60/h, a distinct sobriety gap, noted separately, out of
    scope for this fix)."""
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
    """True if the URL is a RECOGNIZABLE GitHub link -- a specific repo
    (`parse_github_repo`) OR an organization alone (`resolve_github_repo` will
    know how to resolve it) -- used to FILTER which link among several
    project_links is a candidate, before any network resolution (synchronous,
    zero cost)."""
    if not url:
        return False
    if parse_github_repo(url) is not None:
        return True
    return bool(_GH_ORG_ONLY_RE.match(str(url).strip()))


# "Special" organization repos -- config/templates, never real development
# (found by checking: `.github` comes up as a candidate on a real case, with
# no connection to the project's substance).
_GH_SPECIAL_REPO_NAMES = {".github", "profile"}
# Number of repos loaded to pick the MOST POPULAR among them -- the GitHub API
# does NOT support `sort=stars` on this endpoint (verified against the
# official docs: only created/updated/pushed/full_name are accepted), so the
# popularity sort must happen CLIENT-SIDE on a batch loaded by `pushed`
# (zero extra network call, a single GET with a wider per_page).
_ORG_REPOS_CANDIDATE_POOL = 20


async def resolve_github_repo(url: str | None, *, fetch=None) -> tuple[str, str] | None:
    """(owner, repo) -- ALSO resolves an ORGANIZATION-only URL (no specific
    repo in the URL, a common pattern for a multi-repo project: contract +
    frontend + docs + node in separate repos of the same organization) to its
    most RELEVANT repo, rather than silently failing like `parse_github_repo`
    alone.

    Found while checking a REAL case (23/07, CNX/crynux-network): the project
    declares `github.com/crynux-network` (organization) -- `parse_github_repo`
    returns `None` while this organization has a `crynux-node` repo with 272
    stars (real, active development, never detected before this fix).

    2-step selection, verified necessary by a second real test on this same
    case (the naive "just the most recently pushed" sort brought back a
    side repo with 1 star; on `crynux-network-dao`, it brought back
    `.github`, an organization config repo, not development):
      1. Loads a batch of repos sorted by `pushed` (real recent activity),
         excludes FORKS (not the project's original code) and special repos
         (`.github`/`profile`).
      2. Among the remaining candidates, picks the one with the MOST STARS
         (a popularity/relevance proxy -- the API doesn't support server-side
         sort by stars, verified against the official docs); ties broken by
         most recently pushed (already at the top of the sort).

    The direct case (specific repo in the URL) is ALWAYS tried first, zero
    network cost or behavior change on the dominant case -- the organization
    call is only attempted as a fallback."""
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
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocking
        logger.info("project_activity: organization resolution %s failed (%s)", org, exc)
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
    # 23/07 -- "several unrelated repos" safeguard (explicit operator
    # concern): an organization can host genuinely DISTINCT projects
    # (collective, multi-product foundation) where the most-starred repo
    # isn't necessarily the one for the TARGETED project. The only signal
    # available without extra plumbing (no project name/symbol passed to
    # this function): the organization's name itself is almost always
    # derived from the project's name -- repos that SHARE a stem with it
    # (e.g. org "crynux-network" -> "crynux") are preferred over apparently
    # unrelated repos before applying the star sort. If NO candidate shares
    # a stem (an organization with inconsistent naming), the full batch is
    # used as-is -- an honestly documented limit, never a filter that would
    # wrongly empty the selection.
    org_stems = {t for t in re.split(r"[^a-z0-9]+", org.lower()) if len(t) >= 3}
    on_theme = [
        repo for repo in candidates
        if org_stems and any(stem in str(repo["name"]).lower() for stem in org_stems)
    ]
    pool = on_theme or candidates
    # Stable sort (Python): on a star tie, the original order (by `pushed`,
    # most recent first) breaks it -- never an arbitrary choice.
    best = max(pool, key=lambda repo: repo.get("stargazers_count") or 0)
    return org, str(best["name"])


def _days_since(iso_ts: str, *, now: datetime | None = None) -> int | None:
    """Number of whole days since an ISO8601 (UTC) timestamp. None if unreadable."""
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
    """Days since the repo's last commit (default branch). None if undeterminable.

    ``fetch(path)`` (injectable) must return the GitHub API JSON. In prod, the
    default queries ``api.github.com`` (public, no key — moderate throttle).
    Best-effort.
    """
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    fetch = fetch or _fetch_github
    try:
        data = await fetch(f"/repos/{owner}/{repo}/commits?per_page=1")
    except Exception as exc:  # noqa: BLE001 — never blocking
        logger.info("project_activity: github %s/%s failed (%s)", owner, repo, exc)
        return None
    # The API returns a list; read commit.committer.date of the most recent one.
    try:
        commit = data[0]["commit"]
        ts = commit.get("committer", {}).get("date") or commit.get("author", {}).get("date")
    except (KeyError, IndexError, TypeError):
        return None
    return _days_since(ts, now=now) if ts else None


async def _fetch_github(path: str) -> object | None:
    """GET api.github.com (graceful degradation). Prod default (VPS, network allowed)."""
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
    """GitHub snapshot for pre-investment product diligence (description,
    stars, open issues, freshness, age) -- same client/doctrine as
    ``github_days_since_commit`` (best-effort, never blocking, ``fetch``
    injectable for tests). Distinct from it: reads `/repos/{owner}/{repo}`
    (metadata), not `/commits` (history).

    19/07 -- single CANONICAL source for GitHub content, consumed by
    ``conviction_research.py`` (via ``_describe_other_known_link``) -- itself
    the single canonical source of conviction diligence for BOTH pipelines
    (``vc_analysis.py``/`/vc` AND ``momentum_entry.py``, #134). A duplicate
    (``services/github_verify.py``) had been built by mistake the same
    evening before this pre-existing module was discovered; removed in favor
    of this one rather than leaving two clients on the same endpoint
    ("never duplicate an existing client" doctrine)."""
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    fetch = fetch or _fetch_github
    try:
        data = await fetch(f"/repos/{owner}/{repo}")
    except Exception as exc:  # noqa: BLE001 -- never blocking
        logger.info("project_activity: github diligence %s/%s failed (%s)", owner, repo, exc)
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
    """Short line for LLM context (19/07) -- never a fabricated fact: degrades
    honestly if ``snapshot`` is ``None`` (repo not found OR verification
    unavailable -- ``fetch_github_diligence_snapshot`` doesn't distinguish the
    two, unlike the old, removed ``github_verify.py`` -- accepted as a minor
    simplification in favor of a single client already proven/consumed by
    3 other modules, rather than extending a shared client for a single nuance)."""
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
