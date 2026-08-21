"""Idempotent, concurrency-safe column migrations.

Every module in this codebase that grows a table does the same two steps:
read ``PRAGMA table_info``, then ``ALTER TABLE ... ADD COLUMN`` for whatever is
missing. Read-then-write is a race. Two processes starting together -- the API
container and the standalone shadow process, or two test cases -- both see the
column missing, both issue the ALTER, and the loser gets
``sqlite3.OperationalError: duplicate column name``.

Found live on 2026.08.21: `test_concurrent_reservations_never_both_succeed_past_
the_cap` failed exactly this way inside the full suite while passing in
isolation. A flaky test is the mild symptom; the real one is a process failing
to start right after an incident, which is precisely when several come up at
once.

An audit the same day found 33 modules performing this migration and NONE
guarding against it.

The fix is not a lock: SQLite already serialises the writes. It is to accept
that losing the race is a NORMAL outcome and not an error -- the column exists,
which is all the caller wanted.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

# SQLite's message when a column is added twice. Matched on substring because
# the surrounding text varies across versions, while these two words do not.
_DUPLICATE_MARKER = "duplicate column"


def is_duplicate_column_error(exc: BaseException) -> bool:
    """True when an exception means "another writer already added it".

    Deliberately narrow: any OTHER OperationalError (locked database, disk
    full, malformed schema) must still surface. Swallowing those would turn a
    real failure into a silently half-migrated table.
    """
    return isinstance(exc, sqlite3.OperationalError) and _DUPLICATE_MARKER in str(exc).lower()


async def ensure_columns(db, table: str, columns) -> int:
    """Adds every missing column in ``columns`` -- ``(name, ddl)`` pairs.

    Returns how many were actually added by THIS call. Losing the race to
    another process counts as zero added, not as a failure: the desired state
    is reached either way, which is the only thing the caller cares about.

    Does not commit -- the caller owns the transaction boundary, exactly as the
    hand-written versions did.
    """
    cur = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cur.fetchall()}
    added = 0
    for name, ddl in columns:
        if name in existing:
            continue
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added += 1
        except Exception as exc:  # noqa: BLE001 -- narrowed immediately below
            if not is_duplicate_column_error(exc):
                raise
            logger.debug("db_migrations: %s.%s added concurrently by another writer", table, name)
    return added
