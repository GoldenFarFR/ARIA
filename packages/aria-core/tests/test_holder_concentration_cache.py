"""holder_concentration_cache.py -- persisted long-TTL verdict cache (06/08,
operator request during a real Blockscout outage). Offline, direct unit
tests of the module's own read/write/expiry/normalization logic (integration
with momentum_entry._check_holder_concentration is covered in
test_momentum_entry.py's TestHolderConcentrationLongTermCache)."""
from __future__ import annotations

import pytest

from aria_core import holder_concentration_cache as hcc

CONTRACT = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(hcc, "DB_PATH", str(tmp_path / "holder_concentration_cache.db"))


@pytest.mark.asyncio
async def test_never_verified_returns_none():
    assert await hcc.cached_verdict(CONTRACT, "base") is None


@pytest.mark.asyncio
async def test_record_and_read_back_clear_verdict():
    await hcc.record_verdict(CONTRACT, "base", False, "")
    assert await hcc.cached_verdict(CONTRACT, "base") == (False, "")


@pytest.mark.asyncio
async def test_record_and_read_back_rejected_verdict():
    await hcc.record_verdict(CONTRACT, "base", True, "concentration 85% >= 80%")
    assert await hcc.cached_verdict(CONTRACT, "base") == (True, "concentration 85% >= 80%")


@pytest.mark.asyncio
async def test_expired_entry_returns_none(monkeypatch):
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    await hcc.record_verdict(CONTRACT, "base", False, "")
    # backdate expires_at into the past -- same technique as other cache
    # test suites in this codebase (direct row manipulation, no time mock).
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    async with aiosqlite.connect(hcc.DB_PATH) as db:
        await db.execute(
            "UPDATE holder_concentration_verdict_cache SET expires_at = ? WHERE contract = ?",
            (past, CONTRACT.lower()),
        )
        await db.commit()
    assert await hcc.cached_verdict(CONTRACT, "base") is None


@pytest.mark.asyncio
async def test_scoped_per_contract_and_chain():
    await hcc.record_verdict(CONTRACT, "base", False, "")
    assert await hcc.cached_verdict("0x" + "b" * 40, "base") is None
    assert await hcc.cached_verdict(CONTRACT, "ethereum") is None


@pytest.mark.asyncio
async def test_record_is_case_insensitive_on_evm_contracts():
    await hcc.record_verdict(CONTRACT.upper(), "base", False, "")
    assert await hcc.cached_verdict(CONTRACT.lower(), "base") == (False, "")


@pytest.mark.asyncio
async def test_solana_address_case_preserved():
    """Base58 (Solana) is case-SENSITIVE -- unlike EVM, never lowercased."""
    sol = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
    await hcc.record_verdict(sol, "solana", False, "")
    assert await hcc.cached_verdict(sol, "solana") == (False, "")
    assert await hcc.cached_verdict(sol.lower(), "solana") is None


@pytest.mark.asyncio
async def test_overwrite_replaces_previous_verdict():
    await hcc.record_verdict(CONTRACT, "base", True, "was rejected")
    await hcc.record_verdict(CONTRACT, "base", False, "")
    assert await hcc.cached_verdict(CONTRACT, "base") == (False, "")


@pytest.mark.asyncio
async def test_write_purges_other_expired_rows(monkeypatch):
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    stale_contract = "0x" + "c" * 40
    await hcc.record_verdict(stale_contract, "base", False, "")
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    async with aiosqlite.connect(hcc.DB_PATH) as db:
        await db.execute(
            "UPDATE holder_concentration_verdict_cache SET expires_at = ? WHERE contract = ?",
            (past, stale_contract),
        )
        await db.commit()

    await hcc.record_verdict(CONTRACT, "base", False, "")

    async with aiosqlite.connect(hcc.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM holder_concentration_verdict_cache WHERE contract = ?",
                (stale_contract,),
            )
        ).fetchone()
    assert row is None


# ── robustness: real DB failure, never blocking the caller (11/08 audit) ──────────

@pytest.mark.asyncio
async def test_cached_verdict_never_raises_on_db_failure(monkeypatch):
    """Both docstrings in this module already promised "never raises, never
    blocks a fresh evaluation on a lookup failure" -- nothing enforced it
    before this fix. The real caller (_check_holder_concentration) has no
    try/except of its own around either call."""
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(hcc.aiosqlite, "connect", _broken_connect)
    assert await hcc.cached_verdict(CONTRACT, "base") is None


@pytest.mark.asyncio
async def test_record_verdict_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(hcc.aiosqlite, "connect", _broken_connect)
    # must not raise -- a missed cache write only costs one avoidable
    # re-verification next cycle, never a crash of the whole evaluation.
    await hcc.record_verdict(CONTRACT, "base", False, "")


@pytest.mark.asyncio
async def test_recovers_once_db_failure_clears():
    """A transient failure must never leave the module permanently
    degraded -- the very next call on a healthy DB works normally."""
    await hcc.record_verdict(CONTRACT, "base", True, "concentration confirmee")
    assert await hcc.cached_verdict(CONTRACT, "base") == (True, "concentration confirmee")


# ── paid-but-empty cooldown (14/08) -- distinct, much shorter TTL than the
# real-verdict cache above, see the module's own docstring on this ─────────

@pytest.mark.asyncio
async def test_paid_but_empty_never_recorded_returns_false():
    assert await hcc.recently_paid_but_empty(CONTRACT, "base") is False


@pytest.mark.asyncio
async def test_paid_but_empty_recorded_and_read_back():
    await hcc.record_paid_but_empty(CONTRACT, "base")
    assert await hcc.recently_paid_but_empty(CONTRACT, "base") is True


@pytest.mark.asyncio
async def test_paid_but_empty_expired_entry_returns_false():
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    await hcc.record_paid_but_empty(CONTRACT, "base")
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    async with aiosqlite.connect(hcc.DB_PATH) as db:
        await db.execute(
            "UPDATE holder_concentration_paid_empty_cooldown SET expires_at = ? WHERE contract = ?",
            (past, CONTRACT.lower()),
        )
        await db.commit()
    assert await hcc.recently_paid_but_empty(CONTRACT, "base") is False


@pytest.mark.asyncio
async def test_paid_but_empty_scoped_per_contract_and_chain():
    await hcc.record_paid_but_empty(CONTRACT, "base")
    assert await hcc.recently_paid_but_empty("0x" + "b" * 40, "base") is False
    assert await hcc.recently_paid_but_empty(CONTRACT, "ethereum") is False


@pytest.mark.asyncio
async def test_paid_but_empty_independent_from_real_verdict_cache():
    """The two caches must never leak into each other -- a real verdict
    doesn't imply a cooldown and vice versa."""
    await hcc.record_verdict(CONTRACT, "base", False, "")
    assert await hcc.recently_paid_but_empty(CONTRACT, "base") is False

    other = "0x" + "d" * 40
    await hcc.record_paid_but_empty(other, "base")
    assert await hcc.cached_verdict(other, "base") is None


@pytest.mark.asyncio
async def test_recently_paid_but_empty_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(hcc.aiosqlite, "connect", _broken_connect)
    assert await hcc.recently_paid_but_empty(CONTRACT, "base") is False


@pytest.mark.asyncio
async def test_record_paid_but_empty_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(hcc.aiosqlite, "connect", _broken_connect)
    await hcc.record_paid_but_empty(CONTRACT, "base")
