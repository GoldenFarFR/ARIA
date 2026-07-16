"""Surveillance READ-ONLY du wallet agent CDP — registre complet des mouvements
réels (dépôts/retraits), détectés automatiquement via Blockscout (lecture seule,
aucune clé privée, aucune capacité d'exécution). Répond à la demande opérateur
du 16/07 : "détection automatique des fonds quand il arrive ou parte avec un
registre complet du wallet pour que tu vérifie en temps réel".

Réutilise `services/blockscout.py` (déjà construit, Base natif, #157) plutôt que
de dupliquer un client -- `get_token_transfers` pour l'USDC (ERC-20),
`get_transactions` pour l'ETH natif (gas/dépôts). Aucun nouveau client réseau.

Chaque mouvement fraîchement détecté (jamais revu deux fois, `tx_hash` unique)
est classé :
  - "known" : le `tx_hash` correspond à une transaction déjà journalisée par
    `agent_wallet_pilot`/`agent_wallet_log` (ARIA elle-même a initié ce
    mouvement) -- rien d'anormal.
  - "external_deposit" : entrée de fonds non initiée par ARIA (ex. l'opérateur
    finance le wallet manuellement) -- normal, journalisé pour traçabilité.
  - "unexpected_outflow" : SORTIE de fonds non initiée par ARIA -- signal de
    sécurité potentiellement grave (clé compromise ?), à traiter en urgence par
    l'appelant (notification immédiate).

Limite honnête assumée : la classification "known" ne peut matcher QUE les
mouvements passés par `agent_wallet_pilot.py` (swap/transfert loggés) -- un
mouvement initié par un autre outil légitime (ex. l'opérateur utilisant
directement l'app Coinbase) sera classé "external_deposit"/"unexpected_outflow"
même si c'est en réalité lui-même qui agit. Pas un défaut : mieux vaut un faux
positif (opérateur re-confirme que c'était bien lui) qu'un faux négatif silencieux.

Structurellement séparé de `wallet_guard.py` et de `agent_wallet_pilot.py` (aucun
import croisé d'exécution) -- ce module ne PEUT PAS signer ni exécuter quoi que
ce soit, uniquement lire et journaliser."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services.blockscout import get_blockscout_client

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Adresse Base mainnet PUBLIQUE du wallet agent CDP (`aria-agent-wallet-pilot`,
# vérifiée en direct le 16/07 -- cf. agent_wallet_cdp_adapter.py). Une adresse
# publique n'est pas un secret (contrairement aux clés CDP) -- codée en dur ici
# au même titre que ALLOWED_TRANSFER_ADDRESS dans agent_wallet_pilot.py.
MONITORED_WALLET_ADDRESS = "0xF04625162b616c5ad9788811b7be8CDd425B37Ef"


def agent_wallet_monitor_enabled() -> bool:
    """Gate dédié, indépendant des gates pilote/swap/transfert -- la surveillance
    peut tourner même si l'exécution reste désactivée (lecture seule, aucun
    risque à la laisser active plus largement que l'exécution elle-même)."""
    return os.environ.get("ARIA_AGENT_WALLET_MONITOR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


@dataclass(frozen=True)
class WalletMovement:
    tx_hash: str
    direction: str  # "in" | "out"
    asset: str
    amount: float
    counterparty: str
    classification: str  # "known" | "external_deposit" | "unexpected_outflow"
    timestamp: str | None = None


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_wallet_movement_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_hash TEXT NOT NULL UNIQUE,
                direction TEXT NOT NULL,
                asset TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL DEFAULT 0,
                counterparty TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                tx_timestamp TEXT
            )
            """
        )
        await db.commit()


async def _already_seen(tx_hash: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM agent_wallet_movement_log WHERE tx_hash = ?", (tx_hash,)
            )
        ).fetchone()
    return row is not None


async def _record_movement(m: WalletMovement) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO agent_wallet_movement_log
                (tx_hash, direction, asset, amount, counterparty, classification,
                 detected_at, tx_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                m.tx_hash, m.direction, m.asset, m.amount, m.counterparty,
                m.classification, datetime.now(timezone.utc).isoformat(), m.timestamp,
            ),
        )
        await db.commit()


def _classify(tx_hash: str, direction: str, known_tx_hashes: set[str]) -> str:
    if tx_hash in known_tx_hashes:
        return "known"
    return "external_deposit" if direction == "in" else "unexpected_outflow"


async def list_recent_movements(limit: int = 100) -> list[dict]:
    """Registre complet persisté (append-only en pratique -- `INSERT OR IGNORE`
    ne réécrit jamais une ligne existante), le plus récent d'abord."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM agent_wallet_movement_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def check_wallet_activity(
    *, wallet_address: str, chain: str = "base", known_tx_hashes: set[str] | None = None,
) -> list[WalletMovement]:
    """Interroge Blockscout (lecture seule) pour les transferts USDC (ERC-20) ET
    les mouvements ETH natifs récents de ``wallet_address``, journalise chaque
    NOUVEAU mouvement (jamais revu deux fois grâce à l'unicité de ``tx_hash``) et
    renvoie la liste des mouvements fraîchement détectés (pour notification par
    l'appelant -- ce module ne notifie jamais lui-même, voir
    ``format_movement_alert``).

    ``known_tx_hashes`` : hashes déjà journalisés par ``agent_wallet_log``
    (transactions initiées par ARIA elle-même, ok uniquement) -- typiquement
    ``{row['tx_hash'] for row in await agent_wallet_log.list_transactions() if row['status'] == 'ok'}``."""
    await _ensure_table()
    known_tx_hashes = known_tx_hashes or set()
    address_lower = wallet_address.lower()
    client = get_blockscout_client(chain)
    fresh: list[WalletMovement] = []

    token_result = await client.get_token_transfers(wallet_address, limit=50, max_pages=1)
    if not token_result.available:
        logger.info("agent_wallet_monitor: transferts token indisponibles (%s)", token_result.error)
    else:
        for t in token_result.transfers:
            if not t.tx_hash or await _already_seen(t.tx_hash):
                continue
            direction = "in" if (t.to_address or "").lower() == address_lower else "out"
            counterparty = (t.from_address if direction == "in" else t.to_address) or ""
            movement = WalletMovement(
                tx_hash=t.tx_hash, direction=direction, asset=t.token_symbol or "token",
                amount=t.amount or 0.0, counterparty=counterparty,
                classification=_classify(t.tx_hash, direction, known_tx_hashes),
                timestamp=t.timestamp,
            )
            await _record_movement(movement)
            fresh.append(movement)

    tx_result = await client.get_transactions(wallet_address, limit=50)
    if not tx_result.available:
        logger.info("agent_wallet_monitor: transactions natives indisponibles (%s)", tx_result.error)
    else:
        for tx in tx_result.transactions:
            if not tx.tx_hash or await _already_seen(tx.tx_hash):
                continue
            if not tx.value_native or tx.value_native <= 0:
                continue  # appel de contrat sans valeur transférée -- pas un mouvement de fonds
            direction = "in" if (tx.to_address or "").lower() == address_lower else "out"
            counterparty = (tx.from_address if direction == "in" else tx.to_address) or ""
            movement = WalletMovement(
                tx_hash=tx.tx_hash, direction=direction, asset="ETH",
                amount=tx.value_native, counterparty=counterparty,
                classification=_classify(tx.tx_hash, direction, known_tx_hashes),
                timestamp=tx.timestamp,
            )
            await _record_movement(movement)
            fresh.append(movement)

    return fresh


async def get_wallet_balance_summary(
    *, wallet_address: str = "", chain: str = "base",
) -> dict[str, Any]:
    """Solde RÉEL courant du wallet agent (#204, demande opérateur) -- USDC via
    l'adaptateur CDP déjà vérifié en direct (16/07, #157), ETH natif via
    Blockscout (déjà construit, déjà utilisé ailleurs dans ARIA -- lecture
    seule, aucune dépendance au SDK CDP ni à ses identifiants). Chaque valeur
    dégrade honnêtement à ``None`` si indisponible, jamais un solde inventé."""
    wallet_address = wallet_address or MONITORED_WALLET_ADDRESS

    try:
        from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd

        usdc = await usdc_balance_usd(network=chain)
    except Exception as exc:  # noqa: BLE001 -- l'extra cdp-sdk peut manquer, jamais casser l'appelant
        logger.warning("agent_wallet_monitor: lecture solde USDC echouee: %s", exc)
        usdc = None

    eth: float | None = None
    try:
        client = get_blockscout_client(chain)
        info = await client.get_address_info(wallet_address)
        if info.available:
            eth = info.balance_native
    except Exception as exc:  # noqa: BLE001 -- une panne Blockscout ne doit jamais casser l'appelant
        logger.warning("agent_wallet_monitor: lecture solde ETH echouee: %s", exc)

    return {"wallet_address": wallet_address, "chain": chain, "usdc_usd": usdc, "eth": eth}


async def run_agent_wallet_monitor_cycle(*, notifier=None) -> dict:
    """Un tour de heartbeat : lit les mouvements réels du wallet agent CDP
    (``MONITORED_WALLET_ADDRESS``), notifie immédiatement chaque mouvement
    fraîchement détecté (le kill-switch coupe la NOTIFICATION, jamais la
    lecture/journalisation elle-même -- même une en pause, ARIA doit garder un
    registre complet, cf. demande opérateur "registre complet ... pour que tu
    vérifie en temps réel"). Fail-closed : une panne Blockscout/log ne casse
    jamais le heartbeat (``check_wallet_activity`` dégrade déjà silencieusement
    en interne)."""
    if not agent_wallet_monitor_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import agent_wallet_log, outgoing_pause

    try:
        logged = await agent_wallet_log.list_transactions(limit=500)
    except Exception as exc:  # noqa: BLE001 -- une panne du log ne doit jamais bloquer la surveillance
        logged = []
        logger.warning("agent_wallet_monitor: lecture agent_wallet_log echouee: %s", exc)
    known_tx_hashes = {row["tx_hash"] for row in logged if row.get("status") == "ok" and row.get("tx_hash")}

    try:
        movements = await check_wallet_activity(
            wallet_address=MONITORED_WALLET_ADDRESS, known_tx_hashes=known_tx_hashes,
        )
    except Exception as exc:  # noqa: BLE001 -- jamais casser le heartbeat sur une panne de lecture
        return {"outcome": "error", "error": str(exc)[:300]}

    if not movements:
        return {"outcome": "nothing_new"}

    paused = outgoing_pause.is_paused()
    notified = 0
    if notifier and not paused:
        for m in movements:
            try:
                await notifier(format_movement_alert(m))
                notified += 1
            except Exception as exc:  # noqa: BLE001 -- un envoi rate ne doit jamais faire perdre le registre
                logger.warning("agent_wallet_monitor: notification echouee pour %s: %s", m.tx_hash, exc)

    return {
        "outcome": "ok",
        "detected": len(movements),
        "notified": notified,
        "unexpected_outflows": sum(1 for m in movements if m.classification == "unexpected_outflow"),
    }


def format_movement_alert(m: WalletMovement) -> str:
    icon = {"known": "✅", "external_deposit": "💰", "unexpected_outflow": "🚨"}.get(
        m.classification, "•",
    )
    label = {
        "known": "Mouvement initié par ARIA (attendu)",
        "external_deposit": "Dépôt externe détecté",
        "unexpected_outflow": "SORTIE NON INITIÉE PAR ARIA — à vérifier immédiatement",
    }.get(m.classification, m.classification)
    direction_label = "Entrée" if m.direction == "in" else "Sortie"
    counterparty_label = "De" if m.direction == "in" else "Vers"
    return (
        f"{icon} Wallet agent — {label}\n"
        f"{direction_label} : {m.amount} {m.asset}\n"
        f"{counterparty_label} : {m.counterparty}\n"
        f"Tx : {m.tx_hash}"
    )


def format_wallet_balance_summary(summary: dict[str, Any]) -> str:
    """Formate le résultat de ``get_wallet_balance_summary`` pour Telegram
    (#204) -- chaque solde indisponible s'affiche honnêtement comme tel,
    jamais un 0 silencieux qui laisserait croire à un wallet vide."""
    usdc = summary.get("usdc_usd")
    eth = summary.get("eth")
    usdc_line = f"{usdc:.4f} USDC" if usdc is not None else "indisponible (SDK/identifiants CDP absents)"
    eth_line = f"{eth:.6f} ETH" if eth is not None else "indisponible (Blockscout hors service)"
    return (
        f"💼 Wallet agent CDP ({summary.get('chain', 'base')})\n"
        f"Adresse : {summary.get('wallet_address', '?')}\n"
        f"USDC : {usdc_line}\n"
        f"ETH (gas) : {eth_line}"
    )
