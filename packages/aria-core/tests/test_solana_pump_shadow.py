"""Solana "take the train" shadow (16/08) -- pure read+log, never a trigger.
Mirrors v8_rsi_reversal_shadow's isolated-tmp-db + detect/measure state-
machine test pattern; the GeckoTerminal client is always injected/mocked,
never a real network call (same doctrine as test_geckoterminal_client.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import solana_pump_shadow as shadow
from aria_core.services.geckoterminal import PoolSnapshot, TrendingPool

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    yield
    shadow._ensured_db_paths.clear()


async def _rows():
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM solana_pump_shadow_log")
        return [dict(r) for r in await cur.fetchall()]


def _pool(
    *, pool_address="poolA", token_address="tokA", symbol="PUMP",
    price_usd=1.0, m15=30.0, buyers=20, sellers=5, volume_m15=5000.0, reserve=100000.0,
) -> TrendingPool:
    return TrendingPool(
        pool_address=pool_address, token_address=token_address, symbol=symbol,
        price_usd=price_usd,
        price_change_pct={"m5": 5.0, "m15": m15, "m30": m15 + 5, "h1": m15 + 10, "h6": m15 + 20, "h24": m15 + 30},
        transactions_m15={"buys": 40, "sells": 10, "buyers": buyers, "sellers": sellers},
        volume_usd_m15=volume_m15,
        reserve_usd=reserve,
    )


class FakeClient:
    """Injected in place of GeckoTerminalClient -- only get_pool_snapshot is
    exercised by evaluate_open_signals, exactly what these tests need."""

    def __init__(self, price_by_pool: dict[str, float | None]):
        self._prices = price_by_pool
        self.calls: list[str] = []

    async def get_pool_snapshot(self, pool_address, *, network="solana"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        return PoolSnapshot(pool_address=pool_address, price_usd=price, reserve_usd=1000.0, available=True)


# --- record_signals ------------------------------------------------------

@pytest.mark.asyncio
async def test_record_signals_logs_pool_above_threshold():
    logged = await shadow.record_signals([_pool(m15=30.0)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["pool_address"] == "poolA"
    assert rows[0]["status"] == "open"
    assert rows[0]["m15_pct"] == 30.0
    assert rows[0]["entry_price"] == 1.0
    assert rows[0]["buyers_m15"] == 20
    assert rows[0]["sellers_m15"] == 5
    assert rows[0]["volume_usd_m15"] == 5000.0
    assert rows[0]["reserve_usd"] == 100000.0
    assert rows[0]["symbol"] == "PUMP"


@pytest.mark.asyncio
async def test_record_signals_ignores_pool_below_threshold():
    logged = await shadow.record_signals([_pool(m15=24.9)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_exactly_at_threshold_counts():
    logged = await shadow.record_signals([_pool(m15=shadow.M15_SURGE_THRESHOLD_PCT)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_record_signals_never_fabricates_entry_price():
    logged = await shadow.record_signals([_pool(m15=40.0, price_usd=None)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_dedupes_while_already_open():
    await shadow.record_signals([_pool(m15=30.0, price_usd=1.0)], chain=CHAIN)
    logged_again = await shadow.record_signals([_pool(m15=45.0, price_usd=1.4)], chain=CHAIN)
    assert logged_again == 0
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["entry_price"] == 1.0  # first signal wins, never overwritten


@pytest.mark.asyncio
async def test_record_signals_relogs_after_previous_signal_closed():
    await shadow.record_signals([_pool(m15=30.0)], chain=CHAIN)
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute("UPDATE solana_pump_shadow_log SET status = 'closed'")
        await db.commit()
    logged_again = await shadow.record_signals([_pool(m15=30.0)], chain=CHAIN)
    assert logged_again == 1
    rows = await _rows()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_record_signals_multiple_pools_independent():
    pools = [_pool(pool_address="poolA", m15=30.0), _pool(pool_address="poolB", m15=26.0)]
    logged = await shadow.record_signals(pools, chain=CHAIN)
    assert logged == 2
    assert {r["pool_address"] for r in await _rows()} == {"poolA", "poolB"}


@pytest.mark.asyncio
async def test_record_signals_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    shadow._ensured_db_paths.clear()
    logged = await shadow.record_signals([_pool(m15=30.0)], chain=CHAIN)
    assert logged == 0  # fails closed, never raises into the caller


# --- evaluate_open_signals -------------------------------------------------

async def _insert_open_row(*, pool_address="poolA", entry_price=1.0, minutes_ago=20.0):
    detected_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            """
            INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price)
            VALUES (?, ?, 'open', ?, ?)
            """,
            (pool_address, CHAIN, detected_at, entry_price),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_evaluate_measures_m15_once_aged_past_15_minutes():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=20.0)
    client = FakeClient({"poolA": 1.5})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 1
    rows = await _rows()
    assert rows[0]["forward_price_m15"] == 1.5
    assert rows[0]["forward_pct_m15"] == pytest.approx(50.0)
    assert rows[0]["status"] == "open"  # only h2 closes the row


@pytest.mark.asyncio
async def test_evaluate_skips_pool_not_yet_old_enough():
    await _insert_open_row(minutes_ago=5.0)  # younger than the 15min horizon
    client = FakeClient({"poolA": 2.0})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts == {"measured_m15": 0, "measured_h1": 0, "measured_h2": 0, "closed": 0}
    assert client.calls == []


@pytest.mark.asyncio
async def test_evaluate_closes_row_on_h2_checkpoint():
    await _insert_open_row(entry_price=1.0, minutes_ago=130.0)
    client = FakeClient({"poolA": 0.8})
    # First pass advances m15, second h1, third h2 -- exercise all three in sequence.
    await shadow.evaluate_open_signals(client, chain=CHAIN)
    await shadow.evaluate_open_signals(client, chain=CHAIN)
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["closed"] == 1
    rows = await _rows()
    assert rows[0]["status"] == "closed"
    assert rows[0]["forward_pct_h2"] == pytest.approx(-20.0)
    assert rows[0]["closed_at"] is not None


@pytest.mark.asyncio
async def test_evaluate_never_fabricates_when_snapshot_unavailable():
    await _insert_open_row(minutes_ago=20.0)
    client = FakeClient({"poolA": None})  # unavailable
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 0
    rows = await _rows()
    assert rows[0]["forward_price_m15"] is None
    assert rows[0]["status"] == "open"  # left for a future retry, not force-closed


@pytest.mark.asyncio
async def test_evaluate_one_pool_failure_never_blocks_others():
    await _insert_open_row(pool_address="poolA", minutes_ago=20.0)
    await _insert_open_row(pool_address="poolB", minutes_ago=20.0)

    class RaisingThenWorkingClient(FakeClient):
        async def get_pool_snapshot(self, pool_address, *, network="solana"):
            if pool_address == "poolA":
                raise RuntimeError("boom")
            return await super().get_pool_snapshot(pool_address, network=network)

    client = RaisingThenWorkingClient({"poolB": 3.0})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 1  # poolB still measured despite poolA's failure


@pytest.mark.asyncio
async def test_evaluate_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    shadow._ensured_db_paths.clear()
    client = FakeClient({})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 0


# --- run_cycle --------------------------------------------------------

class FakeGeckoClient(FakeClient):
    def __init__(self, pools, price_by_pool):
        super().__init__(price_by_pool)
        self._pools = pools
        self.trending_calls: list[tuple[str, str]] = []

    async def get_trending_pools(self, *, network="solana", duration="5m"):
        from aria_core.services.geckoterminal import TrendingPoolsResult

        self.trending_calls.append((network, duration))
        return TrendingPoolsResult(pools=self._pools, available=True, error=None)


@pytest.mark.asyncio
async def test_run_cycle_fetches_logs_and_measures():
    client = FakeGeckoClient([_pool(m15=30.0)], {})
    result = await shadow.run_cycle(client, network=CHAIN)
    assert result["fetched_pools"] == 1
    assert result["signals_logged"] == 1
    assert client.trending_calls == [("solana", "5m")]


@pytest.mark.asyncio
async def test_run_cycle_handles_unavailable_trending_pools():
    from aria_core.services.geckoterminal import TrendingPoolsResult

    class UnavailableClient(FakeClient):
        async def get_trending_pools(self, *, network="solana", duration="5m"):
            return TrendingPoolsResult(available=False, error="rate limit")

    client = UnavailableClient({})
    result = await shadow.run_cycle(client, network=CHAIN)
    assert result["fetched_pools"] == 0
    assert result["signals_logged"] == 0


# --- summary ------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_computes_win_rate_only_over_closed_rows():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price, forward_pct_h2) "
            "VALUES (?, ?, 'closed', ?, 1.0, 25.0)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price, forward_pct_h2) "
            "VALUES (?, ?, 'closed', ?, 1.0, -10.0)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price) "
            "VALUES (?, ?, 'open', ?, 1.0)",
            ("poolC", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.summary(CHAIN)
    assert result["open"] == 1
    assert result["closed"] == 2
    assert result["wins_h2"] == 1
    assert result["win_rate_h2"] == pytest.approx(0.5)
    assert result["avg_multiplier_h2"] == pytest.approx((1.25 + 0.90) / 2)


@pytest.mark.asyncio
async def test_summary_no_closed_rows_is_none_not_zero():
    result = await shadow.summary(CHAIN)
    assert result["closed"] == 0
    assert result["win_rate_h2"] is None
    assert result["avg_multiplier_h2"] is None
