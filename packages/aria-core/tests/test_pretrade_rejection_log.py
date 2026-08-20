"""Pre-trade gate decision log (20/08). Isolated tmp db, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from aria_core import pretrade_rejection_log as log


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "shadow.db")
    log._ensured_db_paths.clear()
    await log._ensure_table(path)
    yield path
    log._ensured_db_paths.clear()


def _decision(**kw):
    base = dict(
        pocket="ws_exit", chain="solana", mint="mintA", pool_address="poolA",
        blocked=True, reason="blocked_holder_concentration: top_holder=99.0%",
        top_holder_pct=99.0, gate_latency_ms=170.0, would_be_entry_price=1.0,
        would_be_reserve_usd=4000.0, realistic_would_be_entry_price=0.98,
    )
    base.update(kw)
    return log.GateDecision(**base)


async def _rows(path):
    async with aiosqlite.connect(path) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(f"SELECT * FROM {log.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_a_real_reject_is_stored_with_everything_needed_to_measure_it(db):
    await log.record_decision(_decision(), db_path=db)

    row = (await _rows(db))[0]
    assert row["top_holder_pct"] == pytest.approx(99.0)
    assert row["gate_latency_ms"] == pytest.approx(170.0)
    assert row["realistic_would_be_entry_price"] == pytest.approx(0.98)
    assert row["tracking_status"] == "tracking"


@pytest.mark.asyncio
async def test_accepted_candidates_are_logged_too_but_not_tracked(db):
    """A filter can only be judged against what it let through -- but the
    accepted ones become real positions tracked by the pocket itself, so
    double-tracking them here would double the API load for nothing."""
    await log.record_decision(_decision(blocked=False, reason=None, top_holder_pct=40.0), db_path=db)

    row = (await _rows(db))[0]
    assert row["blocked"] == 0
    assert row["top_holder_pct"] == pytest.approx(40.0)
    assert row["tracking_status"] == "not_tracked"


@pytest.mark.asyncio
async def test_a_fail_closed_outage_is_logged_but_never_tracked(db):
    """An outage has no holder data, so there is nothing to validate -- it is
    recorded for visibility, never counted as a filter decision."""
    await log.record_decision(
        _decision(reason="blocked_holder_gate_unavailable: timeout", top_holder_pct=None), db_path=db,
    )

    row = (await _rows(db))[0]
    assert row["blocked"] == 1
    assert row["tracking_status"] == "not_tracked"


@pytest.mark.asyncio
async def test_recording_never_raises_into_the_trading_loop(tmp_path):
    """Losing a log row is bad; blocking an entry decision because logging
    failed would be worse."""
    result = await log.record_decision(_decision(), db_path=str(tmp_path / "nope" / "x.db"))
    assert result is None


@pytest.mark.asyncio
async def test_tracking_uses_the_slippage_adjusted_entry_not_the_raw_price(db):
    """Comparing a raw mid price against a real later price would flatter the
    filter -- the counterfactual must pay the same slippage a real entry would."""
    await log.record_decision(_decision(would_be_entry_price=1.0, realistic_would_be_entry_price=0.5), db_path=db)

    async def _resolve(_pool, _mint, _chain):
        return (0.5, 4000.0)

    await log.advance_avoided_tracking(resolve_price_fn=_resolve, db_path=db)

    row = (await _rows(db))[0]
    assert row["avoided_multiplier"] == pytest.approx(1.0)  # 0.5 / 0.5, not 0.5 / 1.0


@pytest.mark.asyncio
async def test_summary_reports_a_saved_loss_as_a_positive_avoided_pnl(db):
    await log.record_decision(_decision(realistic_would_be_entry_price=1.0), db_path=db)

    async def _resolve(_pool, _mint, _chain):
        return (0.7, 4000.0)  # would have lost 30%

    await log.advance_avoided_tracking(resolve_price_fn=_resolve, db_path=db)
    summary = await log.avoided_pnl_summary(db_path=db)

    assert summary["n_measured"] == 1
    assert summary["avoided_pnl_pct"] == pytest.approx(30.0)
    assert summary["would_have_won_pct"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_summary_exposes_the_filter_cutting_winners(db):
    """The failure mode this whole table exists to make visible rather than
    assumable: a negative avoided PnL means the filter is cutting winners."""
    await log.record_decision(_decision(realistic_would_be_entry_price=1.0), db_path=db)

    async def _resolve(_pool, _mint, _chain):
        return (2.0, 4000.0)  # would have DOUBLED

    await log.advance_avoided_tracking(resolve_price_fn=_resolve, db_path=db)
    summary = await log.avoided_pnl_summary(db_path=db)

    assert summary["avoided_pnl_pct"] == pytest.approx(-100.0)
    assert summary["would_have_won_pct"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_a_pool_that_went_dark_is_marked_not_dropped(db):
    """Dropping unresolvable rows would bias the result toward whichever
    tokens survived -- a pool going dark IS the outcome."""
    await log.record_decision(_decision(), db_path=db)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=log.TRACKING_WINDOW_MINUTES + 1)).isoformat()
    async with aiosqlite.connect(db) as c:
        await c.execute(f"UPDATE {log.TABLE} SET decided_at = ?", (stale,))
        await c.commit()

    async def _resolve(_pool, _mint, _chain):
        return (None, None)

    stats = await log.advance_avoided_tracking(resolve_price_fn=_resolve, db_path=db)

    assert stats["unresolvable"] == 1
    assert (await _rows(db))[0]["tracking_status"] == "unresolvable"


@pytest.mark.asyncio
async def test_tracking_closes_at_the_same_horizon_a_real_position_would_live(db):
    """Tracking past MAX_HOLD would credit the filter with a collapse it never
    actually avoided."""
    await log.record_decision(_decision(), db_path=db)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=log.TRACKING_WINDOW_MINUTES + 1)).isoformat()
    async with aiosqlite.connect(db) as c:
        await c.execute(f"UPDATE {log.TABLE} SET decided_at = ?", (stale,))
        await c.commit()

    async def _resolve(_pool, _mint, _chain):
        return (0.8, 4000.0)

    stats = await log.advance_avoided_tracking(resolve_price_fn=_resolve, db_path=db)

    assert stats["closed"] == 1
    assert (await _rows(db))[0]["tracking_status"] == "closed"


@pytest.mark.asyncio
async def test_a_provider_failure_is_never_read_as_a_verdict(db):
    await log.record_decision(_decision(), db_path=db)

    async def _boom(_pool, _mint, _chain):
        raise RuntimeError("provider down")

    stats = await log.advance_avoided_tracking(resolve_price_fn=_boom, db_path=db)

    assert stats["checked"] == 1
    assert stats["updated"] == 0
    assert (await _rows(db))[0]["tracking_status"] == "tracking"  # still open, no false verdict


@pytest.mark.asyncio
async def test_sweep_is_bounded_so_it_cannot_become_the_biggest_api_consumer(db):
    """This table grows by every gate decision, far faster than the position
    tables it shadows -- an unbounded sweep would quietly dominate the budget."""
    for i in range(10):
        await log.record_decision(_decision(mint=f"mint{i}", pool_address=f"pool{i}"), db_path=db)

    calls = []

    async def _resolve(pool, _mint, _chain):
        calls.append(pool)
        return (0.9, 4000.0)

    await log.advance_avoided_tracking(resolve_price_fn=_resolve, max_rows=3, db_path=db)

    assert len(calls) == 3


@pytest.mark.asyncio
async def test_summary_breaks_latency_down_by_reason(db):
    await log.record_decision(_decision(gate_latency_ms=170.0), db_path=db)
    await log.record_decision(
        _decision(mint="mintB", reason="blocked_holder_gate_unavailable: timeout",
                  top_holder_pct=None, gate_latency_ms=12000.0),
        db_path=db,
    )

    summary = await log.avoided_pnl_summary(db_path=db)
    reasons = {r["reason"]: r for r in summary["by_reason"]}

    assert reasons["blocked_holder_concentration: top_holder=99.0%"]["avg_latency_ms"] == pytest.approx(170.0)
    assert reasons["blocked_holder_gate_unavailable: timeout"]["avg_latency_ms"] == pytest.approx(12000.0)


@pytest.mark.asyncio
async def test_the_ready_to_call_cycle_uses_the_pockets_own_price_cascade(db, monkeypatch):
    """The counterfactual must be measured on the SAME source the real
    positions use -- a different source would make the comparison meaningless
    -- and must share that cascade's throttles rather than adding a parallel,
    uncoordinated load on the same providers."""
    from aria_core import solana_fresh_launch_ws_exit_shadow as pocket

    await log.record_decision(_decision(realistic_would_be_entry_price=1.0), db_path=db)
    calls = []

    async def _fake_snapshot(_client, pool_address, mint, *, chain):
        calls.append((pool_address, mint, chain))
        return SimpleNamespace(available=True, price_usd=0.6, reserve_usd=3000.0, dex_id="rest")

    monkeypatch.setattr(pocket, "_snapshot_with_fallback", _fake_snapshot)

    stats = await log.advance_avoided_tracking_cycle(db_path=db)

    assert stats["updated"] == 1
    assert calls == [("poolA", "mintA", "solana")]
    summary = await log.avoided_pnl_summary(db_path=db)
    assert summary["avoided_pnl_pct"] == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_an_unavailable_snapshot_is_not_read_as_a_price_of_zero(db, monkeypatch):
    """A dead provider must never be recorded as "the token went to zero" --
    that would credit the filter with an enormous fake saving."""
    from aria_core import solana_fresh_launch_ws_exit_shadow as pocket

    await log.record_decision(_decision(), db_path=db)

    async def _unavailable(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=False, price_usd=None, reserve_usd=None, dex_id=None)

    monkeypatch.setattr(pocket, "_snapshot_with_fallback", _unavailable)

    stats = await log.advance_avoided_tracking_cycle(db_path=db)

    assert stats["updated"] == 0
    assert (await _rows(db))[0]["avoided_multiplier"] is None
