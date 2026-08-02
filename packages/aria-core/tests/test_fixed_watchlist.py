"""02/08 -- "megacap" pocket's candidate source: a PERMANENT list of
operator-curated contracts, deliberately the opposite semantics of
manual_candidates.py (no TTL, no purge, never drained by a buy).

First deploy trims AAVE/VIRTUAL out of _DEFAULT_WATCHLIST (recommended
activation sequence, ronde 5 of the design review) -- 8 tokens seeded today,
not 10; the 2 security-allowlisted tokens are added back as a follow-up once
the pocket is confirmed clean."""
from __future__ import annotations

import pytest

from aria_core import fixed_watchlist as fw


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "fixed_watchlist_test.db")
    monkeypatch.setattr(fw, "DB_PATH", db_path)
    yield


@pytest.mark.asyncio
async def test_ensure_table_seeds_the_8_default_tokens():
    rows = await fw.list_watchlist_candidates()
    assert len(rows) == 8
    symbols = {r["symbol"] for r in rows}
    assert symbols == {
        "LINK", "KAITO", "ICP", "ENA", "WETH", "CBBTC", "WBTC", "CBETH",
    }
    # AAVE/VIRTUAL deliberately trimmed for the first deploy -- see module
    # docstring and _DEFAULT_WATCHLIST's own comment.
    assert "AAVE" not in symbols
    assert "VIRTUAL" not in symbols


@pytest.mark.asyncio
async def test_seed_is_idempotent_across_repeated_calls():
    first = await fw.list_watchlist_candidates()
    second = await fw.list_watchlist_candidates()
    assert len(first) == len(second) == 8
    assert {r["contract"] for r in first} == {r["contract"] for r in second}


@pytest.mark.asyncio
async def test_all_default_rows_are_base_chain():
    rows = await fw.list_watchlist_candidates()
    assert all(r["chain"] == "base" for r in rows)


@pytest.mark.asyncio
async def test_add_watchlist_candidate_extends_the_list():
    ok = await fw.add_watchlist_candidate("NEWTOKEN", "0xNEWNEWNEWNEWNEWNEWNEWNEWNEWNEWNEWNEWNEW1", "base")
    assert ok is True
    rows = await fw.list_watchlist_candidates()
    assert len(rows) == 9
    assert any(r["symbol"] == "NEWTOKEN" for r in rows)


@pytest.mark.asyncio
async def test_add_watchlist_candidate_rejects_empty_contract():
    assert await fw.add_watchlist_candidate("X", "", "base") is False


@pytest.mark.asyncio
async def test_no_ttl_no_purge_unlike_manual_candidates():
    """Contrast test proving the semantic difference is real: unlike
    manual_candidates.py (purges rows older than MANUAL_CANDIDATE_TTL_DAYS on
    every read), list_watchlist_candidates() never deletes anything -- even
    an artificially old added_at survives a read."""
    import aiosqlite

    await fw._ensure_table()
    async with aiosqlite.connect(fw.DB_PATH) as db:
        await db.execute(
            "UPDATE fixed_watchlist SET added_at = '2000-01-01T00:00:00+00:00' WHERE symbol = 'LINK'"
        )
        await db.commit()
    rows = await fw.list_watchlist_candidates()
    assert len(rows) == 8
    assert any(r["symbol"] == "LINK" for r in rows)


@pytest.mark.asyncio
async def test_normalize_contract_lowercases_evm_never_solana():
    assert fw._normalize_contract("0xABC", "base") == "0xabc"
    assert fw._normalize_contract("SoLaNaAddr", "solana") == "SoLaNaAddr"
