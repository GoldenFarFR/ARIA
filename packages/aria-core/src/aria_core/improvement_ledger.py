"""Carnet d'auto-amélioration d'ARIA — la mémoire de ses upgrades possibles.

Quand ARIA repère un outil, une source de données, un produit ou une idée qui
pourrait la rendre meilleure, elle le **consigne ici** plutôt que de l'oublier.
Chaque candidat suit un cycle de vie honnête :

    proposed  ->  testing  ->  grafted   (greffé : a PROUVÉ qu'il améliore la calibration)
                           ->  rejected  (testé, n'apporte rien / échoue le dôme)

Principes (dôme) :
- **La greffe passe TOUJOURS par une PR validée par un humain** (via la file de
  tâches worker) — jamais d'auto-fusion de code dans le cœur. ARIA découvre,
  propose, teste ; l'humain valide le merge.
- **Preuve avant greffe** : un candidat ne passe `grafted` que s'il améliore la
  calibration MESURÉE sur le track-record (`evidence` documente le gain). « Ça a
  l'air bien » ne suffit pas.
- Un candidat externe (outil/produit tiers) doit passer le dôme (sanitisation,
  aucune exécution de code non fiable) avant tout test réel.

Stockage local SQLite `aria.db`, table `improvement_candidate` (ajout pur).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_STATUSES = ("proposed", "testing", "grafted", "rejected")
_CATEGORIES = ("tool", "data_source", "product", "artifact", "idea")

_COLUMNS = [
    "id",
    "title",
    "description",
    "category",
    "source",
    "benefit",
    "seam",
    "status",
    "evidence",
    "worker_task_id",
    "created_at",
    "updated_at",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS improvement_candidate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT NOT NULL DEFAULT 'idea',
                source TEXT,
                benefit TEXT,
                seam TEXT,
                status TEXT NOT NULL DEFAULT 'proposed',
                evidence TEXT,
                worker_task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_candidate(
    *,
    title: str,
    description: str = "",
    category: str = "idea",
    source: str = "",
    benefit: str = "",
    seam: str = "",
) -> int:
    """Consigne un candidat d'amélioration (statut ``proposed``). Retourne son id.

    ``category`` ∈ {tool, data_source, product, artifact, idea} (repli 'idea').
    ``seam`` = le point d'ancrage pressenti (ex. ``include_<x>``, ``services/<nom>``)
    pour que la greffe future soit un simple branchement, pas une réécriture.
    Dédoublonnage léger : un même ``title`` déjà non-rejeté n'est pas ré-inséré.
    """
    await _ensure_table()
    cat = category if category in _CATEGORIES else "idea"
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await (
            await db.execute(
                "SELECT id FROM improvement_candidate "
                "WHERE LOWER(title) = LOWER(?) AND status != 'rejected' LIMIT 1",
                (title,),
            )
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = await db.execute(
            """
            INSERT INTO improvement_candidate
            (title, description, category, source, benefit, seam, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (title, description, cat, source, benefit, seam, now, now),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def update_candidate(
    candidate_id: int,
    *,
    status: str | None = None,
    evidence: str | None = None,
    worker_task_id: str | None = None,
) -> dict | None:
    """Met à jour un candidat (avancée dans le cycle de vie). Retourne la ligne, ou None.

    Un passage à ``grafted`` DOIT être adossé à une preuve (``evidence``) : sans elle,
    la transition est refusée (retour None) — on ne greffe jamais sur une impression.
    """
    await _ensure_table()
    fields, values = [], []
    if status is not None:
        if status not in _STATUSES:
            return None
        if status == "grafted" and not (evidence or "").strip():
            return None  # pas de greffe sans preuve
        fields.append("status = ?")
        values.append(status)
    if evidence is not None:
        fields.append("evidence = ?")
        values.append(evidence)
    if worker_task_id is not None:
        fields.append("worker_task_id = ?")
        values.append(worker_task_id)
    if not fields:
        return await get_candidate(candidate_id)
    fields.append("updated_at = ?")
    values.append(datetime.now(timezone.utc).isoformat())
    values.append(candidate_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE improvement_candidate SET {', '.join(fields)} WHERE id = ?", values
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_candidate(candidate_id)


async def get_candidate(candidate_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT * FROM improvement_candidate WHERE id = ?", (candidate_id,)
            )
        ).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def list_candidates(status: str | None = None, limit: int = 50) -> list[dict]:
    """Liste les candidats, du plus récent au plus ancien, filtrable par statut."""
    await _ensure_table()
    query = "SELECT * FROM improvement_candidate"
    params: tuple = ()
    if status is not None:
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY id DESC LIMIT ?"
    params += (limit,)
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(query, params)).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def count_by_status() -> dict:
    """Compteurs par statut (pour un tableau de bord « où en est l'auto-amélioration »)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT status, COUNT(*) FROM improvement_candidate GROUP BY status"
            )
        ).fetchall()
    counts = {s: 0 for s in _STATUSES}
    for status, n in rows:
        counts[status] = int(n)
    return counts
