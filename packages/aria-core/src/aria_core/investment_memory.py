"""Boucle mémoire d'investissement — thèse → décision → résultat/P&L → leçon.

Journal de raisonnement d'ARIA sur ses paris, entièrement local (SQLite
``aria.db``, table ``investment_thesis``). Aucune action financière, aucune
signature, aucun appel réseau : c'est une trace pour attribuer un résultat à
chaque décision et en tirer une leçon — le prérequis d'un scoring VC qui
apprend de ses erreurs (cf. AGENTS.md, règle « auto-critique honnête »).

Cycle de vie d'une ligne :
- ``open`` à l'enregistrement de la thèse (``record_thesis``) ;
- transition unique ``open -> closed`` via ``close_thesis`` (atomique — un
  résultat n'est attribué qu'une fois, on ne réécrit jamais l'historique).

La table est créée paresseusement via ``CREATE TABLE IF NOT EXISTS`` — ajout
pur, aucune altération de schéma existant, donc pas de migration Alembic ; le
backup de ``/opt/aria-data`` reste couvert par la procédure de déploiement
(``docs/deploy-ionos.md``).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Décisions d'investissement autorisées (pur journal — jamais un ordre d'exécution).
VALID_DECISIONS = ("BUY", "WATCH", "SELL", "AVOID")

_COLUMNS = [
    "id",
    "token_address",
    "token_symbol",
    "thesis",
    "decision",
    "score_snapshot",
    "created_at",
    "status",
    "outcome",
    "lesson",
    "closed_at",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS investment_thesis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                token_symbol TEXT,
                thesis TEXT NOT NULL,
                decision TEXT NOT NULL,
                score_snapshot TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                outcome TEXT,
                lesson TEXT,
                closed_at TEXT
            )
            """
        )
        await db.commit()


async def record_thesis(
    *,
    token_address: str,
    thesis: str,
    decision: str,
    token_symbol: str | None = None,
    score_snapshot: str = "{}",
) -> int:
    """Enregistre une thèse ``open`` et retourne son id.

    ``decision`` doit appartenir à ``VALID_DECISIONS`` (validation en amont côté
    appelant recommandée — ``ValueError`` sinon).
    """
    decision = decision.upper()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"décision invalide : {decision!r} (attendu : {', '.join(VALID_DECISIONS)})")

    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO investment_thesis
            (token_address, token_symbol, thesis, decision, score_snapshot, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'open')
            """,
            (token_address, token_symbol, thesis, decision, score_snapshot, now),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def close_thesis(thesis_id: int, *, outcome: str, lesson: str) -> dict | None:
    """Transition atomique ``open -> closed`` (attribue résultat + leçon).

    Retourne la ligne close si la transition a eu lieu, sinon ``None`` (id
    inconnu ou déjà close — on ne réécrit jamais une issue déjà attribuée).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE investment_thesis
            SET status = 'closed', outcome = ?, lesson = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (outcome, lesson, now, thesis_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row_cursor = await db.execute("SELECT * FROM investment_thesis WHERE id = ?", (thesis_id,))
        row = await row_cursor.fetchone()
    if not row:
        return None
    return dict(zip(_COLUMNS, row))


async def get_thesis(thesis_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row_cursor = await db.execute("SELECT * FROM investment_thesis WHERE id = ?", (thesis_id,))
        row = await row_cursor.fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def list_open_theses(limit: int = 20) -> list[dict]:
    """Thèses encore ouvertes (résultat non attribué), les plus récentes d'abord."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row_cursor = await db.execute(
            "SELECT * FROM investment_thesis WHERE status = 'open' ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await row_cursor.fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]
