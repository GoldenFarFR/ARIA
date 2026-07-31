"""x402 revenue ledger (07/24) -- the earn-side symmetric to x402_budget.py.
Same isolated-DB pattern as test_x402_budget.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import x402_revenue_ledger as ledger


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DB_PATH", str(tmp_path / "x402_revenue_test.db"))
    yield


@pytest.mark.asyncio
async def test_empty_ledger_has_zero_revenue():
    assert await ledger.total_revenue() == 0.0


@pytest.mark.asyncio
async def test_record_sale_and_total_revenue():
    await ledger.record_sale(product="wallet_score", amount_usd=0.10, status="ok")
    await ledger.record_sale(product="token_analysis_cached", amount_usd=0.25, status="ok")
    assert await ledger.total_revenue() == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_failed_sale_never_counted_in_revenue():
    await ledger.record_sale(product="wallet_score", amount_usd=0.10, status="ok")
    await ledger.record_sale(product="wallet_score", amount_usd=0.10, status="failed")
    assert await ledger.total_revenue() == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_failed_sale_still_logged_never_dropped():
    await ledger.record_sale(product="wallet_score", amount_usd=0.10, status="failed")
    sales = await ledger.list_sales()
    assert len(sales) == 1
    assert sales[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_default_wallet_recorded():
    await ledger.record_sale(product="wallet_score", amount_usd=0.10, status="ok")
    sales = await ledger.list_sales()
    assert sales[0]["wallet"] == "aria-wallet-X402-EVM"


@pytest.mark.asyncio
async def test_total_revenue_since_a_cutoff():
    old = datetime.now(timezone.utc) - timedelta(days=10)
    await ledger.record_sale(product="wallet_score", amount_usd=0.10, status="ok")
    revenue_all = await ledger.total_revenue()
    revenue_recent = await ledger.total_revenue(since=old)
    assert revenue_all == pytest.approx(0.10)
    assert revenue_recent == pytest.approx(0.10)  # the one sale just recorded is within the window


@pytest.mark.asyncio
async def test_unique_recurring_payers_requires_more_than_one_payment():
    await ledger.record_sale(product="wallet_score", payer_address="0xAAA", amount_usd=0.10, status="ok")
    assert await ledger.unique_recurring_payers() == 0

    await ledger.record_sale(product="wallet_score", payer_address="0xAAA", amount_usd=0.10, status="ok")
    assert await ledger.unique_recurring_payers() == 1


@pytest.mark.asyncio
async def test_unique_recurring_payers_ignores_payments_outside_the_window():
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    async with aiosqlite.connect(ledger.DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS x402_revenue_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL, "
            "payer_address TEXT NOT NULL DEFAULT '', wallet TEXT NOT NULL DEFAULT '', "
            "amount_usd REAL NOT NULL DEFAULT 0, status TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO x402_revenue_log (product, payer_address, wallet, amount_usd, status, created_at) "
            "VALUES ('wallet_score', '0xAAA', 'aria-wallet-X402-EVM', 0.10, 'ok', ?)",
            (old,),
        )
        await db.commit()
    await ledger.record_sale(product="wallet_score", payer_address="0xAAA", amount_usd=0.10, status="ok")

    # Only the recent payment counts -- the stale one (45 days ago) falls
    # outside the default 30-day window, so this payer isn't "recurring" yet.
    assert await ledger.unique_recurring_payers(window_days=30) == 0


@pytest.mark.asyncio
async def test_unique_recurring_payers_ignores_failed_payments():
    await ledger.record_sale(product="wallet_score", payer_address="0xAAA", amount_usd=0.10, status="failed")
    await ledger.record_sale(product="wallet_score", payer_address="0xAAA", amount_usd=0.10, status="failed")
    assert await ledger.unique_recurring_payers() == 0


# ── recent_sale_count (31/07, B20 x402 anti-abuse building block) ─────────

@pytest.mark.asyncio
async def test_recent_sale_count_empty_payer_always_zero():
    assert await ledger.recent_sale_count("", "b20_safety", window_seconds=3600) == 0
    assert await ledger.recent_sale_count("   ", "b20_safety", window_seconds=3600) == 0


@pytest.mark.asyncio
async def test_recent_sale_count_counts_within_window():
    await ledger.record_sale(product="b20_safety", payer_address="0xAAA", amount_usd=0.15, status="ok")
    await ledger.record_sale(product="b20_safety", payer_address="0xAAA", amount_usd=0.15, status="ok")
    assert await ledger.recent_sale_count("0xAAA", "b20_safety", window_seconds=3600) == 2


@pytest.mark.asyncio
async def test_recent_sale_count_case_insensitive_payer():
    await ledger.record_sale(product="b20_safety", payer_address="0xAAA", amount_usd=0.15, status="ok")
    assert await ledger.recent_sale_count("0xaaa", "b20_safety", window_seconds=3600) == 1


@pytest.mark.asyncio
async def test_recent_sale_count_ignores_other_products():
    await ledger.record_sale(product="wallet_score", payer_address="0xAAA", amount_usd=0.10, status="ok")
    assert await ledger.recent_sale_count("0xAAA", "b20_safety", window_seconds=3600) == 0


@pytest.mark.asyncio
async def test_recent_sale_count_ignores_failed_sales():
    await ledger.record_sale(product="b20_safety", payer_address="0xAAA", amount_usd=0.15, status="failed")
    assert await ledger.recent_sale_count("0xAAA", "b20_safety", window_seconds=3600) == 0


@pytest.mark.asyncio
async def test_recent_sale_count_ignores_sales_outside_window():
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    async with aiosqlite.connect(ledger.DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS x402_revenue_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL, "
            "payer_address TEXT NOT NULL DEFAULT '', wallet TEXT NOT NULL DEFAULT '', "
            "amount_usd REAL NOT NULL DEFAULT 0, status TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO x402_revenue_log (product, payer_address, wallet, amount_usd, status, created_at) "
            "VALUES ('b20_safety', '0xAAA', 'aria-wallet-X402-EVM', 0.15, 'ok', ?)",
            (old,),
        )
        await db.commit()
    assert await ledger.recent_sale_count("0xAAA", "b20_safety", window_seconds=3600) == 0
