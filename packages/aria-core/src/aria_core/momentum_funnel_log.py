"""Persistance cumulée du funnel de rejet momentum (19/07) -- réponse directe à la
proposition d'ARIA elle-même en conversation Telegram : "on log pendant 48h le
compteur par étape -- combien rejetés au honeypot, combien au R/R, combien à la
liquidité. Là on saura si c'est le marché ou si mon seuil R/R est calibré trop
serré. Preuve avant opinion."

Le funnel lui-même (comptage par ``hold_reason``) existe déjà et n'est PAS dupliqué
ici -- ``paper_trader.run_paper_cycle`` le calcule à chaque cycle (mandat #192,
16/07) mais ne fait QUE le logger (``logger.info``) puis le perd : aucun autre
appelant ne lit ``actions["momentum_funnel"]`` (vérifié par grep avant d'écrire ce
module). Un cycle individuel (5-20 candidats) n'est de toute façon pas un
échantillon assez grand pour juger "trop strict vs marché plat" -- c'est le CUMUL
dans le temps qui rend le signal exploitable. Ce module ajoute uniquement la
persistance ; aucun changement de la logique de décision (``momentum_entry.py``)
ou du calcul du funnel lui-même.

Append-only en pratique (même doctrine que ``momentum_blacklist.py``/
``agent_wallet_log.py``) : chaque cycle ajoute une ligne par ``reason_code``,
jamais de UPDATE/DELETE. La lecture agrège par fenêtre de temps glissante."""
from __future__ import annotations

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
            CREATE TABLE IF NOT EXISTS momentum_funnel_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                count INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_momentum_funnel_log_recorded_at "
            "ON momentum_funnel_log (recorded_at)"
        )
        await db.commit()


async def record_funnel(funnel: dict[str, int]) -> None:
    """Persiste UN cycle de funnel (appelé depuis ``paper_trader.run_paper_cycle``,
    juste après le calcul déjà existant). Ne fait rien si le funnel est vide (aucun
    candidat rejeté ce cycle -- rien à enregistrer, pas une anomalie)."""
    if not funnel:
        return
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO momentum_funnel_log (recorded_at, reason_code, count) VALUES (?, ?, ?)",
            [(now, str(reason), int(count)) for reason, count in funnel.items()],
        )
        await db.commit()


async def summarize_since(hours: float = 48.0) -> dict[str, int]:
    """Agrège toutes les entrées depuis ``hours`` heures -- ``{reason_code: total}``,
    trié par nul ici (le tri d'affichage se fait dans ``format_funnel_summary``,
    cette fonction reste une lecture brute réutilisable)."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT reason_code, SUM(count) FROM momentum_funnel_log "
            "WHERE recorded_at >= ? GROUP BY reason_code",
            (cutoff,),
        )
        rows = await cursor.fetchall()
    return {reason: int(total) for reason, total in rows}


def format_funnel_summary(summary: dict[str, int], *, hours: float = 48.0) -> str:
    """Rendu Telegram -- classé par fréquence décroissante (la cause dominante de
    rejet en premier, c'est le signal recherché : marché plat vs filtre trop strict)."""
    header = f"📊 Funnel de rejet momentum -- {hours:.0f}h glissantes"
    if not summary:
        return f"{header}\n\nAucun rejet enregistré sur cette période."

    total = sum(summary.values())
    ranked = sorted(summary.items(), key=lambda kv: kv[1], reverse=True)
    lines = [header, f"Total : {total} candidats rejetés/HOLD", ""]
    for reason, count in ranked:
        pct = (count / total * 100.0) if total else 0.0
        lines.append(f"- {reason} : {count} ({pct:.0f}%)")
    return "\n".join(lines)
