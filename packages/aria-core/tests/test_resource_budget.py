"""Tests for the unified resource-budget ledger (#302) -- window math,
suspend-only cap enforcement, and the lazy idempotent legacy-table
migration (never resets a mid-month counter to zero, never double-counts)."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from aria_core.services import resource_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "aria_db_path", lambda: tmp_path / "resource_budget_test.db")
    yield


@pytest.mark.asyncio
async def test_empty_ledger_starts_at_zero_spend():
    assert await budget.spent_in_window("dune_execution") == 0


@pytest.mark.asyncio
async def test_record_spend_accumulates():
    await budget.record_spend("dune_execution", 1)
    await budget.record_spend("dune_execution", 1)
    assert await budget.spent_in_window("dune_execution") == 2


@pytest.mark.asyncio
async def test_record_spend_scoped_per_provider():
    await budget.record_spend("dune_execution", 5)
    await budget.record_spend("some_other_provider", 100)
    assert await budget.spent_in_window("dune_execution") == 5
    assert await budget.spent_in_window("some_other_provider") == 100


@pytest.mark.asyncio
async def test_can_spend_true_below_cap():
    assert await budget.can_spend("dune_execution", 1, cap=10) is True


@pytest.mark.asyncio
async def test_can_spend_false_at_exact_cap():
    await budget.record_spend("dune_execution", 10)
    assert await budget.can_spend("dune_execution", 1, cap=10) is False


@pytest.mark.asyncio
async def test_can_spend_false_when_this_call_would_exceed_cap():
    await budget.record_spend("dune_execution", 8)
    # 8 spent + 5 this call = 13 > cap 10 -- refused even though 8 < 10 alone
    # (the stricter check this module deliberately adds, see module docstring).
    assert await budget.can_spend("dune_execution", 5, cap=10) is False


@pytest.mark.asyncio
async def test_can_spend_rejects_non_positive_cost():
    assert await budget.can_spend("dune_execution", 0, cap=10) is False
    assert await budget.can_spend("dune_execution", -1, cap=10) is False


@pytest.mark.asyncio
async def test_monthly_window_excludes_last_month_spend():
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    last_month = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    await budget.record_spend("dune_execution", 999, now=last_month)
    assert await budget.spent_in_window("dune_execution", window="monthly", now=now) == 0


@pytest.mark.asyncio
async def test_daily_window_excludes_yesterday_spend():
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    yesterday = datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)
    await budget.record_spend("blockscout", 999, now=yesterday)
    assert await budget.spent_in_window("blockscout", window="daily", now=now) == 0


@pytest.mark.asyncio
async def test_unknown_window_raises():
    with pytest.raises(ValueError):
        await budget.spent_in_window("dune_execution", window="weekly")


@pytest.mark.asyncio
async def test_legacy_table_migrated_on_first_use(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy_migration_test.db"
    monkeypatch.setattr(budget, "aria_db_path", lambda: db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE dune_execution_log (executed_at TEXT NOT NULL)")
        await db.execute("INSERT INTO dune_execution_log (executed_at) VALUES (?)", ("2026-08-01T00:00:00+00:00",))
        await db.execute("INSERT INTO dune_execution_log (executed_at) VALUES (?)", ("2026-08-05T00:00:00+00:00",))
        await db.commit()

    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    # Pre-existing mid-month spend must be preserved, never reset to zero --
    # resetting it would silently let the caller blow through the provider's
    # REAL external monthly quota.
    assert await budget.spent_in_window("dune_execution", now=now) == 2


@pytest.mark.asyncio
async def test_legacy_table_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy_idempotent_test.db"
    monkeypatch.setattr(budget, "aria_db_path", lambda: db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE dune_execution_log (executed_at TEXT NOT NULL)")
        await db.execute("INSERT INTO dune_execution_log (executed_at) VALUES (?)", ("2026-08-01T00:00:00+00:00",))
        await db.commit()

    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    # Calling twice must never double-copy the legacy row.
    assert await budget.spent_in_window("dune_execution", now=now) == 1
    assert await budget.spent_in_window("dune_execution", now=now) == 1

    await budget.record_spend("dune_execution", 1, now=now)
    # A real post-migration spend must never re-trigger a re-copy either.
    assert await budget.spent_in_window("dune_execution", now=now) == 2


@pytest.mark.asyncio
async def test_no_legacy_table_starts_clean_no_crash():
    # "dune_execution" is registered in _LEGACY_TABLES but no legacy table
    # was ever created (fresh install) -- must not raise.
    assert await budget.spent_in_window("dune_execution") == 0


@pytest.mark.asyncio
async def test_provider_without_legacy_entry_is_a_no_op_migration():
    # A provider never registered in _LEGACY_TABLES at all -- migration must
    # be a silent no-op, not an error.
    assert await budget.spent_in_window("some_brand_new_provider") == 0
