"""V8 limit-order shadow (07/08 23h40) -- stateful shadow order, unlike
combo_signal_shadow.py/wick_filter_shadow.py's stateless single-point logs.
Tests the state machine directly (record -> pending -> filled/expired ->
closed) with an injected price_atr_fn, no real network/DB beyond an isolated
tmp sqlite file."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import v8_limit_shadow

CONTRACT = "0x" + "c" * 40
CHAIN = "base"


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(v8_limit_shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    v8_limit_shadow._ensured_db_paths.clear()
    v8_limit_shadow._last_processed_at = 0.0
    yield
    v8_limit_shadow._ensured_db_paths.clear()
    v8_limit_shadow._last_processed_at = 0.0


def _price_fn(price: float, atr: float = 0.05):
    async def fn(contract: str, chain: str):
        return price, atr
    return fn


async def _one_row():
    async with aiosqlite.connect(v8_limit_shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM v8_limit_shadow WHERE contract = ?", (CONTRACT,))
        rows = [dict(r) for r in await cur.fetchall()]
    assert len(rows) == 1
    return rows[0]


@pytest.mark.asyncio
async def test_record_signal_opens_a_pending_order():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    row = await _one_row()
    assert row["status"] == "pending"
    assert row["limit_price"] == 1.0
    assert row["stop_price"] == 0.925


@pytest.mark.asyncio
async def test_record_signal_deduplicates_same_contract():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.2, atr_at_signal=0.06, stop_price=1.1,
    )
    row = await _one_row()
    assert row["limit_price"] == 1.0  # first signal wins, second is a no-op


@pytest.mark.asyncio
async def test_record_signal_ignores_invalid_values():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=0.0, atr_at_signal=0.05, stop_price=0.0,
    )
    await v8_limit_shadow.record_signal(
        "", CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.9,
    )
    summary = await v8_limit_shadow.summary()
    assert summary["pending"] == 0


@pytest.mark.asyncio
async def test_pending_fills_when_price_returns_to_signal_close():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.process_shadows(_price_fn(0.98))
    row = await _one_row()
    assert row["status"] == "filled"
    assert row["fill_price"] == 0.98
    assert row["high_water_price"] == 0.98


@pytest.mark.asyncio
async def test_pending_stays_pending_when_price_above_limit():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.process_shadows(_price_fn(1.05))
    row = await _one_row()
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_pending_expires_after_fill_window():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    async with aiosqlite.connect(v8_limit_shadow._db_path()) as db:
        await db.execute(
            "UPDATE v8_limit_shadow SET opened_at = ? WHERE contract = ?",
            ("2020-01-01T00:00:00+00:00", CONTRACT),
        )
        await db.commit()
    await v8_limit_shadow.process_shadows(_price_fn(1.05))
    row = await _one_row()
    assert row["status"] == "expired"
    assert row["close_reason"] == "never filled"


@pytest.mark.asyncio
async def test_filled_shadow_closes_on_stop_hit():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.process_shadows(_price_fn(1.0))  # fills at 1.0
    v8_limit_shadow._last_processed_at = 0.0
    await v8_limit_shadow.process_shadows(_price_fn(0.90))  # below stop_price 0.925
    row = await _one_row()
    assert row["status"] == "closed"
    assert row["close_reason"] == "invalidation"
    assert row["pnl_pct"] == pytest.approx(-10.0)


@pytest.mark.asyncio
async def test_filled_shadow_tracks_high_water_without_closing():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.process_shadows(_price_fn(1.0))
    v8_limit_shadow._last_processed_at = 0.0
    await v8_limit_shadow.process_shadows(_price_fn(1.03))
    row = await _one_row()
    assert row["status"] == "filled"
    assert row["pending_high_water"] == pytest.approx(1.03)


@pytest.mark.asyncio
async def test_filled_shadow_closes_on_absolute_max_hold():
    """Distinct from the stagnation timeout: a position that DID move
    (>= STAGNATION_MIN_MOVE_PCT peak, so stagnation never fires) but keeps
    drifting past MAX_HOLD_HOURS still gets force-closed."""
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.process_shadows(_price_fn(1.0))  # fills at 1.0
    v8_limit_shadow._last_processed_at = 0.0
    await v8_limit_shadow.process_shadows(_price_fn(1.02))  # +2% peak, clears the stagnation bar
    async with aiosqlite.connect(v8_limit_shadow._db_path()) as db:
        await db.execute(
            "UPDATE v8_limit_shadow SET filled_at = ? WHERE contract = ?",
            ("2020-01-01T00:00:00+00:00", CONTRACT),
        )
        await db.commit()
    v8_limit_shadow._last_processed_at = 0.0
    await v8_limit_shadow.process_shadows(_price_fn(1.01))  # still above stop, well past max hold
    row = await _one_row()
    assert row["status"] == "closed"
    assert row["close_reason"] == "duree max scalping"


@pytest.mark.asyncio
async def test_process_shadows_is_throttled(monkeypatch):
    calls = {"n": 0}

    async def counting_fn(contract, chain):
        calls["n"] += 1
        return 1.0, 0.05

    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.process_shadows(counting_fn)
    await v8_limit_shadow.process_shadows(counting_fn)  # immediate second call, throttled
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_summary_aggregates_correctly():
    await v8_limit_shadow.record_signal(
        CONTRACT, CHAIN, symbol="TOK", signal_close=1.0, atr_at_signal=0.05, stop_price=0.925,
    )
    await v8_limit_shadow.process_shadows(_price_fn(1.0))  # fills
    v8_limit_shadow._last_processed_at = 0.0
    await v8_limit_shadow.process_shadows(_price_fn(0.80))  # closes on stop
    summary = await v8_limit_shadow.summary()
    assert summary["closed"] == 1
    assert summary["wins"] == 0
    assert summary["pending"] == 0
    assert summary["filled_open"] == 0
