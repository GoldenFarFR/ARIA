"""shadow_pocket_cap must stay schema-agnostic: every shadow pocket names its
"open" positions differently (exit_reason IS NULL vs status = 'open'), so the
helper is tested against both conventions to prove it never assumes one."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import shadow_pocket_cap as cap


async def _make_db(tmp_path, schema: str, rows: list[dict]) -> aiosqlite.Connection:
    path = str(tmp_path / "shadow.db")
    db = await aiosqlite.connect(path)
    await db.execute(schema)
    if rows:
        cols = list(rows[0].keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        await db.executemany(
            f"INSERT INTO cap_test ({', '.join(cols)}) VALUES ({placeholders})", rows
        )
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_open_position_count_exit_reason_schema(tmp_path):
    db = await _make_db(
        tmp_path,
        "CREATE TABLE cap_test (id INTEGER PRIMARY KEY AUTOINCREMENT, exit_reason TEXT)",
        [{"exit_reason": None}, {"exit_reason": None}, {"exit_reason": "take_profit"}],
    )
    try:
        count = await cap.open_position_count(db, "cap_test", open_clause="exit_reason IS NULL")
        assert count == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_open_position_count_status_schema(tmp_path):
    db = await _make_db(
        tmp_path,
        "CREATE TABLE cap_test (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT)",
        [{"status": "open"}, {"status": "closed"}, {"status": "open"}, {"status": "open"}],
    )
    try:
        count = await cap.open_position_count(db, "cap_test", open_clause="status = 'open'")
        assert count == 3
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_open_position_count_with_params(tmp_path):
    db = await _make_db(
        tmp_path,
        "CREATE TABLE cap_test (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, pocket TEXT)",
        [
            {"status": "open", "pocket": "a"},
            {"status": "open", "pocket": "b"},
            {"status": "closed", "pocket": "a"},
        ],
    )
    try:
        count = await cap.open_position_count(
            db, "cap_test", open_clause="status = 'open' AND pocket = ?", params=("a",)
        )
        assert count == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_at_capacity_below_and_at_and_above_cap(tmp_path):
    rows = [{"exit_reason": None} for _ in range(24)]
    db = await _make_db(
        tmp_path,
        "CREATE TABLE cap_test (id INTEGER PRIMARY KEY AUTOINCREMENT, exit_reason TEXT)",
        rows,
    )
    try:
        # 24 open, cap 25 -- not at capacity yet
        assert await cap.at_capacity(db, "cap_test", open_clause="exit_reason IS NULL", cap=25) is False

        await db.execute("INSERT INTO cap_test (exit_reason) VALUES (NULL)")
        await db.commit()
        # 25 open, cap 25 -- exactly at capacity
        assert await cap.at_capacity(db, "cap_test", open_clause="exit_reason IS NULL", cap=25) is True

        await db.execute("INSERT INTO cap_test (exit_reason) VALUES (NULL)")
        await db.commit()
        # 26 open, cap 25 -- above capacity
        assert await cap.at_capacity(db, "cap_test", open_clause="exit_reason IS NULL", cap=25) is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_at_capacity_uses_module_default_cap(tmp_path):
    rows = [{"status": "open"} for _ in range(cap.MAX_OPEN_POSITIONS_PER_POCKET)]
    db = await _make_db(
        tmp_path,
        "CREATE TABLE cap_test (id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT)",
        rows,
    )
    try:
        assert await cap.at_capacity(db, "cap_test", open_clause="status = 'open'") is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_at_capacity_empty_table(tmp_path):
    db = await _make_db(
        tmp_path,
        "CREATE TABLE cap_test (id INTEGER PRIMARY KEY AUTOINCREMENT, exit_reason TEXT)",
        [],
    )
    try:
        assert await cap.open_position_count(db, "cap_test", open_clause="exit_reason IS NULL") == 0
        assert await cap.at_capacity(db, "cap_test", open_clause="exit_reason IS NULL") is False
    finally:
        await db.close()
