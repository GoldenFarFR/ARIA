"""Dip-recovery shadow (13/08) -- episode dedup + stop/timeout state machine,
mirroring test_v8_rsi_reversal_shadow.py's pattern (isolated tmp sqlite,
state machine tested directly plus end-to-end passes through
record_evaluation with synthetic candles)."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import dip_recovery_shadow as shadow
from aria_core.skills.ta_levels import Candle

CONTRACT = "0x" + "d" * 40
CHAIN = "base"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_tables()
    yield
    shadow._ensured_db_paths.clear()


def _hourly_series(closes: list[float], *, start_ts: int = 0) -> list[Candle]:
    return [
        Candle(ts=start_ts + i * 3600, open=c, high=c, low=c, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


async def _rows(contract=CONTRACT, chain=CHAIN):
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM dip_recovery_shadow WHERE contract = ? AND chain = ? ORDER BY id",
            (contract, chain),
        )
        return [dict(r) for r in await cur.fetchall()]


async def _episode_state(contract=CONTRACT, chain=CHAIN) -> bool | None:
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT in_episode FROM dip_recovery_shadow_episode_state WHERE contract = ? AND chain = ?",
            (contract, chain),
        )
        row = await cur.fetchone()
        return bool(row["in_episode"]) if row is not None else None


# --- record_evaluation: candle-count guard ------------------------------

@pytest.mark.asyncio
async def test_record_evaluation_skips_when_too_few_candles():
    closes = [100.0] * (shadow.MIN_CANDLES_1H - 1)
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes))
    assert await _rows() == []


# --- episode detection / dedup ------------------------------------------

@pytest.mark.asyncio
async def test_record_evaluation_opens_position_on_fresh_30pct_dip():
    closes = [100.0] * shadow.LOOKBACK_CANDLES + [69.0]  # -31% vs 24h ago
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes))
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    # Entry pays the real DEX swap fee (25/08 realism fix) -- never the raw
    # candle close, same doctrine as every other pocket's fill simulation.
    assert rows[0]["entry_price"] == pytest.approx(shadow._realistic_fill_price_candle(69.0))
    assert rows[0]["entry_var_24h_pct"] == pytest.approx(-31.0)
    assert await _episode_state() is True


@pytest.mark.asyncio
async def test_record_evaluation_ignores_dip_under_threshold():
    closes = [100.0] * shadow.LOOKBACK_CANDLES + [75.0]  # -25%, doesn't qualify
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes))
    assert await _rows() == []
    # Never having entered an episode never wrote a row -- no gratuitous
    # INSERT for tokens that never dip (state stays unset, not "False").
    assert await _episode_state() is None


@pytest.mark.asyncio
async def test_record_evaluation_never_reopens_mid_episode():
    closes_a = [100.0] * shadow.LOOKBACK_CANDLES + [69.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_a))
    # Still deep in the same dip on the next passage -- must NOT open a second position.
    closes_b = [100.0] * shadow.LOOKBACK_CANDLES + [65.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_b))
    rows = await _rows()
    assert len(rows) == 1
    # first signal wins, untouched
    assert rows[0]["entry_price"] == pytest.approx(shadow._realistic_fill_price_candle(69.0))


@pytest.mark.asyncio
async def test_record_evaluation_rearms_after_recovery_above_threshold():
    closes_a = [100.0] * shadow.LOOKBACK_CANDLES + [69.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_a))
    # Recovers above the threshold -- episode ends, state re-arms.
    closes_b = [100.0] * shadow.LOOKBACK_CANDLES + [95.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_b))
    assert await _episode_state() is False
    # A brand-new dip episode now opens a SECOND shadow position.
    closes_c = [100.0] * shadow.LOOKBACK_CANDLES + [68.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_c))
    rows = await _rows()
    assert len(rows) == 2
    assert rows[1]["entry_price"] == pytest.approx(shadow._realistic_fill_price_candle(68.0))


# --- stop-loss / timeout --------------------------------------------------

@pytest.mark.asyncio
async def test_stop_loss_closes_position_at_minus_5pct():
    closes_a = [100.0] * shadow.LOOKBACK_CANDLES + [69.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_a))
    # Still under -30%/24h (so no new position opens) but individually
    # dropped -6% from entry (69.0 -> 64.86) -- must trigger the stop.
    closes_b = [100.0] * shadow.LOOKBACK_CANDLES + [64.86]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_b))
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"
    assert rows[0]["close_reason"] == "stop_loss_5pct"
    # -6% raw, but the realistic fee on BOTH legs (25/08 fix) makes the real
    # realized loss slightly worse -- computed via the module's own helpers,
    # never a duplicated fee constant here.
    expected_pnl_pct = (
        shadow._realistic_exit_price_candle(64.86)
        / shadow._realistic_fill_price_candle(69.0)
        - 1.0
    ) * 100.0
    assert rows[0]["pnl_pct"] == pytest.approx(expected_pnl_pct, abs=0.01)
    assert rows[0]["pnl_pct"] < -6.0  # strictly worse than the naive figure


@pytest.mark.asyncio
async def test_timeout_closes_stale_position():
    closes_a = [100.0] * shadow.LOOKBACK_CANDLES + [69.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_a))
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "UPDATE dip_recovery_shadow SET opened_at = ? WHERE contract = ?",
            ("2020-01-01T00:00:00+00:00", CONTRACT),
        )
        await db.commit()
    # Recovered above threshold (no stop-triggering dip), but the position
    # itself is now stale -- timeout should close it regardless.
    closes_b = [100.0] * shadow.LOOKBACK_CANDLES + [95.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_b))
    rows = await _rows()
    assert rows[0]["status"] == "closed"
    assert rows[0]["close_reason"] == "timeout_max_hold"


# --- summary ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_aggregates_open_closed_and_winrate():
    closes_win = [100.0] * shadow.LOOKBACK_CANDLES + [69.0]
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_win))
    closes_recover = [100.0] * shadow.LOOKBACK_CANDLES + [80.0]  # +15.9% vs entry, still no exit signal
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles_1h=_hourly_series(closes_recover))

    other_contract = "0x" + "e" * 40
    closes_loss = [100.0] * shadow.LOOKBACK_CANDLES + [68.0]
    await shadow.record_evaluation(other_contract, CHAIN, symbol="TOK2", candles_1h=_hourly_series(closes_loss))
    closes_stop = [100.0] * shadow.LOOKBACK_CANDLES + [64.0]  # -5.88% from entry -- stops out
    await shadow.record_evaluation(other_contract, CHAIN, symbol="TOK2", candles_1h=_hourly_series(closes_stop))

    summary = await shadow.summary()
    assert summary["open"] == 1
    assert summary["closed"] == 1
    assert summary["wins"] == 0
    assert summary["distinct_tokens"] == 2


# --- realistic fee model (25/08) --------------------------------------------

def test_realistic_fill_price_is_above_the_raw_close():
    assert shadow._realistic_fill_price_candle(100.0) > 100.0


def test_realistic_exit_price_is_below_the_raw_close():
    assert shadow._realistic_exit_price_candle(100.0) < 100.0


def test_realistic_prices_use_the_shared_fee_constant():
    """Never a duplicated fee figure -- same source of truth as every other
    pocket's fill/exit simulation (risk_guard.DEX_SWAP_FEE_PCT)."""
    from aria_core import risk_guard

    assert shadow._realistic_fill_price_candle(100.0) == pytest.approx(
        100.0 * (1.0 + risk_guard.DEX_SWAP_FEE_PCT)
    )
    assert shadow._realistic_exit_price_candle(100.0) == pytest.approx(
        100.0 * (1.0 - risk_guard.DEX_SWAP_FEE_PCT)
    )
