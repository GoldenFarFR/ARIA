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
from aria_core.services.pumpfun_bonding_ws import (
    INITIAL_CURVE_TOKENS,
    price_and_reserve_from_curve,
)

CHAIN = "solana"


# 23/08 -- the pocket's downside rule changed NAME again when FIXED_STOP_PCT was
# disabled: what used to close as `fixed_stop` now closes as `hard_stop` (-20%)
# or `trailing_stop`. These tests were never about which mechanism fires -- they
# assert that a position falling hard DOES get closed by a downside rule, and
# pinning one name makes them fail on a deliberate retune instead of catching a
# position left unprotected. That real invariant is what is asserted now.
# A test that needs one SPECIFIC mechanism sets the distance itself
# (`monkeypatch.setattr(pocket, "FIXED_STOP_PCT", 5.0)`) rather than relying on
# whatever production happens to be running.
_DOWNSIDE_EXITS = {"fixed_stop", "hard_stop", "trailing_stop", "liquidity_collapse"}


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
    left = int(total * (1.0 - progress))
    # 22/08 -- the virtual reserves too, so the fixture is a curve the entry
    # path can actually PRICE. Without them `price_and_reserve_from_curve`
    # returns (None, None) and the on-chain pricing path is never exercised,
    # which is precisely the path that was missing when position 1772 got its
    # entry from a stale feed instead of from the chain.
    return {
        "real_token_reserves": left,
        "virtual_token_reserves": left + 30 * (10 ** decimals),
        "virtual_quote_reserves": int(30 * (10 ** 9) * (1.0 + progress)),
        "real_quote_reserves": int(85 * (10 ** 9) * progress),
        "complete": complete,
    }


class _Stream:
    def __init__(self, *, buyers=5, top_share=0.1, accel=1.4, sol_velocity=0.05,
                 sell_pressure=0.4):
        self._flow = SimpleNamespace(distinct_buyers=buyers, top_buyer_share=top_share,
                                     sol_velocity=sol_velocity, sell_pressure=sell_pressure)
        self._accel = accel

    def get_flow(self, _mint):
        return self._flow

    def buyer_acceleration(self, _mint):
        return self._accel

    def sell_pressure_slope(self, _mint):
        return 0.1


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
async def test_buys_sells_and_bonding_progress_reach_the_gate_log(_tmp_db):
    """specs/008 -- these 3 columns existed in the schema but were always
    NULL for this pocket before this fix (sell_pressure_slope/buys_observed/
    sells_observed never read from `metrics`/`flow`)."""
    stream = _Stream()
    stream._flow.buy_count = 7
    stream._flow.sell_count = 2

    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=stream, resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async with aiosqlite.connect(_tmp_db) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(
            f"SELECT buys_observed, sells_observed, sell_pressure_slope, bonding_progress "
            f"FROM {pretrade_rejection_log.TABLE} WHERE pocket = 'late_bonding'"
        )
        row = dict(await cur.fetchone())

    assert row["buys_observed"] == 7
    assert row["sells_observed"] == 2
    assert row["sell_pressure_slope"] == 0.1  # from _Stream.sell_pressure_slope()
    assert row["bonding_progress"] is not None


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
    # Any downside rule counts -- see _DOWNSIDE_EXITS. What must hold is that
    # a position falling this hard does not stay open.
    assert (await _rows(_tmp_db))[0]["exit_reason"] in _DOWNSIDE_EXITS


@pytest.mark.asyncio
async def test_a_closed_position_unsubscribes_from_the_bonding_feed(_tmp_db):
    """26/08 -- this pocket called add_pools() on entry but never remove_pools()
    on exit, unlike its two siblings (FAST discovery, ws_exit shadow). Every
    closed position stayed subscribed on the WS feed forever, which is why a
    live measurement saw the tracked pool count climb without ever plateauing
    -- a leak, not real trading volume, driving most of the feed's measured
    RU/day cost."""
    feed = _BondingFeed()
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, bonding_ws_feed=feed, db_path=_tmp_db,
    )
    assert feed.subscribed == [("poolA", "mintA")]

    # The feed itself (not REST) prices open positions, so the collapse must
    # be simulated on the feed's own snapshot -- see
    # test_an_open_position_is_priced_from_the_rpc_feed_not_rest above.
    feed._snap = SimpleNamespace(available=True, price_usd=0.0002, reserve_usd=390.0, dex_id="pumpfun")

    stats = await pocket.advance_exit_simulation(bonding_ws_feed=feed, db_path=_tmp_db)

    assert stats["closed"] == 1
    assert feed.unsubscribed == ["poolA"]


@pytest.mark.asyncio
async def test_an_open_position_never_unsubscribes(_tmp_db):
    """The other half of the same invariant: a position that stays open must
    never be dropped from the feed, or it would silently stop pricing."""
    feed = _BondingFeed(price=0.002)  # unchanged from entry -- no exit rule should fire
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, bonding_ws_feed=feed, db_path=_tmp_db,
    )

    stats = await pocket.advance_exit_simulation(
        snapshot_fn=_rest_unused, bonding_ws_feed=feed, db_path=_tmp_db,
    )

    assert stats["closed"] == 0
    assert feed.unsubscribed == []


async def _rest_unused(_client, _pool, _mint, *, chain):
    # The websocket feed answers first (available price), so REST is never
    # called -- this exists only to satisfy the snapshot_fn parameter.
    raise AssertionError("REST should not be reached while the feed is live")


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
async def test_summary_counts_a_stranded_mid_hold_position_as_a_real_loss(_tmp_db):
    """25/08, real bug found live (operator question: "are rugs counted as a
    total loss?"). The old query used COALESCE(realistic_final_multiplier,
    final_multiplier): a position genuinely bought (realistic_entry_price NOT
    NULL) but stranded mid-hold by a liquidity collapse
    (realistic_final_multiplier NULL, this pocket's own dominant exit path)
    fell back to the non-realistic final_multiplier -- a real rug reported at
    whatever optimistic nominal price the last spot tick showed, instead of
    the salvaged-vs-entry loss it actually was (here: nothing salvaged, a
    real -100%, not the nominal final_multiplier=5.0 the old query would have
    reported)."""
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(_tmp_db) as db:
        await db.execute(
            f"INSERT INTO {pocket.TABLE} "
            "(pool_address, token_address, chain, detected_at, entry_price, "
            " realistic_entry_price, realistic_realized_proceeds, realistic_final_multiplier, "
            " final_multiplier, exit_reason, last_checked_at) "
            "VALUES (?, ?, 'solana', ?, 1.0, 1.0, NULL, NULL, 5.0, 'liquidity_collapse', ?)",
            ("poolA", "mintA", now.isoformat(), now.isoformat()),
        )
        await db.commit()
    out = await pocket.summary(
        since=(datetime.fromisoformat(pocket.CONFIG_EPOCH) - timedelta(days=1)).isoformat(),
        db_path=_tmp_db,
    )
    assert out["completed"] == 1
    assert out["avg_pnl_pct"] == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_summary_still_excludes_a_position_never_fillable_at_entry(_tmp_db):
    """Twin of the test above -- a position whose entry itself was never
    genuinely fillable (realistic_entry_price NULL, too thin from the start)
    must stay excluded exactly as before this fix: this trade never really
    happened, unlike a stranded mid-hold position."""
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(_tmp_db) as db:
        await db.execute(
            f"INSERT INTO {pocket.TABLE} "
            "(pool_address, token_address, chain, detected_at, entry_price, "
            " realistic_entry_price, realistic_final_multiplier, "
            " final_multiplier, exit_reason, last_checked_at) "
            "VALUES (?, ?, 'solana', ?, 1.0, NULL, NULL, 0.1, 'trailing_stop', ?)",
            ("poolA", "mintA", now.isoformat(), now.isoformat()),
        )
        await db.commit()
    out = await pocket.summary(
        since=(datetime.fromisoformat(pocket.CONFIG_EPOCH) - timedelta(days=1)).isoformat(),
        db_path=_tmp_db,
    )
    assert out["completed"] == 1  # still counted as a completed row...
    assert out["avg_pnl_pct"] is None  # ...but never scored, mults stays empty


@pytest.mark.asyncio
async def test_the_collect_wide_phase_is_over_and_recorded():
    """20/08 the band was widened to 0.40 to find out WHICH sub-band works;
    21/08 it answered (rug 48.9% at 40-60% vs 27.0% above 80%) and the floor
    went back up to 0.70.

    22/08 -- lowered to 0.50 on the operator's explicit test. The 21/08 verdict
    is NOT retracted: it stands, and this test now guards the floor against
    drifting below the value deliberately chosen rather than asserting a
    number the data preferred. What changed since makes the retest worth
    running: the liquidity floor went 3000$ -> 5500$ (+23.34 points on
    same-day closures) and most 40-60% rugs were thin pools, so they are cut
    before the band is reached; and entries now price from the CHAIN, so the
    band's own numbers were partly measured on stale prices.

    The ceiling is untouched: the headroom above the floor is what absorbs
    execution latency."""
    assert pocket.MIN_BONDING_PROGRESS >= 0.50
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
        self.unsubscribed = []

    def get_snapshot(self, _pool):
        return self._snap

    async def add_pools(self, pairs):
        self.subscribed.extend(pairs)
        return len(pairs)

    def remove_pools(self, pool_addresses):
        self.unsubscribed.extend(pool_addresses)


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
    # A graduated position is exempt from max_hold but must STILL have a
    # downside rule. Which one fires depends on the current settings, so the
    # assertion is on the invariant, not on the mechanism's name.
    assert row["exit_reason"] in _DOWNSIDE_EXITS


@pytest.mark.asyncio
async def test_the_floor_sits_where_the_data_turns_positive():
    """21/08 -- the collect-wide phase answered: rug risk nearly halves climbing
    the curve (48.9% at 40-60% down to 27.0% above 80%) while the win rate
    rises (37.0% to 50.0%), and PnL turned positive at 70%.

    22/08 -- floor lowered to 0.50 as an explicit, reversible operator test.
    The 21/08 numbers are not disowned; they were measured before the 5500$
    liquidity floor and before entries were priced on-chain, and the archived
    epochs make the comparison direct. The guard stays so the floor cannot
    drift BELOW the tested value by accident -- 0.40 was never good."""
    assert pocket.MIN_BONDING_PROGRESS >= 0.50


@pytest.mark.asyncio
async def test_the_worst_band_is_now_refused():
    """Below the floor stays refused, whatever the floor currently is.

    21/08: 40-60% carried 71% of entries and was the worst band on every axis.
    22/08: the floor is a deliberate 0.50 test, so this asserts against the
    CONSTANT rather than a frozen 0.50 -- otherwise the test would pass while
    silently guarding nothing the day the floor moves again.
    """
    under = max(0.0, pocket.MIN_BONDING_PROGRESS - 0.10)
    ok, reason, _ = await pocket.screen_candidate(
        "mintA", "poolA", trade_stream=_Stream(), curve=_curve(under),
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
async def test_the_exit_rule_sees_the_low_reached_between_reads(_tmp_db, monkeypatch):
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

    # 23/08 -- production disabled the fixed stop (FIXED_STOP_PCT=None),
    # but the FILL mechanic it exercises stays a real invariant and comes
    # back the day a fixed stop does. The test therefore sets the distance
    # itself instead of reading the production constant.
    monkeypatch.setattr(pocket, "FIXED_STOP_PCT", 5.0)
    stats = await pocket.advance_exit_simulation(
        bonding_ws_feed=_FeedWithExtremes(), db_path=_tmp_db,
    )
    assert stats["closed"] == 1
    row = (await _rows(_tmp_db))[0]
    assert row["exit_reason"] == "fixed_stop"
    # filled AT the stop: the market is above it now, so the crossing was real
    # and fillable -- not the -30% low, and not the current price.
    #
    # Written against the CONFIGURED stop rather than a hard-coded 0.95: the
    # distance is retuned deliberately (5% -> 12% on 22/08), and pinning the
    # number here would fail on a legitimate change instead of catching a
    # broken fill. What must hold is that the fill lands ON the stop.
    expected = entry * (1 - pocket.FIXED_STOP_PCT / 100.0)
    assert row["realized_proceeds"] == pytest.approx(expected)


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
    # Any downside rule counts -- see _DOWNSIDE_EXITS. What must hold is that
    # a position falling this hard does not stay open.
    assert (await _rows(_tmp_db))[0]["exit_reason"] in _DOWNSIDE_EXITS


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
    # Any downside rule counts -- see _DOWNSIDE_EXITS. What must hold is that
    # a position falling this hard does not stay open.
    assert (await _rows(_tmp_db))[0]["exit_reason"] in _DOWNSIDE_EXITS


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


@pytest.mark.asyncio
async def test_a_zero_realistic_multiplier_is_not_overwritten_by_the_fallback(_tmp_db, monkeypatch):
    """0.0 is a legitimate total-loss multiplier, not an absent value -- the
    former `or` fallback treated it as falsy and silently substituted the
    (higher) simulated multiplier into the reinforced PnL, hiding the real
    loss on the exact trades reinforcement would have hurt most."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    entry = (await _rows(_tmp_db))[0]["entry_price"]

    def _fake_evaluate_exit(row, **kwargs):
        return {
            "peak_price": row["entry_price"],
            "exit_reason": "trailing_stop",
            "remaining_qty": 0.0,
            "realized_proceeds": 0.0,
            "realistic_realized_proceeds": 0.0,
            "realistic_final_multiplier": 0.0,
            "final_multiplier": 0.8,
        }

    monkeypatch.setattr(pocket, "evaluate_exit", _fake_evaluate_exit)

    class _SpikeThenTotalLoss:
        def get_snapshot(self, _pool):
            return SimpleNamespace(available=True, price_usd=0.0,
                                   reserve_usd=0.0, dex_id="pumpfun",
                                   price_high_since_last_read=entry * 1.60,
                                   price_low_since_last_read=0.0)

    await pocket.advance_exit_simulation(bonding_ws_feed=_SpikeThenTotalLoss(), db_path=_tmp_db)
    row = (await _rows(_tmp_db))[0]
    assert row["reinforce_price"] == pytest.approx(entry * 1.30)
    assert row["exit_reason"] == "trailing_stop"
    # The realistic multiplier was 0.0 (total loss) -- the reinforced PnL
    # must reflect that, not the simulated 0.8 the buggy `or` fallback used.
    assert row["reinforced_final_multiplier"] == pytest.approx(0.0, abs=1e-9)


# --- real-execution seam (21/08) ---

@pytest.mark.asyncio
async def test_the_real_fill_price_replaces_the_quoted_one(_tmp_db):
    """Operator's constraint: real trading REPLACES the execution and nothing
    else. Recording the quote while the fill happened elsewhere would
    reproduce, on real money, the optimistic-fill bug found in the exit rule
    earlier the same day."""
    calls = []

    async def _execute(mint, pool, *, chain, quoted_price):
        calls.append((mint, pool, chain, quoted_price))
        return {"entry_price": quoted_price * 1.03, "tx": "sig123"}

    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db, execute_fn=_execute,
    )
    row = (await _rows(_tmp_db))[0]
    assert len(calls) == 1
    quoted = calls[0][3]
    assert row["entry_price"] == pytest.approx(quoted * 1.03)
    # a modelled price impact makes no sense once a genuine price was paid
    assert row["realistic_entry_price"] == pytest.approx(quoted * 1.03)


@pytest.mark.asyncio
async def test_a_failed_buy_records_no_position_at_all(_tmp_db):
    """A shadow row standing in for a failed real trade would corrupt every
    measurement built on this table."""
    async def _fails(_mint, _pool, *, chain, quoted_price):
        return None

    got = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db, execute_fn=_fails,
    )
    assert got is None
    assert await _rows(_tmp_db) == []


@pytest.mark.asyncio
async def test_an_executor_that_raises_never_creates_a_phantom_position(_tmp_db):
    async def _boom(_mint, _pool, *, chain, quoted_price):
        raise RuntimeError("rpc down mid-swap")

    got = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db, execute_fn=_boom,
    )
    assert got is None
    assert await _rows(_tmp_db) == []


@pytest.mark.asyncio
async def test_without_an_executor_the_pocket_is_byte_identical_to_before(_tmp_db):
    """The seam must not perturb the measurement in flight -- default None
    keeps pure simulation."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["entry_price"] > 0
    # the simulated realistic price still carries the modelled impact,
    # i.e. it differs from the raw quote
    assert row["realistic_entry_price"] != row["entry_price"]


@pytest.mark.asyncio
async def test_a_pool_too_thin_to_trade_is_refused(_tmp_db):
    """21/08 -- entries were found on pools holding TWO DOLLARS. Harmless
    while simulating (we only pretend to buy), impossible in reality where a
    1$ order in a 2$ pool moves the price 50%. The real-execution seam added
    the same day makes this a blocker, not a cosmetic gap."""
    async def _thin(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=0.002, reserve_usd=2.0,
                               dex_id="pumpfun")

    got = await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_thin, db_path=_tmp_db,
    )
    assert got is None
    assert await _rows(_tmp_db) == []


@pytest.mark.asyncio
async def test_the_refusal_is_logged_with_its_real_reserve(_tmp_db):
    """A filter that rejects silently cannot be judged against what it let
    through -- same discipline as every other gate here."""
    async def _thin(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=0.002, reserve_usd=1500.0,
                               dex_id="pumpfun")

    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_thin, db_path=_tmp_db,
    )
    async with aiosqlite.connect(_tmp_db) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT reason FROM {pretrade_rejection_log.TABLE} WHERE pocket = 'late_bonding'"
        )
        reasons = [r["reason"] or "" for r in await cur.fetchall()]
    assert any("blocked_thin_liquidity" in r for r in reasons), reasons


# --- re-entry cooldown (21/08) ---

@pytest.mark.asyncio
async def test_a_token_that_just_stopped_us_cannot_be_bought_again(_tmp_db):
    """21/08, the costliest defect of the day, revealed by an operator
    screenshot: CALLOUTS was bought and stopped THREE times in eight minutes,
    then ran +199% without us. Measured over 6h: 423 closures for only 192
    distinct tokens, 73% were re-entries, up to 12 positions on one token --
    re-traded tokens returned +6.7% against +30.0% for those traded once."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async def _crashed(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=0.0002, reserve_usd=9_000.0,
                               dex_id="pumpfun")

    await pocket.advance_exit_simulation(snapshot_fn=_crashed, db_path=_tmp_db)
    assert (await _rows(_tmp_db))[0]["exit_reason"] is not None

    # same token, different pool address: the cooldown follows the UNDERLYING,
    # not the wrapper -- a token is seen under several addresses over its life
    # (curve, then AMM after graduation).
    again = await pocket.consider_candidate(
        "mintA", "poolB", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert again is None
    assert len(await _rows(_tmp_db)) == 1


@pytest.mark.asyncio
async def test_the_cooldown_expires_rather_than_banning_forever(_tmp_db):
    """A token that stopped us and then genuinely recovers stays a legitimate
    opportunity -- CALLOUTS proved that by running +199%. Just not in the
    seconds that follow, while price oscillates around the threshold that just
    ejected us."""
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async def _crashed(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=0.0002, reserve_usd=9_000.0,
                               dex_id="pumpfun")

    await pocket.advance_exit_simulation(snapshot_fn=_crashed, db_path=_tmp_db)

    # push the exit far enough into the past that the cooldown has elapsed
    old = (datetime.now(timezone.utc) - timedelta(minutes=pocket.REENTRY_COOLDOWN_MINUTES + 5)).isoformat()
    async with aiosqlite.connect(_tmp_db) as db:
        await db.execute(f"UPDATE {pocket.TABLE} SET last_checked_at = ?", (old,))
        await db.commit()

    again = await pocket.consider_candidate(
        "mintA", "poolB", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert again is not None
    assert len(await _rows(_tmp_db)) == 2


@pytest.mark.asyncio
async def test_a_different_token_is_unaffected(_tmp_db):
    await pocket.consider_candidate(
        "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )

    async def _crashed(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=0.0002, reserve_usd=9_000.0,
                               dex_id="pumpfun")

    await pocket.advance_exit_simulation(snapshot_fn=_crashed, db_path=_tmp_db)

    other = await pocket.consider_candidate(
        "mintB", "poolB", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert other is not None


@pytest.mark.asyncio
async def test_the_rest_fallback_receives_the_real_geckoterminal_client():
    """The client handed in by the caller must reach the REST cascade.

    22/08 -- it did not: `_price_position` passed a literal None, so the
    GeckoTerminal fallback inside `_snapshot_with_fallback` raised
    AttributeError the moment DexScreener came back empty. Because DexScreener
    is tried FIRST and never touches the client, the bug was invisible on the
    happy path and only fired when the backup source was actually needed --
    7838 swallowed failures between 20/08 and 22/08, every one a candidate
    dropped by a bug rather than by a filter.
    """
    seen: list[object] = []
    sentinel = object()

    async def _rest(client, pool, token, *, chain):
        seen.append(client)
        return SimpleNamespace(available=True, price_usd=1e-6,
                               reserve_usd=9_000.0, dex_id="pumpfun")

    class _NoFeed:
        def get_snapshot(self, _amm):
            raise RuntimeError("no websocket value here")

    await pocket._price_position(
        {"pool_address": "poolA", "token_address": "mintA"},
        chain="solana", bonding_ws_feed=_NoFeed(), snapshot_fn=_rest,
        geckoterminal_client=sentinel,
    )
    assert seen == [sentinel], (
        "the caller's GeckoTerminal client must reach the REST cascade, "
        "otherwise the fallback path calls None.get_pool_snapshot()"
    )


@pytest.mark.asyncio
async def test_an_entry_is_refused_on_a_stale_price(_tmp_db):
    """A price older than the feed's window may watch a position, never open one.

    22/08, id 1772 -- the entry was recorded at a 5941$ implied mcap while
    DexScreener's own 1-second chart shows 13260$ at that exact second. The
    real quote landing 1.4s later was logged as a +121% PEAK and its
    disappearance as a collapse, so the trailing stop sold a position that was
    still climbing (the token went on to 22K, +66% above the true entry). Both
    numbers were artefacts of comparing two prices of the same instant, and the
    feed had been flagging the first one `stale` all along.

    Paired deliberately with the identical non-stale call: without that half,
    the test passes whenever `consider_candidate` refuses for ANY other reason.
    """
    async def _snap(stale):
        async def _fn(_client, _pool, _mint, *, chain):
            return SimpleNamespace(available=True, price_usd=0.002,
                                   reserve_usd=13000.0, dex_id="pumpfun", stale=stale)
        return _fn

    fresh = await pocket.consider_candidate(
        "mintFresh", "poolFresh", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=await _snap(False), db_path=_tmp_db,
    )
    assert fresh is not None, "the same call must succeed when the price is fresh"

    stale = await pocket.consider_candidate(
        "mintStale", "poolStale", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=await _snap(True), db_path=_tmp_db,
    )
    assert stale is None, "a stale price must never open a position"


@pytest.mark.asyncio
async def test_the_entry_price_comes_from_the_chain_not_the_feed(_tmp_db):
    """The curve read on-chain wins over whatever the websocket last heard.

    22/08 -- the resolver already fetches and decodes this exact account to
    check bonding progress, so the reserves that define the price are in hand
    at zero extra cost. An account read cannot be stale: it IS the state at
    that slot. The feed's last-known-state can be older than its own
    staleness window on a pool subscribed seconds ago, which is how position
    1772 was recorded at 5941$ while the chain said 13260$ -- and the real
    quote arriving 1.4s later became a +121% "peak" that never happened.
    """
    class _Feed:
        sol_usd = 200.0

        def get_snapshot(self, _p):
            raise RuntimeError("must not be needed for the entry price")

    async def _wrong_price(_client, _pool, _mint, *, chain):
        # what the feed would have said: an order of magnitude off
        return SimpleNamespace(available=True, price_usd=0.0001,
                               reserve_usd=13000.0, dex_id="pumpfun", stale=False)

    row_id = await pocket.consider_candidate(
        "mintChain", "poolChain", trade_stream=_Stream(),
        resolve_curves_fn=_resolve_ok, snapshot_fn=_wrong_price,
        bonding_ws_feed=_Feed(), db_path=_tmp_db,
    )
    assert row_id is not None
    row = [r for r in await _rows(_tmp_db) if r["pool_address"] == "poolChain"][0]

    curve = _curve(0.78)
    expected, _ = price_and_reserve_from_curve(curve, token_decimals=6, sol_usd=200.0)
    assert expected is not None, "the fixture curve must be priceable"
    assert row["entry_price"] == pytest.approx(expected, rel=1e-9), (
        "the entry must be priced from the on-chain curve, not from the feed"
    )


@pytest.mark.asyncio
async def test_the_entry_records_how_far_the_curve_fell_back(_tmp_db):
    """Operator's hypothesis, 22/08: buy on a pullback, not at a local top.

    Untestable until now. This pocket archives candles only AFTER entry (3859
    rows, zero before), so nothing recorded what the price did in the seconds
    preceding the buy, and the curve tracker kept the CURRENT progress without
    ever keeping its maximum.

    On a bonding curve the price is a deterministic function of progress, so
    progress falling back from its own high IS the pullback -- measured on the
    one axis wash trading cannot fake.

    Recorded, never acted on: nothing filters on this until the data shows it
    separates winners from losers.
    """
    class _Tracker:
        def seconds_tracked(self, _m): return 90.0
        def progress_retracement_of(self, _m): return 0.18

    row_id = await pocket.consider_candidate(
        "mintPull", "poolPull", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_Tracker(), db_path=_tmp_db,
    )
    assert row_id is not None
    row = [r for r in await _rows(_tmp_db) if r["pool_address"] == "poolPull"][0]
    assert row["progress_retracement_at_entry"] == pytest.approx(0.18)

    # a tracker that cannot answer must leave NULL, never 0.0 -- which would
    # read as "bought exactly at the peak" and silently qualify an unmeasured
    # candidate the day a filter is built on this column.
    class _Blind:
        def seconds_tracked(self, _m): return None

    other = await pocket.consider_candidate(
        "mintBlind", "poolBlind", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_Blind(), db_path=_tmp_db,
    )
    assert other is not None
    blind = [r for r in await _rows(_tmp_db) if r["pool_address"] == "poolBlind"][0]
    assert blind["progress_retracement_at_entry"] is None


# --- retracement-gated shadow variant (23/08) -----------------------------
# `min_retracement`/`table`/`archive_module` are all additive, default-None
# (or default-to-the-primary-constant) parameters on `consider_candidate` /
# `advance_exit_simulation`. The tests above this section already exercise
# every call without these parameters and must stay green unchanged -- that
# full suite passing IS the primary non-regression proof. These tests target
# the new behaviour specifically: the gate itself, its fail-closed default,
# and the isolation between the primary pocket's table and the variant's own.

class _RetracementTracker:
    """Same shape as `_Tracker` above, just named for this section's
    intent -- returns a fixed `progress_retracement_of` value."""

    def __init__(self, retracement: float):
        self._retracement = retracement

    def seconds_tracked(self, _m):
        return 90.0

    def progress_retracement_of(self, _m):
        return self._retracement


@pytest.mark.asyncio
async def test_without_min_retracement_the_pocket_is_byte_identical_to_before(_tmp_db):
    """The most important test in this section: a candidate whose retracement
    is essentially zero (bought at the local top, the exact behaviour this
    variant exists to test AGAINST) must still be accepted when
    `min_retracement` is not passed -- the default must never regress the
    primary pocket, which has real capital wired to `execute_fn` in prod."""
    row_id = await pocket.consider_candidate(
        "mintTop", "poolTop", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_RetracementTracker(0.0),
        db_path=_tmp_db,
    )
    assert row_id is not None
    row = (await _rows(_tmp_db))[0]
    assert row["progress_retracement_at_entry"] == pytest.approx(0.0)
    # Landed in the PRIMARY table -- omitting `table` must never divert an
    # entry to the variant's table. Either the variant's table was never
    # created at all (expected: `_ensure_table` only ever runs against it
    # when `table=RETRACEMENT_TABLE` is passed explicitly), or it exists and
    # is empty -- both read as "the default call never touched it".
    try:
        async with aiosqlite.connect(_tmp_db) as db:
            cur = await db.execute(f"SELECT COUNT(*) FROM {pocket.RETRACEMENT_TABLE}")
            count = (await cur.fetchone())[0]
        assert count == 0
    except aiosqlite.OperationalError:
        pass


@pytest.mark.asyncio
async def test_insufficient_retracement_blocks_the_candidate(_tmp_db):
    """23/08, operator-directed ("ok pars sur le retracement en shadow"):
    a candidate that has not pulled back far enough from its local high must
    be refused, with its own distinct reason so it is separable from every
    other gate in `pretrade_rejection_log`."""
    row_id = await pocket.consider_candidate(
        "mintShallow", "poolShallow", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_RetracementTracker(0.02),
        db_path=_tmp_db, min_retracement=0.05, table=pocket.RETRACEMENT_TABLE,
    )
    assert row_id is None

    async with aiosqlite.connect(_tmp_db) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT reason FROM {pretrade_rejection_log.TABLE} WHERE pocket = 'late_bonding'"
        )
        reasons = [r["reason"] or "" for r in await cur.fetchall()]
    assert any("blocked_insufficient_retracement" in r for r in reasons), reasons


@pytest.mark.asyncio
async def test_unknown_retracement_fails_closed_when_the_gate_is_active(_tmp_db):
    """Fail-CLOSED, same discipline as every other gate in this pocket: a
    candidate whose retracement could not be measured (no curve tracker at
    all here) must never be treated as a pass just because `min_retracement`
    cannot prove it insufficient."""
    row_id = await pocket.consider_candidate(
        "mintUnmeasured", "poolUnmeasured", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=None,
        db_path=_tmp_db, min_retracement=0.05, table=pocket.RETRACEMENT_TABLE,
    )
    assert row_id is None
    async with aiosqlite.connect(_tmp_db) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {pocket.RETRACEMENT_TABLE}")
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_sufficient_retracement_is_accepted_into_its_own_table(_tmp_db):
    """Positive case: a real pullback clears the gate and the row lands in the
    VARIANT's table, not the primary pocket's."""
    row_id = await pocket.consider_candidate(
        "mintDip", "poolDip", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_RetracementTracker(0.10),
        db_path=_tmp_db, min_retracement=0.05, table=pocket.RETRACEMENT_TABLE,
    )
    assert row_id is not None
    async with aiosqlite.connect(_tmp_db) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {pocket.RETRACEMENT_TABLE} WHERE id = ?", (row_id,))
        row = dict(await cur.fetchone())
    assert row["pool_address"] == "poolDip"
    assert row["progress_retracement_at_entry"] == pytest.approx(0.10)
    # Never landed in the primary table either.
    assert [r for r in await _rows(_tmp_db) if r["pool_address"] == "poolDip"] == []


@pytest.mark.asyncio
async def test_the_two_tables_never_interact_on_the_same_pool(_tmp_db):
    """The whole point of the separate table: the primary pocket's
    anti-duplicate check must never see the variant's row for the SAME
    pool/mint, and vice versa -- otherwise the second experiment silently
    starves itself (or the first) of real candidates."""
    primary_id = await pocket.consider_candidate(
        "mintBoth", "poolBoth", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_RetracementTracker(0.10),
        db_path=_tmp_db,
    )
    variant_id = await pocket.consider_candidate(
        "mintBoth", "poolBoth", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_RetracementTracker(0.10),
        db_path=_tmp_db, min_retracement=0.05, table=pocket.RETRACEMENT_TABLE,
    )
    assert primary_id is not None and variant_id is not None

    primary_rows = await _rows(_tmp_db)
    async with aiosqlite.connect(_tmp_db) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {pocket.RETRACEMENT_TABLE}")
        variant_rows = [dict(r) for r in await cur.fetchall()]
    assert len(primary_rows) == 1 and len(variant_rows) == 1

    # Closing the VARIANT's position must never touch the primary's open row.
    async def _crashed(_client, _pool, _mint, *, chain):
        return SimpleNamespace(available=True, price_usd=0.0002, reserve_usd=9_000.0,
                               dex_id="pumpfun")

    await pocket.advance_exit_simulation(
        snapshot_fn=_crashed, db_path=_tmp_db, table=pocket.RETRACEMENT_TABLE,
    )
    variant_after = await _rows_from(_tmp_db, pocket.RETRACEMENT_TABLE)
    primary_after = await _rows(_tmp_db)
    assert variant_after[0]["exit_reason"] is not None
    assert primary_after[0]["exit_reason"] is None


async def _rows_from(path, table):
    async with aiosqlite.connect(path) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(f"SELECT * FROM {table} ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_the_stop_level_and_the_fill_are_stored_as_numbers(_tmp_db, monkeypatch):
    """Operator's ask, 22/08: the stop and the fill, to the decimal, as NUMBERS.

    Both already existed -- inside `exit_detail`, as prose rounded to one
    decimal ("low touched -13.9% vs entry"). Prose cannot be averaged,
    bucketed or swept, so the pocket's largest measured leak (a stop set at
    -5% filling at -9.6% median, below -10% half the time) had to be
    re-derived by hand from text every single time.

    Their DIFFERENCE is the slippage. Kept as two columns rather than one so a
    later question -- "did the stop level itself drift?" -- stays answerable.
    """
    # 23/08 -- production disabled the fixed stop (FIXED_STOP_PCT=None),
    # but the FILL mechanic it exercises stays a real invariant and comes
    # back the day a fixed stop does. The test therefore sets the distance
    # itself instead of reading the production constant.
    monkeypatch.setattr(pocket, "FIXED_STOP_PCT", 5.0)
    row_id = await pocket.consider_candidate(
        "mintStop", "poolStop", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, db_path=_tmp_db,
    )
    assert row_id is not None

    async def _crashed(_client, _pool, _mint, *, chain):
        # price gapped well below the -5% fixed stop
        return SimpleNamespace(available=True, price_usd=0.002 * 0.80,
                               reserve_usd=13000.0, dex_id="pumpfun")

    await pocket.advance_exit_simulation(
        None, chain="solana", snapshot_fn=_crashed, db_path=_tmp_db,
    )
    row = [r for r in await _rows(_tmp_db) if r["pool_address"] == "poolStop"][0]
    assert row["exit_reason"] is not None, "the position must have closed"
    assert row["stop_level_pct"] is not None, "the stop level must be stored"
    assert row["fill_level_pct"] is not None, "the fill must be stored"
    assert row["stop_level_pct"] == pytest.approx(-pocket.FIXED_STOP_PCT, abs=0.01)
    # a gap fills BELOW the stop -- that difference is the whole point
    assert row["fill_level_pct"] < row["stop_level_pct"]


@pytest.mark.asyncio
async def test_the_climb_before_the_buy_is_archived(_tmp_db, monkeypatch):
    """The BEFORE half of the standing convention (18/08), finally honoured.

    "je veut les bougies avant et apres le point dachat a chaque futur shadow".
    This pocket wrote only the AFTER half -- 3859 archived rows, zero before --
    so nothing recorded what the curve did in the minutes preceding a buy, and
    the only question left worth asking (is the ENTRY POINT the real lever?)
    could not be answered at all.

    Rebuilt from progress rather than from candles we never had: on a bonding
    curve the price IS `virtual_quote / virtual_token`, so the two carry the
    same information, and the tracker has sampled progress since the mint
    appeared on the creation feed. The scale is anchored on the entry so the
    stored path is directly comparable to the "after" rows.
    """
    stored = {}

    async def _fake_store(*, module, position_id, pool_address, chain, phase, candles):
        stored[phase] = candles
        return len(candles)

    monkeypatch.setattr(pocket.shadow_candle_archive, "store_candles", _fake_store)

    class _Tracker:
        def seconds_tracked(self, _m): return 90.0
        def progress_retracement_of(self, _m): return 0.10
        def progress_history_of(self, _m):
            # climbed 0.60 -> 0.78, i.e. the entry sits at the top of this run
            return [(100.0 + i, 0.60 + i * 0.02) for i in range(10)]

    row_id = await pocket.consider_candidate(
        "mintPath", "poolPath", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_snapshot_ok, curve_tracker=_Tracker(), db_path=_tmp_db,
    )
    assert row_id is not None
    for _ in range(40):                       # the archive task is fire-and-forget
        if "before" in stored:
            break
        await asyncio.sleep(0.01)

    assert "before" in stored, "the pre-entry path must be archived"
    path = stored["before"]
    assert len(path) == 10
    # prices must RISE with progress, and be anchored on the entry price
    assert path[0].close < path[-1].close
    assert all(c.close > 0 for c in path)


@pytest.mark.asyncio
async def test_the_onchain_override_works_on_the_real_snapshot_type(_tmp_db):
    """The REST snapshot has no `stale` field -- overriding it must not raise.

    22/08, found in production: the on-chain price override passed `stale=False`
    to `dataclasses.replace`, which raises on `PoolSnapshot` because that shape
    (unlike the websocket one) has no such field. `consider_candidate` swallows
    every exception by design, so 28 candidates in 20 minutes were dropped in
    SILENCE -- the pocket read as idle rather than broken.

    The existing tests all used SimpleNamespace, which accepts any attribute
    and therefore could never catch this. This one uses the REAL type.
    """
    from aria_core.solana_pump_shadow import PoolSnapshot

    async def _real_shape(_client, _pool, _mint, *, chain):
        return PoolSnapshot(pool_address="poolReal", price_usd=0.002,
                            reserve_usd=13000.0, available=True, dex_id="pumpfun")

    class _Feed:
        sol_usd = 200.0
        def get_snapshot(self, _p):
            raise RuntimeError("force the REST path")

    row_id = await pocket.consider_candidate(
        "mintReal", "poolReal", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
        snapshot_fn=_real_shape, bonding_ws_feed=_Feed(), db_path=_tmp_db,
    )
    assert row_id is not None, (
        "a real PoolSnapshot must survive the on-chain override -- silently "
        "dropping it is how 28 candidates vanished"
    )


# ---------------------------------------------------------------- regime gate
# 23/08 -- operator's direction: "je préfère trader moins si le marché le permet
# pas que trader pour perdre". The gate refuses the MARKET, never the token, so
# the rare runners that carry the whole result are never filtered out.


class TestRegimeGate:
    def test_below_the_window_there_is_no_verdict(self):
        """A fresh epoch must be OPEN, not shut.

        The failure this guards against is circular: refusing on absent data
        would stop the pocket from ever collecting the data that lets it decide.
        """
        assert pocket.regime_median_peak([50.0] * (pocket.REGIME_WINDOW - 1)) is None
        assert pocket.regime_median_peak([]) is None

    def test_the_median_is_taken_on_the_most_recent_window_only(self):
        """Old closures must not keep a dead market open (or a live one shut)."""
        old_boom = [500.0] * pocket.REGIME_WINDOW
        recent_bust = [1.0] * pocket.REGIME_WINDOW
        assert pocket.regime_median_peak(old_boom + recent_bust) == 1.0
        assert pocket.regime_median_peak(recent_bust + old_boom) == 500.0

    def test_the_median_ignores_a_single_spike(self):
        """The whole point of a median over a mean: 29 dead tokens and one
        1000% runner is a DEAD market, and an average would call it alive.

        Asserted against a literal rather than against the live constant, which
        is None while the gate is disarmed -- the median's behaviour is a
        property of the maths, not of the current threshold."""
        peaks = [0.0] * (pocket.REGIME_WINDOW - 1) + [1000.0]
        assert pocket.regime_median_peak(peaks) == 0.0

    @pytest.mark.asyncio
    async def test_an_empty_pocket_reads_as_open(self, _tmp_db):
        state = await pocket.regime_state(db_path=_tmp_db)
        assert state["open"] is True
        assert state["samples"] == 0
        assert state["median_peak_pct"] is None

    @pytest.mark.asyncio
    async def test_a_cold_market_shuts_the_gate_and_a_hot_one_opens_it(self, _tmp_db, monkeypatch):
        """23/08 -- rebuilt on the INDEPENDENT candidates table, not the
        pocket's own trades: this is exactly the fix for the self-feeding bug
        (a shut gate must keep seeing candidates, since it no longer decides
        what reaches the sensor)."""
        import aiosqlite

        await pocket._ensure_regime_candidates_table(_tmp_db)

        async def _fill(peak_pct):
            async with aiosqlite.connect(_tmp_db) as db:
                await db.execute(f"DELETE FROM {pocket.REGIME_CANDIDATES_TABLE}")
                for i in range(pocket.REGIME_WINDOW):
                    await db.execute(
                        f"INSERT INTO {pocket.REGIME_CANDIDATES_TABLE} "
                        f"(pool_address, mint, chain, decided_at, entry_price, "
                        f" reserve_usd, peak_price, last_checked_at, tracking_status) "
                        f"VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"pool{i}", f"mint{i}", "solana", f"2026-08-23T00:{i:02d}:00+00:00",
                         1.0, 5000.0, 1.0 + peak_pct / 100.0,
                         f"2026-08-23T00:{i:02d}:00+00:00", "closed"),
                    )
                await db.commit()

        # The gate is DISARMED in production (threshold None) since 23/08, so
        # the rule is exercised against an explicit threshold. Disarming must
        # not silently delete the mechanism's test coverage -- rearming has to
        # stay one value away.
        monkeypatch.setattr(pocket, "REGIME_MIN_MEDIAN_PEAK_PCT", 20.0)

        await _fill(15.0)
        cold = await pocket.regime_state(db_path=_tmp_db)
        assert cold["open"] is False, "a market below the threshold must shut the gate"
        assert cold["samples"] == pocket.REGIME_WINDOW
        assert cold["disarmed"] is False

        await _fill(25.0)
        hot = await pocket.regime_state(db_path=_tmp_db)
        assert hot["open"] is True, "a market above the threshold must reopen it"

    @pytest.mark.asyncio
    async def test_a_shut_gate_still_records_new_candidates(self, _tmp_db, monkeypatch):
        """The exact defect that made the first version measure negative in
        production: a shut gate must NOT stop feeding its own sensor.
        `record_regime_candidate` must never consult `regime_state` at all."""
        for i in range(pocket.REGIME_WINDOW):
            await pocket.record_regime_candidate(
                pool_address=f"cold{i}", mint=f"cold{i}", chain="solana",
                entry_price=1.0, reserve_usd=5000.0, db_path=_tmp_db,
            )
        monkeypatch.setattr(pocket, "REGIME_MIN_MEDIAN_PEAK_PCT", 999.0)
        assert (await pocket.regime_state(db_path=_tmp_db))["open"] is False

        import aiosqlite
        async with aiosqlite.connect(_tmp_db) as db:
            before = (await (await db.execute(
                f"SELECT COUNT(*) FROM {pocket.REGIME_CANDIDATES_TABLE}"
            )).fetchone())[0]

        await pocket.record_regime_candidate(
            pool_address="poolX", mint="mintX", chain="solana",
            entry_price=1.0, reserve_usd=5000.0, db_path=_tmp_db,
        )

        async with aiosqlite.connect(_tmp_db) as db:
            after = (await (await db.execute(
                f"SELECT COUNT(*) FROM {pocket.REGIME_CANDIDATES_TABLE}"
            )).fetchone())[0]
        assert after == before + 1, "the gate being shut must not starve its own sensor"

    @pytest.mark.asyncio
    async def test_consider_candidate_blocks_on_a_confirmed_toxic_defillama_regime(self, _tmp_db, monkeypatch):
        """25/08 -- the exogenous check must block even when the endogenous
        gate above has no opinion yet (fresh table, < REGIME_WINDOW samples)."""
        async def _toxic(_chain):
            return {"regime": "pic_toxique", "detail": "volume 2.10x son EWMA 30j MAIS TVL -15.0%"}

        monkeypatch.setattr(pocket.chain_liquidity_regime, "latest_regime", _toxic)

        got = await pocket.consider_candidate(
            "mintA", "poolA", trade_stream=_Stream(), resolve_curves_fn=_resolve_ok,
            snapshot_fn=_snapshot_ok, db_path=_tmp_db,
        )

        assert got is None
        rows = await _rows(_tmp_db)
        assert rows == []

    @pytest.mark.asyncio
    async def test_consider_candidate_blocks_when_discovery_only_armed(self, _tmp_db, monkeypatch):
        """25/08 -- /offshadowtrades must block the insert while still
        letting discovery/rejection-logging/regime-candidate tracking run
        normally (unlike /offshadow, which cuts every loop wholesale)."""
        from aria_core import shadow_discovery_only

        monkeypatch.setattr(shadow_discovery_only, "is_discovery_only", lambda: True)

        seen = []

        async def _capture(decision, **_kw):
            seen.append(decision)
            return 1

        from aria_core import pretrade_rejection_log
        original = pretrade_rejection_log.record_decision
        pretrade_rejection_log.record_decision = _capture
        try:
            got = await pocket.consider_candidate(
                "mintDiscoveryOnly", "poolDiscoveryOnly", trade_stream=_Stream(),
                resolve_curves_fn=_resolve_ok, snapshot_fn=_snapshot_ok, db_path=_tmp_db,
            )
        finally:
            pretrade_rejection_log.record_decision = original

        assert got is None
        rows = await _rows(_tmp_db)
        assert rows == []
        reasons = [d.reason for d in seen]
        assert "blocked_discovery_only" in reasons

    @pytest.mark.asyncio
    async def test_recording_a_candidate_never_raises(self, _tmp_db):
        """A measurement must not cost a trade: a bad entry_price is ignored,
        not propagated."""
        await pocket.record_regime_candidate(
            pool_address="poolX", mint="mintX", chain="solana",
            entry_price=0.0, reserve_usd=5000.0, db_path=_tmp_db,
        )
        state = await pocket.regime_state(db_path=_tmp_db)
        assert state["samples"] == 0

    @pytest.mark.asyncio
    async def test_recording_a_candidate_persists_bonding_progress(self, _tmp_db):
        """specs/008 -- the curve position at entry must survive for a future
        backtest, not be silently dropped like sell_pressure_slope/
        buys_observed/sells_observed were before this fix."""
        import aiosqlite

        await pocket.record_regime_candidate(
            pool_address="poolZ", mint="mintZ", chain="solana",
            entry_price=1.0, reserve_usd=5000.0, bonding_progress=0.82,
            db_path=_tmp_db,
        )
        async with aiosqlite.connect(_tmp_db) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT bonding_progress, peak_reached_at, decided_at "
                f"FROM {pocket.REGIME_CANDIDATES_TABLE} WHERE mint='mintZ'"
            )
            row = dict(await cur.fetchone())
        assert row["bonding_progress"] == 0.82
        assert row["peak_reached_at"] == row["decided_at"]

    @pytest.mark.asyncio
    async def test_advance_regime_candidates_tracks_the_peak_forward(self, _tmp_db):
        """The whole point of the websocket path: a free, local read updates
        the peak with no network call, and closes tracking once the window
        elapses -- but the row survives for the median."""
        import aiosqlite

        class _Snap:
            price_usd = 1.8
            price_high_since_last_read = 2.0

        class _Feed:
            def get_snapshot(self, pool_address):
                return _Snap()

        await pocket.record_regime_candidate(
            pool_address="poolY", mint="mintY", chain="solana",
            entry_price=1.0, reserve_usd=5000.0, db_path=_tmp_db,
        )
        stats = await pocket.advance_regime_candidates(
            bonding_ws_feed=_Feed(), db_path=_tmp_db,
        )
        assert stats["checked"] == 1
        assert stats["updated"] == 1

        async with aiosqlite.connect(_tmp_db) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT peak_price, tracking_status FROM {pocket.REGIME_CANDIDATES_TABLE}"
            )
            row = dict(await cur.fetchone())
        assert row["peak_price"] == 2.0
        assert row["tracking_status"] == "tracking", "not expired yet, must stay tracking"

    @pytest.mark.asyncio
    async def test_peak_reached_at_only_moves_on_a_strict_new_high(self, _tmp_db):
        """specs/008 -- `last_checked_at` already means "when we last looked";
        without this distinction, time-to-peak stays unmeasurable even with
        the column added, the exact gap this test exists to close."""
        import aiosqlite

        class _RisingSnap:
            price_usd = 2.0
            price_high_since_last_read = 2.0

        class _FlatSnap:
            price_usd = 1.5  # BELOW the already-recorded peak -- no new high
            price_high_since_last_read = None

        class _Feed:
            def __init__(self, snap):
                self._snap = snap

            def get_snapshot(self, pool_address):
                return self._snap

        await pocket.record_regime_candidate(
            pool_address="poolW", mint="mintW", chain="solana",
            entry_price=1.0, reserve_usd=5000.0, db_path=_tmp_db,
        )
        await pocket.advance_regime_candidates(bonding_ws_feed=_Feed(_RisingSnap()), db_path=_tmp_db)

        async with aiosqlite.connect(_tmp_db) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT peak_reached_at FROM {pocket.REGIME_CANDIDATES_TABLE} WHERE mint='mintW'"
            )
            after_rise = (await cur.fetchone())["peak_reached_at"]

        await pocket.advance_regime_candidates(bonding_ws_feed=_Feed(_FlatSnap()), db_path=_tmp_db)

        async with aiosqlite.connect(_tmp_db) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT peak_price, peak_reached_at FROM {pocket.REGIME_CANDIDATES_TABLE} WHERE mint='mintW'"
            )
            row = dict(await cur.fetchone())
        assert row["peak_price"] == 2.0, "a lower snapshot must never overwrite a real peak"
        assert row["peak_reached_at"] == after_rise, "no new high -> peak_reached_at must not move"

    @pytest.mark.asyncio
    async def test_advance_regime_candidates_expires_after_the_window(self, _tmp_db, monkeypatch):
        import aiosqlite

        class _Snap:
            price_usd = 1.1
            price_high_since_last_read = None

        class _Feed:
            def get_snapshot(self, pool_address):
                return _Snap()

        async def _insert_old_row():
            await pocket._ensure_regime_candidates_table(_tmp_db)
            async with aiosqlite.connect(_tmp_db) as db:
                await db.execute(
                    f"INSERT INTO {pocket.REGIME_CANDIDATES_TABLE} "
                    f"(pool_address, mint, chain, decided_at, entry_price, reserve_usd, "
                    f" peak_price, last_checked_at, tracking_status) "
                    f"VALUES ('poolZ','mintZ','solana','2020-01-01T00:00:00+00:00',"
                    f" 1.0, 5000.0, 1.0, '2020-01-01T00:00:00+00:00', 'tracking')"
                )
                await db.commit()

        await _insert_old_row()
        stats = await pocket.advance_regime_candidates(
            bonding_ws_feed=_Feed(), db_path=_tmp_db,
        )
        assert stats["closed"] == 1

    @pytest.mark.asyncio
    async def test_advance_regime_candidates_is_a_noop_without_a_feed(self, _tmp_db):
        stats = await pocket.advance_regime_candidates(bonding_ws_feed=None, db_path=_tmp_db)
        assert stats == {"checked": 0, "updated": 0, "closed": 0}

    @pytest.mark.asyncio
    async def test_a_disarmed_gate_reads_as_open_never_as_shut(self, _tmp_db, monkeypatch):
        """23/08 -- threshold None means "no opinion on the regime". A mechanism
        with no opinion must never be the thing that stops the pocket trading.

        This is the state production actually runs in: the gate was disarmed the
        same day it shipped, because the replay that justified it fed the sensor
        every token while the real code reads only the trades it TOOK -- +13.10%
        simulated against -0.18% live."""
        monkeypatch.setattr(pocket, "REGIME_MIN_MEDIAN_PEAK_PCT", None)
        state = await pocket.regime_state(db_path=_tmp_db)
        assert state["open"] is True
        assert state["disarmed"] is True

    def test_the_regime_reject_reason_is_tracked_forward(self):
        """`blocked_regime_closed` must be in TRACKED_REJECTS: these candidates
        passed every other filter, so their forward path IS the measurement of
        whether refusing them was right. Untracked, the gate could never be
        proven wrong."""
        import inspect

        from aria_core import pretrade_rejection_log

        source = inspect.getsource(pretrade_rejection_log.record_decision)
        assert '"blocked_regime_closed"' in source
