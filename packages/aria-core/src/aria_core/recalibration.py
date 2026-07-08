"""Requêtes de recalibrage — ARIA escalade quand elle NE PEUT PAS juger en confiance.

Principe opérateur (extension du dôme) : **transparence totale exigée**. Pas de
transparence, pas de confiance. MAIS plutôt que rejeter un token prometteur dans le
noir (faux négatif définitif), ARIA **remonte une requête** à l'opérateur pour
recalibrer l'analyse : « ce token m'intéresse mais il me manque X pour trancher —
comment veux-tu que je le juge ? »

Déclenchement : un token **prometteur** (liquidité/activité réelles) mais **opaque**
(contrat non vérifié, autorité du mint indéterminable, distribution inconnue, LP-lock
non confirmable...). Les scams évidents ne remontent PAS (bruit) — seulement les cas
où l'opacité EMPÊCHE un bon jugement.

Stockage local SQLite `aria.db`, table `recalibration_request` (clé = contrat).
Aucune action financière : c'est une file d'attente de questions pour l'humain.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


# Dimensions de transparence attendues. Une donnée « inconnue » (indisponible), pas
# « mauvaise », rend le token INJUGEABLE en confiance -> candidat au recalibrage.
@dataclass(frozen=True)
class TransparencyVerdict:
    """Le token est-il assez transparent pour être jugé, et sinon que manque-t-il ?"""

    transparent: bool
    missing: list[str] = field(default_factory=list)


def assess_transparency(ctx) -> TransparencyVerdict:
    """Évalue si les faits nécessaires à un jugement fiable sont TOUS accessibles.

    Pur, déterministe. Ne juge PAS la qualité (bon/mauvais) — seulement si l'info
    EXISTE. Un point manquant = opacité = injugeable en confiance.
    """
    missing: list[str] = []
    if ctx.contract_verified is not True:
        missing.append("contrat non vérifié (code source inaccessible)")
    # Si un mint externe existe mais que son autorité n'a pas pu être résolue.
    if ctx.has_mint is True and (ctx.mint_authority in (None, "unknown")):
        missing.append("autorité du mint indéterminable (renoncé/launchpad/dev ?)")
    if ctx.top_holder_pct is None:
        missing.append("distribution des holders inconnue")
    return TransparencyVerdict(transparent=not missing, missing=missing)


def is_promising(ctx, *, min_liquidity_usd: float = 10_000.0) -> bool:
    """Le token vaut-il un regard humain ? (activité réelle, pas de la poussière).

    On n'escalade que des tokens avec une vraie paire et une liquidité non triviale :
    inutile de déranger l'opérateur pour un pool mort ou un scam évident.
    """
    if ctx.best_pair is None:
        return False
    return (ctx.best_pair.liquidity_usd or 0.0) >= min_liquidity_usd


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS recalibration_request (
                contract TEXT PRIMARY KEY,
                symbol TEXT,
                reason TEXT,
                missing TEXT,
                promising_signals TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution TEXT
            )
            """
        )
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def request_recalibration(
    contract: str,
    *,
    symbol: str = "",
    reason: str = "",
    missing: list[str] | None = None,
    promising_signals: list[str] | None = None,
) -> bool:
    """Enregistre (ou rafraîchit) une requête de recalibrage 'pending'. Idempotent.

    Retourne True si c'est une NOUVELLE requête (utile pour notifier l'opérateur une
    seule fois), False si elle existait déjà en attente.
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT status FROM recalibration_request WHERE contract = ?", (contract,)
        )
        row = await cur.fetchone()
        is_new = row is None or row[0] != "pending"
        await db.execute(
            """
            INSERT INTO recalibration_request
                (contract, symbol, reason, missing, promising_signals, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(contract) DO UPDATE SET
                symbol=excluded.symbol, reason=excluded.reason, missing=excluded.missing,
                promising_signals=excluded.promising_signals, status='pending',
                created_at=excluded.created_at, resolved_at=NULL, resolution=NULL
            """,
            (
                contract,
                symbol,
                reason,
                json.dumps(missing or [], ensure_ascii=False),
                json.dumps(promising_signals or [], ensure_ascii=False),
                _now(),
            ),
        )
        await db.commit()
    return is_new


async def maybe_escalate(ctx, *, symbol: str = "") -> bool:
    """Décide et enregistre : escalade si le token est PROMETTEUR mais OPAQUE.

    Retourne True si une nouvelle requête de recalibrage a été créée. Ne fait rien
    (False) si le token est transparent, ou pas assez prometteur pour déranger.
    """
    if not is_promising(ctx):
        return False
    verdict = assess_transparency(ctx)
    if verdict.transparent:
        return False
    liq = ctx.best_pair.liquidity_usd if ctx.best_pair else 0.0
    signals = [f"liquidité ${liq:,.0f}", f"score {ctx.security_score}"]
    return await request_recalibration(
        ctx.contract,
        symbol=symbol,
        reason="prometteur mais opaque : transparence insuffisante pour juger",
        missing=verdict.missing,
        promising_signals=signals,
    )


async def list_pending(limit: int = 20) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM recalibration_request WHERE status = 'pending' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["missing"] = json.loads(d.get("missing") or "[]")
        d["promising_signals"] = json.loads(d.get("promising_signals") or "[]")
        out.append(d)
    return out


async def count_pending() -> int:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM recalibration_request WHERE status = 'pending'"
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def resolve_request(contract: str, resolution: str = "") -> None:
    """Marque une requête comme traitée (l'opérateur a recalibré / tranché)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE recalibration_request SET status='resolved', resolved_at=?, "
            "resolution=? WHERE contract=?",
            (_now(), resolution, contract),
        )
        await db.commit()
