"""Item #188 (29/07) -- timestamped audit trail for the critical capital-
adjacent gates (x402 seller, agent-wallet pilot, agent-wallet transfer).

Born directly from the real security incident on 29/07 (operator-run stress
test on ``aria-wallet-X402-EVM``, a stray private key used from MetaMask):
diagnosing it required knowing what state each gate was in AT THE TIME of
the movement, and none of that was ever persisted -- ``seller_enabled()``/
``agent_wallet_pilot_enabled()``/``agent_wallet_transfer_enabled()`` only
ever read ``os.environ`` live, with zero trace of their value a moment
later, let alone hours before. This module closes exactly that gap, without
touching the gates themselves (never modifies permission_mode/wallet_guard/
config.toml -- purely additive observability).

Append-only, but transition-scoped (not a firehose): ``record_gate_
transition_if_changed`` only writes a new row when the gate's value
genuinely CHANGED since the last recorded state for that gate name -- a gate
checked every 30min for weeks that never flips writes exactly ONE row
(its initial state). Reuses the in-memory-then-DB comparison pattern already
established elsewhere in this codebase (e.g. ``risk_guard``'s newly_
triggered_hard/soft) rather than hitting SQLite on every single check."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# In-memory cache of the last known state per gate -- avoids a DB read on
# every single check (this module is consulted from a 30min heartbeat cycle,
# not a hot path, but the same doctrine applies: never a network/disk round
# trip when a value already known in-process answers the question).
_last_known_state: dict[str, bool] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS gate_state_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gate_name TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_gate_state_log_gate_name "
            "ON gate_state_log (gate_name, recorded_at)"
        )
        await db.commit()


async def record_gate_transition_if_changed(gate_name: str, enabled: bool) -> None:
    """Persists a new row ONLY when ``enabled`` differs from the last known
    state for this gate (in-memory cache first, falls back to the DB's own
    last row on a fresh process so a restart never re-logs a spurious
    "transition" for a gate that never actually changed). Best-effort, same
    doctrine as every other passive log in this codebase: a telemetry write
    failure must never break the caller's own cycle."""
    try:
        if gate_name not in _last_known_state:
            await _ensure_table()
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT enabled FROM gate_state_log WHERE gate_name = ? "
                    "ORDER BY recorded_at DESC LIMIT 1",
                    (gate_name,),
                ) as cur:
                    row = await cur.fetchone()
            if row is not None:
                _last_known_state[gate_name] = bool(row[0])

        if _last_known_state.get(gate_name) == enabled:
            return

        _last_known_state[gate_name] = enabled
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO gate_state_log (gate_name, enabled, recorded_at) VALUES (?, ?, ?)",
                (gate_name, int(enabled), _now()),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- best-effort telemetry, never blocking
        pass


async def state_at(gate_name: str, at: datetime) -> bool | None:
    """Reconstructs what ``gate_name`` was set to at a given point in time --
    the LAST transition recorded at or before ``at``. ``None`` if no
    transition is known before that timestamp (never inferred/guessed)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT enabled FROM gate_state_log WHERE gate_name = ? AND recorded_at <= ? "
            "ORDER BY recorded_at DESC LIMIT 1",
            (gate_name, at.isoformat()),
        ) as cur:
            row = await cur.fetchone()
    return bool(row[0]) if row is not None else None


async def list_history(gate_name: str, limit: int = 50) -> list[dict]:
    """Full transition history for one gate, most recent first."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT gate_name, enabled, recorded_at FROM gate_state_log "
            "WHERE gate_name = ? ORDER BY recorded_at DESC LIMIT ?",
            (gate_name, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# The 4 critical gates this module tracks (Item #188, 29/07) -- capital-
# adjacent and directly implicated in the 29/07 incident diagnosis. Kept as
# a single source of truth so the heartbeat cycle and any future caller
# never diverge on which names are tracked.
TRACKED_GATES = (
    "ARIA_X402_SELLER_ENABLED",
    "ARIA_X402_SELLER_MAINNET",
    "ARIA_AGENT_WALLET_PILOT_ENABLED",
    "ARIA_AGENT_WALLET_TRANSFER_ENABLED",
)


async def snapshot_tracked_gates() -> None:
    """Checks all 4 tracked gates and records any transition since the last
    check -- called from ``agent_wallet_monitor.run_agent_wallet_monitor_
    cycle`` (already-active 30min cycle, never a dedicated new one, same
    doctrine as everywhere else in this codebase: reuse before duplicating).
    Reads the gates' OWN functions directly (never re-implements the env-var
    parsing) so this module can never drift from the real gate logic."""
    from aria_core import x402_seller
    from aria_core.agent_wallet_pilot import agent_wallet_pilot_enabled, agent_wallet_transfer_enabled

    checks = (
        ("ARIA_X402_SELLER_ENABLED", x402_seller.seller_enabled()),
        ("ARIA_X402_SELLER_MAINNET", x402_seller.seller_mainnet_enabled()),
        ("ARIA_AGENT_WALLET_PILOT_ENABLED", agent_wallet_pilot_enabled()),
        ("ARIA_AGENT_WALLET_TRANSFER_ENABLED", agent_wallet_transfer_enabled()),
    )
    for gate_name, enabled in checks:
        await record_gate_transition_if_changed(gate_name, enabled)
