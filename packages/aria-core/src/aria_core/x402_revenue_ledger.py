"""x402 revenue ledger (07/24) -- the missing symmetric half of
``x402_budget.py`` (which only tracks what ARIA *spends*). Records what ARIA
*earns* selling her own synthesized judgment via x402 (``x402_seller.py``,
still dormant -- both gates OFF, no route wired yet). Built now so the
FastAPI route (still to build) has a real ledger to write to from day one,
never bolted on after revenue is already flowing blind.

Same schema philosophy as ``x402_spend_log``: every payment ATTEMPT is
recorded (``status`` in {"ok", "failed"}), never just successes -- a failed
verification/settlement must stay traceable too. ``wallet`` (07/24): the
receiving wallet name -- always ``aria-wallet-X402-EVM`` today (confirmed:
the SAME wallet that spends on Cybercentry/Otto AI/BlockRun also receives
x402 sales, cf. x402_seller.ARIA_X402_RECEIVING_ADDRESS), kept as an
explicit column so a future second wallet doesn't require a schema
migration."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = ("id", "product", "payer_address", "wallet", "amount_usd", "status", "created_at")

DEFAULT_RECEIVING_WALLET = "aria-wallet-X402-EVM"


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS x402_revenue_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT NOT NULL,
                payer_address TEXT NOT NULL DEFAULT '',
                wallet TEXT NOT NULL DEFAULT '',
                amount_usd REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_sale(
    *,
    product: str,
    payer_address: str = "",
    wallet: str = DEFAULT_RECEIVING_WALLET,
    amount_usd: float,
    status: str,
) -> None:
    """Records an x402 sale attempt. ``status`` in {"ok", "failed"} -- a
    payment that failed verification/settlement is still logged, never
    silently dropped (same doctrine as record_spend)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO x402_revenue_log
              (product, payer_address, wallet, amount_usd, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (product, payer_address, wallet, amount_usd, status, now),
        )
        await db.commit()


async def total_revenue(since: datetime | None = None) -> float:
    """Sum of sales ACTUALLY settled (status='ok'). ``since=None`` sums the
    whole history (never just the current week -- revenue, unlike the spend
    cap, has no rolling budget to reset)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        if since is None:
            row = await (
                await db.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM x402_revenue_log WHERE status = 'ok'")
            ).fetchone()
        else:
            row = await (
                await db.execute(
                    "SELECT COALESCE(SUM(amount_usd), 0) FROM x402_revenue_log "
                    "WHERE status = 'ok' AND created_at >= ?",
                    (since.isoformat(),),
                )
            ).fetchone()
    return float(row[0]) if row else 0.0


async def unique_recurring_payers(*, window_days: int = 30) -> int:
    """Distinct payer addresses that have paid MORE THAN ONCE within the
    last ``window_days`` -- the real "are clients hooked" signal proposed in
    docs/x402-seller-scoping.md (phase-2 pricing trigger candidate), not raw
    call volume alone."""
    await _ensure_table()
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT payer_address, COUNT(*) FROM x402_revenue_log "
                "WHERE status = 'ok' AND created_at >= ? AND payer_address != '' "
                "GROUP BY payer_address HAVING COUNT(*) > 1",
                (cutoff,),
            )
        ).fetchall()
    return len(rows)


async def recent_sale_count(payer_address: str, product: str, *, window_seconds: float) -> int:
    """31/07 -- anti-abuse building block (backlog #228, B20 x402 route):
    counts this payer's SETTLED purchases of ``product`` within the last
    ``window_seconds`` -- the ledger already records every sale with
    ``payer_address``, so this is a read on data already collected, never a
    new table. Empty/whitespace ``payer_address`` always returns 0 (never
    rate-limits an unidentified/no-payment request -- that's the payment
    gate's job, not this one's)."""
    payer = (payer_address or "").strip().lower()
    if not payer:
        return 0
    await _ensure_table()
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM x402_revenue_log "
                "WHERE status = 'ok' AND product = ? AND LOWER(payer_address) = ? AND created_at >= ?",
                (product, payer, cutoff),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def list_sales(limit: int = 200) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT id, product, payer_address, wallet, amount_usd, status, created_at "
                "FROM x402_revenue_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]
