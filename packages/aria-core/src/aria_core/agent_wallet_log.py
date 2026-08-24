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

24/08 -- hash-chained (research lead from a GitHub exploration of the
ai-agents topic, HKUDS/Vibe-Trading's audit ledger pattern): every row's
`record_hash` is a sha256 of its own fields PLUS the previous row's
`record_hash`, same principle as a blockchain. A plain SQLite table has no
tamper evidence -- a row edited or deleted after the fact (a compromised
server, or a bug) leaves no trace. With the chain, editing ANY historical
row breaks its hash, which breaks every row after it -- `verify_chain_
integrity()` detects this and says exactly where. This is the append-only
journal for the pilot that will one day move real capital, so it is the
one table in this project worth this cost.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Genesis prev_hash for the first row of the chain -- conventional all-zero
# value, same as a blockchain's genesis block, never a real sha256 output
# (which can't be all zeros in practice) so it's unambiguous in the data.
_GENESIS_HASH = "0" * 64

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
    "implied_cost_pct",
    "cost_note",
    "prev_hash",
    "record_hash",
]


def _compute_record_hash(
    prev_hash: str,
    *,
    wallet_product: str,
    chain: str,
    action_type: str,
    token_in: str,
    token_out: str,
    amount_in: float,
    amount_out: float,
    slippage_bps: int,
    tx_hash: str,
    status: str,
    reason: str,
    created_at: str,
    to_address: str,
    implied_cost_pct: float | None,
    cost_note: str,
) -> str:
    """Deterministic sha256 over every field that matters PLUS the previous
    row's hash -- the chain link. Field order is fixed and must never change
    without a migration note, or every historical hash stops verifying."""
    canonical = "|".join(
        str(x) for x in (
            prev_hash, wallet_product, chain, action_type, token_in, token_out,
            amount_in, amount_out, slippage_bps, tx_hash, status, reason,
            created_at, to_address, implied_cost_pct, cost_note,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        # 13/08 -- real cost visibility (operator request, "annuler les frais"
        # investigation): the CDP SDK's swap RESULT never exposes gasFee/
        # protocolFee (silently dropped in its own internal QuoteSwapResult
        # conversion, verified against the installed SDK source before
        # committing to this design -- see agent_wallet_pilot.py's own
        # docstring on _compute_implied_cost_pct). Instead of extracting
        # fragile SDK internals, ``implied_cost_pct`` compares the amount
        # actually received against the spot price at the moment of the
        # swap -- captures total real cost (fees + slippage combined),
        # never distinguishing gas from protocol fee, but robust to SDK
        # internals changing.
        if "implied_cost_pct" not in cols:
            await db.execute("ALTER TABLE agent_wallet_tx_log ADD COLUMN implied_cost_pct REAL")
        if "cost_note" not in cols:
            await db.execute("ALTER TABLE agent_wallet_tx_log ADD COLUMN cost_note TEXT NOT NULL DEFAULT ''")
        # 24/08 -- hash chain. Rows written BEFORE this migration get '' in
        # both columns (there is no real chain to retroactively attach them
        # to) -- the first row written AFTER this migration becomes the new
        # chain's genesis, same as an empty table would. verify_chain_
        # integrity() treats a pre-migration '' record_hash as expected, not
        # a break, and starts real verification from the first chained row.
        if "prev_hash" not in cols:
            await db.execute("ALTER TABLE agent_wallet_tx_log ADD COLUMN prev_hash TEXT NOT NULL DEFAULT ''")
        if "record_hash" not in cols:
            await db.execute("ALTER TABLE agent_wallet_tx_log ADD COLUMN record_hash TEXT NOT NULL DEFAULT ''")
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
    implied_cost_pct: float | None = None,
    cost_note: str = "",
) -> None:
    """Logs a transaction attempt (``status`` in {"ok", "failed",
    "blocked"}). ``wallet_product`` identifies the product used (e.g.
    "metamask_agent_wallet", "coinbase_agentic_wallet", "trust_wallet_agent_kit")
    — left free-form rather than a closed enum, since the pilot hasn't been
    chosen yet. ``to_address`` (16/07, named exception #4): destination
    address of a transfer -- empty for any other action type (e.g. swap).
    ``implied_cost_pct``/``cost_note`` (13/08): real total cost (fees +
    slippage combined) of a successful swap, measured against the spot price
    -- ``None`` when unavailable (e.g. spot price lookup failed), never a
    fabricated 0.
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # SELECT then INSERT on the SAME connection, no commit in between --
        # SQLite serializes writers on the file lock, so a concurrent
        # record_transaction() call cannot read this same "last hash" and
        # fork the chain; it blocks until this transaction commits.
        last = await (
            await db.execute("SELECT record_hash FROM agent_wallet_tx_log ORDER BY id DESC LIMIT 1")
        ).fetchone()
        prev_hash = (last[0] if last else "") or _GENESIS_HASH
        record_hash = _compute_record_hash(
            prev_hash,
            wallet_product=wallet_product, chain=chain, action_type=action_type,
            token_in=token_in, token_out=token_out, amount_in=amount_in,
            amount_out=amount_out, slippage_bps=slippage_bps, tx_hash=tx_hash,
            status=status, reason=reason, created_at=now, to_address=to_address,
            implied_cost_pct=implied_cost_pct, cost_note=cost_note,
        )
        await db.execute(
            """
            INSERT INTO agent_wallet_tx_log
                (wallet_product, chain, action_type, token_in, token_out,
                 amount_in, amount_out, slippage_bps, tx_hash, status, reason,
                 created_at, to_address, implied_cost_pct, cost_note,
                 prev_hash, record_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wallet_product, chain, action_type, token_in, token_out,
                amount_in, amount_out, slippage_bps, tx_hash, status, reason, now,
                to_address, implied_cost_pct, cost_note, prev_hash, record_hash,
            ),
        )
        await db.commit()


async def verify_chain_integrity() -> dict:
    """Re-walks the whole journal in insertion order and recomputes every
    hash -- returns {"intact": bool, "checked": int, "broken_at_id": int|None,
    "detail": str}. A pre-migration row (both hash columns '') is expected
    and skipped, never counted as a break -- the real chain only starts at
    the first row written after the 24/08 migration. Any row after that
    point whose stored hash doesn't match a fresh recomputation, or whose
    prev_hash doesn't match the previous row's record_hash, means something
    edited this append-only table outside of record_transaction()."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT id, wallet_product, chain, action_type, token_in, token_out, "
                "amount_in, amount_out, slippage_bps, tx_hash, status, reason, "
                "created_at, to_address, implied_cost_pct, cost_note, prev_hash, record_hash "
                "FROM agent_wallet_tx_log ORDER BY id ASC"
            )
        ).fetchall()

    expected_prev = _GENESIS_HASH
    checked = 0
    for row in rows:
        (row_id, wallet_product, chain, action_type, token_in, token_out,
         amount_in, amount_out, slippage_bps, tx_hash, status, reason,
         created_at, to_address, implied_cost_pct, cost_note,
         prev_hash, record_hash) = row

        if not record_hash:
            # Pre-migration row -- no chain to verify, doesn't reset expected_prev.
            continue

        if prev_hash != expected_prev:
            return {
                "intact": False, "checked": checked, "broken_at_id": row_id,
                "detail": f"row {row_id}: prev_hash does not match the previous row's record_hash",
            }
        recomputed = _compute_record_hash(
            prev_hash, wallet_product=wallet_product, chain=chain, action_type=action_type,
            token_in=token_in, token_out=token_out, amount_in=amount_in,
            amount_out=amount_out, slippage_bps=slippage_bps, tx_hash=tx_hash,
            status=status, reason=reason, created_at=created_at, to_address=to_address,
            implied_cost_pct=implied_cost_pct, cost_note=cost_note,
        )
        if recomputed != record_hash:
            return {
                "intact": False, "checked": checked, "broken_at_id": row_id,
                "detail": f"row {row_id}: stored hash does not match its own fields -- edited after the fact",
            }
        expected_prev = record_hash
        checked += 1

    return {"intact": True, "checked": checked, "broken_at_id": None, "detail": ""}


async def list_aria_bought_tokens_still_held() -> set[str]:
    """13/08 -- real gap found live: the pilot's own wallet (public Base
    address) had accumulated 17 held tokens over the past month despite
    every logged swap attempt having FAILED, including one named
    ``www.bairdrop.co`` -- a classic dust-attack pattern (a spammer sends a
    token named like a URL to a known public address, hoping the owner
    visits the link). `agent_wallet_pilot_cycle.py` used to treat ANY token
    balance as an "open position" blocking every new attempt, silently
    starving the pilot for a month on tokens ARIA never bought.

    Returns the CONTRACT addresses (lowercased) of tokens ARIA has actually
    bought (a logged ``status="ok"`` swap with this token as ``token_out``)
    and not yet sold back (no LATER ``status="ok"`` swap with this token as
    ``token_in``) -- based on ARIA's own append-only journal, never a wallet
    ticker/symbol (freely chosen by whoever sends a token, exactly the
    spoofable field a dust attack relies on) and never the raw on-chain
    balance (which can't distinguish a real purchase from unsolicited spam).
    """
    from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

    await _ensure_table()
    usdc = USDC_BASE_ADDRESS.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                """
                SELECT token_in, token_out FROM agent_wallet_tx_log
                WHERE action_type = 'swap' AND status = 'ok'
                ORDER BY created_at ASC
                """
            )
        ).fetchall()

    held: set[str] = set()
    for token_in, token_out in rows:
        token_in_l = (token_in or "").lower()
        token_out_l = (token_out or "").lower()
        if token_out_l and token_out_l != usdc:
            held.add(token_out_l)
        if token_in_l and token_in_l != usdc:
            held.discard(token_in_l)
    return held


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
