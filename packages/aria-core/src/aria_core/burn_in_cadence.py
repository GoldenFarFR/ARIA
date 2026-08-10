"""Generic burn-in-cadence auto-restoration -- built 10/08 to close a real
gap: every prior burn-in (accelerated observation cadence on a freshly
activated gate, cf. CLAUDE.md "Normes permanentes") required a HUMAN to
remember to revert to the calibrated nominal cadence once the burn-in
proved clean (Item #133, Polymarket paper trading, still not reverted as
of 10/08 -- ~2 weeks after the operator's own "MUST REVERT... once a few
cycles have run cleanly" note).

Registered per ``task_id`` with how many CONSECUTIVE clean cycles are
required before flipping. A cycle is "clean" if the caller (normally
``heartbeat.py``'s own per-task try/except) didn't hit an exception or a
timeout -- the exact signal CLAUDE.md's doctrine asks for ("juger
rapidement si des echecs apparaissent tot"), never a business-logic
judgment. Any real failure resets the streak to zero -- burn-in never
completes on a fluke.

Once complete, ``resolve()`` permanently returns the nominal value for
that task_id -- there is no automatic re-entry into burn-in (a genuinely
new rollout registers itself explicitly in ``_REQUIRED_CLEAN_CYCLES``,
never an implicit state transition)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
_TABLE = "burn_in_cadence_state"

# task_id -> consecutive clean cycles required before reverting to nominal.
# Only tasks listed here are tracked at all -- resolve()/record_cycle_result()
# are no-ops for any other task_id.
_REQUIRED_CLEAN_CYCLES = {
    # Item #133, 27/07 activation -- CANDIDATES_PER_CYCLE=1/60min burn-in,
    # nominal CANDIDATES_PER_CYCLE=3/720min (polymarket_paper_trader.py +
    # heartbeat.py's own HeartbeatTask.interval_minutes).
    "polymarket_paper_cycle": 6,
}


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                task_id TEXT PRIMARY KEY,
                clean_cycles INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def _read(task_id: str) -> tuple[int, str | None] | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                f"SELECT clean_cycles, completed_at FROM {_TABLE} WHERE task_id = ?", (task_id,)
            )
        ).fetchone()
    return row


async def _write(task_id: str, *, clean_cycles: int, completed_at: str | None) -> None:
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            INSERT INTO {_TABLE} (task_id, clean_cycles, completed_at, last_updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                clean_cycles = excluded.clean_cycles,
                completed_at = excluded.completed_at,
                last_updated_at = excluded.last_updated_at
            """,
            (task_id, clean_cycles, completed_at, now),
        )
        await db.commit()


async def is_burn_in_active(task_id: str) -> bool:
    """True only for a REGISTERED task_id that hasn't yet completed its
    burn-in. Any unregistered task_id is simply never in burn-in."""
    if task_id not in _REQUIRED_CLEAN_CYCLES:
        return False
    row = await _read(task_id)
    _, completed_at = row or (0, None)
    return completed_at is None


async def record_cycle_result(task_id: str, ok: bool) -> bool:
    """Called once per real cycle attempt (success/timeout/exception --
    caller decides ``ok``). Returns True exactly once: the call that
    crosses the required clean-cycle count (caller logs/notifies only
    then). No-op (always False) for an unregistered task_id or one that
    already completed."""
    required = _REQUIRED_CLEAN_CYCLES.get(task_id)
    if required is None:
        return False
    row = await _read(task_id)
    clean_cycles, completed_at = row or (0, None)
    if completed_at is not None:
        return False  # already done, nothing left to track

    if not ok:
        if clean_cycles != 0:
            await _write(task_id, clean_cycles=0, completed_at=None)
        return False

    clean_cycles += 1
    if clean_cycles >= required:
        await _write(task_id, clean_cycles=clean_cycles, completed_at=datetime.now(timezone.utc).isoformat())
        return True
    await _write(task_id, clean_cycles=clean_cycles, completed_at=None)
    return False


async def resolve(task_id: str, *, burn_in_value, nominal_value):
    """Returns ``burn_in_value`` while task_id's burn-in is still active,
    ``nominal_value`` once complete (or immediately for an unregistered
    task_id -- nothing to burn in)."""
    if await is_burn_in_active(task_id):
        return burn_in_value
    return nominal_value
