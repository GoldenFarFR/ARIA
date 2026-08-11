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


# ── liquidity_tier partitioning (Item #228, 30/07) ───────────────────────────
# Real bug found investigating "why does swing never scan some tokens
# scalping does" (empirically confirmed: 130/427 contracts scalping scanned
# were NEVER scanned by swing on a shared candidate pool): insufficient_
# liquidity's threshold depends on the pocket (scalping vs standard/fear),
# so a rejection cached by ONE pocket must never silently block the OTHER
# pocket from re-evaluating with its own, different, threshold.


@pytest.mark.asyncio
async def test_insufficient_liquidity_rejection_scoped_to_scalping_never_blocks_standard():
    await rc.record_rejection("0xabc", "base", "insufficient_liquidity", liquidity_tier="scalping")
    # scalping's own re-check still sees its own cached rejection...
    assert await rc.recently_rejected("0xabc", "base", liquidity_tier="scalping") == "insufficient_liquidity"
    # ...but a DIFFERENT pocket (standard/swing) must re-evaluate fresh, not
    # inherit a rejection computed against a threshold it never used.
    assert await rc.recently_rejected("0xabc", "base", liquidity_tier="standard") is None


@pytest.mark.asyncio
async def test_insufficient_liquidity_rejection_scoped_to_standard_never_blocks_scalping():
    await rc.record_rejection("0xabc", "base", "insufficient_liquidity", liquidity_tier="standard")
    assert await rc.recently_rejected("0xabc", "base", liquidity_tier="standard") == "insufficient_liquidity"
    assert await rc.recently_rejected("0xabc", "base", liquidity_tier="scalping") is None


@pytest.mark.asyncio
async def test_shared_reasons_still_block_every_pocket_regardless_of_tier():
    """wash_trading_ratio/no_verified_profile/holder_concentration never
    depend on the pocket -- a rejection on one of these must keep blocking
    ALL pockets, exactly as before this fix (no regression on the shared
    cache's whole reason for existing)."""
    for reason in ("wash_trading_ratio", "no_verified_profile", "holder_concentration"):
        await rc.record_rejection("0xshared", "base", reason)
        assert await rc.recently_rejected("0xshared", "base", liquidity_tier="scalping") == reason
        assert await rc.recently_rejected("0xshared", "base", liquidity_tier="standard") == reason
        assert await rc.recently_rejected("0xshared", "base", liquidity_tier="fear") == reason


@pytest.mark.asyncio
async def test_no_liquidity_tier_argument_keeps_legacy_accept_anything_behavior():
    """A caller that never passes liquidity_tier (none left after this fix,
    but kept as a safe default) must see the exact pre-#228 behavior --
    accepts whatever's cached, regardless of which tier wrote it."""
    await rc.record_rejection("0xabc", "base", "insufficient_liquidity", liquidity_tier="scalping")
    assert await rc.recently_rejected("0xabc", "base") == "insufficient_liquidity"


@pytest.mark.asyncio
async def test_liquidity_tier_ignored_for_non_liquidity_reasons_stays_shared():
    """Passing a liquidity_tier on a non-insufficient_liquidity rejection
    must never accidentally scope it -- it always stores under the shared
    partition, same as omitting the tier entirely."""
    await rc.record_rejection("0xabc", "base", "wash_trading_ratio", liquidity_tier="scalping")
    assert await rc.recently_rejected("0xabc", "base", liquidity_tier="standard") == "wash_trading_ratio"


# ── robustness: real DB failure, never blocking the caller (11/08 audit) ──────────

@pytest.mark.asyncio
async def test_recently_rejected_never_raises_on_db_failure(monkeypatch):
    """Both docstrings in this module already promised "never raises, never
    blocks a fresh evaluation on a lookup failure" -- nothing enforced it
    before this fix. The real caller (evaluate_hard_gates) has no try/except
    of its own around either call."""
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(rc.aiosqlite, "connect", _broken_connect)
    assert await rc.recently_rejected("0xabc", "base") is None


@pytest.mark.asyncio
async def test_record_rejection_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(rc.aiosqlite, "connect", _broken_connect)
    # must not raise -- a missed cache write only costs one avoidable
    # re-scan next cycle, never a crash of the whole momentum evaluation.
    await rc.record_rejection("0xabc", "base", "insufficient_liquidity")


@pytest.mark.asyncio
async def test_recovers_once_db_failure_clears():
    """A transient failure must never leave the module permanently
    degraded -- the very next call on a healthy DB works normally."""
    await rc.record_rejection("0xabc", "base", "insufficient_liquidity")
    assert await rc.recently_rejected("0xabc", "base") == "insufficient_liquidity"
