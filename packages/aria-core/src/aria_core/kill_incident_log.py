"""Item #198 (29/07) -- append-only incident log for every kill-switch event
(arm AND lift), across BOTH triggers: the operator's manual /stop|/resume
(gateway/telegram_bot.py) and ARIA's own automatic arm on an unexpected_
outflow (agent_wallet_monitor.py). Born from an operator question raised
while building Item #198 itself: "chaque kill a-t-il son propre rapport
d'incidence ?" -- until this module, ``outgoing_pause.py`` only ever held
the CURRENT state (``pause_state.json``, one snapshot, overwritten on every
``pause()``/``resume()`` call) -- a second incident before the first was
resolved would silently erase all trace that the first one ever happened.

Deliberately kept OUT of ``outgoing_pause.py`` itself: that module is the
most safety-critical primitive in the codebase and is intentionally
dependency-free (json/os/pathlib only, no DB) so the kill-switch itself can
never be taken down by a database problem. This module is purely additive
observability, called from the handful of call sites that already call
``pause()``/``resume()`` -- never wired into the block-check path itself
(``is_paused()`` stays exactly as fast/simple as before). Same doctrine as
``gate_audit_log`` (Item #188): best-effort, a telemetry write failure must
never break the caller's own pause/resume."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

EVENT_ARMED = "armed"
EVENT_LIFTED = "lifted"
TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS kill_incident_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                wallet_name TEXT,
                tx_hash TEXT,
                reason TEXT,
                by TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_kill_incident_log_recorded_at "
            "ON kill_incident_log (recorded_at)"
        )
        await db.commit()


async def record_incident(
    *,
    event_type: str,
    trigger_source: str,
    by: int | str | None = None,
    reason: str = "",
    wallet_name: str | None = None,
    tx_hash: str | None = None,
) -> None:
    """Records one kill-switch event. ``event_type`` in {EVENT_ARMED,
    EVENT_LIFTED}. ``trigger_source`` in {TRIGGER_MANUAL, TRIGGER_AUTO}.
    Best-effort: never raises, so a logging failure can never block the
    actual pause/resume it is merely recording."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO kill_incident_log "
                "(event_type, trigger_source, wallet_name, tx_hash, reason, by, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_type,
                    trigger_source,
                    wallet_name,
                    tx_hash,
                    (reason or "").strip(),
                    str(by) if by is not None else None,
                    _now(),
                ),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- best-effort telemetry, never blocking
        pass


async def list_incidents(limit: int = 50) -> list[dict]:
    """Full incident history (every arm and every lift, manual or auto),
    most recent first."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT event_type, trigger_source, wallet_name, tx_hash, reason, by, recorded_at "
            "FROM kill_incident_log ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
