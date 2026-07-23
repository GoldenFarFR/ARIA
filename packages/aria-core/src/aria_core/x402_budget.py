"""x402 spending cap — explicit operator decision (07/16): $5 maximum per
week, spent STRATEGICALLY ("never run short, but spend enough to optimize
the speed of accumulating data").

Concrete translation of this instruction:
  - Hard cap, never exceeded (`can_spend`/`record_spend` — fail-closed: when
    in doubt about the balance already consumed, refuse rather than risk
    exceeding it).
  - NO artificial throttle below the cap: the speed of durable knowledge
    accumulation is precisely the goal ("optimize the speed") — the only
    legitimate brake is the "one fact, once" discipline (deduplication), not
    a daily drip-feed imposed by this module.
  - Calendar sliding week (Monday 00:00 UTC), not a cumulative total since
    forever.

Structurally separate from `wallet_guard.py`/`agent_wallet_log.py` — same
doctrine as `sepolia_autonomous.py`/`bonding_trade_log.py`: this cap neither
modifies nor bypasses the shared guardrail that protects all real capital at
a larger scale. Scope strictly limited to x402 data/API micropayments
(cents) — NEVER touches real-capital trading (swaps, positions), which stays
on its own separate path (CLAUDE.md, 07/16).

Append-only (same pattern as `agent_directive_log`/`agent_wallet_log`): no
UPDATE/DELETE function here, every attempt (`status` in {"ok", "blocked",
"failed"}) stays traced forever, never silent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

WEEKLY_CAP_USD = 5.0

_COLUMNS = [
    "id",
    "resource",
    "provider",
    "amount_usd",
    "status",
    "reason",
    "created_at",
    "pay_to",
    "contract",
    "token_symbol",
]

# 07/17 -- added after a real false positive from agent_wallet_monitor.py (a
# "EXIT NOT INITIATED BY ARIA" alert on the very first real x402 payment,
# never recognized as "known" because x402_cdp_signer.py doesn't go through
# agent_wallet_log). `pay_to` (the 402's settlement address, already known at
# record_spend time -- never a new network call) lets the monitor correlate a
# detected on-chain movement to an already-logged x402 spend, without
# depending on a possible X-PAYMENT-RESPONSE header (optional in the
# protocol, never guaranteed).
#
# `contract`/`token_symbol` (07/19, #143) -- found while answering a direct
# operator question ("detail each payment, which token"): without these two
# fields, the only way to know WHICH token motivated a payment was to
# manually reconstruct the correlation via timestamps against paper_position
# -- fragile (one real case stayed unidentifiable, that container's logs lost
# at the next redeploy). Optional (empty string): any payment not tied to a
# specific token (Otto AI market_alerts, Cybercentry wallet verification)
# stays valid.
_ADDED_COLUMNS = [
    ("pay_to", "TEXT NOT NULL DEFAULT ''"),
    ("contract", "TEXT NOT NULL DEFAULT ''"),
    ("token_symbol", "TEXT NOT NULL DEFAULT ''"),
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS x402_spend_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                amount_usd REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                pay_to TEXT NOT NULL DEFAULT ''
            )
            """
        )
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(x402_spend_log)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE x402_spend_log ADD COLUMN {name} {ddl}")
        await db.commit()


def week_start(now: datetime | None = None) -> datetime:
    """Start of the current calendar week (Monday 00:00 UTC)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    monday = ref - timedelta(days=ref.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


async def spent_this_week(now: datetime | None = None) -> float:
    """Sum of spends ACTUALLY made (status='ok') since the start of the
    current calendar week. 'blocked'/'failed' attempts never count against
    the cap -- only a payment actually settled consumes the budget."""
    await _ensure_table()
    start = week_start(now).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM x402_spend_log "
                "WHERE status = 'ok' AND created_at >= ?",
                (start,),
            )
        ).fetchone()
    return float(row[0]) if row else 0.0


async def remaining_budget(now: datetime | None = None) -> float:
    spent = await spent_this_week(now)
    return max(0.0, WEEKLY_CAP_USD - spent)


async def can_spend(amount_usd: float, now: datetime | None = None) -> bool:
    """Fail-closed: a negative/zero amount is always refused (nothing to
    pay), and if the remaining balance doesn't cover the requested amount, we
    refuse rather than cut it close to the cap."""
    if amount_usd <= 0:
        return False
    remaining = await remaining_budget(now)
    return amount_usd <= remaining


async def record_spend(
    *,
    resource: str,
    provider: str = "",
    amount_usd: float,
    status: str,
    reason: str = "",
    pay_to: str = "",
    contract: str = "",
    token_symbol: str = "",
) -> None:
    """Records an x402 payment attempt (``status`` in {"ok", "blocked",
    "failed"}) -- never just successes, a cap refusal must stay traced.
    ``pay_to`` (07/17): settlement address declared by the 402, for
    correlation by ``agent_wallet_monitor.py`` (see comment on
    ``_ADDED_COLUMNS``). ``contract``/``token_symbol`` (07/19, #143): token
    concerned if applicable, left empty for any payment not tied to a
    specific token."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO x402_spend_log
              (resource, provider, amount_usd, status, reason, created_at, pay_to, contract, token_symbol)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (resource, provider, amount_usd, status, reason, now, pay_to, contract, token_symbol),
        )
        await db.commit()


async def weekly_status(now: datetime | None = None) -> dict:
    """Diagnostic (same doctrine as the agent-wallet-ledger endpoint,
    #158/#159) -- readable to check the spending pace without having to read
    the DB directly."""
    spent = await spent_this_week(now)
    return {
        "cap_usd": WEEKLY_CAP_USD,
        "spent_usd": round(spent, 4),
        "remaining_usd": round(max(0.0, WEEKLY_CAP_USD - spent), 4),
        "week_started_at": week_start(now).isoformat(),
    }


async def list_spends(limit: int = 200) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM x402_spend_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]
