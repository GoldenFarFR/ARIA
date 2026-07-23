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

import html
import logging
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS
from aria_core.paths import aria_db_path
from aria_core.services.blockscout import get_blockscout_client
from aria_core.services.dexscreener import token_url

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Adresse Base mainnet PUBLIQUE du wallet agent CDP (`aria-agent-wallet-pilot`,
# vérifiée en direct le 16/07 -- cf. agent_wallet_cdp_adapter.py). Une adresse
# publique n'est pas un secret (contrairement aux clés CDP) -- codée en dur ici
# au même titre que ALLOWED_TRANSFER_ADDRESS dans agent_wallet_pilot.py.
# Kept as-is (compat): `get_wallet_balance_summary`/`/agentwallet` continue to
# default to it, signature unchanged.
MONITORED_WALLET_ADDRESS = "0xF04625162b616c5ad9788811b7be8CDd425B37Ef"

# 07/23 -- surveillance extended beyond the single historical pilot wallet
# (operator request: "automatically detect entries, exits, and swaps on her
# wallets in real time"). Addresses verified live on 07/23 via
# `cdp.evm.list_accounts()`/`list_smart_accounts()` (CDP source of truth,
# never copied from memory).
#
# Scope deliberately limited to 3 wallets (explicit operator decision, 07/23,
# after computing the real cost in Blockscout Pro credits) -- excluded:
# `aria-wallet-transfert-EVM` (rarely used) and `aria-spender-smart-st-EVM`
# (still has zero movement, the Spend Permission mechanism isn't wired up) --
# to be re-added once either becomes genuinely active. `aria-wallet-X402-EVM`
# stays monitored -- its role will change (active trading capital migrates to
# `aria-smart-st-EVM`, this wallet will eventually only hold x402 payments in
# and out for the services offered) but remains a real flow worth watching,
# not less than before.
#
# Deliberate dict order: pilot wallet first (existing tests that use
# MONITORED_WALLET_ADDRESS as the recipient depend on this order to be
# classified correctly before `_already_seen` cuts short the following
# wallets in the same transaction).
MONITORED_WALLETS: dict[str, str] = {
    "aria-wallet-X402-EVM": MONITORED_WALLET_ADDRESS,
    "aria-smart-st-EVM": "0x800027f61363EF304c5C2Afee811d9d4074B474c",
    "aria-smart-vc-EVM": "0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07",
}

# 07/23 -- registry of ALL known addresses (not just the 3 monitored ones --
# transfert/spender remain legitimate possible counterparties of a movement
# on a monitored wallet) + the 2 physical Tangem owners, to show a readable
# name in alerts instead of a raw hex address (operator request, 07/23: "for
# wallets we know, show the name"). Addresses verified live (CDP + explicitly
# communicated by the operator), never copied from memory.
_KNOWN_ADDRESS_NAMES: dict[str, str] = {
    "0xF04625162b616c5ad9788811b7be8CDd425B37Ef".lower(): "aria-wallet-X402-EVM",
    "0x584b2B35dac347B2317da0d21b95063de51257Ef".lower(): "aria-wallet-transfert-EVM",
    "0x8e71C3e9396ded76AdA6EA56cD3c315C3D67D79b".lower(): "aria-spender-smart-st-EVM",
    "0x800027f61363EF304c5C2Afee811d9d4074B474c".lower(): "aria-smart-st-EVM",
    "0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07".lower(): "aria-smart-vc-EVM",
    "0x33783cCb570Cb279C25F836806B5c4C3C8309777".lower(): "tangem-01 (owner aria-smart-st)",
    "0x85e3D8128a9b7be14065A4E36C1845041BF65d7F".lower(): "tangem-02 (owner aria-smart-vc)",
}


def _label_address(address: str) -> str:
    """Address enriched with the known name in parentheses
    (``"tangem-01 (0x3378...)"``) if it matches an already-registered
    ARIA wallet/owner -- otherwise the raw address unchanged. Never blocking,
    never a guess on an unknown address."""
    if not address:
        return address
    name = _KNOWN_ADDRESS_NAMES.get(address.lower())
    return f"{name} ({address})" if name else address


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
    direction: str  # "in" | "out" | "swap"
    asset: str
    amount: float
    counterparty: str
    classification: str  # "known" | "external_deposit" | "unexpected_outflow" | "suspicious_token" | "known_x402" | "swap"
    timestamp: str | None = None
    # 22/07 -- enrichissement de l'alerte "known_x402" (quel token a été scanné,
    # quel service a été payé, cf. x402_budget.record_spend). Optionnels et vides
    # par défaut : retrocompatible, aucun autre mouvement (known/external_deposit/
    # unexpected_outflow/suspicious_token) ne les renseigne jamais.
    contract: str = ""
    token_symbol: str = ""
    resource: str = ""
    provider: str = ""
    # 07/23 -- multi-wallet: which wallet moved (empty -> "Wallet agent"
    # default in the alert, unchanged historical behavior for movements
    # built without this field, e.g. all existing tests).
    wallet_name: str = ""
    # 07/23 -- swap: two legs of the same transaction (one asset out, another
    # in) merged into a SINGLE movement rather than two rows fighting over
    # the same tx_hash (UNIQUE DB constraint). When classification == "swap",
    # `asset`/`amount` carry the RECEIVED side, `asset_out`/`amount_out` the
    # GIVEN side. Empty for any other movement.
    asset_out: str = ""
    amount_out: float = 0.0


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
        # 07/23 -- hot migration: multi-wallet + swap columns (SQLite doesn't
        # create them if the table already pre-exists). Idempotent,
        # non-destructive -- same pattern as paper_trader.py (PRAGMA
        # table_info then ALTER TABLE only if the column is missing).
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(agent_wallet_movement_log)")).fetchall()
        }
        for name, ddl in (
            ("wallet_name", "TEXT NOT NULL DEFAULT ''"),
            ("asset_out", "TEXT NOT NULL DEFAULT ''"),
            ("amount_out", "REAL NOT NULL DEFAULT 0"),
        ):
            if name not in existing:
                await db.execute(f"ALTER TABLE agent_wallet_movement_log ADD COLUMN {name} {ddl}")
        # 07/23 -- persisted registry of wallets/contracts already detected
        # as a phishing attempt (address poisoning / fake token impersonating
        # a tracked asset) -- operator request: keep track of who has already
        # tried this kind of attack against our wallets, never just a finding
        # lost cycle after cycle. `first_seen_tx_hash` = the very first
        # attempt that revealed this address -- useful to trace context
        # without re-searching the movement log.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS phishing_blacklist (
                address TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                impersonated_asset TEXT NOT NULL DEFAULT '',
                first_seen_tx_hash TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                occurrences INTEGER NOT NULL DEFAULT 1
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


async def _record_movement(m: WalletMovement) -> bool:
    """Écrit le mouvement -- ``True`` seulement si CETTE tentative a réellement
    créé la ligne (jamais notifier sur une classification qui a perdu la course).

    20/07 -- bug réel trouvé en conditions réelles (capture opérateur, alerte
    "SORTIE NON INITIÉE" sur un paiement x402 pourtant déjà connu) : l'ancien
    ``_already_seen()`` (check) puis ``_record_movement()`` (act) n'était pas
    atomique -- deux passages qui voient tous les deux ``_already_seen() ->
    False`` avant que l'un des deux n'écrive peuvent chacun calculer LEUR PROPRE
    classification (potentiellement différente si leur lecture de
    ``known_x402_spends`` n'était pas au même instant) et NOTIFIER tous les
    deux, alors que seule une des deux lignes gagne réellement l'écriture
    (``INSERT OR IGNORE``, ``tx_hash`` unique). Le perdant notifiait quand même
    avec sa classification potentiellement périmée. Corrigé : le résultat réel
    de l'écriture (``rowcount``) décide maintenant si CETTE tentative a le
    droit de notifier -- jamais la classification calculée en mémoire seule."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO agent_wallet_movement_log
                (tx_hash, direction, asset, amount, counterparty, classification,
                 detected_at, tx_timestamp, wallet_name, asset_out, amount_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                m.tx_hash, m.direction, m.asset, m.amount, m.counterparty,
                m.classification, datetime.now(timezone.utc).isoformat(), m.timestamp,
                m.wallet_name, m.asset_out, m.amount_out,
            ),
        )
        await db.commit()
        return cur.rowcount > 0


async def record_phishing_attempt(
    address: str, *, kind: str, impersonated_asset: str = "", tx_hash: str = "",
) -> None:
    """Records (or increments ``occurrences`` if already known) an address
    involved in a detected phishing attempt (``kind`` = ``"wallet"`` for the
    sender of a fake token, ``"contract"`` for the fake token's contract
    itself). Never blocking: a write failure here must never lose the
    `suspicious_token` movement already logged elsewhere (called after
    `_record_movement`, never before)."""
    if not address:
        return
    await _ensure_table()
    address_lower = address.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await (
            await db.execute("SELECT occurrences FROM phishing_blacklist WHERE address = ?", (address_lower,))
        ).fetchone()
        if existing is None:
            await db.execute(
                """
                INSERT INTO phishing_blacklist
                    (address, kind, impersonated_asset, first_seen_tx_hash, first_seen_at, occurrences)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (address_lower, kind, impersonated_asset, tx_hash, datetime.now(timezone.utc).isoformat()),
            )
        else:
            await db.execute(
                "UPDATE phishing_blacklist SET occurrences = occurrences + 1 WHERE address = ?",
                (address_lower,),
            )
        await db.commit()


async def is_known_phishing_address(address: str) -> bool:
    """Checks whether ``address`` is already known as a phishing wallet or
    contract -- useful for a future guard before an outgoing transfer (the
    current pilot doesn't need it, locked to a single fixed address via
    ``ALLOWED_TRANSFER_ADDRESS``, but useful once a less-constrained
    mechanism exists). Cautious fail-closed: an empty address is never
    "known"."""
    if not address:
        return False
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM phishing_blacklist WHERE address = ?", (address.lower(),)
            )
        ).fetchone()
    return row is not None


async def list_phishing_addresses(limit: int = 100) -> list[dict]:
    """Full registry of detected phishing attempts, most recent (by first
    appearance) first."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM phishing_blacklist ORDER BY first_seen_at DESC LIMIT ?", (limit,)
            )
        ).fetchall()
    return [dict(r) for r in rows]


# 17/07 -- fenêtre de tolérance pour corréler un mouvement on-chain détecté à un
# paiement x402 déjà journalisé (signature -> règlement -> indexation Blockscout
# introduit un délai réel, jamais instantané) -- généreuse mais bornée, jamais
# assez large pour rapprocher deux paiements sans rapport.
_X402_MATCH_WINDOW_MINUTES = 30
_X402_MATCH_AMOUNT_EPSILON = 0.001  # USDC -- tolère l'arrondi flottant, pas plus


def _parse_timestamp(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _matches_known_x402(
    *, counterparty: str, amount: float, timestamp: str | None, known_x402_spends: list[dict],
) -> dict | None:
    """Un mouvement de sortie correspond à un paiement x402 déjà journalisé (même
    destinataire, même montant à l'arrondi près, dans la fenêtre de temps) --
    échoue TOUJOURS vers "pas de correspondance" en cas de doute (donnée manquante,
    timestamp illisible) : mieux vaut un faux positif d'alerte qu'un faux négatif
    silencieux, même doctrine que le reste de ce module.

    22/07 -- renvoie désormais le dict du spend matché (contract/token_symbol/
    resource/provider, cf. `x402_budget.record_spend`) plutôt qu'un simple bool,
    pour permettre à l'alerte d'afficher QUEL token/service a été payé -- logique
    de matching strictement inchangée, seule la valeur de retour change."""
    counterparty_lower = (counterparty or "").lower()
    if not counterparty_lower:
        return None
    movement_ts = _parse_timestamp(timestamp)
    if movement_ts is None:
        return None
    for spend in known_x402_spends:
        if (spend.get("pay_to") or "").lower() != counterparty_lower:
            continue
        if abs(float(spend.get("amount_usd") or 0.0) - amount) > _X402_MATCH_AMOUNT_EPSILON:
            continue
        spend_ts = _parse_timestamp(spend.get("created_at"))
        if spend_ts is None:
            continue
        if abs((movement_ts - spend_ts).total_seconds()) <= _X402_MATCH_WINDOW_MINUTES * 60:
            return spend
    return None


def _classify(
    tx_hash: str, direction: str, known_tx_hashes: set[str],
    *, counterparty: str = "", amount: float = 0.0, timestamp: str | None = None,
    known_x402_spends: list[dict] | None = None,
) -> tuple[str, dict | None]:
    """Renvoie ``(classification, spend_matché)`` -- ``spend_matché`` n'est jamais
    ``None`` seulement quand la classification vaut ``"known_x402"`` (22/07, pour
    enrichir l'alerte avec le token/service payé), ``None`` dans tous les autres cas."""
    if tx_hash in known_tx_hashes:
        return "known", None
    if direction == "in":
        return "external_deposit", None
    if known_x402_spends:
        matched = _matches_known_x402(
            counterparty=counterparty, amount=amount, timestamp=timestamp,
            known_x402_spends=known_x402_spends,
        )
        if matched is not None:
            return "known_x402", matched
    return "unexpected_outflow", None


def _x402_movement_fields(spend: dict | None) -> dict[str, str]:
    """Extrait contract/token_symbol/resource/provider d'un spend x402 matché pour
    peupler un `WalletMovement` -- dict vide (donc "" partout) si `spend` est
    `None`, jamais un champ à moitié rempli."""
    spend = spend or {}
    return {
        "contract": spend.get("contract") or "",
        "token_symbol": spend.get("token_symbol") or "",
        "resource": spend.get("resource") or "",
        "provider": spend.get("provider") or "",
    }


# Adresses officielles des SEULS actifs que l'opérateur suit par NOM dans les alertes
# (ETH natif, sans contrat -- toute "ETH" en ERC-20 est un imposteur par construction ;
# USDC, réutilise la même adresse que l'exécution réelle -- jamais deux sources de
# vérité). Trouvé en conditions réelles (17/07) : deux dépôts "poussière" reçus le même
# jour usurpaient ETH/USDC via un homoglyphe Unicode (ex. "EṬH", T à point souscrit,
# visuellement indiscernable de "ETH" sur un petit écran Telegram) depuis des contrats
# ERC-20 à 0 holder -- l'alerte affichait le symbole tel quel, sans le confronter à
# l'adresse réelle du token, risque réel pour un opérateur qui doit décider vite si un
# dépôt est légitime.
_CANONICAL_ASSET_ADDRESSES: dict[str, str | None] = {
    "ETH": None,  # natif seulement -- aucun contrat ERC-20 légitime ne peut porter ce nom
    "USDC": USDC_BASE_ADDRESS,
}


def _normalize_symbol(symbol: str) -> str:
    """Décompose les diacritiques Unicode (NFKD) et les retire -- ``"EṬH"`` (T +
    U+0323 combining dot below) redevient ``"ETH"``, exactement l'attaque qu'un
    simple ``.upper()`` ne détecte PAS (les caractères combinants ne sont pas des
    lettres, ``.upper()`` les laisse tels quels)."""
    decomposed = unicodedata.normalize("NFKD", symbol or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.strip().upper()


def _lookalike_target(symbol: str | None, token_address: str | None) -> str | None:
    """Renvoie le nom du VRAI actif imité (``"ETH"``/``"USDC"``) si le symbole du
    transfert ressemble à l'un des actifs suivis une fois les diacritiques retirés,
    mais que le contrat ne correspond PAS à l'adresse officielle -- ``None`` si le
    symbole ne ressemble à rien de suivi, ou si c'est authentiquement le bon contrat."""
    normalized = _normalize_symbol(symbol or "")
    if normalized not in _CANONICAL_ASSET_ADDRESSES:
        return None
    real_address = _CANONICAL_ASSET_ADDRESSES[normalized]
    if real_address is None:
        return normalized  # imite ETH natif -- un transfert ERC-20 ne peut jamais l'être légitimement
    if (token_address or "").lower() != real_address.lower():
        return normalized
    return None


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


def _try_build_swap(tx_hash: str, events: list[dict], *, wallet_name: str) -> WalletMovement | None:
    """Merges two opposite movements (one out + one in) sharing the same
    ``tx_hash`` into a SINGLE ``WalletMovement`` classified ``"swap"`` --
    without this merge, the two legs would fight over the same DB row
    (``tx_hash`` UNIQUE) and the second would be silently lost, never
    notified (latent bug in the pre-07/23 code, fixed in passing by this
    restructuring). Returns ``None`` (handle each event individually,
    historical behavior) if: not exactly 2 events, same direction on both
    sides, same asset on both sides (not a real exchange), or one of the two
    legs impersonates a tracked asset -- never hide a security alert behind
    a swap that looks legitimate."""
    if len(events) != 2:
        return None
    a, b = events
    if a["direction"] == b["direction"]:
        return None
    for ev in (a, b):
        if ev["kind"] == "token" and _lookalike_target(ev.get("token_symbol"), ev.get("token_address")):
            return None
    if a["asset"] == b["asset"]:
        return None
    incoming, outgoing = (a, b) if a["direction"] == "in" else (b, a)
    return WalletMovement(
        tx_hash=tx_hash, direction="swap",
        asset=incoming["asset"], amount=incoming["amount"],
        asset_out=outgoing["asset"], amount_out=outgoing["amount"],
        counterparty=outgoing["counterparty"] or incoming["counterparty"],
        classification="swap",
        timestamp=incoming["timestamp"] or outgoing["timestamp"],
        wallet_name=wallet_name,
    )


async def check_wallet_activity(
    *, wallet_address: str, chain: str = "base", wallet_name: str = "",
    known_tx_hashes: set[str] | None = None,
    known_x402_spends: list[dict] | None = None,
) -> list[WalletMovement]:
    """Interroge Blockscout (lecture seule) pour les transferts USDC (ERC-20) ET
    les mouvements ETH natifs récents de ``wallet_address``, journalise chaque
    NOUVEAU mouvement (jamais revu deux fois grâce à l'unicité de ``tx_hash``) et
    renvoie la liste des mouvements fraîchement détectés (pour notification par
    l'appelant -- ce module ne notifie jamais lui-même, voir
    ``format_movement_alert``).

    ``wallet_name`` (07/23): name of the monitored wallet (cf.
    ``MONITORED_WALLETS``) -- propagated onto every ``WalletMovement``
    produced, shown at the top of the alert. Empty by default (backward
    compatible, shows "Wallet agent").

    ``known_tx_hashes`` : hashes déjà journalisés par ``agent_wallet_log``
    (transactions initiées par ARIA elle-même, ok uniquement) -- typiquement
    ``{row['tx_hash'] for row in await agent_wallet_log.list_transactions() if row['status'] == 'ok'}``.

    ``known_x402_spends`` (17/07) : dépenses x402 réglées (``status='ok'``) --
    typiquement ``await x402_budget.list_spends()`` filtré -- corrélées par
    destinataire+montant+fenêtre de temps (pas par ``tx_hash``, jamais garanti
    disponible côté payeur dans le protocole x402, cf. ``_matches_known_x402``).

    07/23 -- swap detection: both sources (token transfers + native tx) are
    first collected WITHOUT being recorded, grouped by ``tx_hash`` -- a group
    with one entry and one exit on two different assets becomes a single
    ``"swap"`` movement (cf. ``_try_build_swap``) rather than two classic
    movements."""
    await _ensure_table()
    known_tx_hashes = known_tx_hashes or set()
    known_x402_spends = known_x402_spends or []
    address_lower = wallet_address.lower()
    client = get_blockscout_client(chain)
    fresh: list[WalletMovement] = []

    raw_by_tx: dict[str, list[dict]] = {}

    token_result = await client.get_token_transfers(wallet_address, limit=50, max_pages=1)
    if not token_result.available:
        logger.info("agent_wallet_monitor: transferts token indisponibles (%s)", token_result.error)
    else:
        for t in token_result.transfers:
            if not t.tx_hash or await _already_seen(t.tx_hash):
                continue
            direction = "in" if (t.to_address or "").lower() == address_lower else "out"
            counterparty = (t.from_address if direction == "in" else t.to_address) or ""
            raw_by_tx.setdefault(t.tx_hash, []).append({
                "kind": "token", "direction": direction, "asset": t.token_symbol or "token",
                "amount": t.amount or 0.0, "counterparty": counterparty, "timestamp": t.timestamp,
                "token_address": t.token_address, "token_symbol": t.token_symbol,
            })

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
            raw_by_tx.setdefault(tx.tx_hash, []).append({
                "kind": "native", "direction": direction, "asset": "ETH",
                "amount": tx.value_native, "counterparty": counterparty, "timestamp": tx.timestamp,
                "token_address": None, "token_symbol": None,
            })

    for tx_hash, events in raw_by_tx.items():
        swap_movement = _try_build_swap(tx_hash, events, wallet_name=wallet_name)
        if swap_movement is not None:
            if await _record_movement(swap_movement):
                fresh.append(swap_movement)
            continue

        for ev in events:
            lookalike = None
            if ev["kind"] == "token":
                classification, matched_spend = _classify(
                    tx_hash, ev["direction"], known_tx_hashes,
                    counterparty=ev["counterparty"], amount=ev["amount"], timestamp=ev["timestamp"],
                    known_x402_spends=known_x402_spends,
                )
                asset_label = ev["asset"]
                lookalike = _lookalike_target(ev.get("token_symbol"), ev.get("token_address"))
                if lookalike:
                    asset_label = f"{asset_label} (FAUX {lookalike} -- contrat non officiel)"
                    classification = "suspicious_token"
                    matched_spend = None  # un token usurpé n'hérite jamais des infos x402
            else:
                # 22/07 -- pas de known_x402_spends ici : un paiement x402 se règle en
                # USDC (cf. x402_budget), jamais en ETH natif -- classification/enrichissement
                # x402 ne s'applique donc structurellement pas à cette branche, comportement
                # inchangé (matched_spend toujours None, `_classify` reçoit les mêmes
                # arguments qu'avant ce chantier).
                classification, matched_spend = _classify(tx_hash, ev["direction"], known_tx_hashes)
                asset_label = ev["asset"]

            movement = WalletMovement(
                tx_hash=tx_hash, direction=ev["direction"], asset=asset_label,
                amount=ev["amount"], counterparty=ev["counterparty"],
                classification=classification, timestamp=ev["timestamp"],
                wallet_name=wallet_name,
                **_x402_movement_fields(matched_spend),
            )
            if await _record_movement(movement):
                fresh.append(movement)
                if classification == "suspicious_token":
                    # 07/23 -- anti-phishing registry (operator request): remembers
                    # both the fake token's CONTRACT and the WALLET that sent it.
                    # Best-effort, never blocking -- a write failure here must
                    # never lose the movement already logged above.
                    try:
                        await record_phishing_attempt(
                            ev.get("token_address") or "", kind="contract",
                            impersonated_asset=lookalike or "", tx_hash=tx_hash,
                        )
                        await record_phishing_attempt(
                            ev["counterparty"], kind="wallet",
                            impersonated_asset=lookalike or "", tx_hash=tx_hash,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "agent_wallet_monitor: failed to log to phishing_blacklist: %s", exc,
                        )

    return fresh


async def _attach_usd_values(other_tokens: list[dict[str, Any]], *, chain: str) -> None:
    """Ajoute ``price_usd``/``value_usd`` à chaque entrée de ``other_tokens``
    (#204 suite, demande opérateur : "si j'achète du Virtual ou une small cap
    il faut qu'il s'affiche avec la quantité de tokens et sa valeur en $") --
    réutilise ``services/dexscreener.fetch_tokens_batch`` (déjà construit,
    #194, jusqu'à 30 adresses en un seul appel), jamais un nouveau client de
    prix dupliqué. Si plusieurs pools existent pour un même token, retient
    celui de plus forte liquidité (même heuristique que
    ``acp_onchain_scan.py``) -- prix le plus fiable, pas le premier venu.
    Mute chaque entrée en place ; ``price_usd``/``value_usd`` restent ``None``
    si le prix est introuvable, jamais une valeur inventée."""
    for t in other_tokens:
        t["price_usd"] = None
        t["value_usd"] = None

    try:
        from aria_core.services.dexscreener import fetch_tokens_batch

        pairs = await fetch_tokens_batch([t["address"] for t in other_tokens], chain=chain)
    except Exception as exc:  # noqa: BLE001 -- une panne de prix n'efface jamais le solde déjà connu
        logger.warning("agent_wallet_monitor: lecture des prix tokens echouee: %s", exc)
        return

    best_by_address: dict[str, Any] = {}
    for p in pairs:
        addr = (p.base_address or "").lower()
        if not addr or not p.price_usd:
            continue
        current = best_by_address.get(addr)
        if current is None or p.liquidity_usd > current.liquidity_usd:
            best_by_address[addr] = p

    for t in other_tokens:
        pair = best_by_address.get(t["address"].lower())
        if pair is None:
            continue
        t["price_usd"] = pair.price_usd
        t["value_usd"] = t["amount"] * pair.price_usd


async def get_wallet_balance_summary(
    *, wallet_address: str = "", chain: str = "base",
) -> dict[str, Any]:
    """Solde RÉEL courant du wallet agent (#204, suite 16/07 : demande
    opérateur "je veux tous voir meme les futurs token achetés") -- TOUS les
    tokens réellement détenus via l'adaptateur CDP (``list_all_token_balances``,
    même appel déjà vérifié en direct pour USDC, 16/07, #157), ETH natif via
    Blockscout (déjà construit, déjà utilisé ailleurs dans ARIA -- lecture
    seule, aucune dépendance au SDK CDP). Chaque valeur dégrade honnêtement à
    ``None`` si indisponible, jamais un solde inventé -- si le pilote swap un
    jour vers un nouveau token, il apparaît ici automatiquement, sans liste
    à maintenir à la main."""
    wallet_address = wallet_address or MONITORED_WALLET_ADDRESS

    usdc: float | None = None
    other_tokens: list[dict[str, Any]] | None = None
    try:
        from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS, list_all_token_balances

        tokens = await list_all_token_balances(network=chain)
    except Exception as exc:  # noqa: BLE001 -- l'extra cdp-sdk peut manquer, jamais casser l'appelant
        logger.warning("agent_wallet_monitor: lecture des soldes tokens echouee: %s", exc)
        tokens = None

    cdp_eth_fallback: float | None = None
    if tokens is not None:
        usdc = 0.0
        other_tokens = []
        for t in tokens:
            if t["address"].lower() == USDC_BASE_ADDRESS.lower():
                usdc = t["amount"]
            elif (t.get("symbol") or "").strip().upper() == "ETH":
                # CDP list_token_balances renvoie aussi l'ETH natif (confirmé en
                # direct le 16/07, /agentwallet) -- jamais un "autre token" acheté,
                # utilisé comme repli si Blockscout échoue, jamais affiché deux fois.
                cdp_eth_fallback = t["amount"]
            else:
                other_tokens.append(t)
        if other_tokens:
            await _attach_usd_values(other_tokens, chain=chain)

    eth: float | None = None
    try:
        client = get_blockscout_client(chain)
        info = await client.get_address_info(wallet_address)
        if info.available:
            eth = info.balance_native
    except Exception as exc:  # noqa: BLE001 -- une panne Blockscout ne doit jamais casser l'appelant
        logger.warning("agent_wallet_monitor: lecture solde ETH echouee: %s", exc)

    if eth is None and cdp_eth_fallback is not None:
        eth = cdp_eth_fallback

    return {
        "wallet_address": wallet_address, "chain": chain,
        "usdc_usd": usdc, "eth": eth, "other_tokens": other_tokens,
    }


async def run_agent_wallet_monitor_cycle(*, notifier=None) -> dict:
    """One heartbeat tick: reads real movements on ALL monitored wallets
    (``MONITORED_WALLETS``, 07/23 -- extended from the single historical
    pilot wallet), immediately notifies each freshly detected movement (the
    kill-switch cuts the NOTIFICATION, never the reading/logging itself --
    even paused, ARIA must keep a full ledger, cf. operator request "full
    ledger ... so you can verify in real time"). Fail-closed PER WALLET: a
    failure on one wallet (e.g. invalid address, Blockscout unavailable for
    this specific request) never blocks surveillance of the others -- only
    if ALL fail AND no movement was found elsewhere does the cycle report
    "error"."""
    if not agent_wallet_monitor_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import agent_wallet_log, outgoing_pause, x402_budget

    try:
        logged = await agent_wallet_log.list_transactions(limit=500)
    except Exception as exc:  # noqa: BLE001 -- une panne du log ne doit jamais bloquer la surveillance
        logged = []
        logger.warning("agent_wallet_monitor: lecture agent_wallet_log echouee: %s", exc)
    known_tx_hashes = {row["tx_hash"] for row in logged if row.get("status") == "ok" and row.get("tx_hash")}

    try:
        x402_spends = await x402_budget.list_spends()
    except Exception as exc:  # noqa: BLE001 -- une panne du log x402 ne doit jamais bloquer la surveillance
        x402_spends = []
        logger.warning("agent_wallet_monitor: lecture x402_budget echouee: %s", exc)
    known_x402_spends = [s for s in x402_spends if s.get("status") == "ok" and s.get("pay_to")]

    movements: list[WalletMovement] = []
    errors: list[str] = []
    for wallet_name, wallet_address in MONITORED_WALLETS.items():
        try:
            wallet_movements = await check_wallet_activity(
                wallet_address=wallet_address, wallet_name=wallet_name,
                known_tx_hashes=known_tx_hashes, known_x402_spends=known_x402_spends,
            )
        except Exception as exc:  # noqa: BLE001 -- une panne sur CE wallet ne bloque jamais les autres
            errors.append(f"{wallet_name}: {str(exc)[:200]}")
            logger.warning("agent_wallet_monitor: cycle failed for %s: %s", wallet_name, exc)
            continue
        movements.extend(wallet_movements)

    if not movements:
        if errors:
            return {"outcome": "error", "error": "; ".join(errors)[:300]}
        return {"outcome": "nothing_new"}

    # 07/23 -- explicit operator decision: the cycle runs in the background
    # (8h, cf. comment in heartbeat.py) without notifying every movement --
    # the operator already sees raw movements via Zerion, only ARIA does the
    # security classification. ONLY "unexpected_outflow" (outflow not
    # initiated by ARIA, possible sign of a compromised key) deserves an
    # immediate alert -- everything else (deposits, detected phishing, swaps,
    # x402 payments) stays silent, logged but never notified.
    paused = outgoing_pause.is_paused()
    notified = 0
    if notifier and not paused:
        for m in movements:
            if m.classification != "unexpected_outflow":
                continue
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


def basescan_tx_url(tx_hash: str) -> str:
    """URL BaseScan publique pour une transaction -- construction pure (aucun appel
    réseau), même patron que `dexscreener.token_url`."""
    return f"https://basescan.org/tx/{tx_hash}"


def _html_link(text: str, url: str) -> str:
    """Telegram HTML link (``<a href="...">text</a>``) -- ``text`` escaped
    (may contain arbitrary on-chain data, e.g. a tx_hash or a token symbol),
    ``url`` built internally by ``basescan_tx_url``/``dexscreener.token_url``
    (never a URL supplied by a third party)."""
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'


def format_movement_alert(m: WalletMovement) -> str:
    """HTML format (Telegram ``parse_mode="HTML"``, cf.
    ``heartbeat._notify_telegram_html``) -- 07/23, operator request: "every
    hash should link to basescan on click". ANY text field coming from
    external data (token asset/symbol, address, resource/provider) is escaped
    via ``html.escape`` before insertion -- a token symbol controlled by a
    malicious third party could otherwise inject HTML markup into the
    message."""
    icon = {
        "known": "✅", "external_deposit": "💰", "unexpected_outflow": "🚨",
        "suspicious_token": "🎣", "known_x402": "🧾", "swap": "🔄",
    }.get(m.classification, "•")
    label = {
        "known": "Mouvement initié par ARIA (attendu)",
        "external_deposit": "Dépôt externe détecté",
        "unexpected_outflow": "SORTIE NON INITIÉE PAR ARIA — à vérifier immédiatement",
        "suspicious_token": "TOKEN SUSPECT — imite un actif suivi, PAS un dépôt réel, ne jamais interagir avec ce contrat",
        "known_x402": "Paiement x402 initié par ARIA (attendu)",
        "swap": "Swap détecté",
    }.get(m.classification, m.classification)
    # 07/23 -- multi-wallet: name of the wallet involved at the top of the
    # alert, empty -> "Wallet agent" (unchanged historical behavior for any
    # movement built without this field).
    wallet_label = html.escape(m.wallet_name or "Wallet agent")
    tx_link = _html_link(m.tx_hash, basescan_tx_url(m.tx_hash))
    if m.classification == "swap":
        lines = [
            f"{icon} {wallet_label} — {html.escape(label)}",
            f"Swap : {m.amount_out} {html.escape(m.asset_out)} -> {m.amount} {html.escape(m.asset)}",
            f"Contrepartie : {html.escape(_label_address(m.counterparty))}",
            f"Tx : {tx_link}",
        ]
        return "\n".join(lines)
    direction_label = "Entrée" if m.direction == "in" else "Sortie"
    counterparty_label = "De" if m.direction == "in" else "Vers"
    lines = [
        f"{icon} {wallet_label} — {html.escape(label)}",
        f"{direction_label} : {m.amount} {html.escape(m.asset)}",
        f"{counterparty_label} : {html.escape(_label_address(m.counterparty))}",
        f"Tx : {tx_link}",
    ]
    # 22/07 -- enrichissement QUE pour un paiement x402 dont le spend matché a pu
    # être rattaché à un token précis (m.contract) : un paiement x402 générique
    # (ex. recherche web) n'a pas de contract, dégradation douce = rien ajouté du
    # tout, jamais une ligne vide ou "N/A".
    # 07/23 -- separate "BaseScan :" line removed (redundant: the tx_hash
    # above is now itself clickable).
    if m.classification == "known_x402" and m.contract:
        # 22/07 (revue croisée) -- token_symbol affiché à côté de l'adresse quand connu
        # (ex. "GEM (0xdead...)") plutôt que laissé mort après avoir été capturé/propagé.
        token_line = (
            f"Token : {html.escape(m.token_symbol)} ({html.escape(m.contract)})"
            if m.token_symbol else f"Token : {html.escape(m.contract)}"
        )
        lines.append(token_line)
        lines.append(f"DexScreener : {_html_link(m.contract, token_url(m.contract, chain='base'))}")
        # 22/07 (revue croisée) -- garde explicite : x402_budget.record_spend() exige
        # `resource` en paramètre obligatoire (jamais vide en pratique), mais sans cette
        # garde une donnée corrompue produirait une ligne "Raison : " à moitié vide,
        # contraire à la doctrine "jamais une ligne vide" déjà appliquée juste au-dessus.
        if m.resource:
            resource = html.escape(m.resource)
            provider = html.escape(m.provider) if m.provider else ""
            lines.append(f"Raison : {resource} via {provider}" if provider else f"Raison : {resource}")
    return "\n".join(lines)


def format_wallet_balance_summary(summary: dict[str, Any]) -> str:
    """Formate le résultat de ``get_wallet_balance_summary`` pour Telegram
    (#204, suite : "je veux tous voir meme les futurs token achetés") --
    chaque solde indisponible s'affiche honnêtement comme tel, jamais un 0
    silencieux qui laisserait croire à un wallet vide. Tout autre token
    détenu (ex. après un futur swap du pilote) s'affiche automatiquement,
    sans liste à maintenir à la main."""
    usdc = summary.get("usdc_usd")
    eth = summary.get("eth")
    other_tokens = summary.get("other_tokens")
    usdc_line = f"{usdc:.4f} USDC" if usdc is not None else "indisponible (SDK/identifiants CDP absents)"
    eth_line = f"{eth:.6f} ETH" if eth is not None else "indisponible (Blockscout hors service)"
    lines = [
        f"💼 Wallet agent CDP ({summary.get('chain', 'base')})",
        f"Adresse : {summary.get('wallet_address', '?')}",
        f"USDC : {usdc_line}",
        f"ETH (gas) : {eth_line}",
    ]
    if other_tokens is None:
        lines.append("Autres tokens : indisponible (SDK/identifiants CDP absents)")
    elif other_tokens:
        lines.append("Autres tokens :")
        for t in other_tokens:
            value_usd = t.get("value_usd")
            value_label = f"(~{value_usd:,.2f} $)" if value_usd is not None else "(prix indisponible)"
            lines.append(f"  - {t['amount']} {t['symbol']} {value_label}")
    else:
        lines.append("Autres tokens : aucun")
    return "\n".join(lines)
