"""Journal des prédictions VC — mesure la PERTINENCE d'ARIA dans le temps.

Chaque analyse `/vc` est enregistrée comme une **prédiction datée** (même sans
trade réel : « shadow »). Plus tard, l'opérateur attribue un résultat réel
(P&L %) via `/vcresult`. On peut alors mesurer si ARIA est réellement pertinente :

- **hit-rate** : part des recommandations BUY dont le résultat est positif ;
- **P&L moyen** par recommandation ;
- **calibration** : est-ce qu'un « Potentiel 8/10 » surperforme réellement un
  « 5/10 » ? (le vrai test d'un analyste).

Logger *toutes* les analyses (pas seulement les trades) accélère l'accumulation
d'un échantillon statistiquement exploitable — à ~2 trades/mois, se limiter aux
positions réelles serait bien trop lent.

Stockage local SQLite `aria.db`, table `vc_prediction` (ajout pur,
`CREATE TABLE IF NOT EXISTS`). Aucune action financière : c'est un journal.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "id",
    "contract",
    "recommandation",
    "potentiel",
    "risque",
    "taille_pct",
    "security_score",
    "llm_used",
    "report_ref",
    "traded",
    "status",
    "outcome_pct",
    "outcome_note",
    "created_at",
    "closed_at",
]

# Buckets de potentiel pour la courbe de calibration.
_CALIB_BUCKETS = [(0, 3, "0-3"), (4, 6, "4-6"), (7, 8, "7-8"), (9, 10, "9-10")]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vc_prediction (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                recommandation TEXT NOT NULL,
                potentiel INTEGER,
                risque TEXT,
                taille_pct REAL DEFAULT 0,
                security_score INTEGER,
                llm_used INTEGER DEFAULT 0,
                report_ref TEXT,
                traded INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                outcome_pct REAL,
                outcome_note TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT
            )
            """
        )
        await db.commit()


async def record_prediction(
    *,
    contract: str,
    recommandation: str,
    potentiel: int | None,
    risque: str,
    taille_pct: float,
    security_score: int,
    llm_used: bool,
    report_ref: str = "",
    traded: bool = False,
) -> int:
    """Enregistre une prédiction VC ``open`` et retourne son id."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO vc_prediction
            (contract, recommandation, potentiel, risque, taille_pct, security_score,
             llm_used, report_ref, traded, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                contract,
                recommandation,
                potentiel,
                risque,
                taille_pct,
                security_score,
                1 if llm_used else 0,
                report_ref,
                1 if traded else 0,
                now,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def close_prediction(prediction_id: int, *, outcome_pct: float, note: str = "") -> dict | None:
    """Attribue un résultat réel (P&L %). Transition atomique ``open -> closed``.

    Retourne la ligne close, ou ``None`` si id inconnu / déjà clôturé (on ne
    réécrit jamais un résultat déjà attribué).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE vc_prediction
            SET status = 'closed', outcome_pct = ?, outcome_note = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (outcome_pct, note, now, prediction_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row = await (await db.execute("SELECT * FROM vc_prediction WHERE id = ?", (prediction_id,))).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def get_prediction(prediction_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT * FROM vc_prediction WHERE id = ?", (prediction_id,))).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def list_open_predictions(limit: int = 20) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM vc_prediction WHERE status = 'open' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def compute_metrics(predictions: list[dict]) -> dict:
    """Métriques de pertinence à partir d'une liste de prédictions (fonction pure, testable).

    Ne considère que les prédictions clôturées (résultat réel attribué) pour les
    taux ; compte les ouvertes séparément.
    """
    closed = [p for p in predictions if p.get("status") == "closed" and p.get("outcome_pct") is not None]
    open_count = sum(1 for p in predictions if p.get("status") == "open")

    buys = [p for p in closed if p.get("recommandation") == "BUY"]
    wins = [p for p in buys if (p.get("outcome_pct") or 0) > 0]
    hit_rate = (len(wins) / len(buys)) if buys else None
    avg_pnl_buy = (sum(p["outcome_pct"] for p in buys) / len(buys)) if buys else None

    # Calibration : P&L moyen par bucket de potentiel (uniquement analyses LLM notées).
    calibration = []
    scored = [p for p in closed if p.get("potentiel") is not None]
    for low, high, label in _CALIB_BUCKETS:
        bucket = [p for p in scored if low <= p["potentiel"] <= high]
        if bucket:
            avg = sum(p["outcome_pct"] for p in bucket) / len(bucket)
            calibration.append({"bucket": label, "count": len(bucket), "avg_pnl": avg})

    return {
        "total": len(predictions),
        "closed": len(closed),
        "open": open_count,
        "buy_count": len(buys),
        "hit_rate": hit_rate,
        "avg_pnl_buy": avg_pnl_buy,
        "calibration": calibration,
    }


async def metrics() -> dict:
    """Charge toutes les prédictions et calcule les métriques de pertinence."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("SELECT * FROM vc_prediction")).fetchall()
    return compute_metrics([dict(zip(_COLUMNS, row)) for row in rows])
