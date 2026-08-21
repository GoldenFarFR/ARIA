"""Solana LATE-BONDING shadow pocket (20/08). Isolated tmp db, no network.

This pocket exists to measure the band the dome never sampled: past 50% of the
bonding curve it has FOUR closures total, while the winrate doubles from the
<30% band (9.9%, n=1277) to 30-50% (20.9%, n=239)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import creator_reputation, pretrade_rejection_log
from aria_core import solana_fresh_launch_ws_exit_shadow as ws_exit_shadow
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
    def __init__(self, *, buyers=5, top_share=0.1, accel=1.4, sol_velocity=0.05):
        self._flow = SimpleNamespace(distinct_buyers=buyers, top_buyer_share=top_share,
                                     sol_velocity=sol_velocity)
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
    its liquidity to the AMM under us. Uses the constant rather than a literal
    so widening the collection band cannot silently void this guarantee."""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(),
        curve=_curve(min(0.999, pocket.MAX_BONDING_PROGRESS + 0.02)),
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
async def test_a_high_curve_with_literally_no_buyer_is_rejected():
    """A curve can sit at 75% for hours after its crowd left -- curve position
    is history, trade flow is the present. 20/08: the required N was relaxed
    to 1 for collection, but ZERO buyers stays refused -- buying what nobody
    buys is the single behaviour the data condemns most clearly (-21.56%)."""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(buyers=0), curve=_curve(0.78),
    )
    assert ok is False and reason.startswith("blocked_no_traction")


@pytest.mark.asyncio
async def test_volume_concentrated_in_one_wallet_is_rejected_as_wash_trading():
    # Uses the constant, not a literal: the cutoff is deliberately loose while
    # collecting, but "one wallet is the ONLY buyer" must stay refused.
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(top_share=pocket.MAX_TOP_BUYER_SHARE + 0.02),
        curve=_curve(0.78),
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
    # 21/08 -- was `liquidity_collapse`. The hard stop now owns this case: a
    # position down 97% necessarily crossed -20% first, and letting the
    # collapse branch claim it is exactly how -81.5% closes kept happening
    # under a -15% trailing stop. It still fills at the real market price.
    assert (await _rows(_tmp_db))[0]["exit_reason"] == "hard_stop"


@pytest.mark.asyncio
async def test_summary_reports_the_average_entry_progress(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    # Anchored on CONFIG_EPOCH rather than on "now": moving the epoch forward
    # is a routine operation here (four times in one day), and a test that
    # breaks on it reports a config change as a regression. Third occurrence,
    # so this one is fixed at the root like the other two.
    out = await pocket.summary(
        since=(datetime.fromisoformat(pocket.CONFIG_EPOCH) - timedelta(days=1)).isoformat(),
        db_path=_tmp_db,
    )

    assert out["open"] == 1
    assert out["completed"] == 0


@pytest.mark.asyncio
async def test_the_collect_wide_phase_is_over_and_recorded():
    """20/08 the band was widened to 0.40 to find out WHICH sub-band works;
    21/08 it answered (rug 48.9% at 40-60% vs 27.0% above 80%) and the floor
    went back up to 0.70. This test replaces the one that guarded the wide
    band, so the transition is explicit rather than a silent narrowing."""
    assert pocket.MIN_BONDING_PROGRESS >= 0.70
    assert pocket.MAX_BONDING_PROGRESS >= 0.98


@pytest.mark.asyncio
async def test_a_token_in_the_paying_band_is_accepted_with_its_context():
    ok, _, metrics = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(buyers=1), curve=_curve(0.75),
    )
    assert ok is True
    # Recorded on the row, so sub-bands stay separable at analysis time.
    assert metrics["bonding_progress"] == pytest.approx(0.75, abs=0.01)


# --- 20/08, RPC-first pricing --------------------------------------------
# This pocket trades tokens still ON their bonding curve, whose price is
# virtual_quote/virtual_token -- reserves the Helius websocket already pushes.
# Going through the REST cascade paid a rate-limited round trip (GeckoTerminal
# was the only provider actually 429-ing: 12 real ones in 20 minutes) for a
# number we were already handed.

class _BondingFeed:
    def __init__(self, *, available=True, price=0.004):
        self._snap = SimpleNamespace(
            available=available, price_usd=price if available else None,
            reserve_usd=14000.0, dex_id="pumpfun",
        )
        self.subscribed = []

    def get_snapshot(self, _pool):
        return self._snap

    async def add_pools(self, pairs):
        self.subscribed.extend(pairs)
        return len(pairs)


@pytest.mark.asyncio
async def test_an_open_position_is_priced_from_the_rpc_feed_not_rest(_tmp_db):
    rest_calls = []

    async def _rest(_client, pool, mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.002, reserve_usd=13000.0, dex_id="raydium")

    feed = _BondingFeed()
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, bonding_ws_feed=feed, db_path=_tmp_db,
    )
    await pocket.advance_exit_simulation(snapshot_fn=_rest, bonding_ws_feed=feed, db_path=_tmp_db)

    assert rest_calls == []  # REST never touched while the curve is live


@pytest.mark.asyncio
async def test_entry_subscribes_the_pool_so_the_feed_can_price_it(_tmp_db):
    """Without the subscription every check would silently fall back to REST."""
    feed = _BondingFeed()
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, bonding_ws_feed=feed, db_path=_tmp_db,
    )
    assert feed.subscribed == [("poolA", "mintA")]


@pytest.mark.asyncio
async def test_a_graduated_curve_falls_back_to_the_rest_cascade(_tmp_db):
    """A curve that completed mid-position has moved its liquidity to the AMM;
    the bonding feed then honestly reports unavailable and REST takes over --
    the cascade is not wrong, it is built for migrated tokens."""
    rest_calls = []

    async def _rest(_client, pool, mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.002, reserve_usd=13000.0, dex_id="raydium")

    live, dead = _BondingFeed(), _BondingFeed(available=False)
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, bonding_ws_feed=live, db_path=_tmp_db,
    )
    await pocket.advance_exit_simulation(snapshot_fn=_rest, bonding_ws_feed=dead, db_path=_tmp_db)

    assert rest_calls == ["poolA"]


@pytest.mark.asyncio
async def test_a_feed_error_falls_back_rather_than_leaving_the_position_unchecked(_tmp_db):
    class _Broken(_BondingFeed):
        def get_snapshot(self, _pool):
            raise RuntimeError("feed down")

    rest_calls = []

    async def _rest(_client, pool, mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.002, reserve_usd=13000.0, dex_id="raydium")

    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, bonding_ws_feed=_BondingFeed(), db_path=_tmp_db,
    )
    await pocket.advance_exit_simulation(snapshot_fn=_rest, bonding_ws_feed=_Broken(), db_path=_tmp_db)

    assert rest_calls == ["poolA"]


@pytest.mark.asyncio
async def test_entry_and_exit_are_priced_from_the_SAME_source(_tmp_db):
    """Real bug found live within 30 minutes of this pocket going live: the
    entry was priced through the REST cascade while the exit used the RPC feed,
    so every PnL compared two different sources. It surfaced as impossible
    arithmetic -- a position whose reserve fell 53% reporting a 79% price drop,
    which a constant-product curve cannot produce."""
    rest_calls = []

    async def _rest(_client, pool, mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.002, reserve_usd=13000.0, dex_id="raydium")

    feed = _BondingFeed(price=0.004)
    row_id = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_rest, bonding_ws_feed=feed, db_path=_tmp_db,
    )

    assert row_id is not None
    assert rest_calls == []  # entry never went through REST while the curve is live
    # ...and the recorded entry price is the RPC one, not the REST one.
    assert (await _rows(_tmp_db))[0]["entry_price"] == pytest.approx(0.004)


@pytest.mark.asyncio
async def test_the_pool_is_subscribed_BEFORE_the_entry_is_priced(_tmp_db):
    """Subscribing after pricing would make the very first read fall back to
    REST, which is exactly how the two-source mismatch happened."""
    feed = _BondingFeed()
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, bonding_ws_feed=feed, db_path=_tmp_db,
    )
    assert feed.subscribed == [("poolA", "mintA")]


@pytest.mark.asyncio
async def test_the_band_reaches_high_enough_to_catch_graduations():
    """21/08 -- the 0.95 ceiling was excluding the band with the HIGHEST chance
    of the best outcome. Measured on 676 closures: migrated positions returned
    +161% (87% winrate, +138.5% without their two best) against -15.93% for
    those that stayed on the curve, and graduation odds rise monotonically with
    entry position (2.0% at 40-60% up to 50.0% at 90-95%)."""
    assert pocket.MAX_BONDING_PROGRESS >= 0.98


@pytest.mark.asyncio
async def test_a_fully_complete_curve_is_still_refused():
    """At 1.0 there is no bonding liquidity left to enter against."""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=_curve(1.0, complete=True),
    )
    assert ok is False and reason.startswith("blocked_outside_band")


@pytest.mark.asyncio
async def test_a_curve_at_97_percent_is_now_accepted():
    ok, _, metrics = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=_curve(0.97),
    )
    assert ok is True
    assert metrics["bonding_progress"] == pytest.approx(0.97, abs=0.01)


# --- 21/08, graduated positions are exempt from max_hold -----------------
# Measured on this pocket's own graduated closures: trailing_stop exits made
# +228.3% (n=47, 71% of a +296% peak) while max_hold exits made -5.3% (n=12)
# despite a +52.4% peak. Those 12 were still alive when the clock killed them.

async def _run_exit(db_path, *, dex_id, age_minutes, price=0.003):
    async with aiosqlite.connect(db_path) as c:
        old = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
        await c.execute(f"UPDATE {pocket.TABLE} SET detected_at = ?", (old,))
        await c.commit()

    async def _snap(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=price, reserve_usd=14000.0, dex_id=dex_id)

    await pocket.advance_exit_simulation(snapshot_fn=_snap, db_path=db_path)
    return (await _rows(db_path))[0]


@pytest.mark.asyncio
async def test_a_graduated_position_is_not_killed_by_the_clock(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    row = await _run_exit(_tmp_db, dex_id="pumpswap", age_minutes=ws_exit_shadow.MAX_HOLD_MINUTES + 30)
    assert row["exit_reason"] != "max_hold"


@pytest.mark.asyncio
async def test_a_position_still_on_its_curve_still_respects_max_hold(_tmp_db):
    """The exemption is for PROVEN traction only -- an ungraduated position
    keeps its timer."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    row = await _run_exit(_tmp_db, dex_id="pumpfun", age_minutes=ws_exit_shadow.MAX_HOLD_MINUTES + 30)
    assert row["exit_reason"] == "max_hold"


@pytest.mark.asyncio
async def test_a_graduated_position_is_still_protected_on_the_downside(_tmp_db):
    """Exempting the timer must never leave a position unprotected: the
    trailing stop and the collapse guard both still apply."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    async with aiosqlite.connect(_tmp_db) as c:
        # peak well above the arm threshold, then a deep fall
        await c.execute(f"UPDATE {pocket.TABLE} SET peak_price = entry_price * 3")
        await c.commit()
    row = await _run_exit(_tmp_db, dex_id="pumpswap", age_minutes=5, price=0.0005)
    assert row["exit_reason"] == "trailing_stop"


@pytest.mark.asyncio
async def test_the_floor_sits_where_the_data_turns_positive():
    """21/08 -- the collect-wide phase answered: rug risk nearly halves climbing
    the curve (48.9% at 40-60% down to 27.0% above 80%) while the win rate
    rises (37.0% to 50.0%), and PnL turns positive at 70%. The floor must not
    drift back below that turn."""
    assert pocket.MIN_BONDING_PROGRESS >= 0.70


@pytest.mark.asyncio
async def test_the_worst_band_is_now_refused():
    """40-60% carried 71% of entries and was the worst band on every axis."""
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=_curve(0.50),
    )
    assert ok is False and reason.startswith("blocked_outside_band")


@pytest.mark.asyncio
async def test_headroom_is_kept_above_the_floor_for_execution_latency():
    """Real execution drifts the entry UP the curve (several points on a token
    actually moving), so the usable band must stay wide enough that latency
    cannot push a candidate straight through the ceiling."""
    assert pocket.MAX_BONDING_PROGRESS - pocket.MIN_BONDING_PROGRESS >= 0.25


# --- 21/08, recent window alongside the cumulative average ---------------
# Operator spotted the real problem: the notification's PnL had not moved off
# -2.0% for over an hour despite violent per-trade swings. Correct but useless
# -- at 775 closures each new one carries 1/776 of the average, so even a +100%
# trade moves the headline by 0.13 points, while the hourly reality was +26.4%
# then -21.9%. A number that cannot move is a number nobody can act on.

async def _close_row(db_path, mult, when):
    async with aiosqlite.connect(db_path) as c:
        await c.execute(
            f"""INSERT INTO {pocket.TABLE}
                (pool_address, token_address, chain, detected_at, entry_price, reserve_usd,
                 exit_reason, realistic_final_multiplier, last_checked_at)
                VALUES ('p','m',?,?,1.0,5000.0,'trailing_stop',?,?)""",
            (CHAIN, when, mult, when),
        )
        await c.commit()


@pytest.mark.asyncio
async def test_the_recent_window_moves_while_the_cumulative_average_barely_does(_tmp_db):
    # a long history of flat closures, then a violent recent swing
    for i in range(200):
        await _close_row(_tmp_db, 1.0, f"2026-08-20T{10 + i % 10:02d}:00:00+00:00")
    for i in range(10):
        await _close_row(_tmp_db, 3.0, f"2026-08-21T{10 + i % 10:02d}:30:00+00:00")

    out = await pocket.summary(since="2026-08-01T00:00:00+00:00", db_path=_tmp_db)

    # The cumulative average is dragged down by 200 flat closures...
    assert out["avg_pnl_pct"] < 20
    # ...while the recent window shows what is actually happening now.
    assert out["recent_avg_pnl_pct"] > out["avg_pnl_pct"]


@pytest.mark.asyncio
async def test_the_recent_window_is_bounded_and_ordered(_tmp_db):
    for i in range(RECENT := pocket.RECENT_WINDOW_CLOSURES + 20):
        await _close_row(_tmp_db, 1.0, f"2026-08-2{i % 2}T{10 + i % 10:02d}:00:00+00:00")

    out = await pocket.summary(since="2026-08-01T00:00:00+00:00", db_path=_tmp_db)

    assert out["recent_n"] == pocket.RECENT_WINDOW_CLOSURES


@pytest.mark.asyncio
async def test_an_empty_pocket_reports_none_rather_than_a_fabricated_zero(_tmp_db):
    out = await pocket.summary(db_path=_tmp_db)
    assert out["recent_avg_pnl_pct"] is None
    assert out["recent_win_rate"] is None


@pytest.mark.asyncio
async def test_closures_from_an_earlier_config_are_not_averaged_in(_tmp_db):
    """21/08 -- operator asked to reset and restart clean. Done as an EPOCH
    MARKER, not a delete: the old rows produced every finding of the last two
    days and this dome never destroys real history."""
    # Anchored on CONFIG_EPOCH itself rather than on hardcoded dates: this
    # test used to break every time the epoch was moved forward, which is a
    # routine operation, not a regression.
    epoch = datetime.fromisoformat(pocket.CONFIG_EPOCH)
    await _close_row(_tmp_db, 5.0, (epoch - timedelta(days=1)).isoformat())  # old config
    await _close_row(_tmp_db, 1.1, (epoch + timedelta(seconds=1)).isoformat())  # current

    out = await pocket.summary(db_path=_tmp_db)

    assert out["completed"] == 1  # only the current-config closure
    assert out["avg_pnl_pct"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_the_old_rows_are_still_readable_on_request(_tmp_db):
    """Not averaged in is not the same as gone."""
    epoch = datetime.fromisoformat(pocket.CONFIG_EPOCH)
    await _close_row(_tmp_db, 5.0, (epoch - timedelta(days=1)).isoformat())

    out = await pocket.summary(
        since=(epoch - timedelta(days=30)).isoformat(), db_path=_tmp_db,
    )

    assert out["completed"] == 1


# --- 21/08, paid DexScreener profile -------------------------------------
# Operator's own idea, and the strongest single signal of the investigation.
# On 150 real closures: WITH a paid profile n=63 PnL +57.4% (+12.2% without
# its two best), rug 22.2%; WITHOUT n=87 PnL -27.7% (-34.9%), rug 51.7%.
# His objection was right too -- scammers pay for profiles as well -- but the
# rug rate is still more than halved, because a ~300$ profile filters out the
# zero-cost rugs that are the bulk of them.

@pytest.mark.asyncio
async def test_a_token_with_project_links_is_recorded_as_having_a_paid_profile(_tmp_db, monkeypatch):
    from aria_core.services import dexscreener

    await _close_row(_tmp_db, 1.5, "2026-08-21T12:00:00+00:00")
    row_id = (await _rows(_tmp_db))[0]["id"]
    monkeypatch.setattr(
        dexscreener, "fetch_token_pairs",
        AsyncMock(return_value=[SimpleNamespace(project_links=[{"url": "https://x.com/t"}])]),
    )

    await pocket._enrich_paid_profile(row_id, "mintA", db_path=_tmp_db)

    assert (await _rows(_tmp_db))[0]["has_paid_profile"] == 1


@pytest.mark.asyncio
async def test_a_token_without_links_is_recorded_as_zero_not_null(_tmp_db, monkeypatch):
    """0 and NULL mean different things: 0 is "checked, no profile", NULL is
    "never checked". Conflating them would silently bias the sample."""
    from aria_core.services import dexscreener

    await _close_row(_tmp_db, 1.0, "2026-08-21T12:00:00+00:00")
    row_id = (await _rows(_tmp_db))[0]["id"]
    monkeypatch.setattr(dexscreener, "fetch_token_pairs",
                        AsyncMock(return_value=[SimpleNamespace(project_links=[])]))

    await pocket._enrich_paid_profile(row_id, "mintA", db_path=_tmp_db)

    assert (await _rows(_tmp_db))[0]["has_paid_profile"] == 0


@pytest.mark.asyncio
async def test_a_provider_failure_leaves_the_field_null_and_never_raises(_tmp_db, monkeypatch):
    from aria_core.services import dexscreener

    await _close_row(_tmp_db, 1.0, "2026-08-21T12:00:00+00:00")
    row_id = (await _rows(_tmp_db))[0]["id"]
    monkeypatch.setattr(dexscreener, "fetch_token_pairs", AsyncMock(side_effect=RuntimeError("down")))

    await pocket._enrich_paid_profile(row_id, "mintA", db_path=_tmp_db)

    assert (await _rows(_tmp_db))[0]["has_paid_profile"] is None


# --- hard stop: the window the trailing stop never covered (21/08) ---

def _row(entry=1.0, peak=None, qty=1.0):
    return {"entry_price": entry, "peak_price": peak if peak is not None else entry,
            "reserve_usd": 10_000.0, "remaining_qty": qty, "realized_proceeds": 0.0,
            "realistic_entry_price": entry, "realistic_realized_proceeds": 0.0,
            "pool_address": "pool"}


def test_hard_stop_fires_when_trailing_never_armed():
    """The exact hole the operator saw live as a -81.5% close."""
    # price dipped through the stop and came back up: the stop fills at the
    # stop, since the market is still bidding above it.
    r = ws_exit_shadow.evaluate_exit(
        _row(), current_price=0.85, reserve_usd=9_000.0, dex_id="pumpfun",
        age_minutes=5.0, window_low=0.78, hard_stop_pct=20.0,
    )
    assert r["exit_reason"] == "hard_stop"
    assert r["realized_proceeds"] == pytest.approx(0.80)


def test_hard_stop_never_fills_above_a_gapped_market():
    """A stop cannot conjure liquidity that is gone -- a price already at -60%
    fills at -60%, never at the -20% stop. Overstating this fill is how a
    counterfactual becomes a fantasy."""
    r = ws_exit_shadow.evaluate_exit(
        _row(), current_price=0.40, reserve_usd=9_000.0, dex_id="pumpfun",
        age_minutes=5.0, hard_stop_pct=20.0,
    )
    assert r["exit_reason"] == "hard_stop"
    assert r["realized_proceeds"] == pytest.approx(0.40)


def test_hard_stop_yields_to_an_armed_trailing_stop():
    """Once the peak has risen past the arming threshold the trailing owns the
    position -- the hard stop must not pre-empt the mechanism that returned
    +58.2% on this pocket's own closures."""
    r = ws_exit_shadow.evaluate_exit(
        _row(peak=2.0), current_price=0.79, reserve_usd=9_000.0, dex_id="pumpfun",
        age_minutes=5.0, hard_stop_pct=20.0,
    )
    assert r["exit_reason"] == "trailing_stop"


def test_hard_stop_takes_priority_over_liquidity_collapse():
    """Chronologically it would have fired first, well before the reserve
    halved -- otherwise the collapse branch keeps claiming closes the stop
    should already have taken."""
    r = ws_exit_shadow.evaluate_exit(
        _row(), current_price=0.19, reserve_usd=1_000.0, dex_id="pumpfun",
        age_minutes=5.0, hard_stop_pct=20.0,
    )
    assert r["exit_reason"] == "hard_stop"


def test_hard_stop_absent_by_default_so_the_control_pocket_is_untouched():
    """FAST-DISCOVERY must keep behaving exactly as before, or the two pockets
    stop differing on one variable and neither result is attributable."""
    r = ws_exit_shadow.evaluate_exit(
        _row(), current_price=0.50, reserve_usd=9_000.0, dex_id="pumpfun",
        age_minutes=5.0,
    )
    assert r["exit_reason"] is None
    assert ws_exit_shadow.HARD_STOP_PCT_DEFAULT is None


def test_late_bonding_actually_passes_its_hard_stop_to_the_shared_rule():
    """A constant defined but never wired is the failure mode this guards."""
    import inspect
    src = inspect.getsource(pocket)
    assert "hard_stop_pct=HARD_STOP_PCT" in src
    assert pocket.HARD_STOP_PCT == 20.0


# --- founding cohort collected at entry (21/08, operator's idea) ---

class _CohortStream(_Stream):
    """A trade stream that also knows who founded the token."""

    def __init__(self, cohort=None, sold=False):
        super().__init__()
        self._cohort = cohort
        self._sold = sold

    def founding_cohort(self, mint):
        return self._cohort

    def founder_sold(self, mint, wallet):
        return self._sold


@pytest.mark.asyncio
async def test_the_founding_cohort_is_recorded_at_entry(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", db_path=_tmp_db, resolve_curves_fn=_resolve_ok, snapshot_fn=_snapshot_ok,
        trade_stream=_CohortStream({"tracked": 8, "exited": 5, "exit_ratio": 0.625, "bundle_size": 4}),
    )
    row = (await _rows(_tmp_db))[0]
    assert row["founding_tracked_at_entry"] == 8
    assert row["founding_exited_at_entry"] == 5
    assert row["founding_exit_ratio_at_entry"] == 0.625
    assert row["founding_bundle_size_at_entry"] == 4


@pytest.mark.asyncio
async def test_an_unknown_cohort_stays_null_never_zero(_tmp_db):
    """"0 founders sold" and "we were not watching" must never be the same
    value -- collapsing them would quietly bias the sample being collected."""
    await pocket.consider_candidate(
        "mintA", "poolA", db_path=_tmp_db, resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, trade_stream=_CohortStream(None),
    )
    row = (await _rows(_tmp_db))[0]
    assert row["founding_tracked_at_entry"] is None
    assert row["founding_bundle_size_at_entry"] is None
    assert row["creator_sold_at_entry"] is None


@pytest.mark.asyncio
async def test_a_creator_seen_selling_is_recorded(_tmp_db):
    """The most direct rug signal there is, and free."""
    await pocket.consider_candidate(
        "mintA", "poolA", db_path=_tmp_db, resolve_curves_fn=_resolve_ok, snapshot_fn=_snapshot_ok,
        trade_stream=_CohortStream({"tracked": 6, "exited": 1, "exit_ratio": 0.167, "bundle_size": 1},
                                   sold=True),
    )
    row = (await _rows(_tmp_db))[0]
    assert row["creator_sold_at_entry"] == 1


@pytest.mark.asyncio
async def test_collecting_the_cohort_never_rejects_an_entry(_tmp_db):
    """Collection only: a filter on this would need its own forward sample,
    and any new entry filter risks cutting the rare winners carrying the PnL."""
    await pocket.consider_candidate(
        "mintA", "poolA", db_path=_tmp_db, resolve_curves_fn=_resolve_ok, snapshot_fn=_snapshot_ok,
        trade_stream=_CohortStream({"tracked": 10, "exited": 10, "exit_ratio": 1.0, "bundle_size": 9},
                                   sold=True),
    )
    assert len(await _rows(_tmp_db)) == 1


@pytest.mark.asyncio
async def test_a_locally_priced_position_is_served_even_with_no_rest_budget(_tmp_db):
    """21/08 -- one migrated position waiting on GeckoTerminal's 16s throttle
    stalled every bonding-curve position behind it, pushing the real gap
    between checks to 41s against a 10s cadence. That is what let a -20% hard
    stop fill at -78%: a stop cannot cut a price it never sees.

    Stated as "a free read is never starved by a paid one" rather than as a
    call ordering, because locally-priced rows no longer go through the REST
    path at all -- a test asserting on that call would pass while proving
    nothing."""
    for mint, pool in (("mintSlow", "poolSlow"), ("mintFast", "poolFast")):
        await pocket.consider_candidate(
            mint, pool, trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
            snapshot_fn=_snapshot_ok, db_path=_tmp_db,
        )

    class _FeedWithOnlyFast:
        def get_snapshot(self, pool_address):
            if pool_address == "poolFast":
                return SimpleNamespace(available=True, price_usd=0.0002,
                                       reserve_usd=390.0, dex_id="pumpfun",
                                       price_high_since_last_read=0.0002,
                                       price_low_since_last_read=0.0002)
            return SimpleNamespace(available=False, price_usd=None)

    rest_calls = []

    async def _rest(_client, pool, _mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.001,
                               reserve_usd=9_000.0, dex_id="pumpfun")

    stats = await pocket.advance_exit_simulation(
        snapshot_fn=_rest, bonding_ws_feed=_FeedWithOnlyFast(), db_path=_tmp_db,
        max_rest_calls=0,
    )

    assert rest_calls == [], "the REST budget was zero, nothing should have been fetched"
    assert stats["checked"] == 1, "the free local read must still have been served"
    closed = [r for r in await _rows(_tmp_db) if r["exit_reason"] is not None]
    assert [r["pool_address"] for r in closed] == ["poolFast"]


class _CurveGoneFeed:
    """The curve feed after graduation: honestly unavailable on the curve
    address, live on the AMM pool."""

    def __init__(self, amm_pool="ammPool"):
        self.amm_pool = amm_pool

    def get_snapshot(self, pool_address):
        if pool_address == self.amm_pool:
            return SimpleNamespace(available=True, price_usd=0.005,
                                   reserve_usd=40_000.0, dex_id="pumpswap")
        return SimpleNamespace(available=False, price_usd=None, reserve_usd=None, dex_id=None)


@pytest.mark.asyncio
async def test_a_graduated_position_gets_its_amm_pool_resolved_once(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    calls = []

    async def _find(_client, mint):
        calls.append(mint)
        return "ammPool"

    n = await pocket.resolve_migrated_pools(
        None, bonding_ws_feed=_CurveGoneFeed(), find_pool_fn=_find, db_path=_tmp_db,
    )
    assert n == 1 and calls == ["mintA"]
    assert (await _rows(_tmp_db))[0]["amm_pool_address"] == "ammPool"

    # resolved once, never re-queried
    await pocket.resolve_migrated_pools(
        None, bonding_ws_feed=_CurveGoneFeed(), find_pool_fn=_find, db_path=_tmp_db,
    )
    assert calls == ["mintA"]


@pytest.mark.asyncio
async def test_a_position_still_on_its_curve_costs_no_rpc_call(_tmp_db):
    """A token that has not graduated has no AMM pool to find -- spending a
    getProgramAccounts on it would be pure waste."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    calls = []

    async def _find(_client, mint):
        calls.append(mint)
        return "ammPool"

    class _StillOnCurve:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=0.001,
                                   reserve_usd=9_000.0, dex_id="pumpfun")

    assert await pocket.resolve_migrated_pools(
        None, bonding_ws_feed=_StillOnCurve(), find_pool_fn=_find, db_path=_tmp_db,
    ) == 0
    assert calls == []


@pytest.mark.asyncio
async def test_a_resolved_amm_pool_is_priced_on_the_rpc_not_rest(_tmp_db):
    """The whole point: after graduation the position must stay on Helius."""
    rest_calls = []

    async def _rest(_client, pool, _mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.001,
                               reserve_usd=9_000.0, dex_id="pumpswap")

    snap = await pocket._price_position(
        {"pool_address": "poolA", "token_address": "mintA", "amm_pool_address": "ammPool"},
        chain="solana", bonding_ws_feed=_CurveGoneFeed(), snapshot_fn=_rest,
    )
    assert snap.dex_id == "pumpswap" and snap.reserve_usd == 40_000.0
    assert rest_calls == [], "the REST cascade must not be reached once the AMM pool is known"


@pytest.mark.asyncio
async def test_a_failed_resolution_is_retried_rather_than_recorded(_tmp_db):
    """Never poison the row with a wrong address -- an unresolved position
    simply tries again next pass."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async def _broken(_client, _mint):
        raise RuntimeError("rpc down")

    assert await pocket.resolve_migrated_pools(
        None, bonding_ws_feed=_CurveGoneFeed(), find_pool_fn=_broken, db_path=_tmp_db,
    ) == 0
    assert (await _rows(_tmp_db))[0]["amm_pool_address"] is None


@pytest.mark.asyncio
async def test_rest_bound_positions_are_capped_per_cycle(_tmp_db):
    """21/08 -- without this ceiling an exit pass took 23.5s for 9 positions
    because every websocket-orphaned one queued behind the throttled REST
    cascade, pushing the gap between checks of the SAME position to 60s. A
    stale check on one position is far cheaper than delaying all the others."""
    for i in range(6):
        await pocket.consider_candidate(
            f"mint{i}", f"pool{i}", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
            snapshot_fn=_snapshot_ok, db_path=_tmp_db,
        )

    rest_calls = []

    async def _rest(_client, pool, _mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.001,
                               reserve_usd=9_000.0, dex_id="pumpfun")

    class _NothingTracked:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=False, price_usd=None)

    stats = await pocket.advance_exit_simulation(
        snapshot_fn=_rest, bonding_ws_feed=_NothingTracked(), db_path=_tmp_db,
        max_rest_calls=2,
    )
    assert len(rest_calls) == 2
    assert stats["deferred_no_rest_budget"] == 4


@pytest.mark.asyncio
async def test_locally_priced_positions_never_consume_the_rest_budget(_tmp_db):
    """The ceiling must not throttle free in-memory reads -- that would make
    the fix worse than the problem."""
    for i in range(5):
        await pocket.consider_candidate(
            f"mint{i}", f"pool{i}", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
            snapshot_fn=_snapshot_ok, db_path=_tmp_db,
        )

    class _AllTracked:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=0.001,
                                   reserve_usd=9_000.0, dex_id="pumpfun")

    stats = await pocket.advance_exit_simulation(
        bonding_ws_feed=_AllTracked(), db_path=_tmp_db, max_rest_calls=1,
    )
    assert stats["checked"] == 5
    assert "deferred_no_rest_budget" not in stats


@pytest.mark.asyncio
async def test_the_exit_rule_sees_the_low_reached_between_reads(_tmp_db):
    """21/08 -- the websocket records the extremes reached since the last
    read, and FAST-DISCOVERY passed them; this pocket did not, so its stop
    could only react to a point sample. That is why a -20% hard stop filled at
    -78%: the crossing HAD been recorded, the pocket never read it."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    entry = (await _rows(_tmp_db))[0]["entry_price"]

    class _FeedWithExtremes:
        """Price recovered to -5% by the time we look, but it dipped to -30%
        in between -- a real stop would have been taken there."""

        def get_snapshot(self, _pool):
            return SimpleNamespace(
                available=True, price_usd=entry * 0.95, reserve_usd=9_000.0,
                dex_id="pumpfun",
                price_high_since_last_read=entry * 0.99,
                price_low_since_last_read=entry * 0.70,
            )

    stats = await pocket.advance_exit_simulation(
        bonding_ws_feed=_FeedWithExtremes(), db_path=_tmp_db,
    )
    assert stats["closed"] == 1
    row = (await _rows(_tmp_db))[0]
    assert row["exit_reason"] == "hard_stop"
    # filled AT the stop: the market is above it now, so the crossing was real
    # and fillable -- not the -30% low, and not the -5% current price.
    assert row["realized_proceeds"] == pytest.approx(entry * 0.80)


@pytest.mark.asyncio
async def test_the_price_path_is_archived_on_every_check(_tmp_db, monkeypatch):
    """18/08 standing convention this pocket never followed. Without the path,
    a position's history is entry/peak/exit only, so no alternative exit
    threshold can be measured -- only guessed at."""
    from aria_core import shadow_snapshot_archive

    stored = []

    async def _capture(**kwargs):
        stored.append(kwargs)
        return True

    monkeypatch.setattr(shadow_snapshot_archive, "store_snapshot", _capture)

    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    class _Feed:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=0.001, reserve_usd=9_000.0,
                                   dex_id="pumpfun", price_high_since_last_read=0.0012,
                                   price_low_since_last_read=0.0009)

    await pocket.advance_exit_simulation(bonding_ws_feed=_Feed(), db_path=_tmp_db)

    assert stored, "no snapshot archived"
    assert stored[0]["module"] == "solana_late_bonding"
    # the window extremes are what makes replaying another stop distance possible
    # named parameters, not a dict passthrough -- routing them through
    # `price_change_pct` silently dropped them (fixed key set, no error)
    assert stored[0]["window_low"] == 0.0009
    assert stored[0]["window_high"] == 0.0012


@pytest.mark.asyncio
async def test_an_archiving_failure_never_blocks_an_exit(_tmp_db, monkeypatch):
    from aria_core import shadow_snapshot_archive

    async def _boom(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(shadow_snapshot_archive, "store_snapshot", _boom)

    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async def _collapsed(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=0.0002, reserve_usd=390.0, dex_id="pumpfun")

    stats = await pocket.advance_exit_simulation(snapshot_fn=_collapsed, db_path=_tmp_db)
    assert stats["closed"] == 1


def test_the_trailing_stop_cannot_be_credited_above_the_real_market():
    """21/08, found by reading a real closure notification. On 45 closures the
    trailing credited its theoretical stop while the low OBSERVED in the same
    window sat 27.4 points lower on average -- worst real case crediting
    +57.6% on a window where price reached -76%. Since this branch carries the
    pocket's entire upside, it inflated every headline PnL reported."""
    row = {"entry_price": 1.0, "peak_price": 2.0, "reserve_usd": 10_000.0,
           "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0,
           "pool_address": "pool"}

    # price collapsed far below the 1.70 stop and is still there
    crashed = ws_exit_shadow.evaluate_exit(
        row, current_price=0.24, reserve_usd=9_000.0, dex_id="pumpfun", age_minutes=5.0,
    )
    assert crashed["exit_reason"] == "trailing_stop"
    assert crashed["realized_proceeds"] == pytest.approx(0.24)

    # dipped through the stop and recovered above it: the crossing was real
    # and fillable there.
    # 21/08 -- a +100% peak now sits in the widest band (18%, measured: the
    # biggest winners pull back up to 14.7% before their peak), so the stop is
    # at 1.64 rather than the previous flat 1.70.
    recovered = ws_exit_shadow.evaluate_exit(
        row, current_price=1.80, reserve_usd=9_000.0, dex_id="pumpfun", age_minutes=5.0,
        window_low=1.60,
    )
    assert recovered["exit_reason"] == "trailing_stop"
    assert recovered["realized_proceeds"] == pytest.approx(1.64)


@pytest.mark.asyncio
async def test_the_feed_is_read_exactly_once_per_position_per_pass(_tmp_db):
    """21/08, self-inflicted: `get_snapshot()` RESETS the window extremes
    after every read, by design. The ordering pass called it separately, so it
    consumed the window and the real evaluation got extremes already flattened
    to the current price -- silently undoing the exit rule's window_low. Two
    fixes that each looked right cancelled each other out, and nothing failed."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    class _ConsumingFeed:
        """Mirrors the real feed: the extremes are returned once, then reset."""

        def __init__(self):
            self.reads = 0
            self._low = 0.0007

        def get_snapshot(self, _pool):
            self.reads += 1
            low, self._low = self._low, 0.001
            return SimpleNamespace(available=True, price_usd=0.001, reserve_usd=9_000.0,
                                   dex_id="pumpfun", price_high_since_last_read=0.0012,
                                   price_low_since_last_read=low)

    feed = _ConsumingFeed()
    stats = await pocket.advance_exit_simulation(bonding_ws_feed=feed, db_path=_tmp_db)

    assert feed.reads == 1, f"the feed was read {feed.reads} times, consuming the window"
    # the -30% low was seen, so the hard stop must have fired
    assert stats["closed"] == 1
    assert (await _rows(_tmp_db))[0]["exit_reason"] == "hard_stop"


@pytest.mark.asyncio
async def test_the_curve_speed_is_recorded_at_entry(_tmp_db):
    """Recording WHERE a curve is but never how fast it moves made a token
    climbing 70->80% in two minutes look identical to one stuck at 72% for an
    hour. Collected only -- nothing rejects on it."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(sol_velocity=0.42), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert (await _rows(_tmp_db))[0]["sol_velocity_at_entry"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_the_summary_reports_live_throughput(_tmp_db):
    """21/08, operator request: see the rate of entries and closures live, to
    project how long until there is enough data to judge anything."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    out = await pocket.summary(db_path=_tmp_db)
    assert out["entries_last_hour"] == 1
    assert out["closures_last_hour"] == 0


@pytest.mark.asyncio
async def test_throughput_survives_an_epoch_reset(_tmp_db):
    """Measured on a rolling wall-clock window, NOT from CONFIG_EPOCH: the
    epoch moves on every parameter change, which would collapse the rate to
    zero right after each reset and read as a stalled pocket."""
    await _close_row(_tmp_db, 1.5, datetime.now(timezone.utc).isoformat())

    # a summary anchored far in the future returns no closures for the epoch...
    out = await pocket.summary(
        since=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), db_path=_tmp_db,
    )
    assert out["completed"] == 0
    # ...but the throughput must still see the real activity
    assert out["closures_last_hour"] == 1


@pytest.mark.asyncio
async def test_throughput_excludes_activity_older_than_its_window(_tmp_db):
    """21/08 -- SQLite's datetime() yields "YYYY-MM-DD HH:MM:SS" while these
    columns hold ISO strings with a "T", and "T" sorts after " ", so a naive
    comparison matched the WHOLE day: 911 entries/hour reported against a real
    ~78."""
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    await _close_row(_tmp_db, 1.5, old)
    async with aiosqlite.connect(_tmp_db) as db:
        await db.execute(
            f"UPDATE {pocket.TABLE} SET last_checked_at = ? WHERE detected_at = ?", (old, old),
        )
        await db.commit()

    out = await pocket.summary(db_path=_tmp_db)
    assert out["entries_last_hour"] == 0, "a 6-hour-old entry must not count as this hour's"
    assert out["closures_last_hour"] == 0
    assert out["closures_24h"] == 1, "but it is still inside the 24h window"


# --- event-driven exit: reacting to a price move instead of waiting a turn ---

@pytest.mark.asyncio
async def test_a_price_move_closes_the_position_without_waiting_for_the_sweep(_tmp_db):
    """21/08 -- the polling sweep measured 8s of lag on a 10s cadence, and 8s
    is enough for a collapsing curve to run from -15% to -30%, which is why a
    -20% stop kept filling near -30%."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    entry = (await _rows(_tmp_db))[0]["entry_price"]

    class _Crashed:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=entry * 0.75,
                                   reserve_usd=9_000.0, dex_id="pumpfun",
                                   price_high_since_last_read=entry * 0.99,
                                   price_low_since_last_read=entry * 0.75)

    out = await pocket.advance_position_by_pool(
        "poolA", bonding_ws_feed=_Crashed(), db_path=_tmp_db,
    )
    assert out == {"checked": 1, "closed": 1}
    assert (await _rows(_tmp_db))[0]["exit_reason"] == "hard_stop"


@pytest.mark.asyncio
async def test_the_event_path_never_reaches_for_rest(_tmp_db):
    """An event handler that could block on a throttled provider would stall
    the very reactivity it exists for. A pool with no local price simply waits
    for the polling sweep."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    rest_calls = []

    async def _rest(_client, pool, _mint, *, chain):
        rest_calls.append(pool)
        return SimpleNamespace(available=True, price_usd=0.0001, reserve_usd=10.0, dex_id="pumpfun")

    class _NotPushedYet:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=False, price_usd=None)

    out = await pocket.advance_position_by_pool(
        "poolA", bonding_ws_feed=_NotPushedYet(), snapshot_fn=_rest, db_path=_tmp_db,
    )
    assert out == {"checked": 0, "closed": 0}
    assert rest_calls == []
    assert (await _rows(_tmp_db))[0]["exit_reason"] is None


@pytest.mark.asyncio
async def test_an_unknown_pool_is_a_no_op(_tmp_db):
    """The feed pushes every tracked pool, most of which are other pockets'
    or candidates we never entered."""
    class _Any:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=0.001, reserve_usd=9_000.0,
                                   dex_id="pumpfun")

    assert await pocket.advance_position_by_pool(
        "poolNeverEntered", bonding_ws_feed=_Any(), db_path=_tmp_db,
    ) == {"checked": 0, "closed": 0}


@pytest.mark.asyncio
async def test_both_exit_paths_run_the_same_code(_tmp_db):
    """Two call sites each carrying their own copy would mean the pocket
    quietly trading two policies at once, surfacing as unexplainable PnL
    rather than as a failure."""
    import inspect

    for fn in (pocket.advance_exit_simulation, pocket.advance_position_by_pool):
        assert "_apply_exit_check" in inspect.getsource(fn)


# --- reinforcement measured in parallel, never acted on (21/08) ---

@pytest.mark.asyncio
async def test_crossing_the_trigger_records_a_would_be_reinforcement(_tmp_db):
    """Operator's idea: capital added after a token proved something returns
    +8.2% while capital committed blind at entry returns -10.4%, on the same
    tokens over the same period."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    entry = (await _rows(_tmp_db))[0]["entry_price"]

    class _Rising:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=entry * 1.40,
                                   reserve_usd=9_000.0, dex_id="pumpfun",
                                   price_high_since_last_read=entry * 1.40,
                                   price_low_since_last_read=entry * 1.40)

    await pocket.advance_exit_simulation(bonding_ws_feed=_Rising(), db_path=_tmp_db)
    row = (await _rows(_tmp_db))[0]
    assert row["reinforce_price"] == pytest.approx(entry * 1.30)
    assert row["reinforce_at"] is not None
    # the live position is untouched: shadow-only, measured in parallel
    assert row["exit_reason"] is None


@pytest.mark.asyncio
async def test_a_position_that_never_rises_records_no_reinforcement(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    entry = (await _rows(_tmp_db))[0]["entry_price"]

    class _Flat:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=entry * 1.05,
                                   reserve_usd=9_000.0, dex_id="pumpfun",
                                   price_high_since_last_read=entry * 1.05,
                                   price_low_since_last_read=entry * 1.05)

    await pocket.advance_exit_simulation(bonding_ws_feed=_Flat(), db_path=_tmp_db)
    row = (await _rows(_tmp_db))[0]
    assert row["reinforce_price"] is None
    assert row["reinforced_final_multiplier"] is None


def test_the_reinforced_pnl_is_weighted_by_capital_deployed():
    """Weighted by capital DEPLOYED, not by position count: a reinforcement
    that never fires means half the capital was never committed."""
    # entry 1.0, reinforced at 1.30, exits at 2.0
    got = pocket._reinforced_multiplier(
        {"entry_price": 1.0, "reinforce_price": 1.30}, 2.0,
    )
    # entry half: +100%, added half: 2.0/1.3-1 = +53.8% -> mean +76.9%
    assert got == pytest.approx(1.769, abs=1e-3)


def test_an_untriggered_position_reports_none_not_its_own_result():
    """Reporting the same number twice would silently pad the sample of
    "reinforced" trades with untouched ones."""
    assert pocket._reinforced_multiplier({"entry_price": 1.0, "reinforce_price": None}, 2.0) is None


@pytest.mark.asyncio
async def test_a_position_crossing_and_closing_in_one_check_still_counts(_tmp_db):
    """The fastest movers are exactly the ones worth reinforcing -- recording
    the trigger after the exit rule would systematically miss them."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    entry = (await _rows(_tmp_db))[0]["entry_price"]

    class _SpikeThenStop:
        """Peaked at +60% and fell back through the trailing stop in the same
        window."""

        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=entry * 1.20,
                                   reserve_usd=9_000.0, dex_id="pumpfun",
                                   price_high_since_last_read=entry * 1.60,
                                   price_low_since_last_read=entry * 1.20)

    await pocket.advance_exit_simulation(bonding_ws_feed=_SpikeThenStop(), db_path=_tmp_db)
    row = (await _rows(_tmp_db))[0]
    assert row["reinforce_price"] == pytest.approx(entry * 1.30)
    assert row["exit_reason"] == "trailing_stop"
    assert row["reinforced_final_multiplier"] is not None
