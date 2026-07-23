""""GitHub substance" signal — judges the REAL quality of development,
not its frequency.

Weak point found by the stress-test (Codex Part 11): `project_activity.py` judges
FRESHNESS (days since the last commit), never its SUBSTANCE — a project
that spams cosmetic commits (README, formatting, one character changed)
would pass the same signal as real technical development.

Design evaluated against an external proposal, technically verified before
implementing (07/23):
- **Real cost confirmed by a direct call**: the commit-list endpoint
  (`GET /repos/{o}/{r}/commits`) NEVER returns the stats (lines
  added/removed) nor the changed files — a SEPARATE call per
  commit (`GET /repos/{o}/{r}/commits/{sha}`) is required for that. Capped at
  `_MAX_COMMITS_ANALYZED` (sample of the most recent), never an
  exhaustive analysis of a potentially long history.
- **Authenticated** via `GITHUB_TOKEN` (already existing, never a new secret)
  — confirmed by a real call that it can read ANY third-party PUBLIC repo
  (not just `GoldenFarFR/ARIA`), authenticated rate limit 5000/h (vs 60/h
  anonymous, which `project_activity._fetch_github` uses today — a distinct
  frugality gap, noted but out of scope for this signal).
- **Never an LLM** to "read" the code (too expensive/slow for a
  systematic signal called on every `/vc` scan) — deterministic heuristics
  only (file extension, diff size, temporal spread).

Purely consultative signal (same doctrine as the other signals from this
work: insider_wallets/deployer_history/sybil_cluster), never a veto.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"

# Analysis window (days) and cap on commits analyzed IN DETAIL (each
# commit costs a separate network call, see the doctrine above).
_WINDOW_DAYS = 90
_MAX_COMMITS_ANALYZED = 30

# Extensions considered real technical CODE (vs cosmetic).
_TECHNICAL_EXTENSIONS = {
    ".sol", ".vy", ".rs", ".go", ".py", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".c", ".cpp", ".h", ".rb", ".php", ".sh",
}
# Filename fragments considered purely cosmetic.
_COSMETIC_NAME_FRAGMENTS = ("readme", "license", "changelog", ".md", ".txt", ".rst")
_TEST_PATH_FRAGMENTS = ("test", "spec", "__tests__")
# Generic commit messages, with no descriptive substance.
_GENERIC_MESSAGES = {"update", "fix", "wip", "commit", "changes", "misc", "."}
_MIN_MESSAGE_LENGTH = 10

# Below this number of analyzed TECHNICAL commits, the sample is too small
# to judge honestly (score -> unavailable, never a fabricated score).
_MIN_TECHNICAL_COMMITS = 8
# Early guardrail (external cross-review, 07/23): below this number of RAW
# commits in the window (before even knowing how many are technical), the
# repo is clearly too inactive -- no point paying for a single detail
# call (one per commit, real cost verified) for a nearly dead repo.
_MIN_RAW_COMMITS_BEFORE_DETAIL = 5

# Functional categories (external cross-review, 07/23) -- more honest
# than a plain count of distinct extensions: two extensions from the SAME
# layer (e.g. .py and .sh from the same backend) shouldn't count as two
# independent diversity signals.
_CATEGORY_CONTRACT_EXTENSIONS = {".sol", ".vy", ".rs", ".move", ".cairo"}
_CATEGORY_JS_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
_CATEGORY_BACKEND_EXTENSIONS = {".py", ".go", ".java", ".rb", ".php", ".c", ".cpp", ".h", ".sh"}

# Normalization (see judge_github_substance): beyond these values, the
# criterion is considered "full score" -- not an exact science, a reasonable
# benchmark to avoid one exceptional project skewing the scale.
_DIFF_SIZE_FULL_SCORE = 50.0
# 5 possible categories (contract/tests/js_ts/backend/other_tech) -- reaching
# 4 already signals a real multi-layer project (contract + frontend +
# scripts + tests, for example), full score without requiring the 5th.
_DISTINCT_CATEGORIES_FULL_SCORE = 4


@dataclass(frozen=True)
class GithubSubstanceFacts:
    commits_analyzed: int = 0
    technical_commits: int = 0
    code_ratio: float | None = None            # share of technical lines vs cosmetic (0-1)
    avg_diff_size: float | None = None         # lines changed / technical commit
    has_tests: bool = False
    distinct_categories: int = 0               # distinct functional categories (contract/tests/js_ts/backend/other_tech)
    regularity_score: float | None = None      # 0-1: temporal spread (1 = distributed, 0 = dump)
    message_quality_score: float | None = None  # 0-1: share of descriptive messages
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class GithubSubstanceVerdict:
    signal: str  # positive / neutral / weak / unknown
    score: float | None = None  # 0-100
    points: list[str] = field(default_factory=list)


def judge_github_substance(facts: GithubSubstanceFacts) -> GithubSubstanceVerdict:
    """Pure, deterministic judgment — same doctrine as the other signals in
    this work. Never an automatic rejection, just one more consultative score."""
    if not facts.available:
        return GithubSubstanceVerdict(signal="unknown", points=[facts.error or "activité GitHub non analysable"])
    if facts.technical_commits < _MIN_TECHNICAL_COMMITS:
        return GithubSubstanceVerdict(
            signal="unknown",
            points=[
                f"seulement {facts.technical_commits} commit(s) technique(s) sur {_WINDOW_DAYS}j "
                f"(< {_MIN_TECHNICAL_COMMITS} requis) -- échantillon trop faible pour juger honnêtement"
            ],
        )

    code_ratio = facts.code_ratio or 0.0
    diff_component = min(1.0, (facts.avg_diff_size or 0.0) / _DIFF_SIZE_FULL_SCORE)
    test_component = 1.0 if facts.has_tests else 0.0
    diversity_component = min(1.0, facts.distinct_categories / _DISTINCT_CATEGORIES_FULL_SCORE)
    regularity_component = facts.regularity_score or 0.0
    message_component = facts.message_quality_score or 0.0

    score = 100.0 * (
        0.30 * code_ratio
        + 0.20 * diff_component
        + 0.15 * test_component
        + 0.15 * diversity_component
        + 0.10 * regularity_component
        + 0.10 * message_component
    )
    score = round(score, 1)

    detail = (
        f"{facts.technical_commits}/{facts.commits_analyzed} commits techniques, "
        f"{code_ratio * 100:.0f}% code réel, diff moyen {facts.avg_diff_size or 0:.0f} lignes, "
        f"tests {'oui' if facts.has_tests else 'non'}, {facts.distinct_categories} catégorie(s) fonctionnelle(s)"
    )

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"
    return GithubSubstanceVerdict(signal=signal, score=score, points=[f"substance {score:.0f}/100 -- {detail}"])


def _is_technical_file(filename: str) -> bool:
    lower = filename.lower()
    if any(frag in lower for frag in _COSMETIC_NAME_FRAGMENTS):
        return False
    return any(lower.endswith(ext) for ext in _TECHNICAL_EXTENSIONS)


def _is_test_file(filename: str) -> bool:
    lower = filename.lower()
    return any(frag in lower for frag in _TEST_PATH_FRAGMENTS)


def _file_extension(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx != -1 else ""


def _file_category(filename: str) -> str | None:
    """FUNCTIONAL category of a technical file (contract/tests/js_ts/
    backend/other_tech) -- more honest than a plain count of distinct
    extensions (two extensions from the SAME layer, e.g. .py and .sh from the same
    backend, shouldn't count as two independent diversity
    signals). ``None`` if the file isn't technical (caller already
    filters via `_is_technical_file`, but robust if called in isolation)."""
    if _is_test_file(filename):
        return "tests"
    ext = _file_extension(filename)
    if ext in _CATEGORY_CONTRACT_EXTENSIONS:
        return "contract"
    if ext in _CATEGORY_JS_TS_EXTENSIONS:
        return "js_ts"
    if ext in _CATEGORY_BACKEND_EXTENSIONS:
        return "backend"
    if ext in _TECHNICAL_EXTENSIONS:
        return "other_tech"
    return None


def _message_is_descriptive(message: str) -> bool:
    first_line = (message or "").strip().splitlines()[0].strip().lower() if message else ""
    if len(first_line) < _MIN_MESSAGE_LENGTH:
        return False
    return first_line not in _GENERIC_MESSAGES


async def _default_fetch(path: str) -> object | None:
    """AUTHENTICATED GET on api.github.com (GITHUB_TOKEN, already existing) -- rate
    limit 5000/h, confirmed by a real call capable of reading a third-party
    public repo. Graceful degradation: any failure -> None, never an exception."""
    import httpx

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{_GITHUB_API}{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=headers)
    if r.status_code != 200:
        return None
    return r.json()


async def gather_github_substance_facts(
    repo_url: str | None, *, fetch=None, now: datetime | None = None,
) -> GithubSubstanceFacts:
    """Best-effort gathering of recent development substance. ``fetch``
    is injectable for offline tests (default: a real authenticated call).
    Any unavailability -> ``available=False``, never invented data.

    07/23 -- uses ``resolve_github_repo`` (not just ``parse_github_repo``):
    a link declared toward an ORGANIZATION alone (e.g. "github.com/crynux-network",
    not a specific repo -- common practice for a multi-repo project) is resolved
    to its most recently active repo, rather than wrongly returning
    unavailable (real case found: CNX/crynux-network, 272 stars, never detected before
    this fix)."""
    from aria_core.services.project_activity import resolve_github_repo

    fetch = fetch or _default_fetch
    parsed = await resolve_github_repo(repo_url, fetch=fetch)
    if not parsed:
        return GithubSubstanceFacts(available=False, error="URL GitHub introuvable ou invalide")
    owner, repo = parsed
    now = now or datetime.now(timezone.utc)

    since = (now - timedelta(days=_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        commits_list = await fetch(
            f"/repos/{owner}/{repo}/commits?per_page={_MAX_COMMITS_ANALYZED}&since={since}"
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocking
        logger.info("github_substance: commit list %s/%s failed (%s)", owner, repo, exc)
        return GithubSubstanceFacts(available=False, error=f"liste des commits indisponible ({exc})")
    if not isinstance(commits_list, list) or not commits_list:
        return GithubSubstanceFacts(available=False, error="aucun commit trouvé ou dépôt inaccessible")

    shas = [c.get("sha") for c in commits_list if isinstance(c, dict) and c.get("sha")]

    # Early guardrail (external cross-review, 07/23): below this number of
    # RAW commits (before even knowing how many are technical), the repo
    # is clearly too inactive -- no point paying for a single detail
    # call (one per commit) for a nearly dead repo.
    if len(shas) < _MIN_RAW_COMMITS_BEFORE_DETAIL:
        return GithubSubstanceFacts(
            available=False,
            error=f"seulement {len(shas)} commit(s) brut(s) sur {_WINDOW_DAYS}j (< {_MIN_RAW_COMMITS_BEFORE_DETAIL} requis)",
        )

    technical_commits = 0
    total_technical_lines = 0
    has_tests = False
    categories: set[str] = set()
    descriptive_count = 0
    commit_days: set[str] = set()
    earliest: datetime | None = None
    latest: datetime | None = None
    analyzed = 0

    for sha in shas:
        try:
            detail = await fetch(f"/repos/{owner}/{repo}/commits/{sha}")
        except Exception as exc:  # noqa: BLE001 -- an isolated failure doesn't invalidate the others
            logger.info("github_substance: commit detail %s failed (%s)", sha, exc)
            continue
        if not isinstance(detail, dict):
            continue
        analyzed += 1

        commit_meta = detail.get("commit") or {}
        message = commit_meta.get("message") or ""
        if _message_is_descriptive(message):
            descriptive_count += 1

        date_str = (commit_meta.get("committer") or {}).get("date") or (commit_meta.get("author") or {}).get("date")
        if date_str:
            try:
                dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                commit_days.add(dt.date().isoformat())
                earliest = dt if earliest is None or dt < earliest else earliest
                latest = dt if latest is None or dt > latest else latest
            except (ValueError, TypeError):
                pass

        files = detail.get("files") or []
        technical_lines_this_commit = 0
        is_technical = False
        for f in files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if _is_test_file(filename):
                has_tests = True
            if _is_technical_file(filename):
                is_technical = True
                technical_lines_this_commit += int(f.get("additions") or 0) + int(f.get("deletions") or 0)
                category = _file_category(filename)
                if category:
                    categories.add(category)
        if is_technical:
            technical_commits += 1
            total_technical_lines += technical_lines_this_commit

    if analyzed == 0:
        return GithubSubstanceFacts(available=False, error="aucun détail de commit accessible")

    code_ratio = (technical_commits / analyzed) if analyzed else None
    avg_diff_size = (total_technical_lines / technical_commits) if technical_commits else None
    message_quality_score = (descriptive_count / analyzed) if analyzed else None

    regularity_score = None
    if earliest is not None and latest is not None:
        span_days = max(1, (latest - earliest).days + 1)
        regularity_score = min(1.0, len(commit_days) / span_days)

    return GithubSubstanceFacts(
        commits_analyzed=analyzed,
        technical_commits=technical_commits,
        code_ratio=code_ratio,
        avg_diff_size=avg_diff_size,
        has_tests=has_tests,
        distinct_categories=len(categories),
        regularity_score=regularity_score,
        message_quality_score=message_quality_score,
        available=True,
    )
