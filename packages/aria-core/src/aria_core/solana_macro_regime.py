"""Macro regime sensor for late-bonding (26/08, specs/008-solana-regime-macro-gate,
Part 1) -- OBSERVATION ONLY. Nothing reads this to gate a trade yet.

The existing endogenous gate (`solana_late_bonding_shadow.regime_state`)
measures the median peak of the last 30 candidates THIS POCKET'S OWN FILTERS
let through -- a closed loop: if the sourcing is looking in the wrong place,
its own candidates read bad and the gate stays shut regardless of what the
real market is doing. This sensor measures something the pocket's filters
never touch: how fast the WHOLE population the curve tracker polls (every
PumpPortal creation, not just the ones clearing band/traction/wash-trading)
reaches the end of its bonding curve. Free -- the curve tracker already
polls every one of these mints for its own reasons (`pumpfun_curve_tracker.py`),
this only adds one INSERT on a threshold crossing it already detects.

Logged for direct comparison against the existing gate for >=100 samples
before any gate-swap is proposed back to the operator -- the endogenous
gate's own history already has one prior rebuild that measured -0.18%/trade
in production against an optimistic simulation (see
`solana_late_bonding_shadow.REGIME_MIN_MEDIAN_PEAK_PCT`'s in-code history,
23/08). Swapping the only active filter for an unvalidated one would risk
repeating that exact mistake.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.solana_late_bonding_shadow import MAX_BONDING_PROGRESS

TABLE = "solana_graduation_log"

# Not literally `complete=True` on the bonding-curve account -- the curve
# tracker's own polling cadence can miss that exact instant (it polls, it
# doesn't stream). Reusing the pocket's own MAX_BONDING_PROGRESS keeps this
# sensor's "graduated" definition consistent with "past this, entry logic no
# longer applies" rather than inventing a second, uncoordinated threshold.
GRADUATION_THRESHOLD = MAX_BONDING_PROGRESS

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return str(shadow_db_path())


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL UNIQUE,
                chain TEXT NOT NULL,
                graduated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_at ON {TABLE}(graduated_at)")
        await db.commit()
    _ensured_db_paths.add(path)


async def record_graduation(
    mint: str, *, chain: str = "solana", db_path: str | None = None,
) -> None:
    """One row per mint crossing GRADUATION_THRESHOLD, read from the curve
    tracker's ALREADY-POLLED progress -- no network call, no extra cost.
    `mint` is UNIQUE: a mint polled multiple times above the threshold (the
    tracker keeps polling until it goes stale) is counted exactly once,
    without needing any in-process dedup state that would not survive a
    restart. Never raises into the caller: a measurement must not cost a
    trade."""
    try:
        await _ensure_table(db_path)
        async with aiosqlite.connect(db_path or _db_path()) as db:
            await db.execute(
                f"INSERT OR IGNORE INTO {TABLE} (mint, chain, graduated_at) VALUES (?, ?, ?)",
                (mint, chain, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- a sensor write never breaks the tracker loop
        pass


async def graduations_per_hour(
    *, window_minutes: float = 60.0, db_path: str | None = None,
) -> float | None:
    """Graduations/hour over the last `window_minutes`, or None if the
    sensor has no data yet in that window -- never a fabricated zero (an
    empty window means "no measurement," not "the market is dead")."""
    await _ensure_table(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    async with aiosqlite.connect(db_path or _db_path()) as db:
        cur = await db.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE graduated_at > ?", (cutoff,)
        )
        row = await cur.fetchone()
    count = row[0] if row else 0
    if count == 0:
        return None
    return count / (window_minutes / 60.0)
