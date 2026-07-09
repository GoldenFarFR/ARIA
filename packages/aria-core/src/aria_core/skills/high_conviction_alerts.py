"""Alertes proactives haute-conviction — au lieu que l'opérateur pense à taper
`/watchlist`, ARIA pousse elle-même un signal Telegram quand le pool screené fait
remonter un candidat qui franchit une barre de conviction claire (`candidate_ranking`,
score composite transparent déjà existant -- rien de dupliqué ici).

Ce n'est PAS un ordre d'achat : un signal de tri qui pointe vers `/vc <contrat>` pour
l'analyse complète, exactement la même doctrine que `candidate_ranking`/`/watchlist`.

Un contrat n'est alerté qu'UNE seule fois (mémorisé localement) -- jamais de spam sur le
même candidat même s'il reste en tête du classement d'un cycle à l'autre. Gaté OFF par
défaut (`ARIA_HIGH_CONVICTION_ALERTS_ENABLED`), respecte le kill-switch existant.
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
    """Un tour : repère le meilleur nouveau candidat haute-conviction du pool (s'il y en
    a un), alerte UNE fois, jamais plus pour ce contrat. Fail-closed à chaque étage.

    ``candidates`` injectable (tests hors-ligne, déjà classés) ; défaut :
    ``candidate_ranking.top_candidates(20)`` sur le pool réel."""
    if not high_conviction_alerts_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    if candidates is None:
        from aria_core.skills.candidate_ranking import top_candidates

        try:
            candidates = await top_candidates(20)
        except Exception as exc:  # noqa: BLE001 -- une panne de scan ne casse jamais le heartbeat
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
            except Exception as exc:  # noqa: BLE001 -- un envoi rate ne doit jamais bloquer le marquage
                return {"outcome": "notify_failed", "error": str(exc)[:300], "contract": candidate.contract}

        await _mark_alerted(candidate.contract, candidate.rank_score)
        return {"outcome": "ok", "contract": candidate.contract, "rank_score": candidate.rank_score}

    return {"outcome": "nothing_new"}
