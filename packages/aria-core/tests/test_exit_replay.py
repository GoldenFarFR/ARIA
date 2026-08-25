"""Replay of the real exit rule against archived price paths."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import exit_replay


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "replay.db")


async def _seed(db_path, *, prices, entry=1.0, position_id=1, module="m", table="t"):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {exit_replay.ARCHIVE_TABLE} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, position_id INTEGER, "
            "price_usd REAL, reserve_usd REAL, dex_id TEXT, window_high REAL, "
            "window_low REAL, checked_at TEXT)"
        )
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, entry_price REAL, "
            "reserve_usd REAL, realistic_entry_price REAL, exit_reason TEXT)"
        )
        await conn.execute(
            f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?, ?, ?)",
            (position_id, entry, 10_000.0, entry, "trailing_stop"),
        )
        for p in prices:
            await conn.execute(
                f"INSERT INTO {exit_replay.ARCHIVE_TABLE} "
                "(module, position_id, price_usd, reserve_usd, dex_id, window_high, window_low, checked_at) "
                "VALUES (?, ?, ?, ?, 'pumpfun', ?, ?, '2026-08-21T00:00:00+00:00')",
                (module, position_id, p, 10_000.0, p, p),
            )
        await conn.commit()


@pytest.mark.asyncio
async def test_a_tighter_stop_exits_earlier_on_the_same_real_path(db):
    """The honest half of the asymmetry: a tighter stop fires at or before the
    real exit, and every point up to there is archived."""
    await _seed(db, prices=[1.0, 1.5, 2.0, 1.9, 1.75, 1.6])

    tight = await exit_replay.replay("m", "t", db_path=db, trailing_stop_pct=5.0)
    loose = await exit_replay.replay("m", "t", db_path=db, trailing_stop_pct=15.0)

    assert tight.positions == 1 and loose.positions == 1
    assert tight.pnl_pct > loose.pnl_pct


@pytest.mark.asyncio
async def test_a_position_that_never_exits_is_flagged_truncated(db):
    """A wider stop is a LOWER BOUND, never a verdict: the path stops where the
    real position was closed, so the replay cannot see what came after."""
    await _seed(db, prices=[1.0, 1.05, 1.10, 1.12])

    res = await exit_replay.replay("m", "t", db_path=db, trailing_stop_pct=90.0)

    assert res.positions == 1
    assert res.truncated == 1
    assert res.by_reason.get("still_open") == 1


@pytest.mark.asyncio
async def test_a_path_too_short_to_discriminate_is_skipped(db):
    """Two points cannot tell one stop distance from another -- replaying them
    would add noise dressed as evidence."""
    await _seed(db, prices=[1.0, 0.5])

    res = await exit_replay.replay("m", "t", db_path=db)

    assert res.positions == 0


@pytest.mark.asyncio
async def test_the_sweep_ranks_by_the_outlier_tested_figure(db):
    """A grid of raw averages invites picking the highest one, which is exactly
    how a handful of trades gets mistaken for an edge."""
    await _seed(db, prices=[1.0, 1.5, 2.0, 1.9, 1.7, 1.5], position_id=1)
    await _seed(db, prices=[1.0, 1.2, 1.1, 1.0, 0.9, 0.8], position_id=2)
    await _seed(db, prices=[1.0, 3.0, 8.0, 6.0, 4.0, 3.0], position_id=3)
    await _seed(db, prices=[1.0, 1.1, 1.0, 0.95, 0.9, 0.85], position_id=4)

    rows = await exit_replay.sweep("m", "t", values=[5.0, 15.0, 30.0], db_path=db)

    assert len(rows) == 3
    assert all("pnl_pct_without_top2" in r for r in rows)
    ranked = [r["pnl_pct_without_top2"] for r in rows]
    assert ranked == sorted(ranked, reverse=True)


def test_a_zero_realistic_multiplier_is_not_overwritten_by_the_fallback(monkeypatch):
    """0.0 is a legitimate total-loss multiplier, not an absent value -- the
    former `or` fallback treated it as falsy and silently substituted the
    (higher) simulated multiplier instead, hiding the real loss."""
    def fake_evaluate_exit(state, **kwargs):
        return {
            "peak_price": state["entry_price"],
            "exit_reason": "trailing_stop",
            "realistic_final_multiplier": 0.0,
            "final_multiplier": 0.8,
        }

    monkeypatch.setattr(exit_replay, "evaluate_exit", fake_evaluate_exit)

    entry = {"entry_price": 1.0, "realistic_entry_price": 1.0}
    path = [{"price_usd": 1.0}, {"price_usd": 0.0}, {"price_usd": 0.0}]

    got = exit_replay.replay_one(entry, path)

    assert got is not None
    _, pnl_pct, _ = got
    assert pnl_pct == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_the_replay_uses_the_production_rule_itself():
    """A replay measuring a copy of the rule measures nothing about the rule."""
    from aria_core import solana_fresh_launch_ws_exit_shadow as prod

    assert exit_replay.evaluate_exit is prod.evaluate_exit
