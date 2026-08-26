"""26/08 -- regression guard for the SQL time-window bug: `datetime('now',
'-N hours')` compared as a raw string against a 'T'-separated ISO-8601
column silently matched the whole table instead of the intended window."""
from __future__ import annotations

import sqlite3

from aria_core.sql_time_window import epoch_hours_ago


def test_epoch_hours_ago_subtracts_the_right_number_of_seconds():
    assert epoch_hours_ago(8, now=1_000_000.0) == 1_000_000.0 - 8 * 3600.0


def test_epoch_hours_ago_defaults_to_the_real_clock():
    import time
    before = time.time()
    result = epoch_hours_ago(1)
    after = time.time()
    assert before - 3600 <= result <= after - 3600


def test_strftime_comparison_correctly_excludes_rows_older_than_the_window():
    """The exact bug: two rows 16h apart, only the recent one should match
    an 8h window -- proven against a real SQLite connection, not assumed."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (decided_at TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", ("2026-08-25T14:39:29.044729+00:00",))  # 16h ago
    conn.execute("INSERT INTO t VALUES (?)", ("2026-08-26T04:00:00.000000+00:00",))  # 2.6h ago
    now = 1787726817.0  # 2026-08-26T06:38:37+00:00 -- matches the "current" instant these fixtures were captured at
    threshold = epoch_hours_ago(8, now=now)
    rows = conn.execute(
        "SELECT decided_at FROM t WHERE CAST(strftime('%s', decided_at) AS INTEGER) > ?",
        (threshold,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2026-08-26T04:00:00.000000+00:00"


def test_without_the_cast_the_comparison_is_always_true_regardless_of_value():
    """strftime('%s', ...) returns TEXT; SQLite ranks TEXT above REAL by type
    affinity, so a bare comparison against a float parameter is a silent
    always-true bug -- found writing this module's own first test."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (decided_at TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", ("2026-08-25T14:39:29.044729+00:00",))  # 16h ago
    threshold = epoch_hours_ago(8, now=1787726817.0)
    rows = conn.execute(
        "SELECT decided_at FROM t WHERE strftime('%s', decided_at) > ?", (threshold,)
    ).fetchall()
    assert len(rows) == 1  # wrongly matches a 16h-old row against an 8h window


def test_the_original_buggy_pattern_would_have_matched_both_rows():
    """Documents the bug this module exists to avoid -- a raw string
    comparison against `datetime('now', ...)` matches everything from
    "today" regardless of hour, because 'T' sorts after any digit that
    follows a space."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (decided_at TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", ("2026-08-26T00:00:01.000000+00:00",))  # ~6.6h ago
    # datetime('now') is fixed by SQLite's own clock, not injectable here --
    # so this test reproduces the exact string-ordering defect directly.
    assert "2026-08-26T00:00:01.000000+00:00" > "2026-08-26 04:00:00"
