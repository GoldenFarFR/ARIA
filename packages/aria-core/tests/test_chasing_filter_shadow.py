"""Anti-chasing shadow filter (Item #65, 08/03) -- module tests. Never
blocks anything; verifies the logging math (distance_pct, would_reject
flags) and the recent_low helper, plus the never-raises doctrine.

DB_PATH is resolved once at import time (same pattern as
rsi_divergence_log.py) -- isolated per test via monkeypatch, not
configure_data_dir (which has no effect on an already-resolved module
constant)."""
import sqlite3

import pytest

from aria_core import chasing_filter_shadow


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(chasing_filter_shadow, "DB_PATH", str(tmp_path / "chasing_test.db"))


class _Candle:
    def __init__(self, low: float, high: float = None, close: float = None):
        self.low = low
        self.high = high if high is not None else low
        self.close = close if close is not None else low


def test_recent_low_from_candles_basic():
    candles = [_Candle(low=v) for v in [10, 9, 8, 12, 11]]
    assert chasing_filter_shadow.recent_low_from_candles(candles, 5) == 8


def test_recent_low_from_candles_insufficient_history():
    candles = [_Candle(low=v) for v in [10, 9, 8]]
    assert chasing_filter_shadow.recent_low_from_candles(candles, 5) is None


def test_recent_low_from_candles_uses_only_the_window():
    # A lower low OUTSIDE the window must never be picked up.
    candles = [_Candle(low=1)] + [_Candle(low=v) for v in [10, 9, 8, 12, 11]]
    assert chasing_filter_shadow.recent_low_from_candles(candles, 5) == 8


@pytest.mark.asyncio
async def test_record_check_computes_distance_and_thresholds(tmp_path):
    await chasing_filter_shadow.record_check(
        "0xabc", "base", wallet="scalping_v3", source="direct_buy",
        recent_low=100.0, recent_low_window=14, execution_price=108.0,
        symbol="TEST", variant="V3 Stochastique ultra-réactif",
    )
    con = sqlite3.connect(str(tmp_path / "chasing_test.db"))
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM chasing_filter_shadow_log").fetchone())
    con.close()
    assert row["contract"] == "0xabc"
    assert row["wallet"] == "scalping_v3"
    assert row["source"] == "direct_buy"
    assert row["recent_low"] == 100.0
    assert row["recent_low_window"] == 14
    assert row["execution_price"] == 108.0
    assert row["distance_pct"] == pytest.approx(8.0, rel=1e-6)
    # distance 8% > 3/5/7 thresholds, but not > 10
    assert row["would_reject_3pct"] == 1
    assert row["would_reject_5pct"] == 1
    assert row["would_reject_7pct"] == 1
    assert row["would_reject_10pct"] == 0


@pytest.mark.asyncio
async def test_record_check_missing_data_leaves_distance_null(tmp_path):
    await chasing_filter_shadow.record_check(
        "0xabc", "base", wallet="scalping_v3", source="direct_buy",
        recent_low=None, recent_low_window=None, execution_price=108.0,
    )
    con = sqlite3.connect(str(tmp_path / "chasing_test.db"))
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM chasing_filter_shadow_log").fetchone())
    con.close()
    assert row["distance_pct"] is None
    assert row["would_reject_3pct"] is None
    assert row["would_reject_5pct"] is None


@pytest.mark.asyncio
async def test_record_check_never_raises_on_write_failure(tmp_path, monkeypatch):
    """Best-effort doctrine: a write failure must never propagate into the
    caller's real trading cycle."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(chasing_filter_shadow, "_ensure_table", _boom)
    # Must not raise.
    await chasing_filter_shadow.record_check(
        "0xabc", "base", wallet="scalping_v3", source="direct_buy",
        recent_low=100.0, recent_low_window=14, execution_price=108.0,
    )


@pytest.mark.asyncio
async def test_record_check_empty_contract_is_noop(tmp_path):
    await chasing_filter_shadow.record_check(
        "", "base", wallet="scalping_v3", source="direct_buy",
        recent_low=100.0, recent_low_window=14, execution_price=108.0,
    )
    db_path = tmp_path / "chasing_test.db"
    if not db_path.exists():
        return
    con = sqlite3.connect(str(db_path))
    exists = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chasing_filter_shadow_log'"
    ).fetchone()
    if exists:
        count = con.execute("SELECT COUNT(*) FROM chasing_filter_shadow_log").fetchone()[0]
        assert count == 0
    con.close()
