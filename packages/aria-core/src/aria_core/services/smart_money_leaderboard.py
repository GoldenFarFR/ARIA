"""Classement "meilleurs investisseurs" (21/07, demande opérateur explicite) --
top 600 wallets EOA repérés par récurrence croisée (>=3 tokens détenus parmi
ceux déjà extraits, cf. ``token_holder_intel.list_cross_token_candidates``),
classés par ``composite_percentile`` RÉEL (``smart_money.py``, performance de
trading -- jamais un score de coordination/Sybil, catégorie différente, cf.
``WalletScoreCard.cross_token_holder_count``, jamais mélangée ici).

Distinct de ``momentum_blacklist.py`` (sécurité -- bannit des CONTRATS de
token pour wash-trading confirmé, incident BRIAN) : ici on DÉMOTE des WALLETS
pour sous-performance de trading, jamais une question de sécurité/fraude --
terminologie volontairement séparée ("classement"/"archivé", jamais "banni")
pour ne jamais confondre les deux mécanismes dans le code ni en le relisant.

Règles (décision opérateur, 21/07, précisées après clarification) :
- Un wallet ne rejoint le classement QUE si son ``composite_percentile`` est
  un vrai nombre mesuré (jamais un défaut fixe type 50/100 le temps que la
  population de comparaison grossisse -- même doctrine "indisponible plutôt
  qu'inventé" que partout ailleurs dans ``smart_money.py``).
- Capacité dure : 600 (relevée depuis 50 le 21/07). Au-delà, le(s) plus bas
  percentile(s) sont retirés et archivés (motif "hors du top 600 --
  capacité").
- Éviction immédiate si ``composite_percentile < 30``, quelle que soit la
  taille actuelle du classement (motif "percentile sous 30").
- Le classement est réévalué à CHAQUE nouveau score produit par
  ``wallet_scan_queue.run_wallet_scan_queue_cycle`` (couverture complète
  atteinte seulement -- un score partiel n'est pas plus fiable pour classer
  qu'il ne l'est pour comparer, même exclusion que ``full_coverage=False``
  ailleurs dans ``smart_money.py``).

À une capacité de 600, la file de scan (``wallet_scan_queue.py``) peut
contenir presque autant de wallets en surveillance hebdomadaire que son
propre débit hebdomadaire (1 wallet/20min ~= 504 scans/semaine) -- corrigé le
même jour : ``list_pending()`` priorise désormais les nouveaux candidats
(rattrapage) sur les simples rescans de surveillance, pour que la découverte
ne soit jamais structurellement affamée."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

MAX_LEADERBOARD_SIZE = 600
EVICTION_PERCENTILE_THRESHOLD = 30.0


def smart_money_leaderboard_enabled() -> bool:
    return os.environ.get("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS smart_money_leaderboard (
                wallet TEXT PRIMARY KEY,
                composite_percentile REAL NOT NULL,
                joined_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS smart_money_leaderboard_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                percentile_at_removal REAL,
                removed_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS smart_money_rejected_wallets (
                wallet TEXT PRIMARY KEY,
                percentile_at_rejection REAL,
                rejected_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def is_rejected(wallet: str) -> bool:
    """Un wallet confirmé sous-performant (percentile mesuré < 30) une fois
    est rejeté DÉFINITIVEMENT -- vérifié par ``discover_and_enqueue_candidates``
    avant tout enfilement, pour qu'il ne réapparaisse jamais simplement parce
    qu'il détient un NOUVEAU token découvert plus tard (21/07, demande
    opérateur explicite)."""
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute("SELECT 1 FROM smart_money_rejected_wallets WHERE wallet = ?", (wallet,))
        ).fetchone()
    return row is not None


async def mark_rejected(wallet: str, percentile: float | None, reason: str) -> None:
    """Rejet PERMANENT -- aucune fonction symétrique de dé-rejet, même doctrine
    que ``momentum_blacklist.py`` (un contrat banni le reste ; ici, un wallet
    confirmé mauvais le reste). Idempotent (``INSERT OR IGNORE`` -- un wallet
    déjà rejeté n'est jamais réécrit)."""
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO smart_money_rejected_wallets "
            "(wallet, percentile_at_rejection, rejected_at, reason) VALUES (?, ?, ?, ?)",
            (wallet, percentile, datetime.now(timezone.utc).isoformat(), reason),
        )
        await db.commit()


async def _archive(db: aiosqlite.Connection, wallet: str, percentile: float | None, reason: str) -> None:
    await db.execute(
        "INSERT INTO smart_money_leaderboard_archive "
        "(wallet, percentile_at_removal, removed_at, reason) VALUES (?, ?, ?, ?)",
        (wallet, percentile, datetime.now(timezone.utc).isoformat(), reason),
    )


async def update_leaderboard(wallet: str, composite_percentile: float | None) -> str:
    """Insère/actualise/évince un wallet selon son ``composite_percentile``
    RÉEL le plus récent. Retourne l'action effectuée (jamais un booléen
    opaque) : ``no_percentile`` / ``not_eligible`` / ``added`` / ``updated`` /
    ``evicted_low_score`` / ``evicted_capacity``.

    ``composite_percentile=None`` (pas assez de population pour comparer) :
    NO-OP, jamais un score inventé pour forcer une entrée."""
    if composite_percentile is None:
        return "no_percentile"
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return "no_percentile"

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        if composite_percentile < EVICTION_PERCENTILE_THRESHOLD:
            existing = await (
                await db.execute("SELECT 1 FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
            ).fetchone()
            if not existing:
                return "not_eligible"
            await db.execute("DELETE FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
            await _archive(db, wallet, composite_percentile, "percentile sous 30")
            await db.commit()
            return "evicted_low_score"

        row = await (
            await db.execute("SELECT 1 FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
        ).fetchone()
        if row:
            await db.execute(
                "UPDATE smart_money_leaderboard SET composite_percentile = ?, last_updated_at = ? WHERE wallet = ?",
                (composite_percentile, now, wallet),
            )
            action = "updated"
        else:
            await db.execute(
                "INSERT INTO smart_money_leaderboard "
                "(wallet, composite_percentile, joined_at, last_updated_at) VALUES (?, ?, ?, ?)",
                (wallet, composite_percentile, now, now),
            )
            action = "added"
        await db.commit()

        # Capacité dure : au-delà de 50, retire le(s) plus bas percentile(s).
        rows = await (
            await db.execute(
                "SELECT wallet, composite_percentile FROM smart_money_leaderboard "
                "ORDER BY composite_percentile DESC"
            )
        ).fetchall()
        if len(rows) > MAX_LEADERBOARD_SIZE:
            overflow = rows[MAX_LEADERBOARD_SIZE:]
            overflow_wallets = {w for w, _ in overflow}
            for w, pct in overflow:
                await db.execute("DELETE FROM smart_money_leaderboard WHERE wallet = ?", (w,))
                await _archive(db, w, pct, f"hors du top {MAX_LEADERBOARD_SIZE} (capacité)")
            await db.commit()
            if wallet in overflow_wallets:
                action = "evicted_capacity"
        return action


async def remove_and_archive(wallet: str, reason: str) -> str:
    """Retrait EXPLICITE, indépendant du percentile (contrairement à
    ``update_leaderboard``) -- réponse au trou trouvé le 21/07 : un wallet
    retiré de ``wallet_scan_queue`` pour inactivité (90j+ sans activité
    on-chain réelle) gardait sa dernière note pour toujours dans le
    classement, jamais signalé comme "plus suivi". Retourne ``removed`` ou
    ``not_present`` (jamais une erreur si le wallet n'était pas dans le
    classement -- rien à faire de plus)."""
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return "not_present"
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT composite_percentile FROM smart_money_leaderboard WHERE wallet = ?", (wallet,)
            )
        ).fetchone()
        if not row:
            return "not_present"
        await db.execute("DELETE FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
        await _archive(db, wallet, row[0], reason)
        await db.commit()
    return "removed"


async def get_leaderboard() -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT wallet, composite_percentile, joined_at, last_updated_at "
                "FROM smart_money_leaderboard ORDER BY composite_percentile DESC"
            )
        ).fetchall()
    out = []
    for i, r in enumerate(rows, 1):
        d = dict(r)
        d["rank"] = i
        out.append(d)
    return out


async def get_archive(limit: int = 50) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT wallet, percentile_at_removal, removed_at, reason "
                "FROM smart_money_leaderboard_archive ORDER BY removed_at DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def discover_and_enqueue_candidates(*, min_token_count: int = 3) -> dict:
    """Repère les wallets EOA récurrents (``token_holder_intel``, gratuit --
    lecture locale pure) et les enfile dans ``wallet_scan_queue.py`` pour un
    scoring réel -- déclencheur du classement, jamais un score en lui-même.
    Idempotent (``enqueue_wallets`` ignore déjà les doublons -- un wallet
    déjà en file, en rattrapage ou en surveillance, n'est jamais réinjecté).

    Triple gate -- ``ARIA_SMART_MONEY_LEADERBOARD_ENABLED`` en plus de
    ``ARIA_WALLET_SCAN_QUEUE_ENABLED``/``ARIA_WALLET_SCORING_ENABLED`` (tous
    OFF par défaut), même patron que ``wallet_candidate_sourcing.py``."""
    if not smart_money_leaderboard_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core.services.smart_money import wallet_scoring_enabled
    from aria_core.services.wallet_scan_queue import enqueue_wallets, wallet_scan_queue_enabled

    if not wallet_scan_queue_enabled() or not wallet_scoring_enabled():
        return {"outcome": "skipped", "reason": "downstream_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    from aria_core import token_holder_intel

    candidates = await token_holder_intel.list_cross_token_candidates(min_token_count=min_token_count)
    if not candidates:
        return {"outcome": "no_candidate"}

    # Un wallet déjà rejeté DÉFINITIVEMENT (percentile confirmé < 30) ne doit
    # jamais réapparaître simplement parce qu'il détient un nouveau token
    # découvert plus tard (21/07, demande opérateur explicite).
    addresses = []
    already_rejected = 0
    for c in candidates:
        if await is_rejected(c["holder_address"]):
            already_rejected += 1
            continue
        addresses.append(c["holder_address"])
    if not addresses:
        return {"outcome": "no_candidate", "already_rejected": already_rejected}

    added = await enqueue_wallets(addresses)
    return {
        "outcome": "ok",
        "candidates_found": len(candidates),
        "already_rejected": already_rejected,
        "added_to_queue": len(added),
    }
