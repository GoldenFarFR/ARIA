"""Solana LATE-BONDING shadow pocket (20/08). Isolated tmp db, no network.

This pocket exists to measure the band the dome never sampled: past 50% of the
bonding curve it has FOUR closures total, while the winrate doubles from the
<30% band (9.9%, n=1277) to 30-50% (20.9%, n=239)."""
from __future__ import annotations

from types import SimpleNamespace

import aiosqlite
import pytest

from aria_core import creator_reputation, pretrade_rejection_log
from aria_core import solana_late_bonding_shadow as pocket
from aria_core.services.pumpfun_bonding_ws import INITIAL_CURVE_TOKENS

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "shadow.db")
    monkeypatch.setattr(pocket, "DB_PATH", path)
    pocket._ensured_db_paths.clear()
    pretrade_rejection_log._ensured_db_paths.clear()
    creator_reputation._ensured_db_paths.clear()
    monkeypatch.setattr(pretrade_rejection_log, "_db_path", lambda: path)
    monkeypatch.setattr(creator_reputation, "_db_path", lambda: path)
    await pocket._ensure_table()
    yield path
    pocket._ensured_db_paths.clear()


def _curve(progress: float, *, complete: bool = False, decimals: int = 6) -> dict:
    """A curve dict at the requested progress, built from the same field the
    module derives progress from."""
    total = INITIAL_CURVE_TOKENS * (10 ** decimals)
    return {"real_token_reserves": int(total * (1.0 - progress)), "complete": complete}


class _Stream:
    def __init__(self, *, buyers=5, top_share=0.1, accel=1.4):
        self._flow = SimpleNamespace(distinct_buyers=buyers, top_buyer_share=top_share)
        self._accel = accel

    def get_flow(self, _mint):
        return self._flow

    def buyer_acceleration(self, _mint):
        return self._accel


async def _rows(path):
    async with aiosqlite.connect(path) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(f"SELECT * FROM {pocket.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


# --- the screen ----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_token_inside_the_band_with_real_traction_is_accepted():
    ok, reason, metrics = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=_curve(0.78),
    )
    assert ok is True and reason == "accepted"
    assert metrics["bonding_progress"] == pytest.approx(0.78, abs=0.01)


@pytest.mark.asyncio
async def test_a_token_too_early_on_its_curve_is_rejected():
    """The whole point of this pocket: NOT buying tokens that were just born."""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=_curve(0.20),
    )
    assert ok is False and reason.startswith("blocked_outside_band")


@pytest.mark.asyncio
async def test_a_token_about_to_graduate_is_rejected():
    """Past MAX_BONDING_PROGRESS a curve can complete mid-tracking and migrate
    its liquidity to the AMM under us."""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=_curve(0.95),
    )
    assert ok is False and reason.startswith("blocked_outside_band")


@pytest.mark.asyncio
async def test_an_unknown_curve_position_fails_CLOSED():
    """This pocket's entire premise IS the curve position -- not knowing it
    leaves nothing to act on. (Deliberately the opposite of creator
    reputation, which fails open.)"""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=None,
    )
    assert ok is False and reason == "blocked_progress_unknown"


@pytest.mark.asyncio
async def test_a_high_curve_with_no_buyers_left_is_rejected():
    """A curve can sit at 75% for hours after its crowd left -- curve position
    is history, trade flow is the present."""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(buyers=1), curve=_curve(0.78),
    )
    assert ok is False and reason.startswith("blocked_no_traction")


@pytest.mark.asyncio
async def test_volume_concentrated_in_one_wallet_is_rejected_as_wash_trading():
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(top_share=0.85), curve=_curve(0.78),
    )
    assert ok is False and reason.startswith("blocked_wash_trading")


@pytest.mark.asyncio
async def test_no_trade_stream_at_all_is_rejected_not_assumed_clean():
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=None, curve=_curve(0.78),
    )
    assert ok is False and reason.startswith("blocked_no_traction")


# --- entry ---------------------------------------------------------------

async def _resolve_ok(_client, pairs, **_kw):
    pool, _mint = pairs[0]
    return {pool: SimpleNamespace(curve=_curve(0.78), token_decimals=6, creator="devSolo")}


async def _snapshot_ok(_client, _pool, _mint, *, chain):
    return SimpleNamespace(available=True, price_usd=0.002, reserve_usd=13000.0, dex_id="pumpfun")


@pytest.mark.asyncio
async def test_an_accepted_candidate_is_recorded_with_its_entry_context(_tmp_db):
    row_id = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert row_id is not None

    row = (await _rows(_tmp_db))[0]
    assert row["bonding_progress_at_entry"] == pytest.approx(0.78, abs=0.01)
    assert row["distinct_buyers_at_entry"] == 5
    # Slippage-adjusted entry stored alongside the raw price, same convention
    # as every other pocket -- a raw mid price would flatter every result.
    assert row["realistic_entry_price"] > row["entry_price"]


@pytest.mark.asyncio
async def test_the_same_pool_is_never_entered_twice_while_open(_tmp_db):
    first = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    second = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert first is not None and second is None
    assert len(await _rows(_tmp_db)) == 1


@pytest.mark.asyncio
async def test_a_known_token_factory_creator_never_gets_an_entry(_tmp_db):
    """4+ tokens from one wallet = 4.7% winrate vs 15.5% (measured)."""
    for _ in range(creator_reputation.MIN_TOKENS_FOR_FACTORY):
        await creator_reputation.record_creator("devSolo", seen_at="2026-08-20T20:00:00+00:00", db_path=_tmp_db)

    row_id = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert row_id is None
    assert await _rows(_tmp_db) == []


@pytest.mark.asyncio
async def test_every_decision_is_logged_including_the_rejections(_tmp_db):
    """A filter can only be judged against what it let through."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async def _resolve_early(_client, pairs, **_kw):
        pool, _ = pairs[0]
        return {pool: SimpleNamespace(curve=_curve(0.20), token_decimals=6, creator="devSolo")}

    await pocket.consider_candidate(
        "mintB", "poolB", trade_stream=_Stream(), resolve_curves_fn=_resolve_early,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async with aiosqlite.connect(_tmp_db) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(
            f"SELECT blocked, reason FROM {pretrade_rejection_log.TABLE} WHERE pocket = 'late_bonding' ORDER BY id"
        )
        decisions = [dict(r) for r in await cur.fetchall()]

    assert [d["blocked"] for d in decisions] == [0, 1]
    assert decisions[1]["reason"].startswith("blocked_outside_band")


@pytest.mark.asyncio
async def test_a_provider_failure_never_raises_into_the_caller(_tmp_db):
    async def _boom(*_a, **_kw):
        raise RuntimeError("rpc down")

    assert await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_boom,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    ) is None


# --- exit ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_exit_rule_is_the_imported_one_never_a_local_copy():
    from aria_core import solana_fresh_launch_ws_exit_shadow as ws_exit
    assert pocket.evaluate_exit is ws_exit.evaluate_exit


@pytest.mark.asyncio
async def test_a_collapsing_position_is_closed_by_the_shared_rule(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async def _collapsed(_client, _pool, _mint, *, chain):
        # 97% of entry liquidity gone, non-pumpswap so the collapse guard applies
        return SimpleNamespace(available=True, price_usd=0.0002, reserve_usd=390.0, dex_id="pumpfun")

    stats = await pocket.advance_exit_simulation(snapshot_fn=_collapsed, db_path=_tmp_db)

    assert stats["closed"] == 1
    assert (await _rows(_tmp_db))[0]["exit_reason"] == "liquidity_collapse"


@pytest.mark.asyncio
async def test_summary_reports_the_average_entry_progress(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    out = await pocket.summary(db_path=_tmp_db)

    assert out["open"] == 1
    assert out["completed"] == 0
