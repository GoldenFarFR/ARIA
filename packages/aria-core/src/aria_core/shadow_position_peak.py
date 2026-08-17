"""Peak concurrent open-position tracker for the Solana/Robinhood pump
shadows -- 17/08, operator-requested ("le pic de trade ouvert en meme
temp... la valeur la plus haute jamais enregistrer... il faut faire un
suivie"). One row per chain via ``SingleRowStore``'s atomic ``mutate()``
(``BEGIN IMMEDIATE``) so two concurrent calls from the same process never
lose an update to each other's read-modify-write.

Only ever WIDENS a stored peak, never resets it here -- a reset (e.g. to
match a fresh archive-and-restart of the shadow tables) is a deliberate,
separate operator action, not something this module decides on its own.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.single_row_state import SingleRowStore

# 17/08 -- see solana_pump_shadow.py's own DB_PATH comment (same incident,
# same fix): dedicated file, no longer shared with the prod container. Reads
# the SAME solana_pump_shadow_log/robinhood_pump_shadow_log tables below, so
# it must live in the same DB as those modules now do.
DB_PATH = str(shadow_db_path())

_OPEN_TABLE = {
    "solana": "solana_pump_shadow_log",
    "robinhood": "robinhood_pump_shadow_log",
}
_COLUMNS = [
    ("peak_count", "INTEGER NOT NULL", 0),
    ("peak_at", "TEXT", None),
]


def _store(chain: str) -> SingleRowStore:
    return SingleRowStore(DB_PATH, f"{chain}_shadow_position_peak_state", _COLUMNS)


async def current_open_count(chain: str) -> int:
    table = _OPEN_TABLE[chain]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {table} WHERE exit_reason IS NULL")
        (count,) = await cur.fetchone()
    return count


async def check_and_record_peak(chain: str) -> tuple[int, int]:
    """Counts the chain's currently-open positions, widens the stored
    peak if this count is a new high, and returns
    ``(current_open, stored_peak_after_this_check)``."""
    open_count = await current_open_count(chain)
    now_iso = datetime.now(timezone.utc).isoformat()

    def _apply(row):
        prev_peak = row[0] if row else 0
        if open_count > prev_peak:
            return {"peak_count": open_count, "peak_at": now_iso}, open_count
        return None, prev_peak

    peak = await _store(chain).mutate(("peak_count",), _apply)
    return open_count, peak


async def get_peak(chain: str) -> tuple[int, str | None]:
    row = await _store(chain).read("peak_count", "peak_at")
    if row is None:
        return 0, None
    return row[0], row[1]


async def seed_peak_if_higher(chain: str, count: int, at_iso: str) -> None:
    """One-time seed from a historical calculation (e.g. an archive-wide
    replay) -- only applied if it exceeds whatever is already stored,
    same non-decreasing invariant as ``check_and_record_peak``."""

    def _apply(row):
        prev_peak = row[0] if row else 0
        if count > prev_peak:
            return {"peak_count": count, "peak_at": at_iso}, None
        return None, None

    await _store(chain).mutate(("peak_count",), _apply)
