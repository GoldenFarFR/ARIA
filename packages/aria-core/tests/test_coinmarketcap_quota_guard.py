"""Proactive CoinMarketCap monthly credit-quota guard (12/08) -- real
incident: the VPS key was found at 14569/15000 monthly credits (97%) with
zero protection in the code, only a per-minute throttle. See the module's
own docstring for why this is proactive (polls the real remaining quota via
the free /v1/key/info endpoint) rather than reactive like
goplus_quota_suspension.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import coinmarketcap_quota_guard as cqg


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cqg, "DB_PATH", str(tmp_path / "coinmarketcap_quota_guard_test.db"))


def _fake_fetch(credits_left, limit=15000):
    async def _inner():
        return credits_left, limit
    return _inner


@pytest.mark.asyncio
async def test_healthy_quota_never_suspends(monkeypatch):
    monkeypatch.setattr(cqg, "_fetch_real_quota", _fake_fetch(10000))
    assert await cqg.is_suspended() is False


@pytest.mark.asyncio
async def test_quota_under_threshold_suspends(monkeypatch):
    monkeypatch.setattr(cqg, "_fetch_real_quota", _fake_fetch(500))  # 500/15000 = 3.3% < 5%
    assert await cqg.is_suspended() is True


@pytest.mark.asyncio
async def test_quota_exactly_at_threshold_is_not_suspended(monkeypatch):
    monkeypatch.setattr(cqg, "_fetch_real_quota", _fake_fetch(750))  # 750/15000 = 5.0%, not < 5%
    assert await cqg.is_suspended() is False


@pytest.mark.asyncio
async def test_cache_is_not_refreshed_before_ttl_expires(monkeypatch):
    calls = []

    async def counting_fetch():
        calls.append(1)
        return 10000, 15000

    monkeypatch.setattr(cqg, "_fetch_real_quota", counting_fetch)
    assert await cqg.is_suspended() is False
    assert await cqg.is_suspended() is False
    assert await cqg.is_suspended() is False
    assert len(calls) == 1  # only the first call actually hit the network


@pytest.mark.asyncio
async def test_stale_cache_triggers_a_fresh_check(monkeypatch):
    await cqg._store().write(
        {
            "credits_left": 10000,
            "credit_limit_monthly": 15000,
            "checked_at": (datetime.now(timezone.utc) - timedelta(seconds=cqg._CACHE_TTL_SECONDS + 1)).isoformat(),
            "suspended": 0,
        }
    )
    monkeypatch.setattr(cqg, "_fetch_real_quota", _fake_fetch(200))  # now exhausted
    assert await cqg.is_suspended() is True


@pytest.mark.asyncio
async def test_unreadable_quota_fails_open_keeping_last_known_state(monkeypatch):
    async def broken_fetch():
        return None

    await cqg._store().write(
        {
            "credits_left": 200,
            "credit_limit_monthly": 15000,
            "checked_at": (datetime.now(timezone.utc) - timedelta(seconds=cqg._CACHE_TTL_SECONDS + 1)).isoformat(),
            "suspended": 1,
        }
    )
    monkeypatch.setattr(cqg, "_fetch_real_quota", broken_fetch)
    assert await cqg.is_suspended() is True  # keeps the last known (suspended) state


@pytest.mark.asyncio
async def test_quota_recovering_above_threshold_disarms(monkeypatch):
    monkeypatch.setattr(cqg, "_fetch_real_quota", _fake_fetch(200))
    assert await cqg.is_suspended() is True

    await cqg._store().write(
        {
            "credits_left": 200,
            "credit_limit_monthly": 15000,
            "checked_at": (datetime.now(timezone.utc) - timedelta(seconds=cqg._CACHE_TTL_SECONDS + 1)).isoformat(),
            "suspended": 1,
        }
    )
    monkeypatch.setattr(cqg, "_fetch_real_quota", _fake_fetch(15000))  # reset (new month)
    assert await cqg.is_suspended() is False


@pytest.mark.asyncio
async def test_notification_fires_exactly_once_on_first_armament(monkeypatch):
    notified = []

    async def fake_notify(credits_left, limit):
        notified.append((credits_left, limit))

    monkeypatch.setattr(cqg, "_notify_armed", fake_notify)
    monkeypatch.setattr(cqg, "_fetch_real_quota", _fake_fetch(200))
    assert await cqg.is_suspended() is True
    assert notified == [(200, 15000)]

    # Force a second refresh while still exhausted -- must NOT re-notify.
    await cqg._store().write(
        {
            "credits_left": 200,
            "credit_limit_monthly": 15000,
            "checked_at": (datetime.now(timezone.utc) - timedelta(seconds=cqg._CACHE_TTL_SECONDS + 1)).isoformat(),
            "suspended": 1,
        }
    )
    assert await cqg.is_suspended() is True
    assert notified == [(200, 15000)]  # unchanged, still only one notification
