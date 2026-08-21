"""Solana LATE-BONDING shadow pocket (20/08). Isolated tmp db, no network.

This pocket exists to measure the band the dome never sampled: past 50% of the
bonding curve it has FOUR closures total, while the winrate doubles from the
<30% band (9.9%, n=1277) to 30-50% (20.9%, n=239)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
