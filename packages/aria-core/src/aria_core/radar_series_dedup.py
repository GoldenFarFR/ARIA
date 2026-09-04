"""Factual liquidity-signature duplicate detection for ARIA RADAR V1.

04/09, built after live observation while manually verifying real radar
alerts (operator ask: "fait un suivi des prochaines alertes... verifie
qu'elles sont suffisament bien concu pour justifier de prendre le risque
d'investir"): two distinct serial-deploy bots on Robinhood each reuse a
near-identical initial reserve (~$50,554.1x, then ~$21,0xx-21,3xx) across
dozens of DIFFERENT contracts (confirmed live, distinct pool_address each
time) and reused ticker names within the same hour, dominating the radar
with zero-value noise. Documented: HANDOFF_PIPELINE_MOMENTUM.md's
2026.09.04 entry.

**Purely a noise-dedup mechanism, never a security verdict.** A reserve
amount matching a recent one is a fact about liquidity scripting (the same
deployer/bot reusing a fixed funding amount), not proof the token is
unsafe -- same doctrine as FLOW IMBALANCE (qualified_candidate_radar.py):
render/suppress on facts, never fabricate a causal conclusion.

**Records every EVALUATED candidate, not just the ones that end up
notified** -- otherwise the match chain would stop working the moment the
first duplicate gets suppressed (there would be nothing left to compare
candidate #3 against once #2 was silently dropped)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from .paths import shadow_db_path

TABLE = "radar_series_dedup_log"

DEFAULT_WINDOW_SECONDS = 3600.0
DEFAULT_TOLERANCE_USD = 1.0


def _db_path() -> str:
    return str(shadow_db_path())


async def _ensure_table() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                reserve_usd REAL NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_chain ON {TABLE} (chain)")
        await db.commit()


async def record_and_check_duplicate(
    chain: str, reserve_usd: float | None, *, now: datetime | None = None,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    tolerance_usd: float = DEFAULT_TOLERANCE_USD,
) -> bool:
    """Returns True iff ``reserve_usd`` matches (within ``tolerance_usd``) a
    reserve reading already recorded for this ``chain`` within
    ``window_seconds``. Records this reading regardless of the outcome (see
    module docstring on why). ``None`` is fail-closed: never recorded, never
    treated as a duplicate -- an unmeasurable reserve is not a fact to
    compare against."""
    if reserve_usd is None:
        return False
    await _ensure_table()
    at = now or datetime.now(timezone.utc)
    cutoff = (at - timedelta(seconds=window_seconds)).isoformat()

    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            f"SELECT reserve_usd FROM {TABLE} WHERE chain = ? AND recorded_at >= ?",
            (chain, cutoff),
        )
        rows = await cur.fetchall()
        is_duplicate = any(abs(row[0] - reserve_usd) <= tolerance_usd for row in rows)

        await db.execute(
            f"INSERT INTO {TABLE} (chain, reserve_usd, recorded_at) VALUES (?, ?, ?)",
            (chain, reserve_usd, at.isoformat()),
        )
        await db.commit()

    return is_duplicate
