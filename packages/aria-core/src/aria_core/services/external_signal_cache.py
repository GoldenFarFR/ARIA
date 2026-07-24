"""Persisted TTL cache for expensive external-signal lookups (GitHub/Website/
Docs/X substance) -- 24/07, design validated by the operator on 23/07 (this
module implements it).

Why this exists, distinct from ``skills/vc_cache.py``: that module is an
IN-MEMORY cache (300s TTL, wiped on every container restart/redeploy) built
for a `/vc` analysis that's re-requested within minutes. The substance
signals (GitHub commit history, a full website/docs crawl, an X profile
lookup) are each a REAL paid/rate-limited call (Tavily/TwitterAPI.io/GitHub
API) whose underlying facts barely change over days -- re-scanning the same
project on every `/vc`/heartbeat pass is pure waste. This cache survives
restarts (SQLite, same `aria.db` every other persisted module already uses)
and uses TTLs measured in DAYS, not seconds.

Freshness policy (3 states, operator's own design, 23/07):
  1. Never scanned (no row) -> caller does a fresh scan.
  2. Scanned less than the TTL ago -> served straight from this cache
     (near-instant, zero external call).
  3. Scanned more than the TTL ago -> caller re-scans (exactly like case 1)
     to refresh the row AND deliver the fresh report in real time to
     whichever caller is waiting on it -- never a silent background update.
Cases 1 and 3 are indistinguishable from THIS module's point of view (both
are simply "no fresh cache hit" -- ``get_cached`` returns ``None`` for both);
the caller does the real scan and calls ``store`` either way.

Deliberately excluded from this cache (operator's explicit doctrine, distinct
from this chantier): safety_screen/security_score (a honeypot/rug can happen
in minutes -- security scans must always be fresh) and the 8h anti-front-
running delay on momentum/VC alerts (a wholly separate mechanism, see #39).

``signal_type`` is a short fixed string per caller (e.g. "github_substance"),
``target_key`` is the natural identifier being looked up (a URL, an X
handle) -- normalized (lowercased, trimmed) so two callers referring to the
same project don't create duplicate rows. The payload is any JSON-
serializable dict (callers are responsible for their own dataclass <-> dict
conversion, e.g. via ``dataclasses.asdict``) -- this module never inspects
its shape."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


def _normalize_key(target_key: str) -> str:
    return (target_key or "").strip().lower()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS external_signal_cache (
                signal_type TEXT NOT NULL,
                target_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (signal_type, target_key)
            )
            """
        )
        await db.commit()


async def get_cached(signal_type: str, target_key: str, *, ttl_days: float) -> dict | None:
    """The cached payload if a row exists AND it's younger than ``ttl_days``,
    otherwise ``None`` (covers both "never scanned" and "expired" -- the
    caller treats both the same way: do a fresh scan). Malformed JSON (should
    never happen, defense in depth) degrades to ``None``, never raises."""
    await _ensure_table()
    key = _normalize_key(target_key)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT payload_json, cached_at FROM external_signal_cache "
            "WHERE signal_type = ? AND target_key = ?",
            (signal_type, key),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    payload_json, cached_at_str = row
    try:
        cached_at = datetime.fromisoformat(cached_at_str)
    except (TypeError, ValueError):
        return None
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - cached_at > timedelta(days=ttl_days):
        return None
    try:
        return json.loads(payload_json)
    except (TypeError, ValueError) as exc:  # noqa: BLE001 -- never blocking
        logger.info("external_signal_cache: malformed payload for %s/%s (%s)", signal_type, key, exc)
        return None


async def store(signal_type: str, target_key: str, payload: dict) -> None:
    """Upserts the payload with the current timestamp. Never stores an
    unavailable/failed result (callers only call this on a real, usable
    fact-gathering success) -- a transient network failure must never freeze
    a "no signal" result in the cache for days."""
    await _ensure_table()
    key = _normalize_key(target_key)
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO external_signal_cache (signal_type, target_key, payload_json, cached_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (signal_type, target_key) DO UPDATE SET payload_json = excluded.payload_json, "
            "cached_at = excluded.cached_at",
            (signal_type, key, json.dumps(payload), now),
        )
        await db.commit()
