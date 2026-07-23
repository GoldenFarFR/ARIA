"""Sealed Ledger -- sealed, cryptographically chained, append-only trade
ledger (07/19, proposed by ARIA, locked in after several rounds of cross-review
with the operator and an external critique -- full transcript in the Aria
Telegram conversation of 07/19).

Goal: prove the paper-trading track record WITHOUT ever asking anyone to take
ARIA's word on trust. Every decision is sealed BEFORE knowing the outcome
(server timestamp, never editable by the caller), every exit references its
entry, PnL is ALWAYS recomputed from the real execution prices (VWAP of the
fills), never from the decision price. A third party reading the exported
ledger can re-verify the entire hash chain without needing to trust anyone --
see ``verify_chain()``, a PURE function that doesn't depend on any access to
this database.

ISOLATED v0 (operator decision, 07/19): this module runs standalone, filled
by hand on a few test trades, to validate the seal + GitHub export + third-party
re-verification BEFORE wiring it to the real ``paper_trader.py`` engine --
exactly ARIA's own suggestion ("otherwise you're debugging the crypto and the
integration at the same time").

Deliberate deviation from the spec locked in the conversation: SQLite storage
here, not Postgres on Render -- no Postgres database exists anywhere in this
stack today (exhaustive grep before coding, no DATABASE_URL configured) and
provisioning a new external service is its own infra decision, not something
to slip into this project without separate validation. The design's integrity
guarantee does NOT depend on the storage engine -- it rests entirely on the
cryptographic chaining (SHA-256, canonical JSON, prev_hash), so SQLite
preserves the core property 100% for this isolated proof phase. Switching to
Postgres = a pure storage migration the day the real paper-trading is wired
in, not a design rewrite.

Another honest deviation: no GPG-signed GitHub commit (no signing
infrastructure exists on this VPS -- creating keys/commit-signature config is
a security posture change that deserves its own explicit operator validation,
never done on the fly here). The ledger's integrity doesn't rest on Git
signing anyway (explicitly stated in the conversation: "Your integrity
guarantee must never rest on GitHub's branch protection... it rests on the
cryptographic chaining").
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

# "Genesis" hash -- prev_hash of the very first event ever written to the
# chain. Fixed, public value (not a secret): 64 zeros, the same convention as
# other chained systems (e.g. the Bitcoin genesis block references a hash of zeros).
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
    """Raised when an event can't be safely chained -- never caught silently,
    never a fallback that writes anyway."""


@dataclass(frozen=True)
class LedgerEvent:
    """Immutable representation of an already-sealed event. ``payload`` holds
    the fields specific to the event type (see the record_* functions' docstrings)."""

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
    """Canonical serialization: sorted keys, no whitespace. Deterministic --
    two Python objects with the same keys/values ALWAYS produce the same
    string, regardless of key insertion order on the caller's side. This is
    the property that makes the hash reproducible by a third party (bug
    explicitly identified in the design conversation: non-canonical JSON gives
    a different hash depending on key order)."""
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
    """The hash covers ALL identity/time/chaining fields + the full payload --
    never the hash itself (which doesn't exist yet at computation time)."""
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
        # HARD guardrail (not just the absence of an UPDATE/DELETE function on
        # the Python side, like the rest of the codebase -- e.g.
        # agent_wallet_log.py): a SQLite trigger that ABSOLUTELY refuses any
        # rewrite attempt, even by a future caller that got the wrong
        # function. Explicitly requested in the spec ("anti-UPDATE/DELETE
        # trigger constraint").
        await db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS sealed_ledger_no_update
            BEFORE UPDATE ON sealed_ledger_events
            BEGIN
                SELECT RAISE(ABORT, 'sealed_ledger_events is append-only: UPDATE forbidden');
            END
            """
        )
        await db.execute(
            """
            CREATE TRIGGER IF NOT EXISTS sealed_ledger_no_delete
            BEFORE DELETE ON sealed_ledger_events
            BEGIN
                SELECT RAISE(ABORT, 'sealed_ledger_events is append-only: DELETE forbidden');
            END
            """
        )
        await db.commit()


async def _last_event() -> tuple[int, str] | None:
    """(sequence, hash) of the last event written, or None if the chain is empty."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT sequence, hash FROM sealed_ledger_events ORDER BY sequence DESC LIMIT 1"
            )
        ).fetchone()
    return (row[0], row[1]) if row else None


async def _append_event(*, trade_id: str, event_type: str, payload: dict) -> LedgerEvent:
    """Core of the chaining: timestamp set HERE (server source, never passed
    by the caller -- no one can backdate a decision), sequence assigned
    strictly increasing, hash computed by chaining on the previous hash. One
    connection, one INSERT -- no window where two concurrent writes could
    compute the same prev_hash (SQLite serializes writes on one connection,
    and aiosqlite opens a fresh connection per call here, so the real risk
    would be an actual multi-process concurrent run -- out of scope for the
    isolated v0, filled by hand sequentially)."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type: {event_type!r}")

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
            # sequence/hash already taken = a concurrent write won the race --
            # fail loud rather than silently write a diverging chain.
            raise ChainIntegrityError(
                f"Write conflict on the chain (sequence={sequence}) -- "
                f"another concurrent write occurred. Retry."
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


# ── Writing -- one entry point per event type, never a generic function ────
# (a caller can't get the required fields wrong: each function declares
# exactly what ITS event type requires, matching the locked-in spec).

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
    """Buy intent, sealed BEFORE knowing the outcome. ``decision_price_usd``
    = mid-price snapshot (DexScreener in v0) at time T -- never touched up afterward."""
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
    """The reality of entry execution, linked to its decision via
    ``entry_decision_hash``. In SIMULATED mode, ``execution_price_usd`` ==
    the entry's ``decision_price_usd`` (no slippage possible on a fictitious
    fill) -- the caller is responsible for passing the same value, this
    function doesn't guess it to avoid a false certainty about what the
    caller actually meant to record."""
    if fill_status not in FILL_STATUSES:
        raise ValueError(f"invalid fill_status: {fill_status!r}")
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
    """Exit intent -- ``decision_price_usd`` is the mid-price snapshot at the
    time of THIS exit decision (never the entry's), sealed the same way."""
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
    """An EXIT_DECISION can spawn 1..N EXIT_FILL (fragmented liquidity in
    reality). ``sequence_index`` = this fill's position in its own exit
    sequence (1, 2, 3...) -- distinct from ``sequence`` (global position in
    the whole chain)."""
    if fill_status not in FILL_STATUSES:
        raise ValueError(f"invalid fill_status: {fill_status!r}")
    if sequence_index < 1:
        raise ValueError("sequence_index must start at 1")
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
    """Terminal safety marker: liquidity disappeared before the exit could
    complete. The ``remaining_quantity`` remainder is frozen as never sold --
    NEVER value it at the mid-price in a PnL computation, or we reintroduce
    exactly the fiction this ledger exists to kill."""
    payload = {
        "exit_decision_hash": exit_decision_hash,
        "remaining_quantity": remaining_quantity,
        "reason": reason,
    }
    return await _append_event(trade_id=trade_id, event_type="EXIT_ABANDONED", payload=payload)


# ── Reading ───────────────────────────────────────────────────────────────────────────

async def list_events(*, trade_id: str | None = None) -> list[dict]:
    """All events, sorted by global sequence. Filterable by trade_id."""
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


# ── VWAP / slippage -- PnL is NEVER computed from a decision_price ───────────────

def compute_vwap(fills: list[dict]) -> float:
    """VWAP (Volume Weighted Average Price) over a list of fills, each with
    ``execution_price_usd`` and ``filled_quantity``. 0.0 if no fill (zero
    quantity) -- never a division by zero bubbling up as an exception to an
    unsuspecting caller."""
    total_qty = sum(f["filled_quantity"] for f in fills)
    if total_qty <= 0:
        return 0.0
    return sum(f["execution_price_usd"] * f["filled_quantity"] for f in fills) / total_qty


def compute_slippage_bps(*, vwap_fills: float, decision_price_usd: float) -> float | None:
    """Slippage BPS = (VWAP_fills - decision_price) / decision_price * 10000.
    Sign preserved -- negative slippage on exit IS a loss, never hidden.
    ``None`` (not 0.0) if decision_price_usd <= 0 -- an unavailable value is
    never confused with a real zero slippage."""
    if decision_price_usd <= 0:
        return None
    return (vwap_fills - decision_price_usd) / decision_price_usd * 10000


async def compute_trade_pnl(trade_id: str) -> dict:
    """Reconstructs a trade's full lifecycle from its sealed events and
    computes PnL + entry/exit slippage. PnL is NEVER read from a stored field
    -- always recomputed from the fills' VWAP, on every call. ``status``:
    "OPEN" (no EXIT_DECISION yet), "PARTIAL" (exit in progress), "ABANDONED"
    (remainder frozen), "CLOSED" (target quantity fully exited)."""
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
        # The remainder is NEVER valued -- final PnL only on the portion
        # actually exited, consistent with the rule agreed in the design conversation.
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


# ── Third-party re-verification -- PURE function, no DB access ────────────────────────────

def verify_chain(events: list[dict]) -> tuple[bool, str | None]:
    """THE proof: anyone can call this function with a list of raw events
    (read from the JSONL exported to GitHub, or from any copy of the
    database) WITHOUT access to this module or any database, and confirm the
    chain is intact. Recomputes each hash from the raw fields (never from the
    already-stored hash) and verifies the prev_hash chaining end to end.

    ``events`` must be sorted by increasing ``sequence`` (the function sorts
    it itself for safety, but makes NO assumption about the input order).

    Returns (True, None) if the chain is intact, otherwise (False, "reason + event_id").
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
