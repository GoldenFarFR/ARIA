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
