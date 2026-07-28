"""Append-only history of every DEX composite score computed
(``dex_composite_score.py``), one row per scan -- same pattern as
``wallet_score_log`` (``services/smart_money.py``), never
``thesis_checkpoint`` (verified 28/07: that table is scoped exclusively to
ALREADY-OPEN VC positions via ``weekly_training.review_open_theses``, with a
qualitative ``verdict`` text field, not a numeric score -- a structurally
different use case).

Purpose: the composite's weights/thresholds are a first pass, not yet
calibrated against real outcomes (same caveat already documented on
``bonding_entry.py``'s own composite, whose holder-concentration floor had to
be recalibrated after observing ~380 real candidates). Without a timestamped
record per candidate -- including candidates never bought -- there is no way
to later check whether the score actually correlates with what happened.
Pure write/read, no scoring logic depends on this module.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dex_score_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                scored_at TEXT NOT NULL,
                score_json TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_dex_score(contract: str, score_json: str) -> None:
    """Pure write, append-only -- one row per scan, never overwritten."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO dex_score_log (contract, scored_at, score_json) VALUES (?, ?, ?)",
            (contract.lower(), datetime.now(timezone.utc).isoformat(), score_json),
        )
        await db.commit()


async def list_dex_scores(contract: str, limit: int = 50) -> list[dict]:
    """Most recent scans first for a given contract -- empty list if never scanned."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM dex_score_log WHERE LOWER(contract) = LOWER(?) "
            "ORDER BY id DESC LIMIT ?",
            (contract, limit),
        )
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["score"] = json.loads(d.get("score_json") or "{}")
        except (TypeError, ValueError):
            d["score"] = {}
        out.append(d)
    return out
