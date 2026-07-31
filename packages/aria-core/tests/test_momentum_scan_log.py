"""Item #193 (28/07) -- log exhaustif par token (pas par paire) des candidats
scannés par le pipeline momentum. DB isolée par test (même patron que
test_momentum_funnel_log.py), aucun appel réseau."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import momentum_scan_log as msl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(msl, "DB_PATH", str(tmp_path / "momentum_scan_test.db"))


@pytest.mark.asyncio
async def test_record_scan_then_count_distinct():
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    await msl.record_scan("0xBBB", "base", None)  # BUY
    count = await msl.count_distinct_scanned(hours=24.0)
    assert count == 2


@pytest.mark.asyncio
async def test_same_contract_scanned_twice_counts_once():
    """A token re-scanned across several cycles must count ONCE, not once
    per evaluation -- distinct from momentum_funnel_log's own exhaustive
    (but non-deduplicated) evaluation count."""
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    await msl.record_scan("0xAAA", "base", "no_entry_signal")
    assert await msl.count_distinct_scanned(hours=24.0) == 1
    assert await msl.total_scans(hours=24.0) == 2


@pytest.mark.asyncio
async def test_same_token_different_pools_counts_once():
    """Operator-explicit requirement (28/07): keyed on the TOKEN contract,
    never a pair/pool address -- a token with several pools must count once,
    not once per pool it happens to have."""
    # Same token contract, evaluated via two different pools in two cycles
    # (the caller always passes the TOKEN contract, never pair_address --
    # this test locks that invariant in, doesn't simulate the pool lookup
    # itself).
    await msl.record_scan("0xTOKEN", "base", "no_trades_available")
    await msl.record_scan("0xTOKEN", "base", "no_trades_available")
    assert await msl.count_distinct_scanned(hours=24.0) == 1


@pytest.mark.asyncio
async def test_different_chains_same_contract_count_separately():
    """(contract, chain) is the real key -- the same address on two chains
    is genuinely two different tokens."""
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    await msl.record_scan("0xAAA", "ethereum", "volume_too_low")
    assert await msl.count_distinct_scanned(hours=24.0) == 2


@pytest.mark.asyncio
async def test_contract_case_insensitive_dedup():
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    await msl.record_scan("0xaaa", "base", "no_entry_signal")
    assert await msl.count_distinct_scanned(hours=24.0) == 1


@pytest.mark.asyncio
async def test_missing_contract_is_noop():
    await msl.record_scan("", "base", "volume_too_low")
    assert await msl.count_distinct_scanned(hours=24.0) == 0


@pytest.mark.asyncio
async def test_extra_columns_persisted_but_never_used_for_dedup():
    """symbol/price/mode/wallet are free extras (operator request, 28/07) --
    purely informational, in particular symbol is NEVER a lookup key (a
    ticker is never unique across tokens, unlike a contract address)."""
    await msl.record_scan(
        "0xAAA", "base", "volume_too_low",
        symbol="TOK", price=1.23, mode="standard", wallet="swing",
    )
    row = await msl.last_scan_for("0xAAA", "base")
    assert row["symbol"] == "TOK"
    assert row["price"] == pytest.approx(1.23)
    # Two DIFFERENT tickers on the SAME contract still count/lookup as ONE
    # token -- symbol never participates in the key.
    await msl.record_scan("0xAAA", "base", "no_entry_signal", symbol="RENAMED")
    assert await msl.count_distinct_scanned(hours=24.0) == 1


@pytest.mark.asyncio
async def test_last_scan_for_returns_most_recent():
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    await msl.record_scan("0xAAA", "base", "no_entry_signal")
    row = await msl.last_scan_for("0xAAA", "base")
    assert row["hold_reason"] == "no_entry_signal"


@pytest.mark.asyncio
async def test_last_scan_for_unknown_contract_returns_none():
    assert await msl.last_scan_for("0xNEVERSEEN", "base") is None


# ── last_scan_map (31/07, batched priority lookup for the discovery limit) ──

@pytest.mark.asyncio
async def test_last_scan_map_empty_input_is_a_noop():
    assert await msl.last_scan_map([]) == {}


@pytest.mark.asyncio
async def test_last_scan_map_omits_never_scanned_pairs():
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    result = await msl.last_scan_map([("0xAAA", "base"), ("0xNEVERSEEN", "base")])
    assert ("0xaaa", "base") in result
    assert ("0xneverseen", "base") not in result


@pytest.mark.asyncio
async def test_last_scan_map_returns_most_recent_per_pair():
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    first = (await msl.last_scan_for("0xAAA", "base"))["scanned_at"]
    await msl.record_scan("0xAAA", "base", "no_entry_signal")
    result = await msl.last_scan_map([("0xAAA", "base")])
    assert result[("0xaaa", "base")] >= first


@pytest.mark.asyncio
async def test_last_scan_map_keeps_chains_separate():
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    await msl.record_scan("0xAAA", "ethereum", "no_entry_signal")
    result = await msl.last_scan_map([("0xAAA", "base"), ("0xAAA", "ethereum")])
    assert ("0xaaa", "base") in result
    assert ("0xaaa", "ethereum") in result


@pytest.mark.asyncio
async def test_last_scan_map_case_insensitive_contract():
    await msl.record_scan("0xAAA", "base", "volume_too_low")
    result = await msl.last_scan_map([("0xaaa", "base")])
    assert ("0xaaa", "base") in result


@pytest.mark.asyncio
async def test_last_scan_map_handles_many_pairs_in_one_query():
    pairs = [(f"0x{i:040x}", "base") for i in range(30)]
    for contract, chain in pairs[:10]:
        await msl.record_scan(contract, chain, "volume_too_low")
    result = await msl.last_scan_map(pairs)
    assert len(result) == 10


@pytest.mark.asyncio
async def test_count_distinct_excludes_entries_older_than_window():
    await msl.record_scan("0xAAA", "base", "volume_too_low")  # dans la fenêtre

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    async with aiosqlite.connect(msl.DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS momentum_scan_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, contract TEXT NOT NULL, "
            "chain TEXT NOT NULL DEFAULT 'base', hold_reason TEXT, symbol TEXT, "
            "price REAL, mode TEXT, wallet TEXT, scanned_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO momentum_scan_log (contract, chain, hold_reason, scanned_at) "
            "VALUES (?, ?, ?, ?)",
            ("0xbbb", "base", "volume_too_low", old_ts),
        )
        await db.commit()

    assert await msl.count_distinct_scanned(hours=24.0) == 1  # 0xBBB hors fenêtre
    assert await msl.count_distinct_scanned(hours=96.0) == 2  # fenêtre élargie
