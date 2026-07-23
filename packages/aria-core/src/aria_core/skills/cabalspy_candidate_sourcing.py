"""Sourcing de wallets candidats depuis CabalSpy (23/07, décision opérateur
explicite -- changement de politique assumé, voir docstring de
`services/cabalspy.py`).

Deux volets bien séparés, jamais mélangés :
1. **Catégorisation** (`cabalspy_kol_wallets`) : TOUS les wallets labellisés
   récupérés, TOUTES chaînes confondues (Base/BNB/Solana) -- simple
   répertoire, ne préjuge en rien de leur score, jamais un signal de trading.
2. **Sourcing réel vers le scoring** (`wallet_scan_queue.enqueue_wallets`) :
   UNIQUEMENT les wallets Base -- le seul pipeline downstream (`smart_money.py`,
   Blockscout) qui sait aujourd'hui les traiter (câblé Base-only en dur,
   vérifié dans le code). BNB (EVM, effort d'extension pas encore vérifié) et
   Solana (format d'adresse différent, aucun Blockscout, chantier séparé) sont
   catégorisés mais jamais enfilés dans le scoring tant que ce pipeline n'est
   pas étendu -- éviter de scorer à tort une adresse avec le mauvais
   explorateur plutôt que de deviner un comportement dégradé.

Type "kol" priorisé (identité complète : name/twitter/telegram, vérifié réel
sur Base -- 200 wallets). Type "smart" câblé aussi (repli honnête) mais
signalé comme probable doublon de ce que `smart_money.py` détecte déjà par
comportement, gratuitement -- jamais recommandé comme source prioritaire."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Chaînes catégorisées (toutes) vs chaînes réellement sourcées vers le scoring
# (Base seule, pipeline downstream vérifié capable de les traiter).
_CATALOGUED_BLOCKCHAINS = ("base", "bnb", "solana")
_SCORABLE_BLOCKCHAINS = ("base",)

# La liste des KOL ne bouge pas d'un jour à l'autre -- éviter de re-fetcher à
# chaque cycle heartbeat (économie de crédits CabalSpy, 300-10000/mois selon
# palier). Une synchronisation complète par semaine suffit largement.
MIN_RESYNC_INTERVAL_DAYS = 7


def cabalspy_sourcing_enabled() -> bool:
    return os.environ.get("ARIA_CABALSPY_SOURCING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cabalspy_kol_wallets (
                wallet TEXT NOT NULL,
                blockchain TEXT NOT NULL,
                wallet_type TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                twitter TEXT NOT NULL DEFAULT '',
                telegram TEXT NOT NULL DEFAULT '',
                sourced_at TEXT NOT NULL,
                PRIMARY KEY (wallet, blockchain, wallet_type)
            )
            """
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS cabalspy_sourcing_state (id INTEGER PRIMARY KEY CHECK (id = 1), last_full_sync_at TEXT)"
        )
        await db.commit()


async def _last_full_sync_at() -> datetime | None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT last_full_sync_at FROM cabalspy_sourcing_state WHERE id = 1")).fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


async def _mark_full_sync_done(now: datetime) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO cabalspy_sourcing_state (id, last_full_sync_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_full_sync_at = excluded.last_full_sync_at",
            (now.isoformat(),),
        )
        await db.commit()


async def _store_wallets(wallets: list, *, now: datetime) -> int:
    if not wallets:
        return 0
    await _ensure_tables()
    stored = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for w in wallets:
            cursor = await db.execute(
                "INSERT OR REPLACE INTO cabalspy_kol_wallets "
                "(wallet, blockchain, wallet_type, name, twitter, telegram, sourced_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (w.wallet_address.lower(), w.blockchain, w.type, w.name, w.twitter, w.telegram, now.isoformat()),
            )
            if cursor.rowcount:
                stored += 1
        await db.commit()
    return stored


async def catalogued_wallets(blockchain: str | None = None) -> list[dict]:
    """Répertoire catégorisé -- lecture seule, jamais un signal de trading en
    lui-même. Filtrable par chaîne."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if blockchain:
            cursor = await db.execute(
                "SELECT * FROM cabalspy_kol_wallets WHERE blockchain = ? ORDER BY sourced_at DESC", (blockchain,),
            )
        else:
            cursor = await db.execute("SELECT * FROM cabalspy_kol_wallets ORDER BY blockchain, sourced_at DESC")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def run_cabalspy_candidate_sourcing_cycle(notifier=None, *, now: datetime | None = None) -> dict:
    """Un tour : si la dernière synchronisation complète a moins de
    `MIN_RESYNC_INTERVAL_DAYS`, ne fait rien (économie de crédits). Sinon,
    récupère la liste "kol" pour chaque chaîne catégorisée, stocke TOUT dans
    le répertoire, puis enfile UNIQUEMENT les wallets Base dans
    `wallet_scan_queue` (le seul pipeline de scoring qui les traite
    aujourd'hui). Gate dédié + downstream (queue/scoring), fail-closed,
    respecte le kill-switch."""
    if not cabalspy_sourcing_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core.services.cabalspy import is_cabalspy_configured, list_wallets
    from aria_core.services.smart_money import wallet_scoring_enabled
    from aria_core.services.wallet_scan_queue import enqueue_wallets, wallet_scan_queue_enabled

    if not is_cabalspy_configured():
        return {"outcome": "skipped", "reason": "no_api_key"}

    if not wallet_scan_queue_enabled() or not wallet_scoring_enabled():
        return {"outcome": "skipped", "reason": "downstream_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    now = now or datetime.now(timezone.utc)
    last_sync = await _last_full_sync_at()
    if last_sync is not None and (now - last_sync) < timedelta(days=MIN_RESYNC_INTERVAL_DAYS):
        return {"outcome": "skipped", "reason": "resync_not_due", "last_full_sync_at": last_sync.isoformat()}

    per_chain: dict[str, int] = {}
    total_stored = 0
    base_wallets: list[str] = []

    for blockchain in _CATALOGUED_BLOCKCHAINS:
        wallets = await list_wallets(blockchain, wallet_type="kol")
        if not wallets:
            per_chain[blockchain] = 0
            continue
        stored = await _store_wallets(wallets, now=now)
        per_chain[blockchain] = stored
        total_stored += stored
        if blockchain in _SCORABLE_BLOCKCHAINS:
            base_wallets.extend(w.wallet_address for w in wallets)

    await _mark_full_sync_done(now)

    added = await enqueue_wallets(base_wallets) if base_wallets else []

    if (total_stored or added) and notifier is not None:
        detail = ", ".join(f"{chain}:{count}" for chain, count in per_chain.items())
        await notifier(
            f"🔍 Sourcing CabalSpy -- {total_stored} wallet(s) KOL catalogué(s) ({detail}), "
            f"{len(added)} ajouté(s) à la file de scoring (Base uniquement)."
        )

    return {"outcome": "ok", "stored_per_chain": per_chain, "queued_for_scoring": len(added)}
