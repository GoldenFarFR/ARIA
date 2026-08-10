"""Tracks every real case where ALL crawl layers of
``website_substance._default_crawl`` fail together on the same URL --
built 10/08, explicit operator request ("construis le compteur et prepare
le terrain pour de futur candidat") after asking whether the current
3-layer chain (scraper maison / Firecrawl / Tavily) needs a 4th fallback.

Deliberate answer to that question: NOT YET, at the real measured volume
(~1-3 web-linked candidates/day) a 3-layer chain already gives strong
redundancy (a site has to defeat all three to actually fail) -- adding
more providers speculatively, with no evidence any real site needs it,
would be manufactured busywork. This module is the evidence-gathering
step instead: log every real total failure, so a future decision to add a
4th provider is made from real data, never a guess.

Append-only, session-facing (no Telegram -- a crawl-provider gap is not an
urgent alert, same doctrine as the other low-stakes watchdog logs in this
codebase)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS website_crawl_failure_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                layer_errors TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_all_layers_failed(url: str, layer_errors: dict[str, str]) -> None:
    """Called once every registered crawl layer has been tried and none
    returned ``available=True`` -- ``layer_errors`` maps each layer's name
    (e.g. "scraper_maison"/"firecrawl"/"tavily") to its own error string,
    so a future review can tell WHICH layers actually failed, not just
    that the crawl as a whole did."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO website_crawl_failure_log (url, layer_errors, occurred_at) VALUES (?, ?, ?)",
            (url, json.dumps(layer_errors), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    logger.info("website_crawl_failure_log: all layers failed for %s (%s)", url, layer_errors)


async def failure_count_since(days: int) -> int:
    """How many total-failure events were recorded in the last ``days``
    days -- the real signal to consult before deciding a 4th crawl
    provider is actually worth building."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM website_crawl_failure_log WHERE occurred_at >= ?", (cutoff,)
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def recent_failures(limit: int = 20) -> list[dict]:
    """Most recent total-failure events, newest first -- ``layer_errors``
    already parsed back into a dict for direct inspection."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT url, layer_errors, occurred_at FROM website_crawl_failure_log "
                "ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [
        {"url": r[0], "layer_errors": json.loads(r[1]), "occurred_at": r[2]}
        for r in rows
    ]
