"""Persisted TTL cache for external substance signals (24/07, item #40) --
DB isolated per test (same pattern as test_momentum_funnel_log.py), zero
network call."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core.services import external_signal_cache as cache


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "external_signal_cache_test.db"))


@pytest.mark.asyncio
async def test_get_cached_none_when_never_scanned():
    assert await cache.get_cached("github_substance", "https://github.com/acme/x", ttl_days=7.0) is None


@pytest.mark.asyncio
async def test_store_then_get_cached_roundtrip():
    await cache.store("github_substance", "https://github.com/acme/x", {"available": True, "commits_analyzed": 42})
    result = await cache.get_cached("github_substance", "https://github.com/acme/x", ttl_days=7.0)
    assert result == {"available": True, "commits_analyzed": 42}


@pytest.mark.asyncio
async def test_get_cached_normalizes_key_case_and_whitespace():
    await cache.store("x_substance", "  AcmeProject  ", {"available": True})
    result = await cache.get_cached("x_substance", "acmeproject", ttl_days=7.0)
    assert result == {"available": True}


@pytest.mark.asyncio
async def test_get_cached_none_when_expired():
    await cache.store("docs_substance", "https://docs.acme.xyz", {"available": True})
    old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    async with aiosqlite.connect(cache.DB_PATH) as db:
        await db.execute(
            "UPDATE external_signal_cache SET cached_at = ? WHERE signal_type = ? AND target_key = ?",
            (old_ts, "docs_substance", "https://docs.acme.xyz"),
        )
        await db.commit()
    assert await cache.get_cached("docs_substance", "https://docs.acme.xyz", ttl_days=15.0) is None


@pytest.mark.asyncio
async def test_get_cached_still_fresh_just_under_ttl():
    await cache.store("website_substance", "https://acme.xyz", {"available": True})
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    async with aiosqlite.connect(cache.DB_PATH) as db:
        await db.execute(
            "UPDATE external_signal_cache SET cached_at = ? WHERE signal_type = ? AND target_key = ?",
            (recent_ts, "website_substance", "https://acme.xyz"),
        )
        await db.commit()
    result = await cache.get_cached("website_substance", "https://acme.xyz", ttl_days=15.0)
    assert result == {"available": True}


@pytest.mark.asyncio
async def test_store_overwrites_existing_row():
    await cache.store("github_substance", "https://github.com/acme/x", {"available": True, "commits_analyzed": 1})
    await cache.store("github_substance", "https://github.com/acme/x", {"available": True, "commits_analyzed": 99})
    result = await cache.get_cached("github_substance", "https://github.com/acme/x", ttl_days=7.0)
    assert result == {"available": True, "commits_analyzed": 99}


@pytest.mark.asyncio
async def test_different_signal_types_never_collide_on_same_key():
    await cache.store("github_substance", "https://acme.xyz", {"available": True, "kind": "github"})
    await cache.store("website_substance", "https://acme.xyz", {"available": True, "kind": "website"})
    github_result = await cache.get_cached("github_substance", "https://acme.xyz", ttl_days=7.0)
    website_result = await cache.get_cached("website_substance", "https://acme.xyz", ttl_days=15.0)
    assert github_result == {"available": True, "kind": "github"}
    assert website_result == {"available": True, "kind": "website"}


@pytest.mark.asyncio
async def test_get_cached_none_on_malformed_json(monkeypatch):
    await cache._ensure_table()
    async with aiosqlite.connect(cache.DB_PATH) as db:
        await db.execute(
            "INSERT INTO external_signal_cache (signal_type, target_key, payload_json, cached_at) "
            "VALUES (?, ?, ?, ?)",
            ("docs_substance", "https://broken.xyz", "{not valid json", datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    assert await cache.get_cached("docs_substance", "https://broken.xyz", ttl_days=15.0) is None
