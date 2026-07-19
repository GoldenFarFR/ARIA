"""Sealed Ledger -- registre de trades scellé, cryptographiquement chaîné, append-only
(19/07, proposition ARIA, verrouillée après plusieurs tours de revue croisée avec l'opérateur
et une critique externe -- transcript complet dans la conversation Telegram Aria du 19/07).

But : prouver le track-record du paper-trading SANS jamais demander qu'on fasse confiance
à ARIA sur parole. Chaque décision est scellée AVANT de connaître le résultat (timestamp
serveur, jamais éditable par l'appelant), chaque sortie référence son entrée, le PnL est
TOUJOURS recalculé sur les prix d'exécution réels (VWAP des fills), jamais sur le prix de
décision. Un tiers qui lit le registre exporté peut revérifier toute la chaîne de hash sans
avoir besoin de faire confiance à qui que ce soit -- voir ``verify_chain()``, une fonction
PURE qui ne dépend d'aucun accès à cette base de données.

v0 ISOLÉ (décision opérateur, 19/07) : ce module tourne en autonomie, rempli à la main sur
quelques trades de test, pour valider le sceau + l'export GitHub + la re-vérification tierce
AVANT de le câbler sur le vrai moteur ``paper_trader.py`` -- exactement le vote d'ARIA
("sinon tu débugges la crypto et l'intégration en même temps").

Écart assumé vs la spec figée dans la conversation : stockage SQLite ici, pas Postgres sur
Render -- aucune base Postgres n'existe nulle part dans ce stack aujourd'hui (grep exhaustif
avant de coder, aucun DATABASE_URL configuré) et provisionner un nouveau service externe est
sa propre décision d'infra, pas quelque chose à glisser dans ce chantier sans validation
séparée. La garantie d'intégrité du design ne dépend PAS du moteur de stockage -- elle repose
entièrement sur le chaînage cryptographique (SHA-256, JSON canonique, prev_hash), donc SQLite
préserve la propriété centrale à 100% pour cette phase de preuve isolée. Bascule vers Postgres
= une migration de stockage pure le jour où on câble le vrai paper-trading, pas une réécriture
du design.

Autre écart honnête : pas de commit GitHub signé GPG (aucune infra de signing n'existe sur ce
VPS -- créer des clés/de la config de signature de commits est un changement de posture de
sécurité qui mérite sa propre validation opérateur explicite, jamais fait à la volée ici).
L'intégrité du registre ne repose de toute façon pas sur la signature Git (acté explicitement
dans la conversation : "Ta garantie d'intégrité ne doit jamais reposer sur la branch
protection de GitHub... elle repose sur le chaînage cryptographique").
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Hash du "génesis" -- prev_hash du tout premier événement jamais écrit dans la chaîne.
# Valeur fixe et publique (pas un secret) : 64 zéros, la même convention que d'autres
# systèmes à chaînage (ex. le bloc génésis Bitcoin référence un hash de zéros).
GENESIS_HASH = "0" * 64

EVENT_TYPES = (
    "ENTRY_DECISION",
    "ENTRY_FILL",
    "EXIT_DECISION",
    "EXIT_FILL",
    "EXIT_ABANDONED",
)

FILL_STATUSES = ("PARTIAL", "FINAL")


class ChainIntegrityError(RuntimeError):
    """Levée quand un événement ne peut pas être chaîné en toute sécurité --
    jamais attrapée silencieusement, jamais un fallback qui écrit quand même."""


@dataclass(frozen=True)
class LedgerEvent:
    """Représentation immuable d'un événement déjà scellé. ``payload`` contient les
    champs spécifiques au type d'événement (voir docstrings des fonctions record_*)."""

    event_id: str
    trade_id: str
    event_type: str
    sequence: int
    timestamp_utc: str
    prev_hash: str
    hash: str
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "trade_id": self.trade_id,
            "event_type": self.event_type,
            "sequence": self.sequence,
            "timestamp_utc": self.timestamp_utc,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "payload": self.payload,
        }


def canonical_json(obj) -> str:
    """Sérialisation canonique : clés triées, aucun espace. Déterministe -- deux objets
    Python avec les mêmes clés/valeurs produisent TOUJOURS la même chaîne, quel que soit
    l'ordre d'insertion des clés côté appelant. C'est la propriété qui rend le hash
    reproductible par un tiers (bug identifié explicitement dans la conversation de design :
    un JSON non-canonique donne un hash différent selon l'ordre des clés)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _compute_event_hash(
    *,
    event_id: str,
    trade_id: str,
    event_type: str,
    sequence: int,
    timestamp_utc: str,
    prev_hash: str,
    payload: dict,
) -> str:
    """Le hash porte sur TOUS les champs d'identité/temps/chaînage + le payload complet
    -- jamais sur le hash lui-même (qui n'existe pas encore au moment du calcul)."""
    hashable = {
        "event_id": event_id,
        "trade_id": trade_id,
        "event_type": event_type,
        "sequence": sequence,
        "timestamp_utc": timestamp_utc,
        "prev_hash": prev_hash,
        "payload": payload,
    }
    return hashlib.sha256(canonical_json(hashable).encode("utf-8")).hexdigest()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sealed_ledger_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                trade_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                sequence INTEGER NOT NULL UNIQUE,
                timestamp_utc TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL
            )
            """
        )
        # Garde-fou DUR (pas seulement l'absence de fonction UPDATE/DELETE côté Python,
        # comme le reste du codebase -- ex. agent_wallet_log.py) : un trigger SQLite qui
        # refuse ABSOLUMENT toute tentative de réécriture, même par un futur appelant qui
        # se tromperait de fonction. Demandé explicitement dans la spec ("contrainte
        # trigger anti-UPDATE/DELETE").
        await db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS sealed_ledger_no_update
            BEFORE UPDATE ON sealed_ledger_events
            BEGIN
                SELECT RAISE(ABORT, 'sealed_ledger_events est append-only : UPDATE interdit');
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS sealed_ledger_no_delete
            BEFORE DELETE ON sealed_ledger_events
            BEGIN
                SELECT RAISE(ABORT, 'sealed_ledger_events est append-only : DELETE interdit');
            END
            """
        )
        await db.commit()


async def _last_event() -> tuple[int, str] | None:
    """(sequence, hash) du dernier événement écrit, ou None si la chaîne est vide."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT sequence, hash FROM sealed_ledger_events ORDER BY sequence DESC LIMIT 1"
            )
        ).fetchone()
    return (row[0], row[1]) if row else None


async def _append_event(*, trade_id: str, event_type: str, payload: dict) -> LedgerEvent:
    """Cœur du chaînage : timestamp fixé ICI (source serveur, jamais passé par l'appelant
    -- personne ne peut antidater une décision), sequence assignée de façon strictement
    croissante, hash calculé en chaînant sur le hash précédent. Une seule connexion, un
    seul INSERT -- pas de fenêtre où deux écritures concurrentes pourraient calculer le
    même prev_hash (SQLite sérialise les écritures sur une connexion, et aiosqlite ouvre
    une connexion neuve par appel ici, donc le risque réel serait un vrai run concurrent
    multi-process -- hors scope v0 isolé, rempli à la main séquentiellement)."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type inconnu : {event_type!r}")

    await _ensure_table()
    prev = await _last_event()
    prev_hash = prev[1] if prev else GENESIS_HASH
    sequence = (prev[0] + 1) if prev else 1

    event_id = str(uuid4())
    timestamp_utc = datetime.now(timezone.utc).isoformat()
    event_hash = _compute_event_hash(
        event_id=event_id,
        trade_id=trade_id,
        event_type=event_type,
        sequence=sequence,
        timestamp_utc=timestamp_utc,
        prev_hash=prev_hash,
        payload=payload,
    )

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """
                INSERT INTO sealed_ledger_events
                    (event_id, trade_id, event_type, sequence, timestamp_utc,
                     prev_hash, hash, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, trade_id, event_type, sequence, timestamp_utc,
                    prev_hash, event_hash, canonical_json(payload),
                ),
            )
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            # sequence/hash déjà pris = une écriture concurrente a gagné la course --
            # fail loud plutôt que d'écrire une chaîne divergente en silence.
            raise ChainIntegrityError(
                f"Conflit d'écriture sur la chaîne (sequence={sequence}) -- "
                f"une autre écriture concurrente a eu lieu. Réessayer."
            ) from exc

    return LedgerEvent(
        event_id=event_id,
        trade_id=trade_id,
        event_type=event_type,
        sequence=sequence,
        timestamp_utc=timestamp_utc,
        prev_hash=prev_hash,
        hash=event_hash,
        payload=payload,
    )


# ── Écriture -- un point d'entrée par type d'événement, jamais de fonction générique ────
# (un appelant ne peut pas se tromper de champs obligatoires : chaque fonction déclare
# exactement ce que SON type d'événement exige, conforme à la spec figée).

async def record_entry_decision(
    *,
    trade_id: str,
    token_address: str,
    chain: str,
    decision_price_usd: float,
    target_size_usd: float,
    thesis: str,
    conviction: int,
    pipeline: str,
    source_price: str,
    mode: str = "SIMULATED",
) -> LedgerEvent:
    """Intention d'achat, scellée AVANT de connaître le résultat. ``decision_price_usd``
    = mid-price snapshoté (DexScreener en v0) au moment T -- jamais retouché après coup."""
    payload = {
        "token_address": token_address,
        "chain": chain,
        "action": "BUY",
        "decision_price_usd": decision_price_usd,
        "target_size_usd": target_size_usd,
        "thesis": thesis,
        "conviction": conviction,
        "pipeline": pipeline,
        "source_price": source_price,
        "mode": mode,
    }
    return await _append_event(trade_id=trade_id, event_type="ENTRY_DECISION", payload=payload)


async def record_entry_fill(
    *,
    trade_id: str,
    entry_decision_hash: str,
    execution_price_usd: float,
    filled_quantity: float,
    tx_hash: str = "",
    gas_paid_usd: float = 0.0,
    fill_status: str = "FINAL",
    mode: str = "SIMULATED",
) -> LedgerEvent:
    """Réalité de l'exécution d'entrée, liée à sa décision via ``entry_decision_hash``.
    En mode SIMULATED, ``execution_price_usd`` == le ``decision_price_usd`` de l'entrée
    (aucun slippage possible sur un remplissage fictif) -- l'appelant est responsable de
    passer la même valeur, cette fonction ne le devine pas pour éviter une fausse
    certitude sur ce que l'appelant a réellement voulu enregistrer."""
    if fill_status not in FILL_STATUSES:
        raise ValueError(f"fill_status invalide : {fill_status!r}")
    payload = {
        "entry_decision_hash": entry_decision_hash,
        "execution_price_usd": execution_price_usd,
        "filled_quantity": filled_quantity,
        "tx_hash": tx_hash,
        "gas_paid_usd": gas_paid_usd,
        "fill_status": fill_status,
        "mode": mode,
    }
    return await _append_event(trade_id=trade_id, event_type="ENTRY_FILL", payload=payload)


async def record_exit_decision(
    *,
    trade_id: str,
    entry_decision_hash: str,
    decision_price_usd: float,
    target_quantity: float,
    exit_reason: str,
) -> LedgerEvent:
    """Intention de sortie -- ``decision_price_usd`` est le mid-price snapshoté au moment
    de CETTE décision de sortie (jamais celui de l'entrée), scellé au même titre."""
    payload = {
        "entry_decision_hash": entry_decision_hash,
        "decision_price_usd": decision_price_usd,
        "target_quantity": target_quantity,
        "exit_reason": exit_reason,
    }
    return await _append_event(trade_id=trade_id, event_type="EXIT_DECISION", payload=payload)


async def record_exit_fill(
    *,
    trade_id: str,
    exit_decision_hash: str,
    sequence_index: int,
    execution_price_usd: float,
    filled_quantity: float,
    fill_status: str,
    tx_hash: str = "",
    gas_paid_usd: float = 0.0,
    mode: str = "SIMULATED",
) -> LedgerEvent:
    """Un EXIT_DECISION peut engendrer 1..N EXIT_FILL (liquidité fragmentée en réel).
    ``sequence_index`` = position de CE fill dans sa propre séquence de sortie (1, 2, 3…)
    -- distinct de ``sequence`` (position globale dans toute la chaîne)."""
    if fill_status not in FILL_STATUSES:
        raise ValueError(f"fill_status invalide : {fill_status!r}")
    if sequence_index < 1:
        raise ValueError("sequence_index doit démarrer à 1")
    payload = {
        "exit_decision_hash": exit_decision_hash,
        "sequence_index": sequence_index,
        "execution_price_usd": execution_price_usd,
        "filled_quantity": filled_quantity,
        "fill_status": fill_status,
        "tx_hash": tx_hash,
        "gas_paid_usd": gas_paid_usd,
        "mode": mode,
    }
    return await _append_event(trade_id=trade_id, event_type="EXIT_FILL", payload=payload)


async def record_exit_abandoned(
    *,
    trade_id: str,
    exit_decision_hash: str,
    remaining_quantity: float,
    reason: str,
) -> LedgerEvent:
    """Marqueur terminal de sécurité : la liquidité a disparu avant que la sortie ne se
    complète. Le reliquat ``remaining_quantity`` est figé comme jamais vendu -- ne JAMAIS
    le valoriser au mid-price dans un calcul de PnL, sinon on réintroduit exactement la
    fiction que ce registre existe pour tuer."""
    payload = {
        "exit_decision_hash": exit_decision_hash,
        "remaining_quantity": remaining_quantity,
        "reason": reason,
    }
    return await _append_event(trade_id=trade_id, event_type="EXIT_ABANDONED", payload=payload)


# ── Lecture ───────────────────────────────────────────────────────────────────────────

async def list_events(*, trade_id: str | None = None) -> list[dict]:
    """Tous les événements, triés par séquence globale. Filtrable par trade_id."""
    await _ensure_table()
    query = "SELECT event_id, trade_id, event_type, sequence, timestamp_utc, prev_hash, hash, payload_json FROM sealed_ledger_events"
    params: tuple = ()
    if trade_id:
        query += " WHERE trade_id = ?"
        params = (trade_id,)
    query += " ORDER BY sequence ASC"
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(query, params)).fetchall()
    return [
        {
            "event_id": r[0], "trade_id": r[1], "event_type": r[2], "sequence": r[3],
            "timestamp_utc": r[4], "prev_hash": r[5], "hash": r[6],
            "payload": json.loads(r[7]),
        }
        for r in rows
    ]


# ── VWAP / slippage -- le PnL ne se calcule JAMAIS sur un decision_price ───────────────

def compute_vwap(fills: list[dict]) -> float:
    """VWAP (Volume Weighted Average Price) sur une liste de fills, chacun avec
    ``execution_price_usd`` et ``filled_quantity``. 0.0 si aucun fill (quantité nulle) --
    jamais une division par zéro qui remonte comme exception à un appelant qui ne
    s'y attend pas."""
    total_qty = sum(f["filled_quantity"] for f in fills)
    if total_qty <= 0:
        return 0.0
    return sum(f["execution_price_usd"] * f["filled_quantity"] for f in fills) / total_qty


def compute_slippage_bps(*, vwap_fills: float, decision_price_usd: float) -> float | None:
    """Slippage BPS = (VWAP_fills - decision_price) / decision_price * 10000. Signe
    conservé -- un slippage négatif à la sortie EST une perte, jamais masqué. ``None``
    (pas 0.0) si decision_price_usd <= 0 -- une valeur indisponible n'est jamais confondue
    avec un slippage nul réel."""
    if decision_price_usd <= 0:
        return None
    return (vwap_fills - decision_price_usd) / decision_price_usd * 10000


async def compute_trade_pnl(trade_id: str) -> dict:
    """Reconstruit le cycle de vie complet d'un trade depuis ses événements scellés et
    calcule PnL + slippage entrée/sortie. Le PnL n'est JAMAIS lu depuis un champ stocké --
    toujours recalculé depuis les VWAP des fills, à chaque appel. ``status`` :
    "OPEN" (pas encore d'EXIT_DECISION), "PARTIAL" (sortie en cours), "ABANDONED"
    (reliquat figé), "CLOSED" (quantité cible entièrement sortie)."""
    events = await list_events(trade_id=trade_id)
    entry_decisions = [e for e in events if e["event_type"] == "ENTRY_DECISION"]
    entry_fills = [e for e in events if e["event_type"] == "ENTRY_FILL"]
    exit_decisions = [e for e in events if e["event_type"] == "EXIT_DECISION"]
    exit_fills = [e for e in events if e["event_type"] == "EXIT_FILL"]
    exit_abandoned = [e for e in events if e["event_type"] == "EXIT_ABANDONED"]

    if not entry_decisions:
        return {"trade_id": trade_id, "status": "UNKNOWN", "events": len(events)}

    entry_decision_price = entry_decisions[0]["payload"]["decision_price_usd"]
    entry_vwap = compute_vwap([e["payload"] for e in entry_fills])
    entry_slippage_bps = compute_slippage_bps(
        vwap_fills=entry_vwap, decision_price_usd=entry_decision_price,
    )

    result = {
        "trade_id": trade_id,
        "token_address": entry_decisions[0]["payload"]["token_address"],
        "entry_decision_price_usd": entry_decision_price,
        "entry_vwap_usd": entry_vwap,
        "entry_slippage_bps": entry_slippage_bps,
        "status": "OPEN",
    }

    if not exit_decisions:
        return result

    exit_decision_price = exit_decisions[-1]["payload"]["decision_price_usd"]
    target_quantity = exit_decisions[-1]["payload"]["target_quantity"]
    filled_quantity = sum(e["payload"]["filled_quantity"] for e in exit_fills)
    exit_vwap = compute_vwap([e["payload"] for e in exit_fills])
    exit_slippage_bps = compute_slippage_bps(
        vwap_fills=exit_vwap, decision_price_usd=exit_decision_price,
    )

    result.update({
        "exit_decision_price_usd": exit_decision_price,
        "exit_vwap_usd": exit_vwap,
        "exit_slippage_bps": exit_slippage_bps,
        "target_quantity": target_quantity,
        "filled_quantity": filled_quantity,
    })

    if exit_abandoned:
        # Le reliquat n'est JAMAIS valorisé -- PnL final uniquement sur la portion
        # réellement sortie, conforme à la règle actée dans la conversation de design.
        result["status"] = "ABANDONED"
        result["abandoned_quantity"] = exit_abandoned[-1]["payload"]["remaining_quantity"]
    elif filled_quantity >= target_quantity > 0:
        result["status"] = "CLOSED"
    else:
        result["status"] = "PARTIAL"

    if filled_quantity > 0 and entry_vwap > 0:
        result["pnl_usd"] = (exit_vwap - entry_vwap) * filled_quantity
        result["pnl_pct"] = (exit_vwap - entry_vwap) / entry_vwap * 100

    return result


# ── Re-vérification tierce -- fonction PURE, aucun accès DB ────────────────────────────

def verify_chain(events: list[dict]) -> tuple[bool, str | None]:
    """LA preuve : n'importe qui peut appeler cette fonction avec une liste d'événements
    bruts (lus depuis le JSONL exporté sur GitHub, ou depuis n'importe quelle copie de la
    base) SANS accès à ce module ni à aucune base de données, et confirmer que la chaîne
    est intacte. Recalcule chaque hash depuis les champs bruts (jamais depuis le hash déjà
    stocké) et vérifie le chaînage prev_hash de bout en bout.

    ``events`` doit être trié par ``sequence`` croissante (la fonction le trie elle-même
    par sécurité, mais ne fait AUCUNE hypothèse sur l'ordre d'entrée).

    Retourne (True, None) si la chaîne est intacte, sinon (False, "raison + event_id").
    """
    if not events:
        return True, None

    ordered = sorted(events, key=lambda e: e["sequence"])

    expected_prev_hash = GENESIS_HASH
    expected_sequence = 1
    for ev in ordered:
        if ev["sequence"] != expected_sequence:
            return False, (
                f"séquence rompue à event_id={ev['event_id']} : "
                f"attendu {expected_sequence}, trouvé {ev['sequence']}"
            )
        if ev["prev_hash"] != expected_prev_hash:
            return False, (
                f"chaînage rompu à event_id={ev['event_id']} (sequence={ev['sequence']}) : "
                f"prev_hash ne correspond pas au hash de l'événement précédent"
            )
        recomputed = _compute_event_hash(
            event_id=ev["event_id"],
            trade_id=ev["trade_id"],
            event_type=ev["event_type"],
            sequence=ev["sequence"],
            timestamp_utc=ev["timestamp_utc"],
            prev_hash=ev["prev_hash"],
            payload=ev["payload"],
        )
        if recomputed != ev["hash"]:
            return False, (
                f"hash falsifié à event_id={ev['event_id']} (sequence={ev['sequence']}) : "
                f"le contenu ne correspond plus au hash stocké -- ligne altérée après coup"
            )
        expected_prev_hash = ev["hash"]
        expected_sequence += 1

    return True, None
