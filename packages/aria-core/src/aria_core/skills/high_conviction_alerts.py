"""Proactive high-conviction alerts — instead of relying on the operator to think
to type `/watchlist`, ARIA herself pushes a Telegram signal when the screened pool
surfaces a candidate that crosses a clear conviction bar (`candidate_ranking`,
already-existing transparent composite score -- nothing duplicated here).

This is NOT a buy order: a sorting signal that points to `/vc <contract>` for
the full analysis, exactly the same doctrine as `candidate_ranking`/`/watchlist`.

A contract is only alerted ONCE (remembered locally) -- never spam on the
same candidate even if it stays at the top of the ranking from one cycle to the
next. Gated OFF by default (`ARIA_HIGH_CONVICTION_ALERTS_ENABLED`), respects the
existing kill-switch.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

MIN_RANK_SCORE = 80.0
REQUIRED_VERDICT = "SAFE"


def high_conviction_alerts_enabled() -> bool:
    return os.environ.get("ARIA_HIGH_CONVICTION_ALERTS_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS high_conviction_alert_log ("
            "contract TEXT PRIMARY KEY, alerted_at TEXT NOT NULL, rank_score REAL NOT NULL)"
        )
        await db.commit()


async def _already_alerted(contract: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_table()
        cursor = await db.execute(
            "SELECT 1 FROM high_conviction_alert_log WHERE contract = ?", (contract,)
        )
        row = await cursor.fetchone()
    return row is not None


async def _mark_alerted(contract: str, rank_score: float) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure_table()
        await db.execute(
            "INSERT OR IGNORE INTO high_conviction_alert_log (contract, alerted_at, rank_score) "
            "VALUES (?, ?, ?)",
            (contract, _now(), rank_score),
        )
        await db.commit()


def _is_high_conviction(candidate) -> bool:
    return candidate.verdict == REQUIRED_VERDICT and candidate.rank_score >= MIN_RANK_SCORE


def format_alert(candidate) -> str:
    label = candidate.symbol or candidate.contract[:10]
    holder = f"{candidate.top_holder_pct:.1f}%" if candidate.top_holder_pct is not None else "indisponible"
    return (
        "Alerte haute conviction — pool screené\n\n"
        f"{label} · score {candidate.rank_score:.0f}/100 · {candidate.verdict}\n"
        f"Liquidité : {candidate.liquidity_usd:,.0f} $ · Détention top holder : {holder}\n"
        f"Contrat : {candidate.contract}\n\n"
        "Signal de tri automatique, pas un ordre d'achat — envoie /vc <contrat> pour "
        "l'analyse complète avant toute décision."
    )


async def run_high_conviction_alert_cycle(*, candidates=None, notifier=None) -> dict:
    """One pass: spots the best new high-conviction candidate in the pool (if there
    is one), alerts ONCE, never again for that contract. Fail-closed at every stage.

    ``candidates`` injectable (offline tests, already ranked); default:
    ``candidate_ranking.top_candidates(20)`` against the real pool."""
    if not high_conviction_alerts_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    if candidates is None:
        from aria_core.skills.candidate_ranking import top_candidates

        try:
            candidates = await top_candidates(20)
        except Exception as exc:  # noqa: BLE001 -- a scan failure must never break the heartbeat
            return {"outcome": "error", "error": str(exc)[:300]}

    for candidate in candidates:
        if not _is_high_conviction(candidate):
            continue
        if await _already_alerted(candidate.contract):
            continue

        message = format_alert(candidate)
        if notifier:
            try:
                await notifier(message)
            except Exception as exc:  # noqa: BLE001 -- a failed send must never block marking it
                return {"outcome": "notify_failed", "error": str(exc)[:300], "contract": candidate.contract}

        await _mark_alerted(candidate.contract, candidate.rank_score)
        return {"outcome": "ok", "contract": candidate.contract, "rank_score": candidate.rank_score}

    return {"outcome": "nothing_new"}
