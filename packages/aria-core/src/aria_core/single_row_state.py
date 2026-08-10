"""Shared async single-row (id=1) SQLite state-table plumbing -- factored
out 10/08 from ``holder_concentration_outage_bypass.py`` and
``goplus_quota_suspension.py`` (built the same day, same connect/
create-table/read/write shape, only the arm threshold and the backoff
POLICY differed between the two). This module owns only the SQL plumbing
-- every arm/disarm decision (thresholds, fixed vs exponential backoff)
stays in the caller, never here.

Table and column names are always hardcoded Python identifiers supplied
by our own source code at construction time, never external/network
input -- safe to compose directly into SQL text (there is no user-facing
path that could ever control a ``SingleRowStore`` argument)."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

ROW_ID = 1


def parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


class SingleRowStore:
    """One (id=1) row holding a fixed set of columns plus a managed
    ``last_updated_at``, auto-created on first use. ``columns`` is an
    ordered list of ``(name, sql_type_ddl, default_value)`` -- the default
    is bound as a real SQL parameter (never string-interpolated), so
    ``INTEGER NOT NULL DEFAULT 0`` columns get an explicit ``0``, not the
    schema's own DEFAULT clause (which only applies when a column is
    omitted from the INSERT entirely)."""

    def __init__(self, db_path: str, table_name: str, columns: list[tuple[str, str, object]]):
        self._db_path = db_path
        self._table_name = table_name
        self._columns = columns

    async def ensure_table(self) -> None:
        cols_sql = ", ".join(f"{name} {ddl}" for name, ddl, _ in self._columns)
        col_names = ", ".join(name for name, _, _ in self._columns)
        placeholders = ", ".join("?" for _ in self._columns)
        defaults = tuple(default for _, _, default in self._columns)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table_name} "
                f"(id INTEGER PRIMARY KEY, {cols_sql}, last_updated_at TEXT NOT NULL)"
            )
            await db.execute(
                f"INSERT OR IGNORE INTO {self._table_name} (id, {col_names}, last_updated_at) "
                f"VALUES (?, {placeholders}, ?)",
                (ROW_ID, *defaults, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def read(self, *column_names: str) -> tuple | None:
        await self.ensure_table()
        cols = ", ".join(column_names)
        async with aiosqlite.connect(self._db_path) as db:
            row = await (
                await db.execute(f"SELECT {cols} FROM {self._table_name} WHERE id = ?", (ROW_ID,))
            ).fetchone()
        return row

    async def write(self, values: dict) -> None:
        await self.ensure_table()
        set_clause = ", ".join(f"{k} = ?" for k in values)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE {self._table_name} SET {set_clause}, last_updated_at = ? WHERE id = ?",
                (*values.values(), datetime.now(timezone.utc).isoformat(), ROW_ID),
            )
            await db.commit()

    async def mutate(self, read_columns: tuple[str, ...], fn):
        """Atomic read-modify-write -- 10/08, added after a real concurrency
        gap flagged independently twice by the Devil's Advocate: ``read()``
        then ``write()`` as two separate connections/transactions lets two
        concurrent callers both read the same stale row and overwrite each
        other's increment (a real path here -- the state row is GLOBAL,
        shared across every candidate the momentum pipeline evaluates
        concurrently, not per-token).

        ``BEGIN IMMEDIATE`` acquires SQLite's write lock BEFORE the SELECT,
        so a second concurrent ``mutate()`` call blocks until the first
        commits rather than reading a value about to be overwritten.
        ``fn(row) -> (values: dict, result)`` computes the new column
        values from the current row (``None`` if never initialized) --
        ``mutate()`` applies ``values`` (skipped entirely if falsy, e.g. a
        pure read-only decision) and returns ``result`` verbatim."""
        await self.ensure_table()
        cols = ", ".join(read_columns)
        async with aiosqlite.connect(self._db_path, isolation_level=None) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute(f"SELECT {cols} FROM {self._table_name} WHERE id = ?", (ROW_ID,))
                ).fetchone()
                values, result = fn(row)
                if values:
                    set_clause = ", ".join(f"{k} = ?" for k in values)
                    await db.execute(
                        f"UPDATE {self._table_name} SET {set_clause}, last_updated_at = ? WHERE id = ?",
                        (*values.values(), datetime.now(timezone.utc).isoformat(), ROW_ID),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return result
