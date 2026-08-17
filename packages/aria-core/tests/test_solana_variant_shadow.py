"""Multi-variant Solana shadow (17/08) -- three parallel entry-threshold
experiments sharing one discovery pass, exit mechanics WITHOUT a trailing
stop. Same isolated-tmp-db + injected-client pattern as
test_solana_pump_shadow.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import solana_variant_shadow as shadow
from aria_core.services.geckoterminal import PoolSnapshot, TrendingPool
from aria_core.services.rugcheck import RugCheckReport

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=RugCheckReport(available=False, error="test default")),
    )
    # Force the DexScreener-primary fallback in _snapshot_with_fallback to
    # miss, so every snapshot call in these tests exercises the injected
    # GeckoTerminal-style FakeClient deterministically.
    from aria_core import solana_pump_shadow as base_shadow
    monkeypatch.setattr(base_shadow.dexscreener, "fetch_token_pairs", AsyncMock(return_value=[]))
    yield
    shadow._ensured_db_paths.clear()


async def _rows(variant: str | None = None):
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        if variant:
            cur = await db.execute(f"SELECT * FROM {shadow.TABLE} WHERE variant = ?", (variant,))
        else:
            cur = await db.execute(f"SELECT * FROM {shadow.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


def _pool(
    *, pool_address="poolA", token_address="tokA", symbol="PUMP",
    price_usd=1.0, m5=18.0, reserve=8000.0, pool_created_at=None,
) -> TrendingPool:
    return TrendingPool(
        pool_address=pool_address, token_address=token_address, symbol=symbol,
        price_usd=price_usd,
        price_change_pct={"m5": m5, "m15": m5, "m30": m5, "h1": m5, "h6": m5, "h24": m5},
        transactions_m15={"buys": 0, "sells": 0, "buyers": 0, "sellers": 0},
        volume_usd_m15=None,
        reserve_usd=reserve,
        pool_created_at=pool_created_at if pool_created_at is not None else (
            datetime.now(timezone.utc)
            - timedelta(minutes=(shadow.MIN_POOL_AGE_MINUTES + shadow.MAX_POOL_AGE_MINUTES) / 2)
        ),
    )


class FakeClient:
    def __init__(self, price_by_pool: dict[str, float | None], reserve_by_pool: dict[str, float] | None = None):
        self._prices = price_by_pool
        self._reserves = dict(reserve_by_pool or {})
        self.calls: list[str] = []

    async def get_pool_snapshot(self, pool_address, *, network="solana"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        reserve = self._reserves.get(pool_address, 1000.0)
        return PoolSnapshot(pool_address=pool_address, price_usd=price, reserve_usd=reserve, available=True)


# --- record_signals: multi-variant classification -------------------------

@pytest.mark.asyncio
async def test_pool_at_12pct_qualifies_5_and_10_but_not_15():
    logged = await shadow.record_signals([_pool(m5=12.0, reserve=8000.0)], chain=CHAIN)
    assert logged == 2
    variants_logged = {r["variant"] for r in await _rows()}
    assert variants_logged == {"m5_5pct", "m5_10pct"}


@pytest.mark.asyncio
async def test_pool_at_20pct_qualifies_all_three():
    logged = await shadow.record_signals([_pool(m5=20.0, reserve=8000.0)], chain=CHAIN)
    assert logged == 3
    variants_logged = {r["variant"] for r in await _rows()}
    assert variants_logged == {"m5_5pct", "m5_10pct", "m5_15pct"}


@pytest.mark.asyncio
async def test_pool_below_all_thresholds_logs_nothing():
    logged = await shadow.record_signals([_pool(m5=3.0)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_liquidity_floor_enforced_per_variant_at_5000():
    # m5=20% qualifies all three thresholds, but reserve=4000 fails EVERY
    # variant's 5000$ liquidity floor -- none should be logged.
    logged = await shadow.record_signals([_pool(m5=20.0, reserve=4000.0)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_liquidity_exactly_at_5000_floor_counts():
    logged = await shadow.record_signals([_pool(m5=20.0, reserve=5000.0)], chain=CHAIN)
    assert logged == 3


# --- record_signals: age window --------------------------------------------

@pytest.mark.asyncio
async def test_pool_too_young_is_skipped():
    too_young = datetime.now(timezone.utc) - timedelta(minutes=shadow.MIN_POOL_AGE_MINUTES - 1)
    logged = await shadow.record_signals([_pool(m5=20.0, pool_created_at=too_young)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_pool_too_old_is_skipped():
    too_old = datetime.now(timezone.utc) - timedelta(minutes=shadow.MAX_POOL_AGE_MINUTES + 1)
    logged = await shadow.record_signals([_pool(m5=20.0, pool_created_at=too_old)], chain=CHAIN)
    assert logged == 0


# --- record_signals: dedupe per variant, not just per pool -----------------

@pytest.mark.asyncio
async def test_same_pool_dedupes_independently_per_variant():
    await shadow.record_signals([_pool(m5=20.0, reserve=8000.0)], chain=CHAIN)
    # Close only the m5_5pct row -- the other two variants stay "open" for
    # this pool.
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"UPDATE {shadow.TABLE} SET exit_reason = 'scale_out_complete' WHERE variant = 'm5_5pct'"
        )
        await db.commit()
    # A second discovery pass on the SAME pool: m5_5pct should re-open
    # (its prior row is closed), the other two should NOT duplicate (still
    # open).
    logged_again = await shadow.record_signals([_pool(m5=20.0, reserve=8000.0)], chain=CHAIN)
    assert logged_again == 1
    rows = await _rows("m5_5pct")
    assert len(rows) == 2
    for variant in ("m5_10pct", "m5_15pct"):
        assert len(await _rows(variant)) == 1


# --- record_signals: stop once a variant hits its target -------------------

@pytest.mark.asyncio
async def test_variant_stops_logging_once_target_reached(monkeypatch):
    monkeypatch.setattr(shadow, "TARGET_CLOSURES_PER_VARIANT", 1)
    await shadow.record_signals([_pool(pool_address="poolA", m5=20.0, reserve=8000.0)], chain=CHAIN)
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(f"UPDATE {shadow.TABLE} SET exit_reason = 'max_hold' WHERE variant = 'm5_5pct'")
        await db.commit()
    # m5_5pct has now closed 1/1 -- a brand new pool should no longer open
    # a position for it, while the other two variants (still under target)
    # keep logging normally.
    logged = await shadow.record_signals([_pool(pool_address="poolB", m5=20.0, reserve=8000.0)], chain=CHAIN)
    assert logged == 2
    assert {r["variant"] for r in await _rows()} == {"m5_5pct", "m5_10pct", "m5_15pct"}
    assert len(await _rows("m5_5pct")) == 1  # only the original closed row, no new one


# --- advance_exit_simulation: no trailing stop ever ------------------------

@pytest.mark.asyncio
async def test_scale_out_fills_and_completes():
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=20.0, reserve=8000.0)], chain=CHAIN)
    # reserve held steady at entry level -- +30% price move must trigger a
    # scale-out fill, not a false liquidity_collapse from FakeClient's default.
    client = FakeClient({"poolA": 1.30}, reserve_by_pool={"poolA": 8000.0})
    result = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert result["scale_out_fills"] >= 3  # all three variants filled at least one rung
    rows = await _rows()
    for r in rows:
        assert r["remaining_qty"] < 1.0


@pytest.mark.asyncio
async def test_liquidity_collapse_closes_immediately():
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=20.0, reserve=8000.0)], chain=CHAIN)
    # Reserve fell below 50% of its entry value (8000 -> under 4000).
    client = FakeClient({"poolA": 0.5}, reserve_by_pool={"poolA": 3000.0})
    result = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert result["closed_liquidity_collapse"] == 3
    rows = await _rows()
    assert all(r["exit_reason"] == "liquidity_collapse" for r in rows)


@pytest.mark.asyncio
async def test_severe_crash_without_trailing_stop_stays_open_until_max_hold():
    """The whole point of this module: a -80% crash that would have fired
    solana_pump_shadow.py's trailing stop must NOT close this variant's
    position early -- it should stay open (still tracked, unrealized loss)
    until max_hold, since no price-based stop exists here."""
    old_enough = datetime.now(timezone.utc) - timedelta(minutes=shadow.MIN_POOL_AGE_MINUTES + 5)
    await shadow.record_signals(
        [_pool(pool_address="poolA", price_usd=1.0, m5=20.0, reserve=8000.0, pool_created_at=old_enough)],
        chain=CHAIN,
    )
    # Reserve unchanged (no liquidity collapse), price crashed -80% -- would
    # have fired a -20% trailing stop many times over in the base module.
    client = FakeClient({"poolA": 0.20}, reserve_by_pool={"poolA": 8000.0})
    result = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert result["closed_liquidity_collapse"] == 0
    assert result["closed_scale_out_complete"] == 0
    rows = await _rows()
    # Still open -- not enough time elapsed for max_hold yet, and there is
    # no trailing_stop exit reason in this module at all.
    assert all(r["exit_reason"] is None for r in rows)
    assert "trailing_stop" not in {r["exit_reason"] for r in rows}


@pytest.mark.asyncio
async def test_max_hold_closes_after_crash_with_no_trailing_stop():
    # pool_created_at must still satisfy the 20-120min DISCOVERY age window
    # (unrelated to how long the position itself has been open) -- only
    # detected_at, backdated below, drives max_hold.
    await shadow.record_signals(
        [_pool(pool_address="poolA", price_usd=1.0, m5=20.0, reserve=8000.0)],
        chain=CHAIN,
    )
    # record_signals stamps detected_at=now, so directly age the rows to
    # simulate a position opened MAX_HOLD_MINUTES+ ago.
    stale = (datetime.now(timezone.utc) - timedelta(minutes=shadow.MAX_HOLD_MINUTES + 1)).isoformat()
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(f"UPDATE {shadow.TABLE} SET detected_at = ?", (stale,))
        await db.commit()
    client = FakeClient({"poolA": 0.20}, reserve_by_pool={"poolA": 8000.0})
    result = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert result["closed_max_hold"] == 3
    rows = await _rows()
    assert all(r["exit_reason"] == "max_hold" for r in rows)
    assert all(r["final_multiplier"] is not None and r["final_multiplier"] < 1.0 for r in rows)


# --- variant_summary --------------------------------------------------------

@pytest.mark.asyncio
async def test_variant_summary_computes_winrate_and_multiplier():
    await shadow._ensure_table()
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (variant, pool_address, chain, detected_at, entry_price, "
            "exit_reason, final_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m5_5pct", "p1", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, "scale_out_complete", 1.5),
        )
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (variant, pool_address, chain, detected_at, entry_price, "
            "exit_reason, final_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m5_5pct", "p2", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, "max_hold", 0.7),
        )
        await db.commit()
    summary = await shadow.variant_summary("m5_5pct", chain=CHAIN)
    assert summary["completed"] == 2
    assert summary["wins"] == 1
    assert summary["win_rate"] == 0.5
    assert summary["avg_multiplier"] == pytest.approx(1.1)
    assert summary["by_exit_reason"] == {"scale_out_complete": 1, "max_hold": 1}


@pytest.mark.asyncio
async def test_closures_so_far_counts_only_closed_rows():
    await shadow.record_signals([_pool(pool_address="poolA", m5=20.0, reserve=8000.0)], chain=CHAIN)
    assert await shadow.closures_so_far("m5_5pct") == 0
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(f"UPDATE {shadow.TABLE} SET exit_reason = 'max_hold' WHERE variant = 'm5_5pct'")
        await db.commit()
    assert await shadow.closures_so_far("m5_5pct") == 1
