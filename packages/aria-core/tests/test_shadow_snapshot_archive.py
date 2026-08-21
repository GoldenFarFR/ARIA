"""Shared exit-check snapshot archive (18/08) -- store_snapshot/get_snapshots,
one row per check (no dedup key, every call is a fresh timestamp), never
raises into the caller."""
from __future__ import annotations

import pytest

from aria_core import shadow_snapshot_archive as archive


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "DB_PATH", str(tmp_path / "shadow.db"))
    archive._ensured_db_paths.clear()
    await archive._ensure_table()
    yield
    archive._ensured_db_paths.clear()


async def test_store_and_read_back_a_full_snapshot():
    ok = await archive.store_snapshot(
        module="solana_support_bounce", position_id=1, pool_address="pool1", chain="solana",
        price_usd=1.23, reserve_usd=5000.0, dex_id="raydium",
        price_change_pct={"m5": 1.0, "m15": 2.0, "m30": 3.0, "h1": 4.0, "h6": 5.0, "h24": 6.0},
        transactions={"h1": {"buys": 10, "sells": 4}},
        volume_usd={"h1": 900.0},
    )
    assert ok is True
    rows = await archive.get_snapshots(module="solana_support_bounce", position_id=1)
    assert len(rows) == 1
    row = rows[0]
    assert row["price_usd"] == pytest.approx(1.23)
    assert row["reserve_usd"] == pytest.approx(5000.0)
    assert row["dex_id"] == "raydium"
    assert row["price_change_m5"] == pytest.approx(1.0)
    assert row["price_change_h24"] == pytest.approx(6.0)
    assert row["transactions_json"] == '{"h1": {"buys": 10, "sells": 4}}'
    assert row["volume_usd_json"] == '{"h1": 900.0}'


async def test_repeated_checks_accumulate_one_row_each_never_overwrite():
    for price in (1.0, 1.1, 1.2):
        await archive.store_snapshot(
            module="solana_support_bounce", position_id=2, pool_address="poolX", chain="solana",
            price_usd=price, reserve_usd=1000.0, dex_id=None,
            price_change_pct=None, transactions=None, volume_usd=None,
        )
    rows = await archive.get_snapshots(module="solana_support_bounce", position_id=2)
    assert len(rows) == 3
    assert [r["price_usd"] for r in rows] == [1.0, 1.1, 1.2]


async def test_missing_optional_fields_stay_null_never_fabricated():
    ok = await archive.store_snapshot(
        module="solana_support_bounce_v2", position_id=3, pool_address="poolY", chain="solana",
        price_usd=2.0, reserve_usd=None, dex_id=None,
        price_change_pct={}, transactions=None, volume_usd=None,
    )
    assert ok is True
    rows = await archive.get_snapshots(module="solana_support_bounce_v2", position_id=3)
    row = rows[0]
    assert row["reserve_usd"] is None
    assert row["dex_id"] is None
    assert row["price_change_m5"] is None
    assert row["transactions_json"] is None
    assert row["volume_usd_json"] is None


async def test_different_modules_never_collide_on_same_position_id():
    await archive.store_snapshot(
        module="solana_support_bounce", position_id=1, pool_address="poolA", chain="solana",
        price_usd=1.0, reserve_usd=1.0, dex_id=None, price_change_pct=None, transactions=None, volume_usd=None,
    )
    await archive.store_snapshot(
        module="solana_support_bounce_v2", position_id=1, pool_address="poolB", chain="solana",
        price_usd=2.0, reserve_usd=2.0, dex_id=None, price_change_pct=None, transactions=None, volume_usd=None,
    )
    v1_rows = await archive.get_snapshots(module="solana_support_bounce", position_id=1)
    v2_rows = await archive.get_snapshots(module="solana_support_bounce_v2", position_id=1)
    assert len(v1_rows) == 1
    assert len(v2_rows) == 1


async def test_store_snapshot_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*args, **kwargs):
        raise RuntimeError("simulated db outage")

    import aiosqlite

    monkeypatch.setattr(aiosqlite, "connect", _broken_connect)
    ok = await archive.store_snapshot(
        module="solana_support_bounce", position_id=1, pool_address="pool1", chain="solana",
        price_usd=1.0, reserve_usd=1.0, dex_id=None, price_change_pct=None, transactions=None, volume_usd=None,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_window_extremes_are_stored_as_named_columns(tmp_path, monkeypatch):
    """21/08 -- these were first passed through `price_change_pct`, whose
    fixed key set (m5/m15/m30/h1/h6/h24) silently dropped them: 149 rows
    archived with the extremes missing and no error anywhere. A dict-keyed
    passthrough will always swallow a key it does not know; a named column
    cannot."""
    import aiosqlite

    from aria_core import shadow_snapshot_archive as arch

    db_path = str(tmp_path / "snap.db")
    monkeypatch.setattr(arch, "_db_path", lambda: db_path)
    arch._ensured_db_paths.discard(db_path)

    assert await arch.store_snapshot(
        module="test_module", position_id=1, pool_address="pool", chain="solana",
        price_usd=0.001, reserve_usd=9_000.0, dex_id="pumpfun",
        price_change_pct=None, transactions=None, volume_usd=None,
        window_high=0.0012, window_low=0.0008,
    )

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            f"SELECT window_high, window_low FROM {arch.TABLE} WHERE position_id = 1"
        )
        assert await cur.fetchone() == (0.0012, 0.0008)
