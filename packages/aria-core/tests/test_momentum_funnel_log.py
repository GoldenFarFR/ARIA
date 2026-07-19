"""Persistance cumulée du funnel de rejet momentum (19/07) -- DB isolée par test
(même patron que test_momentum_blacklist.py), aucun appel réseau."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import momentum_funnel_log as fl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "DB_PATH", str(tmp_path / "momentum_funnel_test.db"))


@pytest.mark.asyncio
async def test_record_empty_funnel_is_noop():
    await fl.record_funnel({})
    assert await fl.summarize_since(48) == {}


@pytest.mark.asyncio
async def test_record_then_summarize_roundtrip():
    await fl.record_funnel({"no_entry_signal": 3, "ohlcv_unavailable": 1})
    summary = await fl.summarize_since(48)
    assert summary == {"no_entry_signal": 3, "ohlcv_unavailable": 1}


@pytest.mark.asyncio
async def test_multiple_cycles_accumulate():
    await fl.record_funnel({"no_entry_signal": 3})
    await fl.record_funnel({"no_entry_signal": 5, "wash_trading_ratio": 2})
    summary = await fl.summarize_since(48)
    assert summary == {"no_entry_signal": 8, "wash_trading_ratio": 2}


@pytest.mark.asyncio
async def test_summarize_excludes_entries_older_than_window():
    await fl.record_funnel({"no_entry_signal": 1})  # dans la fenêtre (maintenant)

    # Insère directement une entrée artificiellement ancienne (hors fenêtre 48h) --
    # contourne record_funnel (toujours "maintenant") pour isoler ce chemin.
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    async with aiosqlite.connect(fl.DB_PATH) as db:
        await db.execute(
            "INSERT INTO momentum_funnel_log (recorded_at, reason_code, count) VALUES (?, ?, ?)",
            (old_ts, "no_entry_signal", 99),
        )
        await db.commit()

    summary = await fl.summarize_since(48)
    assert summary == {"no_entry_signal": 1}  # les 99 anciens sont hors fenêtre


@pytest.mark.asyncio
async def test_summarize_custom_window_includes_older_entry():
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    async with aiosqlite.connect(fl.DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS momentum_funnel_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, "
            "reason_code TEXT NOT NULL, count INTEGER NOT NULL)"
        )
        await db.execute(
            "INSERT INTO momentum_funnel_log (recorded_at, reason_code, count) VALUES (?, ?, ?)",
            (old_ts, "no_entry_signal", 7),
        )
        await db.commit()

    summary = await fl.summarize_since(96)  # fenêtre élargie -- couvre les 72h
    assert summary == {"no_entry_signal": 7}


def test_format_funnel_summary_empty():
    text = fl.format_funnel_summary({}, hours=48)
    assert "Aucun rejet" in text
    assert "48" in text


def test_format_funnel_summary_sorted_by_frequency_desc_with_percentages():
    text = fl.format_funnel_summary(
        {"no_entry_signal": 6, "ohlcv_unavailable": 3, "blacklisted": 1}, hours=48
    )
    idx_signal = text.index("no_entry_signal")
    idx_ohlcv = text.index("ohlcv_unavailable")
    idx_blacklist = text.index("blacklisted")
    assert idx_signal < idx_ohlcv < idx_blacklist  # ordre décroissant de fréquence
    assert "60%" in text  # 6/10
    assert "Total : 10" in text
