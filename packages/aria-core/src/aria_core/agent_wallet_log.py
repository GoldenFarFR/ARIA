"""Journal append-only des transactions du futur pilote « agent wallet » (MetaMask
Agent Wallet / Coinbase Agentic Wallets / Trust Wallet Agent Kit — capital réel
étape 2, diligence CLAUDE.md 14-15/07). Seam construit AVANT le choix définitif du
produit et AVANT tout dépôt réel : ce module n'est appelé par aucun code de
production pour l'instant, il attend d'être câblé une fois le pilote décidé.

Même doctrine que `bonding_trade_log.py` (#60, Arena) : enregistre CHAQUE tentative
d'exécution (`status` in {"ok", "failed", "blocked"}), jamais seulement les succès —
un refus côté garde-fou (plafond dépassé, slippage hors tolérance, kill-switch
désactivé) doit rester tracé, jamais silencieux.

Structurellement séparé de `wallet_guard.py` — même principe que
`sepolia_autonomous.py`/`bonding_trade_log.py` : jamais mélangé au garde-fou partagé
qui protège tout ce qui touchera un jour du capital réel à plus grande échelle.
Append-only : aucune fonction UPDATE/DELETE ici (même doctrine que
`aria_directives.py::aria_directive_log`).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "id",
    "wallet_product",
    "chain",
    "action_type",
    "token_in",
    "token_out",
    "amount_in",
    "amount_out",
    "slippage_bps",
    "tx_hash",
    "status",
    "reason",
    "created_at",
    "to_address",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_wallet_tx_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_product TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT '',
                action_type TEXT NOT NULL,
                token_in TEXT NOT NULL DEFAULT '',
                token_out TEXT NOT NULL DEFAULT '',
                amount_in REAL NOT NULL DEFAULT 0,
                amount_out REAL NOT NULL DEFAULT 0,
                slippage_bps INTEGER NOT NULL DEFAULT 0,
                tx_hash TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        # Migration à chaud idempotente (même patron que vc_predictions.py/exam.py) --
        # to_address ajouté le 16/07 pour l'exception nommée #4 (transfert USDC vers
        # une adresse unique autorisée) : jamais dans la définition CREATE TABLE
        # ci-dessus pour ne pas casser une base déjà existante sans cette colonne.
        cols = [row[1] async for row in await db.execute("PRAGMA table_info(agent_wallet_tx_log)")]
        if "to_address" not in cols:
            await db.execute("ALTER TABLE agent_wallet_tx_log ADD COLUMN to_address TEXT NOT NULL DEFAULT ''")
        await db.commit()


async def record_transaction(
    *,
    wallet_product: str,
    chain: str = "",
    action_type: str,
    token_in: str = "",
    token_out: str = "",
    amount_in: float = 0.0,
    amount_out: float = 0.0,
    slippage_bps: int = 0,
    tx_hash: str = "",
    status: str,
    reason: str = "",
    to_address: str = "",
) -> None:
    """Enregistre une tentative de transaction (``status`` in {"ok", "failed",
    "blocked"}). ``wallet_product`` identifie le produit utilisé (ex.
    "metamask_agent_wallet", "coinbase_agentic_wallet", "trust_wallet_agent_kit")
    — laissé libre plutôt qu'un enum fermé, le pilote n'étant pas encore choisi.
    ``to_address`` (16/07, exception nommée #4) : adresse de destination d'un
    transfert -- vide pour tout autre type d'action (ex. swap).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO agent_wallet_tx_log
                (wallet_product, chain, action_type, token_in, token_out,
                 amount_in, amount_out, slippage_bps, tx_hash, status, reason,
                 created_at, to_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wallet_product, chain, action_type, token_in, token_out,
                amount_in, amount_out, slippage_bps, tx_hash, status, reason, now,
                to_address,
            ),
        )
        await db.commit()


async def list_transactions(limit: int = 200) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM agent_wallet_tx_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def recent_failed_swap(token_out: str, *, within_minutes: int) -> bool:
    """Vrai si la DERNIÈRE tentative de swap vers ``token_out`` (n'importe
    quelle jambe d'entrée) est un échec technique (``status="failed"``) survenu
    il y a moins de ``within_minutes`` -- cooldown léger après une panne
    transitoire (RPC, slippage dépassé), pour la boucle de décision autonome du
    pilote agent-wallet (18/07). Réutilise le journal déjà existant, aucune
    nouvelle table -- jamais confondu avec ``momentum_blacklist.py`` (réservé
    aux vraies menaces de sécurité confirmées, jamais une panne technique).
    Si le DERNIER essai pour ce token a réussi ou a été bloqué (pas 'failed'),
    ou n'existe pas du tout, le token n'est jamais mis en cooldown ici."""
    await _ensure_table()
    token = (token_out or "").strip().lower()
    if not token:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                # LOWER() des deux côtés : token_out est stocké TEL QUE fourni par
                # l'appelant (record_transaction ne normalise pas la casse) --
                # jamais supposer que tout appelant historique a déjà lowercasé.
                "SELECT status, created_at FROM agent_wallet_tx_log "
                "WHERE action_type = 'swap' AND LOWER(token_out) = ? "
                "ORDER BY id DESC LIMIT 1",
                (token,),
            )
        ).fetchone()
    if not row or row[0] != "failed":
        return False
    try:
        ts = datetime.fromisoformat(row[1])
    except (ValueError, TypeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    elapsed_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    return elapsed_min < within_minutes
