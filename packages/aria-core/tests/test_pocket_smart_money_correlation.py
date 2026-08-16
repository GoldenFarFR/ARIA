"""pocket_smart_money_correlation.py (16/08, v11 prep #146) -- shadow log
of whether a known smart-money wallet already held a token before
scalping_v8/v9 entered it. Pure observation: must never raise, never block
the caller, never touch a pocket outside (v8, v9)."""
from __future__ import annotations

import json

import aiosqlite
import pytest

from aria_core import pocket_smart_money_correlation as psmc
from aria_core.services import blockscout as blockscout_module
from aria_core.services import smart_money_leaderboard
from aria_core import wallet_copy_shadow

CONTRACT = "0x" + "a" * 40
SMART_WALLET = "0x" + "b" * 40
OTHER_WALLET = "0x" + "c" * 40


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(psmc, "DB_PATH", str(tmp_path / "psmc.db"))
    yield


@pytest.fixture()
def _enabled(monkeypatch):
    monkeypatch.setenv("ARIA_POCKET_SMART_MONEY_CORRELATION_ENABLED", "true")


class _FakeHolder:
    def __init__(self, address):
        self.address = address


class _FakeHoldersResult:
    def __init__(self, holders, available=True):
        self.holders = holders
        self.available = available


async def _rows():
    await psmc._ensure_table()
    async with aiosqlite.connect(psmc.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM pocket_smart_money_correlation_log ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]


def _mock_sources(monkeypatch, *, leaderboard_wallets=(), tracked_wallets=(), holders, available=True):
    async def _fake_leaderboard():
        return [{"wallet": w} for w in leaderboard_wallets]

    monkeypatch.setattr(smart_money_leaderboard, "get_leaderboard", _fake_leaderboard)
    monkeypatch.setattr(wallet_copy_shadow, "TRACKED_WALLETS", {w: {} for w in tracked_wallets})

    async def _fake_dynamic():
        return {}

    monkeypatch.setattr(wallet_copy_shadow, "_dynamic_tracked_wallets", _fake_dynamic)

    async def _fake_get_token_holders(self, token_address):
        return _FakeHoldersResult([_FakeHolder(h) for h in holders], available=available)

    monkeypatch.setattr(blockscout_module.BlockscoutClient, "get_token_holders", _fake_get_token_holders)


@pytest.mark.asyncio
async def test_gate_disabled_is_a_clean_noop(monkeypatch):
    _mock_sources(monkeypatch, leaderboard_wallets=[SMART_WALLET], holders=[SMART_WALLET])
    await psmc.record_entry_correlation(1, "scalping_v8", CONTRACT, "base")
    assert await _rows() == []


@pytest.mark.asyncio
async def test_pocket_outside_v8_v9_is_a_clean_noop(_enabled, monkeypatch):
    _mock_sources(monkeypatch, leaderboard_wallets=[SMART_WALLET], holders=[SMART_WALLET])
    await psmc.record_entry_correlation(1, "swing", CONTRACT, "base")
    assert await _rows() == []


@pytest.mark.asyncio
async def test_smart_wallet_present_at_entry_is_recorded(_enabled, monkeypatch):
    _mock_sources(
        monkeypatch,
        leaderboard_wallets=[SMART_WALLET],
        tracked_wallets=[],
        holders=[SMART_WALLET, OTHER_WALLET],
    )
    await psmc.record_entry_correlation(42, "scalping_v8", CONTRACT, "base")

    rows = await _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["position_id"] == 42
    assert row["pocket"] == "scalping_v8"
    assert row["contract"] == CONTRACT.lower()
    assert json.loads(row["smart_wallets_present"]) == [SMART_WALLET]
    assert row["smart_wallets_checked"] == 1


@pytest.mark.asyncio
async def test_no_smart_wallet_present_still_recorded_with_empty_list(_enabled, monkeypatch):
    _mock_sources(monkeypatch, leaderboard_wallets=[SMART_WALLET], holders=[OTHER_WALLET])
    await psmc.record_entry_correlation(7, "scalping_v9", CONTRACT, "base")

    rows = await _rows()
    assert len(rows) == 1
    assert json.loads(rows[0]["smart_wallets_present"]) == []
    assert rows[0]["smart_wallets_checked"] == 1


@pytest.mark.asyncio
async def test_leaderboard_failure_degrades_gracefully_never_raises(_enabled, monkeypatch):
    async def _boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(smart_money_leaderboard, "get_leaderboard", _boom)
    monkeypatch.setattr(wallet_copy_shadow, "TRACKED_WALLETS", {})

    async def _fake_dynamic():
        return {}

    monkeypatch.setattr(wallet_copy_shadow, "_dynamic_tracked_wallets", _fake_dynamic)

    # No known wallets at all (both sources empty/failed) -> zero-cost path,
    # Blockscout is never even called.
    await psmc.record_entry_correlation(1, "scalping_v8", CONTRACT, "base")
    rows = await _rows()
    assert len(rows) == 1
    assert json.loads(rows[0]["smart_wallets_present"]) == []
    assert rows[0]["smart_wallets_checked"] == 0


@pytest.mark.asyncio
async def test_blockscout_unavailable_degrades_to_no_match_never_raises(_enabled, monkeypatch):
    _mock_sources(monkeypatch, leaderboard_wallets=[SMART_WALLET], holders=[], available=False)
    await psmc.record_entry_correlation(1, "scalping_v8", CONTRACT, "base")

    rows = await _rows()
    assert len(rows) == 1
    assert json.loads(rows[0]["smart_wallets_present"]) == []


@pytest.mark.asyncio
async def test_db_insert_failure_never_raises(_enabled, monkeypatch):
    _mock_sources(monkeypatch, leaderboard_wallets=[SMART_WALLET], holders=[SMART_WALLET])
    monkeypatch.setattr(psmc, "DB_PATH", "/nonexistent/dir/x.db")
    # Never raises -- best-effort, a real position must never be affected.
    await psmc.record_entry_correlation(1, "scalping_v8", CONTRACT, "base")


@pytest.mark.asyncio
async def test_summary_on_empty_log_reports_zero_never_fabricates(monkeypatch):
    result = await psmc.summary()
    assert result == {"logged_entries": 0, "with_smart_money": None, "without_smart_money": None}


@pytest.mark.asyncio
async def test_summary_splits_winrate_by_smart_money_presence(_enabled, monkeypatch):
    _mock_sources(monkeypatch, leaderboard_wallets=[SMART_WALLET], holders=[SMART_WALLET])
    await psmc.record_entry_correlation(1, "scalping_v8", CONTRACT, "base")  # with smart money

    _mock_sources(monkeypatch, leaderboard_wallets=[SMART_WALLET], holders=[OTHER_WALLET])
    await psmc.record_entry_correlation(2, "scalping_v8", CONTRACT, "base")  # without

    async with aiosqlite.connect(psmc.DB_PATH) as db:
        await db.execute(
            "CREATE TABLE paper_position (id INTEGER PRIMARY KEY, status TEXT, pnl_pct REAL)"
        )
        await db.execute(
            "CREATE TABLE paper_position_archive (id INTEGER PRIMARY KEY, status TEXT, pnl_pct REAL)"
        )
        await db.execute("INSERT INTO paper_position (id, status, pnl_pct) VALUES (1, 'closed', 5.0)")
        await db.execute("INSERT INTO paper_position (id, status, pnl_pct) VALUES (2, 'closed', -3.0)")
        await db.commit()

    result = await psmc.summary()
    assert result["logged_entries"] == 2
    assert result["with_smart_money"] == {"n_closed": 1, "winrate_pct": 100.0, "avg_pnl_pct": 5.0}
    assert result["without_smart_money"] == {"n_closed": 1, "winrate_pct": 0.0, "avg_pnl_pct": -3.0}
