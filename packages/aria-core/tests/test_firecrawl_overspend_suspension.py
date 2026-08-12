"""Auto-armed Firecrawl overspend suspension (12/08) -- see the module's own
docstring for the real incident this replaces (a single crawl costing 156
credits, 39% of the monthly free-plan budget, ~10x the worst-case estimate)."""
from __future__ import annotations

import pytest

from aria_core import firecrawl_overspend_suspension as fos


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(fos, "DB_PATH", str(tmp_path / "firecrawl_overspend_suspension_test.db"))


@pytest.mark.asyncio
async def test_not_suspended_initially():
    assert await fos.is_suspended() is False


@pytest.mark.asyncio
async def test_cost_at_or_below_ceiling_never_arms():
    assert await fos.record_crawl_cost(credits=fos._SINGLE_CRAWL_CREDIT_CEILING, url="https://example.com") is False
    assert await fos.is_suspended() is False


@pytest.mark.asyncio
async def test_cost_above_ceiling_arms_immediately_no_streak_needed():
    """Unlike goplus_quota_suspension, a SINGLE overspend event is the
    whole signal -- no consecutive-failure count needed."""
    assert await fos.record_crawl_cost(credits=fos._SINGLE_CRAWL_CREDIT_CEILING + 1, url="https://example.com") is True
    assert await fos.is_suspended() is True


@pytest.mark.asyncio
async def test_real_incident_shape_arms():
    assert await fos.record_crawl_cost(credits=156, url="https://x.com/SeamlessFi", caller="website_substance") is True
    assert await fos.is_suspended() is True


@pytest.mark.asyncio
async def test_already_suspended_does_not_rearm():
    assert await fos.record_crawl_cost(credits=100, url="https://a.example") is True
    assert await fos.record_crawl_cost(credits=200, url="https://b.example") is False
    assert await fos.is_suspended() is True


@pytest.mark.asyncio
async def test_next_month_start_handles_december_rollover():
    from datetime import datetime, timezone

    dec = datetime(2026, 12, 15, tzinfo=timezone.utc)
    result = fos._next_month_start(dec)
    assert (result.year, result.month, result.day) == (2027, 1, 1)
