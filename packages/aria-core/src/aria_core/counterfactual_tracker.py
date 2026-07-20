"""#176 (20/07), volet apprentissage b -- tracker contrefactuel des candidats REJETÉS
par un garde dur du pipeline momentum. Réponse au plan acté avec l'opérateur
("et concernant l'apprentissage ?") : reset hebdo (#173) -> sizing Formule B (#174) ->
slippage simulé (#175) -> apprentissage (régime, #176a + contrefactuel, ici).

Enregistre CHAQUE rejet DIGNE d'un contrefactuel (contrat/chaîne/raison/prix au moment
du rejet), puis un cycle heartbeat dédié (gaté OFF, ``ARIA_COUNTERFACTUAL_TRACKER_
ENABLED``) revisite après un délai fixe et enregistre l'évolution de prix -- une simple
comparaison AVANT/APRÈS, jamais une resimulation du pipeline d'entrée (les seuils
peuvent avoir changé depuis, resimuler serait trompeur ET coûterait un scan complet par
candidat). But : objectiver si les seuils durs coûtent de vrais gains manqués -- jamais
un jugement automatique, juste des chiffres bruts pour qu'une session future puisse
juger avec des faits.

Raisons DÉLIBÉRÉMENT exclues de l'enregistrement (``_EXCLUDED_REASONS``) -- aucun
contrefactuel utile : le token n'avait tout simplement aucun signal/donnée exploitable
(``no_entry_signal``/``ohlcv_unavailable``), ou le rejet est une menace CONFIRMÉE où un
gain de prix sur le papier serait trompeur (``blacklisted``/tout code ``honeypot_*`` --
on ne peut jamais vendre un vrai honeypot, peu importe ce que le prix affiché fait
ensuite). Tout AUTRE ``hold_reason`` (présent aujourd'hui ou ajouté demain par un futur
garde-fou) est inclus par défaut -- fail-open à l'inclusion, jamais l'inverse : un
enregistrement en trop est gratuit (juste une ligne SQLite), un enregistrement manqué
serait un angle mort silencieux.

L'ENREGISTREMENT lui-même n'est PAS gaté (même doctrine que ``momentum_funnel_log.py``
-- un sous-produit passif de l'évaluation déjà en cours, aucun appel réseau
supplémentaire, strictement additif). Seul le CYCLE DE REVISITE (un vrai appel réseau
par candidat dû) est gaté."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


def counterfactual_tracker_enabled() -> bool:
    """Gate additif -- ``run_revisit_cycle()`` (le seul volet qui coûte un vrai appel
    réseau par candidat dû) n'est appelé depuis le heartbeat que si ce flag est actif
    (OFF par défaut, même patron que les autres tâches heartbeat). L'ENREGISTREMENT des
    rejets (``record_rejection``, appelé depuis ``paper_trader.run_paper_cycle``) reste
    inconditionnel -- même doctrine que ``momentum_funnel_log.py``, aucun appel réseau,
    rien à gater."""
    return os.environ.get("ARIA_COUNTERFACTUAL_TRACKER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

# Délai avant qu'un rejet devienne "dû" pour une revisite -- assez long pour qu'un vrai
# mouvement de prix ait eu le temps de se former, assez court pour rester exploitable
# (pas des années de dérive de marché sans rapport avec la décision d'origine).
REVISIT_AFTER_DAYS = 7.0

_EXCLUDED_REASONS = frozenset({
    "no_entry_signal", "ohlcv_unavailable", "blacklisted",
    "honeypot_rejected", "honeypot_unavailable", "chain_not_covered",
})


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS counterfactual_rejection (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT NOT NULL DEFAULT '',
                reject_reason TEXT NOT NULL,
                price_at_rejection REAL NOT NULL,
                rejected_at TEXT NOT NULL,
                revisited_at TEXT,
                price_at_revisit REAL,
                price_change_pct REAL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_counterfactual_rejection_rejected_at "
            "ON counterfactual_rejection (rejected_at)"
        )
        await db.commit()


def is_trackable_reason(hold_reason: str | None) -> bool:
    """``True`` si ce rejet mérite un contrefactuel -- un vrai seuil discrétionnaire a
    bloqué un candidat avec un prix connu, PAS une absence de donnée/signal ni une
    menace confirmée (cf. docstring du module)."""
    return bool(hold_reason) and hold_reason not in _EXCLUDED_REASONS


async def record_rejection(
    contract: str, chain: str, symbol: str, hold_reason: str | None, price: float | None,
) -> None:
    """Enregistre un rejet -- no-op silencieux si ``hold_reason`` n'est pas trackable ou
    si ``price`` est absent/invalide (aucun point de départ pour un contrefactuel).
    Jamais une exception qui remonterait à l'appelant (``paper_trader.run_paper_cycle``)
    -- un échec d'écriture de télémétrie ne doit jamais casser un cycle de trading réel."""
    if not is_trackable_reason(hold_reason) or not price or price <= 0:
        return
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO counterfactual_rejection
                  (contract, chain, symbol, reject_reason, price_at_rejection, rejected_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (contract, chain or "base", symbol or "", hold_reason, price, _now()),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — télémétrie best-effort, jamais bloquante
        logger.info("counterfactual_tracker: échec d'enregistrement pour %s", contract, exc_info=True)


async def list_due_for_revisit(*, older_than_days: float = REVISIT_AFTER_DAYS, limit: int = 20) -> list[dict]:
    """Rejets jamais revisités, plus vieux que ``older_than_days`` -- les plus anciens
    d'abord (FIFO, jamais un ordre arbitraire qui laisserait certains candidats
    éternellement en attente si le volume dépasse ``limit`` par cycle)."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM counterfactual_rejection "
            "WHERE revisited_at IS NULL AND rejected_at <= ? "
            "ORDER BY rejected_at ASC LIMIT ?",
            (cutoff, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def record_revisit(row_id: int, price_at_revisit: float | None) -> None:
    """Enregistre le résultat d'une revisite -- ``price_at_revisit=None`` (prix
    introuvable au moment de la revisite, ex. token illiquide/rug depuis) marque quand
    même la ligne comme revisitée (jamais retentée en boucle), mais laisse ``price_
    change_pct`` à ``NULL`` -- jamais un 0% inventé qui serait indiscernable d'un vrai
    prix stable."""
    await _ensure_table()
    now = _now()
    change_pct = None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT price_at_rejection FROM counterfactual_rejection WHERE id = ?", (row_id,),
        )
        row = await cursor.fetchone()
        if row and price_at_revisit and price_at_revisit > 0 and row["price_at_rejection"]:
            change_pct = (price_at_revisit / row["price_at_rejection"] - 1.0) * 100.0
        await db.execute(
            "UPDATE counterfactual_rejection "
            "SET revisited_at = ?, price_at_revisit = ?, price_change_pct = ? WHERE id = ?",
            (now, price_at_revisit, change_pct, row_id),
        )
        await db.commit()


async def run_revisit_cycle(*, limit: int = 20) -> dict:
    """Un tour de revisite : pour chaque rejet dû, refetch le prix RÉEL actuel (même
    client que le reste du pipeline momentum, ``paper_trader._default_pair_lookup`` --
    jamais un second client dupliqué) et enregistre l'évolution. Gaté par l'appelant
    (``heartbeat.py``, ``ARIA_COUNTERFACTUAL_TRACKER_ENABLED``) -- cette fonction ne
    vérifie pas le gate elle-même, même patron que les autres cycles (``bonding_
    discovery_cycle``, etc.)."""
    from aria_core import paper_trader

    due = await list_due_for_revisit(limit=limit)
    revisited = 0
    price_unavailable = 0
    for row in due:
        price = None
        try:
            pair = await paper_trader._default_pair_lookup(row["contract"], chain=row["chain"] or "base")
            price = pair.price_usd if pair is not None else None
        except Exception:  # noqa: BLE001 — une panne réseau sur CE candidat ne bloque pas les autres
            price = None
        if not price or price <= 0:
            price_unavailable += 1
        await record_revisit(row["id"], price)
        revisited += 1
    return {"due": len(due), "revisited": revisited, "price_unavailable": price_unavailable}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def summarize_revisited(*, limit: int = 500) -> dict:
    """Agrège les contrefactuels déjà résolus (revisités) -- par raison de rejet :
    combien, évolution de prix moyenne/médiane, combien auraient "significativement"
    monté (>= +50%, seuil de lecture, pas un jugement -- cf. format_counterfactual_
    summary pour l'avertissement sur la taille d'échantillon)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM counterfactual_rejection "
            "WHERE revisited_at IS NOT NULL AND price_change_pct IS NOT NULL "
            "ORDER BY revisited_at DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

    buckets: dict[str, list[float]] = {}
    for r in rows:
        buckets.setdefault(r["reject_reason"], []).append(r["price_change_pct"])

    by_reason: dict[str, dict] = {}
    for reason, changes in buckets.items():
        changes_sorted = sorted(changes)
        n = len(changes_sorted)
        median = changes_sorted[n // 2] if n % 2 == 1 else (changes_sorted[n // 2 - 1] + changes_sorted[n // 2]) / 2.0
        by_reason[reason] = {
            "count": n,
            "avg_price_change_pct": sum(changes) / n,
            "median_price_change_pct": median,
            "would_have_gained_50pct_or_more": sum(1 for c in changes if c >= 50.0),
        }
    return {"resolved_total": len(rows), "by_reason": by_reason}


def format_counterfactual_summary(summary: dict) -> str:
    header = "🔍 Contrefactuel des candidats rejetés (seuils durs momentum)"
    by_reason = summary.get("by_reason") or {}
    if not by_reason:
        return f"{header}\n\nAucun contrefactuel résolu pour l'instant (rien à revisiter, ou cycle pas encore activé)."

    lines = [header, f"{summary.get('resolved_total', 0)} rejet(s) revisité(s) au total", ""]
    ranked = sorted(by_reason.items(), key=lambda kv: kv[1]["count"], reverse=True)
    for reason, stats in ranked:
        lines.append(
            f"- {reason} : {stats['count']} · évolution moyenne {stats['avg_price_change_pct']:+.1f}%"
            f" (médiane {stats['median_price_change_pct']:+.1f}%)"
            f" · {stats['would_have_gained_50pct_or_more']} auraient pris ≥+50%"
        )
    lines.append("")
    lines.append(
        "Lecture prudente : un petit nombre de résolutions par case ne prouve rien -- "
        "ne pas ajuster un seuil dur sur la base de quelques contrefactuels seulement."
    )
    return "\n".join(lines)
