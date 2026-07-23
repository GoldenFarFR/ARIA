"""Signal 'substance GitHub' -- juge la qualité réelle du développement, pas
sa fréquence (item #23, conception vérifiée avant implémentation)."""
from __future__ import annotations

import pytest

from aria_core.skills.github_substance import (
    GithubSubstanceFacts,
    gather_github_substance_facts,
    judge_github_substance,
)

REPO_URL = "https://github.com/acme/protocol"


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_github_substance(GithubSubstanceFacts(available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_below_min_technical_commits_is_unknown():
    v = judge_github_substance(GithubSubstanceFacts(technical_commits=3, commits_analyzed=20, available=True))
    assert v.signal == "unknown"
    assert any("échantillon trop faible" in p for p in v.points)


def test_high_quality_signals_is_positive():
    v = judge_github_substance(
        GithubSubstanceFacts(
            commits_analyzed=30, technical_commits=25, code_ratio=0.9, avg_diff_size=80.0,
            has_tests=True, distinct_categories=5, regularity_score=0.8, message_quality_score=0.9,
            available=True,
        )
    )
    assert v.signal == "positive"
    assert v.score is not None and v.score >= 70


def test_low_quality_signals_is_weak():
    v = judge_github_substance(
        GithubSubstanceFacts(
            commits_analyzed=30, technical_commits=10, code_ratio=0.1, avg_diff_size=2.0,
            has_tests=False, distinct_categories=1, regularity_score=0.1, message_quality_score=0.1,
            available=True,
        )
    )
    assert v.signal == "weak"
    assert v.score is not None and v.score < 40


def test_middling_signals_is_neutral():
    v = judge_github_substance(
        GithubSubstanceFacts(
            commits_analyzed=20, technical_commits=15, code_ratio=0.5, avg_diff_size=25.0,
            has_tests=False, distinct_categories=2, regularity_score=0.5, message_quality_score=0.5,
            available=True,
        )
    )
    assert v.signal == "neutral"


# ── Récolte (fetch factice) ──────────────────────────────────────────────


def _commit_list_item(sha: str) -> dict:
    return {"sha": sha}


def _commit_detail(
    sha: str, *, message: str, date: str, files: list[dict],
) -> dict:
    return {
        "sha": sha,
        "commit": {"message": message, "committer": {"date": date}},
        "files": files,
    }


@pytest.mark.asyncio
async def test_gather_invalid_url_unavailable():
    facts = await gather_github_substance_facts("not-a-github-url")
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_no_url_unavailable():
    facts = await gather_github_substance_facts(None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_empty_commit_list_unavailable():
    async def fetch(path):
        return []

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_fetch_failure_unavailable():
    async def fetch(path):
        raise RuntimeError("panne réseau")

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_classifies_technical_vs_cosmetic_commits():
    calls = {"n": 0}

    async def fetch(path):
        if "commits?per_page" in path:
            return [_commit_list_item(f"sha{i}") for i in range(1, 6)]
        if path.endswith("/sha1"):
            return _commit_detail(
                "sha1", message="feat: add liquidity pool math", date="2026-06-01T10:00:00Z",
                files=[{"filename": "contracts/Pool.sol", "additions": 40, "deletions": 10}],
            )
        if path.endswith("/sha2"):
            return _commit_detail(
                "sha2", message="update readme", date="2026-06-02T10:00:00Z",
                files=[{"filename": "README.md", "additions": 5, "deletions": 1}],
            )
        return None

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)

    assert facts.available is True
    assert facts.commits_analyzed == 2
    assert facts.technical_commits == 1  # seul sha1 touche un fichier technique
    assert facts.code_ratio == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_gather_detects_test_files():
    async def fetch(path):
        if "commits?per_page" in path:
            return [_commit_list_item(f"sha{i}") for i in range(1, 6)]
        if not path.endswith("/sha1"):
            return None
        return _commit_detail(
            "sha1", message="add coverage for swap logic", date="2026-06-01T10:00:00Z",
            files=[
                {"filename": "contracts/Pool.sol", "additions": 10, "deletions": 2},
                {"filename": "test/Pool.test.ts", "additions": 30, "deletions": 0},
            ],
        )

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)

    assert facts.has_tests is True
    # contract (.sol) + tests (chemin "test/...") -- 2 catégories fonctionnelles distinctes
    assert facts.distinct_categories == 2


@pytest.mark.asyncio
async def test_gather_message_quality_distinguishes_generic_from_descriptive():
    async def fetch(path):
        if "commits?per_page" in path:
            return [_commit_list_item(f"sha{i}") for i in range(1, 6)]
        if path.endswith("/sha1"):
            return _commit_detail(
                "sha1", message="fix", date="2026-06-01T10:00:00Z",
                files=[{"filename": "contracts/Pool.sol", "additions": 1, "deletions": 1}],
            )
        if path.endswith("/sha2"):
            return _commit_detail(
                "sha2", message="implement flash-loan protection in swap router", date="2026-06-02T10:00:00Z",
                files=[{"filename": "contracts/Router.sol", "additions": 20, "deletions": 5}],
            )
        return None

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)

    assert facts.message_quality_score == pytest.approx(0.5)  # 1 générique, 1 descriptif


@pytest.mark.asyncio
async def test_gather_regularity_penalizes_single_day_dump():
    async def fetch(path):
        if "commits?per_page" in path:
            return [_commit_list_item(f"sha{i}") for i in range(5)]
        return _commit_detail(
            path.rsplit("/", 1)[-1], message="bulk commit", date="2026-06-01T10:00:00Z",
            files=[{"filename": "contracts/Pool.sol", "additions": 10, "deletions": 0}],
        )

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)

    assert facts.regularity_score == pytest.approx(1.0)  # span = 1 jour, 1 jour distinct -> 1/1


@pytest.mark.asyncio
async def test_gather_isolated_commit_detail_failure_does_not_break_others():
    async def fetch(path):
        if "commits?per_page" in path:
            return [_commit_list_item(f"sha{i}") for i in range(1, 6)]
        if path.endswith("/sha1"):
            raise RuntimeError("panne isolée")
        if path.endswith("/sha2"):
            return _commit_detail(
                "sha2", message="add real feature", date="2026-06-02T10:00:00Z",
                files=[{"filename": "contracts/Pool.sol", "additions": 10, "deletions": 0}],
            )
        return None

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)

    assert facts.available is True
    assert facts.commits_analyzed == 1


@pytest.mark.asyncio
async def test_gather_all_detail_failures_unavailable():
    async def fetch(path):
        if "commits?per_page" in path:
            return [_commit_list_item(f"sha{i}") for i in range(1, 6)]
        raise RuntimeError("panne réseau")

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)

    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_below_min_raw_commits_unavailable_before_any_detail_call():
    """23/07 -- garde-fou précoce (revue croisée externe) : sous le seuil de
    commits BRUTS, aucun appel détail n'est jamais tenté (coût réseau évité)."""
    detail_calls = []

    async def fetch(path):
        if "commits?per_page" in path:
            return [_commit_list_item("sha1"), _commit_list_item("sha2")]
        detail_calls.append(path)
        return _commit_detail(
            "sha1", message="add real feature", date="2026-06-01T10:00:00Z",
            files=[{"filename": "contracts/Pool.sol", "additions": 10, "deletions": 0}],
        )

    facts = await gather_github_substance_facts(REPO_URL, fetch=fetch)

    assert facts.available is False
    assert detail_calls == []  # zéro appel détail -- coût évité
