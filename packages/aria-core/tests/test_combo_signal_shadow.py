"""Indicator-combination shadow log (06/08) -- append-only log, never blocks,
never fetches. Mirrors test_wick_filter_shadow.py's structure (same shadow
pattern) for the DB layer, plus dedicated tests for the pure ``compute_combos``
signal logic (no DB, no I/O)."""
from __future__ import annotations

import pytest

from aria_core import combo_signal_shadow
from aria_core.skills.ta_levels import Candle

CONTRACT = "0x" + "c" * 40


def _flat_candles(n: int, price: float = 1.0, volume: float = 100.0) -> list[Candle]:
    return [Candle(ts=i, open=price, high=price, low=price, close=price, volume=volume) for i in range(n)]


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(combo_signal_shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    combo_signal_shadow._ensured_db_paths.clear()
    yield
    combo_signal_shadow._ensured_db_paths.clear()


# -- _and3 (optional-boolean AND) --------------------------------------------

def test_and3_both_true_fires():
    assert combo_signal_shadow._and3(True, True) == 1


def test_and3_one_false_does_not_fire():
    assert combo_signal_shadow._and3(True, False) == 0


def test_and3_any_unknown_is_unknown_never_false():
    assert combo_signal_shadow._and3(True, None) is None
    assert combo_signal_shadow._and3(None, None, True) is None


def test_and3_three_true_fires():
    assert combo_signal_shadow._and3(True, True, True) == 1


# -- compute_combos (pure, no I/O) -------------------------------------------

def test_compute_combos_insufficient_warmup_is_all_unknown():
    # 5 candles: far short of every indicator's warmup (MFI needs 11,
    # stochastic 14, MACD's slow EMA 26, VWAP z-score 40).
    combos = combo_signal_shadow.compute_combos(_flat_candles(5))
    assert all(v is None for v in combos.values())


def test_compute_combos_flat_market_never_fabricates_a_verdict():
    # Warmed up (60 candles) but perfectly flat: zero-range candles and
    # zero-width channels make several inputs undefined -- results must stay
    # None (unknown), never a fabricated True/False, same doctrine as every
    # indicator's own division-by-zero guard.
    combos = combo_signal_shadow.compute_combos(_flat_candles(60))
    assert combos["combo3_boll_wick"] is None  # zero-width Bollinger channel
    assert combos["combo5_macd_wick"] is None  # zero-range candle -> no wick
    assert combos["combo6_triple"] is None  # needs wick too


def test_compute_combos_returns_all_six_keys():
    combos = combo_signal_shadow.compute_combos(_flat_candles(60))
    assert set(combos.keys()) == {
        "combo1_rsi_div_mfi", "combo2_stoch_rsi_div", "combo3_boll_wick",
        "combo4_vwap_rsi_div", "combo5_macd_wick", "combo6_triple",
    }


# -- record_evaluation / list_recent (DB layer) ------------------------------

@pytest.mark.asyncio
async def test_record_persists_computed_combos(monkeypatch):
    monkeypatch.setattr(
        combo_signal_shadow, "compute_combos",
        lambda candles: {
            "combo1_rsi_div_mfi": 1, "combo2_stoch_rsi_div": 0, "combo3_boll_wick": None,
            "combo4_vwap_rsi_div": 1, "combo5_macd_wick": 0, "combo6_triple": None,
        },
    )
    await combo_signal_shadow.record_evaluation(
        CONTRACT, "base", wallet="scalping_v8", candles=_flat_candles(60), symbol="TOK",
    )
    rows = await combo_signal_shadow.list_recent()
    assert len(rows) == 1
    row = rows[0]
    assert row["wallet"] == "scalping_v8"
    assert row["symbol"] == "TOK"
    assert row["combo1_rsi_div_mfi"] == 1
    assert row["combo2_stoch_rsi_div"] == 0
    assert row["combo3_boll_wick"] is None
    assert row["combo4_vwap_rsi_div"] == 1
    assert row["combo5_macd_wick"] == 0
    assert row["combo6_triple"] is None


@pytest.mark.asyncio
async def test_record_empty_contract_is_a_noop():
    await combo_signal_shadow.record_evaluation(
        "", "base", wallet="scalping_v8", candles=_flat_candles(60),
    )
    assert await combo_signal_shadow.list_recent() == []


@pytest.mark.asyncio
async def test_record_empty_candles_is_a_noop():
    await combo_signal_shadow.record_evaluation(
        CONTRACT, "base", wallet="scalping_v8", candles=[],
    )
    assert await combo_signal_shadow.list_recent() == []


@pytest.mark.asyncio
async def test_record_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(combo_signal_shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    combo_signal_shadow._ensured_db_paths.clear()
    # must not raise into the caller's real v8 evaluation path
    await combo_signal_shadow.record_evaluation(
        CONTRACT, "base", wallet="scalping_v8", candles=_flat_candles(60),
    )
