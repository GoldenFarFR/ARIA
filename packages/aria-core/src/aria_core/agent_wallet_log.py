"""Append-only transaction journal for the future "agent wallet" pilot (MetaMask
Agent Wallet / Coinbase Agentic Wallets / Trust Wallet Agent Kit — real capital
stage 2, CLAUDE.md diligence 14-15/07). Seam built BEFORE the product was
finally chosen and BEFORE any real deposit: this module isn't called by any
production code yet, it's waiting to be wired up once the pilot is decided.

Same doctrine as `bonding_trade_log.py` (#60, Arena): logs EVERY execution
attempt (`status` in {"ok", "failed", "blocked"}), never just successes —
a guard-rail refusal (cap exceeded, slippage out of tolerance, kill-switch
disabled) must stay traced, never silent.

Structurally separate from `wallet_guard.py` — same principle as
`sepolia_autonomous.py`/`bonding_trade_log.py`: never mixed with the shared
guard-rail that protects everything that will one day touch real capital at
larger scale. Append-only: no UPDATE/DELETE function here (same doctrine as
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
        # Idempotent hot migration (same pattern as vc_predictions.py/exam.py) --
        # to_address added on 16/07 for named exception #4 (USDC transfer to a
        # single allowed address): never in the CREATE TABLE definition above so
        # as not to break an already-existing database without this column.
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
    """Logs a transaction attempt (``status`` in {"ok", "failed",
    "blocked"}). ``wallet_product`` identifies the product used (e.g.
    "metamask_agent_wallet", "coinbase_agentic_wallet", "trust_wallet_agent_kit")
    — left free-form rather than a closed enum, since the pilot hasn't been
    chosen yet. ``to_address`` (16/07, named exception #4): destination
    address of a transfer -- empty for any other action type (e.g. swap).
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


# 19/07 -- real incident (URANUS, 2 failures 05:42/06:44 UTC): a Pydantic
# `ValidationError` on the CDP SDK response (`CommonSwapResponseFees.gasFee`
# required but `None` -- confirmed bug on the Coinbase SDK side, cf. CLAUDE.md)
# is DETERMINISTIC: the same token will fail identically on every new attempt
# until this SDK bug is fixed upstream, unlike a transient network/RPC outage
# which can succeed on the next try. Detected by substring match on the error
# message -- never a new table, never touches `momentum_blacklist.py` (reserved
# for real security threats, not technical failures, doctrine decided 17/07).
_STRUCTURAL_FAILURE_MARKERS = ("validation error", "pydantic")


def is_structural_swap_failure(reason: str) -> bool:
    """True if ``reason`` carries the signature of a STRUCTURAL failure (will
    recur identically), not a transient hiccup (network, slippage)."""
    lower = (reason or "").lower()
    return any(marker in lower for marker in _STRUCTURAL_FAILURE_MARKERS)


async def recent_failed_swap(
    token_out: str, *, within_minutes: int, structural_within_minutes: int | None = None,
) -> bool:
    """True if the LATEST swap attempt toward ``token_out`` (whichever input
    leg) is a technical failure (``status="failed"``) that happened less than
    ``within_minutes`` ago -- light cooldown after a transient outage (RPC,
    slippage exceeded), for the agent-wallet pilot's autonomous decision loop
    (18/07). Reuses the already-existing journal, no new table -- never
    confused with ``momentum_blacklist.py`` (reserved for real confirmed
    security threats, never a technical failure). If the LATEST attempt for
    this token succeeded or was blocked (not 'failed'), or doesn't exist at
    all, the token is never put on cooldown here.

    ``structural_within_minutes`` (19/07, optional): if provided AND the
    LATEST failure is structural (``is_structural_swap_failure``), this longer
    cooldown replaces ``within_minutes`` for this specific case -- avoids
    endlessly retrying, every ``within_minutes``, a token that will always
    fail for the SAME deterministic reason. ``None`` (default) preserves the
    historical behavior (a single cooldown, regardless of cause)."""
    await _ensure_table()
    token = (token_out or "").strip().lower()
    if not token:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                # LOWER() on both sides: token_out is stored AS GIVEN by the
                # caller (record_transaction doesn't normalize case) -- never
                # assume every historical caller already lowercased it.
                "SELECT status, created_at, reason FROM agent_wallet_tx_log "
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
    effective_minutes = within_minutes
    if structural_within_minutes is not None and is_structural_swap_failure(row[2]):
        effective_minutes = structural_within_minutes
    return elapsed_min < effective_minutes
