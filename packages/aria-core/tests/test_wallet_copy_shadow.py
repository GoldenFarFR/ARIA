"""Wallet-copy shadow -- fictional per-wallet ledger, never real capital,
never a live trigger (cf. module docstring). Same isolated-DB test pattern
as test_narrative_signal_shadow.py."""
from __future__ import annotations

import pytest

from aria_core import wallet_copy_shadow as wcs
from aria_core.services.blockscout import TokenTransfer, TokenTransfersResult

WALLET = list(wcs.TRACKED_WALLETS.keys())[0]
META = wcs.TRACKED_WALLETS[WALLET]
TOKEN = "0x" + "c" * 40
WETH = "0x4200000000000000000000000000000000000006"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "wallet_copy_shadow.db")
    monkeypatch.setattr(wcs, "DB_PATH", db_path)
    monkeypatch.setattr(wcs, "_table_ready", False)
    yield


def _transfer(*, tx_hash, to_address="", from_address="", token_address=TOKEN, symbol="TP"):
    return TokenTransfer(
        tx_hash=tx_hash, from_address=from_address, to_address=to_address,
        token_address=token_address, token_symbol=symbol, token_name="TestProto",
        amount=1_000.0, timestamp="2026-08-08T12:00:00Z",
    )


def _mock_transfers(monkeypatch, transfers, *, available=True, error=None):
    async def _fake(address, limit=50, *, max_pages=1, token_type=None):
        return TokenTransfersResult(transfers=transfers, available=available, error=error)

    from aria_core.services.blockscout import blockscout_client

    monkeypatch.setattr(blockscout_client, "get_token_transfers", _fake)


def _mock_price(monkeypatch, price):
    async def _fake(contract):
        return price

    monkeypatch.setattr(wcs, "_current_price_usd", _fake)


@pytest.mark.asyncio
async def test_detected_buy_opens_a_fictional_position(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 1
    assert result.closed == 0
    stats = (await wcs.summary())[WALLET]
    assert stats["open_positions"] == 1
    assert stats["closed_positions"] == 0


@pytest.mark.asyncio
async def test_matching_sell_closes_the_position_and_computes_realized_pnl(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    await wcs.scan_wallet(WALLET, META)

    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xsell1", from_address=WALLET)])
    _mock_price(monkeypatch, 2.0)  # doubled -> +100% on the fictional POSITION_SIZE_USD stake
    result = await wcs.scan_wallet(WALLET, META)
    assert result.closed == 1

    stats = (await wcs.summary())[WALLET]
    assert stats["closed_positions"] == 1
    assert stats["open_positions"] == 0
    assert stats["realized_pnl_usd"] == pytest.approx(wcs.POSITION_SIZE_USD, rel=1e-6)


@pytest.mark.asyncio
async def test_quote_token_transfer_is_never_copied(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xweth1", to_address=WALLET, token_address=WETH, symbol="WETH")])
    _mock_price(monkeypatch, 1.0)
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 0
    assert (await wcs.summary())[WALLET]["open_positions"] == 0


@pytest.mark.asyncio
async def test_second_buy_while_already_open_is_ignored(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    await wcs.scan_wallet(WALLET, META)

    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy2", to_address=WALLET)])
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 0
    assert (await wcs.summary())[WALLET]["open_positions"] == 1


@pytest.mark.asyncio
async def test_cursor_prevents_reprocessing_the_same_transfer(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    first = await wcs.scan_wallet(WALLET, META)
    assert first.opened == 1

    # Same transfer still returned by Blockscout (unchanged window) -- must
    # not open a second fictional position for the already-open contract,
    # and cursor logic must not reprocess a transfer already handled.
    second = await wcs.scan_wallet(WALLET, META)
    assert second.opened == 0
    assert (await wcs.summary())[WALLET]["open_positions"] == 1


@pytest.mark.asyncio
async def test_blockscout_failure_is_best_effort_never_raises(monkeypatch):
    _mock_transfers(monkeypatch, [], available=False, error="blockscout unavailable")
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 0
    assert result.closed == 0
    assert result.error == "blockscout unavailable"


@pytest.mark.asyncio
async def test_scan_failure_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(wcs, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(wcs, "_table_ready", False)
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    result = await wcs.scan_wallet(WALLET, META)
    assert result.error is not None


@pytest.mark.asyncio
async def test_summary_reports_all_tracked_wallets_even_with_no_activity():
    result = await wcs.summary()
    assert set(result.keys()) == set(wcs.TRACKED_WALLETS.keys())
    for wallet, stats in result.items():
        assert stats["closed_positions"] == 0
        assert stats["open_positions"] == 0
        assert stats["label"] == wcs.TRACKED_WALLETS[wallet]["label"]
        assert stats["status"] == "unknown"  # never scanned yet -- no activity clock started


@pytest.mark.asyncio
async def test_summary_reports_active_after_a_recent_transfer(monkeypatch):
    from datetime import datetime, timezone

    recent = datetime.now(timezone.utc).isoformat()
    _mock_price(monkeypatch, 1.0)
    # Uses a genuinely fresh timestamp -- _transfer()'s default is fixed
    # (2026-08-08), which would already be "dormant" by the time this test
    # runs on a later date.
    import aria_core.services.blockscout as bc

    def _fresh_transfer(*, tx_hash, to_address="", from_address="", token_address=TOKEN, symbol="TP"):
        return bc.TokenTransfer(
            tx_hash=tx_hash, from_address=from_address, to_address=to_address,
            token_address=token_address, token_symbol=symbol, token_name="TestProto",
            amount=1_000.0, timestamp=recent,
        )

    _mock_transfers(monkeypatch, [_fresh_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    await wcs.scan_wallet(WALLET, META)
    stats = (await wcs.summary())[WALLET]
    assert stats["status"] == "active"


@pytest.mark.asyncio
async def test_summary_reports_dormant_after_inactivity_window():
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=wcs.INACTIVITY_THRESHOLD_DAYS + 1)).isoformat()
    await wcs._ensure_tables()
    await wcs._set_cursor(WALLET, "0xold", last_transfer_at=old)
    stats = (await wcs.summary())[WALLET]
    assert stats["status"] == "dormant"


def test_songz_wallet_age_caveat_documented():
    """08/08 -- vérifié réel (blockscout, première tx 2026-07-12, ~1 mois),
    incohérent avec le "all-time" 2432-trade que fomoscan attribue au handle
    -- verrouille contre une régression silencieuse de la mise en garde."""
    songz = wcs.TRACKED_WALLETS["0xd3a61ba3bd055f6aa962cc7554e117b4baf8d0a5"]
    assert songz["wallet_first_tx_at"] == "2026-07-12"
    assert "CAVEAT" in songz["evidence"]


@pytest.mark.asyncio
async def test_run_scan_cycle_covers_all_eight_wallets(monkeypatch):
    async def _empty(address, limit=50, *, max_pages=1, token_type=None):
        return TokenTransfersResult(transfers=[], available=True, error=None)

    from aria_core.services.blockscout import blockscout_client

    monkeypatch.setattr(blockscout_client, "get_token_transfers", _empty)
    results = await wcs.run_scan_cycle()
    assert len(results) == len(wcs.TRACKED_WALLETS) == 8


def test_position_size_is_a_fixed_fictional_stake_not_real_capital():
    assert wcs.POSITION_SIZE_USD == 1_000.0


@pytest.mark.asyncio
async def test_current_price_rejects_a_dust_pool_below_liquidity_floor(monkeypatch):
    """10/08 real bug: a spam token's near-empty pool still returns a
    'price' from DexScreener -- must degrade to None like no pool found,
    never be trusted as a real market price."""
    from aria_core.services import dexscreener as ds

    async def _fake_pairs(contract, *, chain="base"):
        return [ds.PairSnapshot(liquidity_usd=1.45, price_usd=4.268e-24)]

    monkeypatch.setattr(ds, "fetch_token_pairs", _fake_pairs)
    price = await wcs._current_price_usd(TOKEN)
    assert price is None


@pytest.mark.asyncio
async def test_current_price_accepts_a_pool_above_liquidity_floor(monkeypatch):
    from aria_core.services import dexscreener as ds

    async def _fake_pairs(contract, *, chain="base"):
        return [ds.PairSnapshot(liquidity_usd=50_000.0, price_usd=0.05)]

    monkeypatch.setattr(ds, "fetch_token_pairs", _fake_pairs)
    price = await wcs._current_price_usd(TOKEN)
    assert price == 0.05


@pytest.mark.asyncio
async def test_summary_excludes_implausible_price_ratio_artifacts():
    """A dust-pool artifact recorded before the liquidity-floor fix existed
    (or any future edge case it doesn't catch) must not pollute the
    aggregate P&L -- mechanical safety net independent of the floor above."""
    await wcs._ensure_tables()
    import aiosqlite

    async with aiosqlite.connect(wcs.DB_PATH) as db:
        # Closed position: absurd ratio (dust artifact) -- must be excluded.
        await db.execute(
            "INSERT INTO wallet_copy_shadow_position "
            "(wallet_address, wallet_label, contract, entry_tx_hash, entry_price_usd, "
            " entry_at, status, exit_tx_hash, exit_price_usd, exit_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?)",
            (WALLET, META["label"], TOKEN, "0xdust", 4.268e-24,
             "2026-08-08T12:00:00Z", "0xdustexit", 3.687e-08, "2026-08-09T12:00:00Z"),
        )
        # Closed position: plausible x3 gain -- must be counted.
        await db.execute(
            "INSERT INTO wallet_copy_shadow_position "
            "(wallet_address, wallet_label, contract, entry_tx_hash, entry_price_usd, "
            " entry_at, status, exit_tx_hash, exit_price_usd, exit_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?)",
            (WALLET, META["label"], "0x" + "d" * 40, "0xreal", 1.0,
             "2026-08-08T12:00:00Z", "0xrealexit", 3.0, "2026-08-09T12:00:00Z"),
        )
        await db.commit()

    result = await wcs.summary()
    entry = result[WALLET]
    assert entry["closed_positions"] == 1
    assert entry["realized_pnl_usd"] == pytest.approx(2_000.0)


# ── #146 (14/08): leaderboard-sourced dynamic candidates, seam for a future
#    scalping pocket (v11, undesigned) -- closes the gap between the real
#    smart_money_leaderboard (dynamic scoring) and this module's previously
#    static, hand-picked TRACKED_WALLETS list ─────────────────────────────

LEADERBOARD_WALLET_HIGH = "0x" + "1" * 40
LEADERBOARD_WALLET_LOW = "0x" + "2" * 40


async def _seed_leaderboard(entries: list[tuple]) -> None:
    """``entries``: list of (wallet, composite_percentile). Recreates the
    real smart_money_leaderboard schema directly in the isolated test DB --
    this module reads it via a raw SQL SELECT, never imports
    smart_money_leaderboard.py itself (pure local-DB read, same doctrine as
    the rest of this module)."""
    import aiosqlite

    async with aiosqlite.connect(wcs.DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS smart_money_leaderboard ("
            "wallet TEXT PRIMARY KEY, composite_percentile REAL NOT NULL, "
            "joined_at TEXT NOT NULL, last_updated_at TEXT NOT NULL)"
        )
        for wallet, pct in entries:
            await db.execute(
                "INSERT INTO smart_money_leaderboard (wallet, composite_percentile, joined_at, last_updated_at) "
                "VALUES (?, ?, '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z')",
                (wallet, pct),
            )
        await db.commit()


@pytest.mark.asyncio
async def test_discover_leaderboard_candidates_adds_wallets_above_threshold():
    await _seed_leaderboard([(LEADERBOARD_WALLET_HIGH, 90.0), (LEADERBOARD_WALLET_LOW, 50.0)])
    added = await wcs.discover_leaderboard_candidates(min_percentile=80.0)
    assert added == 1
    dynamic = await wcs._dynamic_tracked_wallets()
    assert LEADERBOARD_WALLET_HIGH in dynamic
    assert LEADERBOARD_WALLET_LOW not in dynamic
    assert dynamic[LEADERBOARD_WALLET_HIGH]["tier"] == "leaderboard_dynamic"


@pytest.mark.asyncio
async def test_discover_leaderboard_candidates_never_duplicates_static_wallets():
    """A wallet already hand-picked in TRACKED_WALLETS must never also be
    added dynamically -- would double-scan it and blend evidence tiers."""
    await _seed_leaderboard([(WALLET, 99.0)])
    added = await wcs.discover_leaderboard_candidates(min_percentile=80.0)
    assert added == 0
    dynamic = await wcs._dynamic_tracked_wallets()
    assert WALLET not in dynamic


@pytest.mark.asyncio
async def test_discover_leaderboard_candidates_idempotent_across_cycles():
    await _seed_leaderboard([(LEADERBOARD_WALLET_HIGH, 90.0)])
    first = await wcs.discover_leaderboard_candidates(min_percentile=80.0)
    second = await wcs.discover_leaderboard_candidates(min_percentile=80.0)
    assert first == 1
    assert second == 0  # already tracked, never re-added/duplicated


@pytest.mark.asyncio
async def test_discover_leaderboard_candidates_no_table_degrades_to_zero():
    """smart_money_leaderboard doesn't exist yet in this isolated DB (never
    seeded) -- must degrade to 0, never raise."""
    added = await wcs.discover_leaderboard_candidates(min_percentile=80.0)
    assert added == 0


# ----------------------- registry lifecycle (#172, 15/08) -----------------------


@pytest.mark.asyncio
async def test_discover_leaderboard_candidates_evicts_worst_when_full():
    entries = [(f"0x{i:040x}", 80.0 + i) for i in range(wcs.MAX_DYNAMIC_CANDIDATES)]
    await _seed_leaderboard(entries)
    added = await wcs.discover_leaderboard_candidates(min_percentile=80.0)
    assert added == wcs.MAX_DYNAMIC_CANDIDATES
    worst_wallet = entries[0][0]  # lowest percentile (80.0) of the batch

    newcomer = "0x" + "9" * 40
    await _seed_leaderboard([(newcomer, 999.0)])
    added_second = await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    assert added_second == 1
    dynamic = await wcs._dynamic_tracked_wallets()
    assert len(dynamic) == wcs.MAX_DYNAMIC_CANDIDATES  # never grows past the cap
    assert newcomer in dynamic
    assert worst_wallet not in dynamic  # evicted to make room


@pytest.mark.asyncio
async def test_discover_leaderboard_candidates_skips_when_not_better_than_worst_and_full():
    entries = [(f"0x{i:040x}", 80.0 + i) for i in range(wcs.MAX_DYNAMIC_CANDIDATES)]
    await _seed_leaderboard(entries)
    await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    weak_newcomer = "0x" + "8" * 40
    await _seed_leaderboard([(weak_newcomer, 80.0)])  # ties the current worst, not strictly better
    added = await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    assert added == 0
    dynamic = await wcs._dynamic_tracked_wallets()
    assert weak_newcomer not in dynamic
    assert len(dynamic) == wcs.MAX_DYNAMIC_CANDIDATES  # unchanged, no eviction for nothing


@pytest.mark.asyncio
async def test_evict_stale_dynamic_candidates_removes_expired_ttl():
    import aiosqlite

    await _seed_leaderboard([(LEADERBOARD_WALLET_HIGH, 90.0)])
    await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    async with aiosqlite.connect(wcs.DB_PATH) as db:
        await db.execute(
            "UPDATE wallet_copy_shadow_dynamic_candidates SET added_at = ? WHERE wallet_address = ?",
            ("2020-01-01T00:00:00+00:00", LEADERBOARD_WALLET_HIGH),
        )
        await db.commit()

    evicted = await wcs.evict_stale_dynamic_candidates()

    assert evicted == 1
    dynamic = await wcs._dynamic_tracked_wallets()
    assert LEADERBOARD_WALLET_HIGH not in dynamic


@pytest.mark.asyncio
async def test_evict_stale_dynamic_candidates_removes_dormant_wallets():
    import aiosqlite

    await _seed_leaderboard([(LEADERBOARD_WALLET_HIGH, 90.0)])
    await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    async with aiosqlite.connect(wcs.DB_PATH) as db:
        await db.execute(
            "INSERT INTO wallet_copy_shadow_cursor "
            "(wallet_address, last_tx_hash, last_scanned_at, last_transfer_at) "
            "VALUES (?, 'somehash', '2026-08-15T00:00:00+00:00', ?)",
            (LEADERBOARD_WALLET_HIGH, "2020-01-01T00:00:00+00:00"),
        )
        await db.commit()

    evicted = await wcs.evict_stale_dynamic_candidates()

    assert evicted == 1
    dynamic = await wcs._dynamic_tracked_wallets()
    assert LEADERBOARD_WALLET_HIGH not in dynamic


@pytest.mark.asyncio
async def test_evict_stale_dynamic_candidates_never_evicts_unscanned_candidate():
    """No cursor row yet (never scanned once) -- 'not scanned yet' is
    distinct from 'went quiet', must never be evicted as dormant."""
    await _seed_leaderboard([(LEADERBOARD_WALLET_HIGH, 90.0)])
    await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    evicted = await wcs.evict_stale_dynamic_candidates()

    assert evicted == 0
    dynamic = await wcs._dynamic_tracked_wallets()
    assert LEADERBOARD_WALLET_HIGH in dynamic


@pytest.mark.asyncio
async def test_evict_stale_dynamic_candidates_never_touches_static_wallets():
    """The 8 hand-picked TRACKED_WALLETS are permanent -- eviction only ever
    reads/writes wallet_copy_shadow_dynamic_candidates."""
    evicted = await wcs.evict_stale_dynamic_candidates()
    assert evicted == 0
    assert WALLET in wcs.TRACKED_WALLETS


@pytest.mark.asyncio
async def test_evict_stale_dynamic_candidates_no_table_degrades_to_zero():
    evicted = await wcs.evict_stale_dynamic_candidates()
    assert evicted == 0


@pytest.mark.asyncio
async def test_run_scan_cycle_includes_dynamic_candidates(monkeypatch):
    await _seed_leaderboard([(LEADERBOARD_WALLET_HIGH, 90.0)])
    await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    scanned = []

    async def _fake_scan(wallet, meta):
        scanned.append(wallet)
        return wcs.ShadowScanResult(wallet, 0, 0)

    async def _fake_refresh(wallet):
        return None

    monkeypatch.setattr(wcs, "scan_wallet", _fake_scan)
    monkeypatch.setattr(wcs, "refresh_open_marks", _fake_refresh)

    await wcs.run_scan_cycle()

    assert LEADERBOARD_WALLET_HIGH in scanned
    assert WALLET in scanned  # the 8 static wallets are still scanned too


@pytest.mark.asyncio
async def test_summary_reports_dynamic_candidates_separately():
    await _seed_leaderboard([(LEADERBOARD_WALLET_HIGH, 90.0)])
    await wcs.discover_leaderboard_candidates(min_percentile=80.0)

    result = await wcs.summary()

    assert LEADERBOARD_WALLET_HIGH in result
    assert result[LEADERBOARD_WALLET_HIGH]["tier"] == "leaderboard_dynamic"
    assert "90.0" in result[LEADERBOARD_WALLET_HIGH]["evidence"]
