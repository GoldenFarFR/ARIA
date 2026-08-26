"""SQLite time-window helper -- avoid comparing `datetime('now', ...)` as a
raw string against an ISO-8601 TEXT column.

Found live 26/08: `WHERE decided_at > datetime('now','-8 hours')` silently
matched almost the ENTIRE table (16h of history) instead of the intended 8h
window. SQLite's `datetime('now', ...)` renders with a SPACE separator
(``'2026-08-25 22:38:45'``), while every ``decided_at``-style column in this
project stores 'T'-separated ISO-8601 (``'2026-08-25T14:39:29...'``).
Compared as raw strings, 'T' (ASCII 84) sorts after any digit that follows a
space, so every row from "today" reads as "after" the threshold regardless
of its actual time -- the filter silently expands to the whole table with no
error, no warning, just a wrong (and plausible-looking) count.
"""
from __future__ import annotations

import time


def epoch_hours_ago(hours: float, *, now: float | None = None) -> float:
    """Unix epoch timestamp ``hours`` hours before ``now`` (real clock by
    default). Pair with ``CAST(strftime('%s', column) AS INTEGER) > ?`` in
    SQL -- never a string comparison against ``datetime('now', ...)``:

        threshold = epoch_hours_ago(8)
        db.execute(
            "... WHERE CAST(strftime('%s', decided_at) AS INTEGER) > ?",
            (threshold,),
        )

    The ``CAST`` is not optional. ``strftime('%s', ...)`` itself returns a
    TEXT value, and SQLite's type affinity ranks TEXT above REAL/INTEGER --
    so a bare ``strftime('%s', column) > ?`` against a float parameter is
    ALWAYS true regardless of the actual timestamps, the same class of
    silent-wrong-filter bug this module exists to prevent (found writing
    this module's own first test).
    """
    base = now if now is not None else time.time()
    return base - hours * 3600.0
