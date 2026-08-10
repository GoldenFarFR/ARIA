"""Total-failure counter for website_substance._default_crawl (10/08,
"construis le compteur et prepare le terrain pour de futur candidat") --
isolated temp DB, no real network/LLM calls."""
from __future__ import annotations

import pytest

from aria_core import website_crawl_failure_log as wcfl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(wcfl, "DB_PATH", str(tmp_path / "website_crawl_failure_log_test.db"))


@pytest.mark.asyncio
async def test_no_failures_initially():
    assert await wcfl.failure_count_since(days=7) == 0
    assert await wcfl.recent_failures() == []


@pytest.mark.asyncio
async def test_record_then_count_and_list():
    await wcfl.record_all_layers_failed(
        "https://example.com",
        {"scraper_maison": "homepage inaccessible", "firecrawl": "clé non configurée", "tavily": "budget épuisé"},
    )
    assert await wcfl.failure_count_since(days=7) == 1
    failures = await wcfl.recent_failures()
    assert len(failures) == 1
    assert failures[0]["url"] == "https://example.com"
    assert failures[0]["layer_errors"]["tavily"] == "budget épuisé"


@pytest.mark.asyncio
async def test_old_failures_excluded_from_recent_window():
    from datetime import datetime, timedelta, timezone

    import aiosqlite

    await wcfl.record_all_layers_failed("https://old.example.com", {"tavily": "x"})

    # Backdate the row past the 7-day window.
    async with aiosqlite.connect(wcfl.DB_PATH) as db:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        await db.execute("UPDATE website_crawl_failure_log SET occurred_at = ?", (old_ts,))
        await db.commit()

    assert await wcfl.failure_count_since(days=7) == 0


@pytest.mark.asyncio
async def test_recent_failures_newest_first():
    await wcfl.record_all_layers_failed("https://a.example.com", {"tavily": "x"})
    await wcfl.record_all_layers_failed("https://b.example.com", {"tavily": "y"})
    failures = await wcfl.recent_failures()
    assert failures[0]["url"] == "https://b.example.com"
    assert failures[1]["url"] == "https://a.example.com"
