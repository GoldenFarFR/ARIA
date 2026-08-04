"""Persistent, append-only log of circuit-breaker transitions (04/08,
operator request after losing the container's raw stdout/stderr logs across
a redeploy -- "if it can help track things, implement permanent logs").

Only the 9 provider states that have a REAL open/closed circuit today are
covered here (verified against the actual code, not assumed):
``blockscout:<chain>``, ``dexscreener``, ``goplus``, ``goplus_auth``,
``wallet_transfers_alchemy``, ``wallet_transfers_moralis``,
``ohlcv_<provider>`` (one per OHLCV cascade provider -- geckoterminal/
mobula/dexpaprika/coinmarketcap/codex/dune). The other 12 service files
identified during the 04/08 mapping only keep a bare failure counter with
no real cooldown (8) or no counter at all (2) or delegate to a breaker
already covered here (2) -- deliberately out of scope; see
``docs/HANDOFF_AUTOMATISATION.md``.

Design: this module is a PURE logger (write + read of past transitions) --
it never imports the 5 service modules that call it, so there is no import
cycle. The in-memory state of those modules stays the single source of
truth for "is it open right now" (read live by
``circuit_breaker_status.get_circuit_status``); this table only answers
"when did it last open/close" and "how often has it opened" for the
Telegram alert and human follow-up.

Only a genuine TRANSITION is recorded (opened: the failure counter reaches
the threshold on this exact call; closed: a success arrives right after the
counter was at/above threshold) -- never one row per failure, which would
turn a sustained outage into thousands of rows. A circuit's cooldown expires
on its own (no code path re-checks and flips it back to "closed") -- so a
service that stays broken shows as a run of ``opened`` rows with no
``closed`` in between; that sequence (not a stored duration) is what the
Telegram alert watches for.

Best-effort, fire-and-forget: ``record_transition_nowait`` is a SYNC
function (every call site is a sync method called from async HTTP-client
code, e.g. ``BlockscoutClient._record_failure``) that schedules the actual
DB write as a background task. A write failure here must never surface in
the caller's real network call."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

EVENTS = ("opened", "closed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS circuit_breaker_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                event TEXT NOT NULL,
                consecutive_failures INTEGER,
                cooldown_seconds REAL,
                detail TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_circuit_breaker_event_service_recorded_at "
            "ON circuit_breaker_event (service, recorded_at)"
        )
        await db.commit()


async def _record_transition(
    service: str, event: str, *,
    consecutive_failures: int | None, cooldown_seconds: float | None,
    detail: str | None,
) -> None:
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO circuit_breaker_event "
                "(service, event, consecutive_failures, cooldown_seconds, detail, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (service, event, consecutive_failures, cooldown_seconds, detail, _now()),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- best-effort telemetry, never blocking
        logger.info("circuit_breaker_log: write failed for %s/%s (%s)", service, event, exc)


def record_transition_nowait(
    service: str, event: str, *,
    consecutive_failures: int | None = None, cooldown_seconds: float | None = None,
    detail: str | None = None,
) -> None:
    """Schedules the DB write in the background -- never awaited by the
    (synchronous) caller. ``event`` must be ``"opened"`` or ``"closed"``
    (defensive assert, same doctrine as ``rsi_divergence_log.record_
    divergence`` -- a typo here would silently create an unanalyzable
    bucket)."""
    if event not in EVENTS:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. a standalone script) -- nothing to schedule
        # this onto. Telemetry only, never worth spinning up a loop for.
        logger.info("circuit_breaker_log: no running loop, dropping %s/%s", service, event)
        return
    task = loop.create_task(
        _record_transition(
            service, event,
            consecutive_failures=consecutive_failures, cooldown_seconds=cooldown_seconds,
            detail=detail,
        )
    )
    # Fire-and-forget, but keep a reference so the task isn't garbage-collected
    # mid-flight (a known asyncio footgun) -- drop it once it completes.
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_background_tasks: set[asyncio.Task] = set()


async def recent_events(service: str | None = None, limit: int = 100) -> list[dict]:
    """Most recent transitions, newest first -- capped, never an unbounded
    dump. Optionally scoped to one ``service``."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if service:
            cursor = await db.execute(
                "SELECT service, event, consecutive_failures, cooldown_seconds, detail, recorded_at "
                "FROM circuit_breaker_event WHERE service = ? ORDER BY recorded_at DESC LIMIT ?",
                (service, max(1, min(limit, 500))),
            )
        else:
            cursor = await db.execute(
                "SELECT service, event, consecutive_failures, cooldown_seconds, detail, recorded_at "
                "FROM circuit_breaker_event ORDER BY recorded_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def last_event_per_service() -> dict[str, dict]:
    """Latest transition row for every ``service`` that has ever logged one --
    keyed by service name. Used to answer "when did it last open/close" and,
    combined with the live in-memory state, "is a still-open circuit backed
    by a fresh 'opened' row or a stale one from a redeploy ago"."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT e.service, e.event, e.consecutive_failures, e.cooldown_seconds,
                   e.detail, e.recorded_at
            FROM circuit_breaker_event e
            INNER JOIN (
                SELECT service, MAX(recorded_at) AS max_recorded_at
                FROM circuit_breaker_event GROUP BY service
            ) latest
            ON e.service = latest.service AND e.recorded_at = latest.max_recorded_at
            """
        )
        rows = await cursor.fetchall()
    return {row["service"]: dict(row) for row in rows}


async def count_opened_since(service: str, since_iso: str) -> int:
    """How many times ``service`` opened since ``since_iso`` -- the signal
    the Telegram alert watches (a service opening repeatedly without a
    ``closed`` in between means it never actually recovered, not that it
    merely had a rough patch)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM circuit_breaker_event "
            "WHERE service = ? AND event = 'opened' AND recorded_at >= ?",
            (service, since_iso),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0
