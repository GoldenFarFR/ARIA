"""Wick-confirmation shadow filter (08/05) -- append-only log, never blocks.
Mirrors test_chasing_filter_shadow.py's structure (same shadow pattern)."""
from __future__ import annotations

import pytest

from aria_core import wick_filter_shadow

CONTRACT = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(wick_filter_shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    wick_filter_shadow._ensured_db_paths.clear()
    yield
    wick_filter_shadow._ensured_db_paths.clear()


@pytest.mark.asyncio
async def test_record_below_threshold_would_block():
    await wick_filter_shadow.record_trigger(
        CONTRACT, "base", wallet="scalping_v6", source="limit_order_trigger",
        wick_ratio=0.12, symbol="TOK",
    )
    rows = await wick_filter_shadow.list_recent()
    assert len(rows) == 1
    assert rows[0]["wick_ratio"] == 0.12
    assert rows[0]["would_block"] == 1
    assert rows[0]["wallet"] == "scalping_v6"


@pytest.mark.asyncio
async def test_record_at_or_above_threshold_would_not_block():
    await wick_filter_shadow.record_trigger(
        CONTRACT, "base", wallet="scalping_v7", source="limit_order_trigger",
        wick_ratio=wick_filter_shadow.WICK_SHADOW_THRESHOLD,
    )
    rows = await wick_filter_shadow.list_recent()
    assert rows[0]["would_block"] == 0


@pytest.mark.asyncio
async def test_record_unknown_ratio_never_fabricates_a_verdict():
    await wick_filter_shadow.record_trigger(
        CONTRACT, "base", wallet="scalping_v6", source="limit_order_trigger", wick_ratio=None,
    )
    rows = await wick_filter_shadow.list_recent()
    assert rows[0]["wick_ratio"] is None
    assert rows[0]["would_block"] is None


@pytest.mark.asyncio
async def test_record_empty_contract_is_a_noop():
    await wick_filter_shadow.record_trigger(
        "", "base", wallet="scalping_v6", source="limit_order_trigger", wick_ratio=0.5,
    )
    assert await wick_filter_shadow.list_recent() == []


@pytest.mark.asyncio
async def test_record_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(wick_filter_shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    wick_filter_shadow._ensured_db_paths.clear()
    # must not raise into the caller's trading path
    await wick_filter_shadow.record_trigger(
        CONTRACT, "base", wallet="scalping_v6", source="limit_order_trigger", wick_ratio=0.5,
    )
