"""Capteur d'activité GitHub + tour de surveillance des thèses."""
from __future__ import annotations

from datetime import datetime, timezone

import aria_core.thesis_journal as tj
import pytest
from aria_core.services.project_activity import (
    github_days_since_commit,
    github_url_from_links,
    parse_github_repo,
)

NOW = datetime(2026, 7, 7, tzinfo=timezone.utc)


def test_parse_github_repo_variants():
    assert parse_github_repo("https://github.com/aaronjmars/aeon") == ("aaronjmars", "aeon")
    assert parse_github_repo("https://github.com/foo/bar.git") == ("foo", "bar")
    assert parse_github_repo("http://github.com/foo/bar/tree/main") == ("foo", "bar")
    assert parse_github_repo("https://x.com/foo") is None
    assert parse_github_repo(None) is None


def test_github_url_from_links():
    links = [{"url": "https://x.com/proj"}, {"url": "https://github.com/o/r"}]
    assert github_url_from_links(links) == "https://github.com/o/r"
    assert github_url_from_links([{"url": "https://t.me/x"}]) is None


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
