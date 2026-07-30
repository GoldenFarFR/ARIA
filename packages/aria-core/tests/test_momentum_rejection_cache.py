"""Item #193 (30/07, operator-raised gap): a token rejected by a STABLE hard
gate was re-evaluated from scratch on every subsequent discovery cycle.
``momentum_rejection_cache`` remembers a cacheable rejection for a TTL,
sparing the network round-trip until it's actually worth retrying."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import momentum_rejection_cache as rc


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "rejection_cache_test.db")
    monkeypatch.setattr(rc, "DB_PATH", db_path)
    yield


@pytest.mark.asyncio
async def test_never_rejected_returns_none():
    assert await rc.recently_rejected("0xabc", "base") is None


@pytest.mark.asyncio
async def test_cacheable_reason_is_remembered():
    await rc.record_rejection("0xABC", "base", "insufficient_liquidity")
    assert await rc.recently_rejected("0xabc", "base") == "insufficient_liquidity"


@pytest.mark.asyncio
async def test_non_cacheable_reason_is_never_recorded():
    """already_parabolic moves every minute -- caching it would delay
    noticing a real pullback, so it must never be written at all."""
    await rc.record_rejection("0xabc", "base", "already_parabolic")
    assert await rc.recently_rejected("0xabc", "base") is None


@pytest.mark.asyncio
async def test_blacklisted_and_honeypot_codes_are_never_cacheable():
    for reason in ("blacklisted", "honeypot_rejected", "honeypot_unavailable", "chain_not_covered"):
        await rc.record_rejection("0xabc", "base", reason)
    assert await rc.recently_rejected("0xabc", "base") is None


@pytest.mark.asyncio
async def test_expired_rejection_is_not_returned(monkeypatch):
    await rc.record_rejection("0xabc", "base", "volume_too_low")
    future = datetime.now(timezone.utc) + timedelta(seconds=rc.REJECTION_CACHE_TTL_SECONDS + 1)

    real_datetime = rc.datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(rc, "datetime", _FrozenDatetime)
    assert await rc.recently_rejected("0xabc", "base") is None


@pytest.mark.asyncio
async def test_case_insensitive_for_evm_case_sensitive_for_solana():
    await rc.record_rejection("0xABC", "base", "wash_trading_ratio")
    assert await rc.recently_rejected("0xabc", "BASE") == "wash_trading_ratio"

    await rc.record_rejection("SoLmixedCase", "solana", "no_verified_profile")
    assert await rc.recently_rejected("SoLmixedCase", "solana") == "no_verified_profile"
    assert await rc.recently_rejected("solmixedcase", "solana") is None


@pytest.mark.asyncio
async def test_re_record_replaces_previous_reason_and_extends_ttl():
    await rc.record_rejection("0xabc", "base", "insufficient_liquidity")
    await rc.record_rejection("0xabc", "base", "volume_too_low")
    assert await rc.recently_rejected("0xabc", "base") == "volume_too_low"


@pytest.mark.asyncio
async def test_missing_contract_or_chain_is_a_no_op():
    await rc.record_rejection("", "base", "insufficient_liquidity")
    await rc.record_rejection("0xabc", "", "insufficient_liquidity")
    assert await rc.recently_rejected("0xabc", "base") is None
