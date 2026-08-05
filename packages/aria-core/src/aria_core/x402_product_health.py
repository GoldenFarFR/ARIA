"""Per-product health tracking for x402 paid endpoints (05/08, operator
request: "je veux que le client soit satisfait avec toute les informations
possible disponible... éviter de faire payer un x402 cassé ou un résultat
pas satisfaisant"). Distinct from ``x402_revenue_ledger`` (which only knows
about settled SALES): this tracks every real invocation of a paid route
handler, success or not, so a broken product (empty/opaque/error result)
is visible even though the SDK itself never actually settles payment on a
non-2xx response (verified live 05/08: the middleware cancels settlement
on any >=400 response, so a broken product never overcharges anyone -- the
risk this module addresses is reputational/UX, not billing).

``outcome`` is one of:
  - "success" -- a genuinely useful result was returned (2xx).
  - "no_result" -- the handler ran fine but had nothing useful to say
    (wallet never scored, B20 scan unresolved/"opaque").
  - "error" -- malformed input or an internal failure (4xx/5xx other than
    the "no_result" cases above).

Same schema philosophy as ``x402_budget``/``x402_revenue_ledger``: every
real attempt is recorded, never just the good ones -- a product silently
failing 100% of the time must be visible."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_VALID_OUTCOMES = ("success", "no_result", "error")


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS x402_product_health_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_attempt(product: str, outcome: str) -> None:
    """Records one real invocation of a paid route handler. ``outcome`` must
    be one of ``_VALID_OUTCOMES`` -- a caller passing anything else is a bug
    at the call site, not something to silently coerce."""
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {_VALID_OUTCOMES}, got {outcome!r}")
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO x402_product_health_log (product, outcome, created_at) VALUES (?, ?, ?)",
            (product, outcome, now),
        )
        await db.commit()


async def success_rate(product: str, *, window: int = 50) -> dict:
    """Success rate over the last ``window`` real attempts for ``product``
    (most recent first, never a lifetime average that would hide a recent
    regression). Returns ``{"attempts": n, "successes": n, "rate_pct": float
    | None}`` -- ``rate_pct`` is ``None`` only when there are zero attempts
    yet (never a fabricated 0% or 100%)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT outcome FROM x402_product_health_log WHERE product = ? "
            "ORDER BY id DESC LIMIT ?",
            (product, window),
        )
        rows = await cursor.fetchall()
    attempts = len(rows)
    successes = sum(1 for (outcome,) in rows if outcome == "success")
    rate_pct = round(100.0 * successes / attempts, 1) if attempts else None
    return {"attempts": attempts, "successes": successes, "rate_pct": rate_pct}
