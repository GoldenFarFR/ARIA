"""Concurrency-safe column migrations.

Read-then-write is a race: two processes both see a column missing, both issue
the ALTER, and the loser gets "duplicate column name". Found live 2026.08.21 in
the full test suite; 33 modules had the same unguarded pattern.
"""

from __future__ import annotations

import asyncio
import sqlite3

import aiosqlite
import pytest

from aria_core import db_migrations


async def _table(path):
    db = await aiosqlite.connect(path)
    await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    await db.commit()
    return db


def test_missing_columns_are_added(tmp_path):
    async def run():
        db = await _table(tmp_path / "a.db")
        added = await db_migrations.ensure_columns(db, "t", [("a", "TEXT"), ("b", "REAL")])
        await db.commit()
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(t)")).fetchall()}
        await db.close()
        return added, cols
    added, cols = asyncio.run(run())
    assert added == 2
    assert {"a", "b"} <= cols


def test_running_twice_adds_nothing_the_second_time(tmp_path):
    async def run():
        db = await _table(tmp_path / "b.db")
        first = await db_migrations.ensure_columns(db, "t", [("a", "TEXT")])
        second = await db_migrations.ensure_columns(db, "t", [("a", "TEXT")])
        await db.close()
        return first, second
    assert asyncio.run(run()) == (1, 0)


def test_losing_the_race_is_not_an_error(tmp_path):
    # THE regression: another writer adds the column between our PRAGMA read
    # and our ALTER. The desired state is reached, so this must not raise.
    async def run():
        db = await _table(tmp_path / "c.db")
        original = db.execute
        state = {"raced": False}

        async def racing_execute(sql, *a, **kw):
            if "ADD COLUMN" in sql and not state["raced"]:
                state["raced"] = True
                raise sqlite3.OperationalError("duplicate column name: a")
            return await original(sql, *a, **kw)

        db.execute = racing_execute
        added = await db_migrations.ensure_columns(db, "t", [("a", "TEXT")])
        db.execute = original
        await db.close()
        return added, state["raced"]
    added, raced = asyncio.run(run())
    assert raced is True
    assert added == 0


def test_a_real_failure_still_surfaces(tmp_path):
    # Swallowing every OperationalError would turn a locked database or a full
    # disk into a silently half-migrated table.
    async def run():
        db = await _table(tmp_path / "d.db")
        original = db.execute

        async def failing_execute(sql, *a, **kw):
            if "ADD COLUMN" in sql:
                raise sqlite3.OperationalError("database is locked")
            return await original(sql, *a, **kw)

        db.execute = failing_execute
        try:
            await db_migrations.ensure_columns(db, "t", [("a", "TEXT")])
        finally:
            db.execute = original
            await db.close()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        asyncio.run(run())


def test_the_duplicate_detector_is_narrow():
    assert db_migrations.is_duplicate_column_error(
        sqlite3.OperationalError("duplicate column name: x")) is True
    assert db_migrations.is_duplicate_column_error(
        sqlite3.OperationalError("database is locked")) is False
    assert db_migrations.is_duplicate_column_error(ValueError("duplicate column")) is False
