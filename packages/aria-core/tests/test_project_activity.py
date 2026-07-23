"""Capteur d'activité GitHub + tour de surveillance des thèses."""
from __future__ import annotations

from datetime import datetime, timezone

import aria_core.thesis_journal as tj
import pytest
from aria_core.services.project_activity import (
    fetch_github_diligence_snapshot,
    github_days_since_commit,
    is_github_link,
    parse_github_repo,
    resolve_github_repo,
)

NOW = datetime(2026, 7, 7, tzinfo=timezone.utc)


def test_parse_github_repo_variants():
    assert parse_github_repo("https://github.com/aaronjmars/aeon") == ("aaronjmars", "aeon")
    assert parse_github_repo("https://github.com/foo/bar.git") == ("foo", "bar")
    assert parse_github_repo("http://github.com/foo/bar/tree/main") == ("foo", "bar")
    assert parse_github_repo("https://x.com/foo") is None
    assert parse_github_repo(None) is None


# ── Résolution d'organisation seule (23/07, cas réel CNX/crynux-network) ────


def test_parse_github_repo_none_on_org_only_url():
    """Confirme le trou trouvé : parse_github_repo (repo précis exigé) échoue
    sur une URL d'organisation seule, sans second segment."""
    assert parse_github_repo("https://github.com/crynux-network") is None
    assert parse_github_repo("https://github.com/crynux-network/") is None


def test_is_github_link_recognizes_both_repo_and_org_only():
    assert is_github_link("https://github.com/foo/bar") is True
    assert is_github_link("https://github.com/crynux-network") is True
    assert is_github_link("https://x.com/foo") is False
    assert is_github_link(None) is False


@pytest.mark.asyncio
async def test_resolve_github_repo_direct_repo_never_calls_fetch():
    """Cas dominant (repo précis dans l'URL) -- zéro appel réseau, comportement
    strictement identique à parse_github_repo seul."""
    calls = []

    async def fetch(path):
        calls.append(path)
        return None

    result = await resolve_github_repo("https://github.com/aaronjmars/aeon", fetch=fetch)
    assert result == ("aaronjmars", "aeon")
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_github_repo_resolves_org_only_to_most_active_repo():
    """Cas réel CNX/crynux-network : l'organisation seule est résolue vers son
    repo le plus récemment poussé (`sort=pushed`)."""
    async def fetch(path):
        assert "orgs/crynux-network/repos" in path
        assert "sort=pushed" in path
        return [{"name": "crynux-node", "stargazers_count": 272, "pushed_at": "2026-07-22T02:35:28Z"}]

    result = await resolve_github_repo("https://github.com/crynux-network", fetch=fetch)
    assert result == ("crynux-network", "crynux-node")


@pytest.mark.asyncio
async def test_resolve_github_repo_picks_most_starred_not_just_most_recent():
    """Reproduit fidèlement le cas réel CNX/crynux-network qui a révélé le
    problème : le repo le plus RÉCEMMENT poussé (crynux-portal, 1 étoile,
    probablement un frontend annexe) n'est PAS le plus pertinent -- le repo à
    272 étoiles (crynux-node, le vrai cœur du projet) doit gagner malgré un
    push plus ancien. L'API GitHub ne supporte pas `sort=stars` (vérifié
    contre la doc officielle) -- la sélection se fait donc côté client sur
    le lot chargé par `pushed`."""
    async def fetch(path):
        return [
            {"name": "crynux-portal", "stargazers_count": 1, "fork": False, "archived": False},
            {"name": "crynux-relay", "stargazers_count": 2, "fork": False, "archived": False},
            {"name": "crynux-relay-wallet", "stargazers_count": 0, "fork": False, "archived": False},
            {"name": "crynux-node", "stargazers_count": 272, "fork": False, "archived": False},
            {"name": "crynux-worker", "stargazers_count": 1, "fork": False, "archived": False},
        ]

    result = await resolve_github_repo("https://github.com/crynux-network", fetch=fetch)
    assert result == ("crynux-network", "crynux-node")


@pytest.mark.asyncio
async def test_resolve_github_repo_excludes_dot_github_special_repo():
    """Reproduit le second cas réel (crynux-network-dao) : `.github` est un
    repo de CONFIGURATION d'organisation (templates d'issues/workflows), jamais
    du développement -- ne doit jamais être choisi même s'il apparaît dans le
    lot, quel que soit son classement par date."""
    async def fetch(path):
        return [
            {"name": "crynux-token", "stargazers_count": 0, "fork": False, "archived": False},
            {"name": ".github", "stargazers_count": 0, "fork": False, "archived": False},
            {"name": "crynux-dao-documents", "stargazers_count": 0, "fork": False, "archived": False},
        ]

    result = await resolve_github_repo("https://github.com/crynux-network-dao", fetch=fetch)
    assert result is not None
    assert result[1] != ".github"


@pytest.mark.asyncio
async def test_resolve_github_repo_prefers_org_theme_over_unrelated_popular_repo():
    """Préoccupation opérateur explicite (23/07) : une organisation peut héberger
    des projets SANS RAPPORT entre eux (collectif, fondation multi-produits) --
    un repo populaire mais étranger au projet ciblé ne doit pas gagner seulement
    parce qu'il a plus d'étoiles. Le repo qui partage la racine du nom de
    l'organisation (signal du projet réellement ciblé) doit primer."""
    async def fetch(path):
        return [
            {"name": "unrelated-framework", "stargazers_count": 9000, "fork": False, "archived": False},
            {"name": "acmechain-core", "stargazers_count": 40, "fork": False, "archived": False},
            {"name": "acmechain-sdk", "stargazers_count": 5, "fork": False, "archived": False},
        ]

    result = await resolve_github_repo("https://github.com/acmechain", fetch=fetch)
    assert result == ("acmechain", "acmechain-core")


@pytest.mark.asyncio
async def test_resolve_github_repo_falls_back_to_full_pool_when_no_theme_match():
    """Si AUCUN candidat ne partage la racine du nom d'organisation (nommage
    incohérent), le filtre ne doit jamais vider la sélection à tort -- repli
    sur le lot complet, tri par étoiles inchangé."""
    async def fetch(path):
        return [
            {"name": "totally-different-name", "stargazers_count": 10, "fork": False, "archived": False},
            {"name": "another-unrelated-repo", "stargazers_count": 3, "fork": False, "archived": False},
        ]

    result = await resolve_github_repo("https://github.com/someorgname", fetch=fetch)
    assert result == ("someorgname", "totally-different-name")


@pytest.mark.asyncio
async def test_resolve_github_repo_excludes_forks():
    """Un fork n'est pas le code ORIGINAL du projet -- jamais choisi même s'il
    a plus d'étoiles qu'un repo original (les étoiles d'un fork sont souvent
    héritées du projet forké, pas gagnées par ce déploiement)."""
    async def fetch(path):
        return [
            {"name": "some-popular-fork", "stargazers_count": 5000, "fork": True, "archived": False},
            {"name": "original-work", "stargazers_count": 3, "fork": False, "archived": False},
        ]

    result = await resolve_github_repo("https://github.com/someorg", fetch=fetch)
    assert result == ("someorg", "original-work")


@pytest.mark.asyncio
async def test_resolve_github_repo_all_candidates_excluded_returns_none():
    async def fetch(path):
        return [{"name": ".github", "stargazers_count": 0, "fork": False, "archived": False}]

    result = await resolve_github_repo("https://github.com/emptyafterfilter", fetch=fetch)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_github_repo_org_with_no_repos_returns_none():
    async def fetch(path):
        return []

    result = await resolve_github_repo("https://github.com/emptyorg", fetch=fetch)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_github_repo_org_fetch_failure_returns_none():
    async def fetch(path):
        raise RuntimeError("panne réseau")

    result = await resolve_github_repo("https://github.com/crynux-network", fetch=fetch)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_github_repo_non_github_url_returns_none():
    result = await resolve_github_repo("https://x.com/foo")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_github_repo_none_url_returns_none():
    result = await resolve_github_repo(None)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_github_repo_reserved_org_names_excluded():
    result = await resolve_github_repo("https://github.com/sponsors")
    assert result is None


@pytest.mark.asyncio
async def test_days_since_commit_recent():
    async def fetch(path):
        return [{"commit": {"committer": {"date": "2026-07-04T00:00:00Z"}}}]

    d = await github_days_since_commit("https://github.com/o/r", fetch=fetch, now=NOW)
    assert d == 3


@pytest.mark.asyncio
async def test_days_since_commit_falls_back_to_author_date():
    async def fetch(path):
        return [{"commit": {"author": {"date": "2026-06-07T00:00:00Z"}}}]

    d = await github_days_since_commit("https://github.com/o/r", fetch=fetch, now=NOW)
    assert d == 30


@pytest.mark.asyncio
async def test_non_github_returns_none():
    async def fetch(path):
        raise AssertionError("ne doit pas fetch")

    assert await github_days_since_commit("https://x.com/foo", fetch=fetch) is None


@pytest.mark.asyncio
async def test_fetch_failure_degrades():
    async def fetch(path):
        raise RuntimeError("api down")

    assert await github_days_since_commit("https://github.com/o/r", fetch=fetch) is None


# ── Diligence produit (GitHub, 10/07) ──────────────────────
# (le site officiel/website_url_from_links a été retiré le 19/07, #134 --
# conviction_research.py couvre désormais le site officiel pour les deux
# pipelines via known_links, jamais dupliqué ici.)

@pytest.mark.asyncio
async def test_github_diligence_snapshot_returns_structured_facts():
    async def fetch(path):
        return {
            "id": 1, "description": "A cool repo", "stargazers_count": 42,
            "open_issues_count": 3, "pushed_at": "2026-07-01T00:00:00Z",
            "archived": False, "fork": False,
        }

    snap = await fetch_github_diligence_snapshot(
        "https://github.com/o/r", fetch=fetch, now=NOW
    )
    assert snap == {
        "description": "A cool repo", "stars": 42, "open_issues": 3,
        "days_since_push": 6, "archived": False, "fork": False, "age_days": None,
    }


@pytest.mark.asyncio
async def test_github_diligence_snapshot_computes_age_from_created_at():
    """19/07 -- age_days ajouté (consolidation avec l'ancien github_verify.py,
    doublon retiré) : réutilise le MÊME appel /repos/{owner}/{repo} déjà fait,
    aucun coût réseau supplémentaire."""
    async def fetch(path):
        return {
            "id": 1, "description": "", "stargazers_count": 0, "open_issues_count": 0,
            "pushed_at": "2026-07-01T00:00:00Z", "created_at": "2026-02-01T00:00:00Z",
            "archived": False, "fork": False,
        }

    snap = await fetch_github_diligence_snapshot(
        "https://github.com/o/r", fetch=fetch, now=NOW
    )
    assert snap["age_days"] is not None and snap["age_days"] > 0


@pytest.mark.asyncio
async def test_github_diligence_snapshot_non_github_returns_none():
    async def fetch(path):
        raise AssertionError("ne doit pas fetch")

    assert await fetch_github_diligence_snapshot("https://x.com/foo", fetch=fetch) is None


@pytest.mark.asyncio
async def test_github_diligence_snapshot_degrades_on_failure():
    async def fetch(path):
        raise RuntimeError("api down")

    assert await fetch_github_diligence_snapshot("https://github.com/o/r", fetch=fetch) is None


# ── Tour de surveillance ───────────────────────────────────────

@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "DB_PATH", str(tmp_path / "j.db"))


@pytest.mark.asyncio
async def test_review_flags_stagnating_and_invalidated():
    positions = [
        {"contract": "0xA", "entry_price": 1.0, "invalidation_price": 0.8, "github_url": "gh_stale"},
        {"contract": "0xB", "entry_price": 1.0, "invalidation_price": 0.9, "github_url": "gh_fresh"},
        {"contract": "0xC", "entry_price": 1.0, "invalidation_price": 0.95, "github_url": "gh_fresh"},
    ]

    async def price_fn(c):
        return {"0xA": 1.1, "0xB": 0.85, "0xC": 1.3}[c]  # C: prix ok, B: sous invalidation

    async def activity_fn(url):
        return {"gh_stale": 60, "gh_fresh": 2}.get(url)

    alerts = await tj.review_open_theses(positions, price_fn=price_fn, activity_fn=activity_fn)
    verdicts = {a["contract"]: a["verdict"] for a in alerts}
    assert verdicts.get("0xA") == "stagnating"   # projet mort
    assert verdicts.get("0xB") == "invalidated"  # prix sous invalidation
    assert "0xC" not in verdicts                  # delivering -> pas d'alerte
    # checkpoints consignés pour les 3
    assert len(await tj.list_checkpoints("0xC")) == 1


@pytest.mark.asyncio
async def test_review_one_failure_not_fatal():
    positions = [{"contract": "0xA"}, {"contract": "0xB"}]

    async def price_fn(c):
        if c == "0xA":
            raise RuntimeError("boom")
        return 1.0

    async def activity_fn(url):
        return 2

    # ne lève pas malgré l'échec de 0xA
    alerts = await tj.review_open_theses(positions, price_fn=price_fn, activity_fn=activity_fn)
    assert isinstance(alerts, list)
