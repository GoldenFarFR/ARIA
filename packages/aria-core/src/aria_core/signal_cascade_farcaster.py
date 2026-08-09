"""Multi-source signal cascade -- Farcaster column, stages 1+2. Second
column built (operator build order: GitHub/Farcaster free -> web
budget-bounded -> X pay-per-use, cf. docs/HANDOFF_PIPELINE_MOMENTUM.md's
"multi-source signal cascade" entry), same structural pattern as
``signal_cascade_github.py`` -- read that module's docstring for the shared
doctrine (COLLECT decoupled from the technical filter, best-effort, never a
trigger).

Unlike GitHub, no ready-made "substance" judge existed for Farcaster before
this module -- ``services/farcaster.verify_profile`` only ever checked
profile EXISTENCE/legitimacy (follower count, Warpcast's own spam label),
used today exclusively post-BUY via ``conviction_research.py``. This module
adds the judgment layer: a profile is 'positive' if it exists, carries no
spam label, and clears a minimum follower floor (``MIN_FOLLOWERS`` --
initial, uncalibrated on real data, same "periskable threshold" caveat as
every first-pass constant in this codebase). Acceleration reuses the exact
same definition as the GitHub column (previous signal weak/unknown/none ->
now positive) -- a follower-GROWTH-based acceleration (this profile's
audience just spiked) was considered but deliberately deferred: it would
need a stored follower-count history this first pass doesn't yet build,
and the simpler definition already answers the operator's core question
(a profile that just became credible).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

REEVALUATION_TTL_DAYS = 1.0

# Legitimacy floor for a 'positive' signal -- deliberately modest for a
# first pass (never calibrated against a real distribution of project
# Farcaster accounts). Revisit once enough watchlist history accumulates.
MIN_FOLLOWERS = 100

_WATCHLIST_DDL = """
CREATE TABLE IF NOT EXISTS farcaster_signal_cascade_watchlist (
    profile_url TEXT PRIMARY KEY,
    contract TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT 'base',
    symbol TEXT,
    first_seen_at TEXT NOT NULL,
    last_evaluated_at TEXT,
    last_score REAL,
    last_signal TEXT,
    previous_signal TEXT,
    accelerating INTEGER NOT NULL DEFAULT 0,
    last_follower_count INTEGER
)
"""

_table_ready = False


async def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_WATCHLIST_DDL)
        await db.commit()
    _table_ready = True


def _find_farcaster_link(project_links: list[dict] | None) -> str | None:
    for link in project_links or []:
        if isinstance(link, dict) and link.get("label") == "Farcaster" and link.get("url"):
            return link["url"]
    return None


async def enqueue_candidate(contract: str, chain: str, project_links: list[dict] | None, *, symbol: str | None = None) -> None:
    """Stage 1 COLLECT -- same doctrine as ``signal_cascade_github.
    enqueue_candidate``: called for every candidate whose best pair just
    resolved, before any liquidity/technical/security filter. Zero network
    cost, best-effort, never raises."""
    try:
        profile_url = _find_farcaster_link(project_links)
        if not profile_url:
            return
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO farcaster_signal_cascade_watchlist "
                "(profile_url, contract, chain, symbol, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                (profile_url, contract, chain, symbol, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- stage 1, never blocking the caller
        logger.info("signal_cascade_farcaster: enqueue failed for %s (%s)", contract[:10], exc)


def _judge(verification, *, min_followers: int = MIN_FOLLOWERS) -> tuple[str, float | None, str]:
    """Pure, deterministic judgment -- same doctrine as
    ``github_substance.judge_github_substance``: never invents a value,
    'unknown' when the sample can't honestly support a verdict."""
    if not verification.available:
        return "unknown", None, verification.error or "profil Farcaster non vérifiable"
    if verification.exists is False:
        return "unknown", None, "lien Farcaster mort ou jamais publié"
    followers = verification.follower_count or 0
    if verification.spam_label:
        return "weak", 0.0, f"labellisé spam par Warpcast ({verification.spam_label})"
    if followers >= min_followers:
        score = min(100.0, 100.0 * followers / (min_followers * 5))
        return "positive", round(score, 1), f"{followers} abonnés, pas de label spam"
    return "neutral", round(100.0 * followers / min_followers, 1), f"{followers} abonnés (< {min_followers}, sous le seuil de légitimité)"


async def _pick_next_due(db: aiosqlite.Connection):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REEVALUATION_TTL_DAYS)).isoformat()
    cursor = await db.execute(
        "SELECT profile_url, contract, chain, symbol, last_signal FROM farcaster_signal_cascade_watchlist "
        "WHERE last_evaluated_at IS NULL OR last_evaluated_at < ? "
        "ORDER BY last_evaluated_at IS NOT NULL, last_evaluated_at ASC LIMIT 1",
        (cutoff,),
    )
    return await cursor.fetchone()


async def run_refresh_cycle() -> dict:
    """Stage 2 QUANTITATIVE FILTER, one profile per call -- same throttling
    doctrine as the GitHub column (a shared heartbeat cadence, no internal
    rate limiter needed for a single free API call per pass). Best-effort:
    never raises."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await _pick_next_due(db)
        if row is None:
            return {"evaluated": None}
        profile_url, contract, chain, symbol, previous_signal = row

        from aria_core.services.farcaster import verify_profile

        verification = await verify_profile(profile_url)
        signal, score, detail = _judge(verification)
        accelerating = previous_signal in (None, "weak", "unknown") and signal == "positive"

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE farcaster_signal_cascade_watchlist SET last_evaluated_at = ?, last_score = ?, "
                "previous_signal = last_signal, last_signal = ?, accelerating = ?, last_follower_count = ? "
                "WHERE profile_url = ?",
                (
                    datetime.now(timezone.utc).isoformat(), score, signal, int(accelerating),
                    verification.follower_count, profile_url,
                ),
            )
            await db.commit()

        from aria_core import signal_cascade_convergence

        await signal_cascade_convergence.record_source_signal(
            contract, chain, "farcaster", signal,
            accelerating=accelerating, detail=f"{profile_url} -- {detail}", symbol=symbol,
        )

        if accelerating:
            logger.info(
                "signal_cascade_farcaster: %s (%s) accelerating -- %s -> positive",
                symbol or contract[:10], profile_url, previous_signal,
            )
        return {
            "evaluated": profile_url, "contract": contract, "chain": chain,
            "signal": signal, "score": score, "accelerating": accelerating,
        }
    except Exception as exc:  # noqa: BLE001 -- shadow-style stage, never blocking
        logger.info("signal_cascade_farcaster: refresh cycle failed (%s)", exc)
        return {"evaluated": None, "error": str(exc)}


async def list_stage2_positive() -> list[dict]:
    """What stage 2 lets through. Every 'positive' result is also pushed to
    stage 3 (``signal_cascade_convergence.record_source_signal``, called
    from ``run_refresh_cycle`` above)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT profile_url, contract, chain, symbol, last_score, accelerating, last_evaluated_at "
            "FROM farcaster_signal_cascade_watchlist WHERE last_signal = 'positive' "
            "ORDER BY accelerating DESC, last_evaluated_at DESC"
        )
        rows = await cursor.fetchall()
    return [
        {
            "profile_url": r[0], "contract": r[1], "chain": r[2], "symbol": r[3],
            "score": r[4], "accelerating": bool(r[5]), "last_evaluated_at": r[6],
        }
        for r in rows
    ]
