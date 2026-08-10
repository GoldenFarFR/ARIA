"""x402 spending cap — explicit operator decision (07/16): $5 maximum per
week, spent STRATEGICALLY ("never run short, but spend enough to optimize
the speed of accumulating data").

Concrete translation of this instruction:
  - Hard cap, never exceeded (`try_reserve`/`settle` — fail-closed: when
    in doubt about the balance already consumed, refuse rather than risk
    exceeding it).
  - NO artificial throttle below the cap: the speed of durable knowledge
    accumulation is precisely the goal ("optimize the speed") — the only
    legitimate brake is the "one fact, once" discipline (deduplication), not
    a daily drip-feed imposed by this module.
  - Rolling 7-day window (now - 7 days), not a calendar week and not a
    cumulative total since forever. Was a calendar week (Monday 00:00 UTC)
    until 03/08 -- security-review workflow finding: a calendar boundary let
    up to ~2x the cap be spent in a few minutes around the reset. NOTE for
    any future session: `x_research_budget.py`/`blockscout_credit_budget.py`
    still document (and use) the OLD calendar-week design "by doctrine,
    matching x402_budget.py" -- this module intentionally diverges from them
    now; don't silently re-align one onto the other without a fresh decision.

Structurally separate from `wallet_guard.py`/`agent_wallet_log.py` — same
doctrine as `sepolia_autonomous.py`/`bonding_trade_log.py`: this cap neither
modifies nor bypasses the shared guardrail that protects all real capital at
a larger scale. Scope strictly limited to x402 data/API micropayments
(cents) — NEVER touches real-capital trading (swaps, positions), which stays
on its own separate path (CLAUDE.md, 07/16).

Append-only (same pattern as `agent_directive_log`/`agent_wallet_log`): no
DELETE function here, every attempt (`status` in {"ok", "blocked", "failed",
"pending"}) stays traced forever, never silent. 03/08 -- `try_reserve`/
`settle` (two-step atomic reserve-then-settle, replacing the old
`can_spend`/`record_spend` check-then-act) is the one place that UPDATEs a
row (a "pending" reservation resolving to "ok"/"failed") -- see their own
docstrings for why a race condition made the old pair unsafe under
concurrent callers (confirmed real: two parallel workflow sub-agents both
passed a budget check before either had recorded its spend)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

# 27/07 -- same fix as tavily_budget.py (real bug found via a live test
# failure): a module-level ``DB_PATH`` froze at import time, before the
# per-test isolation fixture ever ran, so every test shared one persistent
# path across every suite run. Resolved dynamically now.

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
    async with aiosqlite.connect(str(aria_db_path())) as db:
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
    """Start of the ROLLING 7-day window (now - 7 days), NOT a calendar week.

    03/08 -- was a calendar week (Monday 00:00 UTC) until this date --
    security-review workflow finding: a calendar boundary lets up to ~2x
    the cap be spent in a few minutes around the reset (Sunday night +
    Monday morning both count as "fresh"). A rolling window has no such
    edge -- the sum always covers exactly the trailing 7 days, wherever
    "now" falls. Name kept as ``week_start`` (not renamed) -- every caller
    across the codebase already imports it by this name."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref - timedelta(days=7)


# 03/08 -- how long a "pending" reservation (try_reserve, never settled)
# still counts against the budget before being treated as abandoned/failed.
# Comfortably above the real HTTP timeouts already observed in this
# pipeline (12-25s) -- long enough that a genuinely in-flight payment is
# never double-counted as free budget, short enough that a crashed process
# doesn't freeze real budget for the rest of the week.
PENDING_TIMEOUT_MINUTES = 5


async def spent_this_week(now: datetime | None = None) -> float:
    """Sum of spends ACTUALLY made (status='ok') PLUS any still-in-flight
    reservation (status='pending', younger than PENDING_TIMEOUT_MINUTES)
    within the trailing rolling 7-day window. 'blocked'/'failed' attempts
    never count against the cap. A 'pending' row is included so that two
    concurrent callers can't both see "budget still free" while one of them
    is mid-flight (see try_reserve's own docstring) -- a stale 'pending'
    (crashed before settle()) ages out on its own after the timeout, never
    permanently freezing the budget."""
    await _ensure_table()
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    start = week_start(ref).isoformat()
    pending_cutoff = (ref - timedelta(minutes=PENDING_TIMEOUT_MINUTES)).isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM x402_spend_log "
                "WHERE created_at >= ? AND (status = 'ok' OR (status = 'pending' AND created_at >= ?))",
                (start, pending_cutoff),
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
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            """
            INSERT INTO x402_spend_log
              (resource, provider, amount_usd, status, reason, created_at, pay_to, contract, token_symbol)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (resource, provider, amount_usd, status, reason, now, pay_to, contract, token_symbol),
        )
        await db.commit()


async def try_reserve(
    amount_usd: float, *, resource: str, provider: str = "", contract: str = "", token_symbol: str = "",
) -> int | None:
    """Atomically checks the budget AND reserves ``amount_usd`` in one SQLite
    transaction (``BEGIN IMMEDIATE`` -- a file-level lock, unlike an
    in-process ``asyncio.Lock`` which does NOT protect two separate
    processes/containers calling this at the same time -- confirmed the
    real cause of a 03/08 incident: two parallel workflow sub-agents, two
    separate Python processes, each with its own event loop, both read "budget
    still free" before either had recorded a spend).

    Returns the reservation's row id (status='pending') if accepted, ``None``
    if refused (nothing reserved -- the caller logs its own 'blocked' via
    ``record_spend`` as before). Replaces the old check-then-act pair
    (``can_spend`` then, much later, ``record_spend``) for any caller that
    needs the reservation to actually hold against a concurrent caller --
    ``can_spend``/``record_spend`` themselves are kept unchanged (still used
    by read-only callers/diagnostics), just no longer safe for a real
    reserve-then-spend sequence under concurrency.

    Caller MUST eventually call ``settle(reservation_id, status=...)`` --
    an un-settled reservation still counts against the budget for up to
    ``PENDING_TIMEOUT_MINUTES`` (see ``spent_this_week``), then ages out on
    its own (never a permanent freeze on a crash mid-payment)."""
    await _ensure_table()
    if amount_usd <= 0:
        return None
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(str(aria_db_path()), isolation_level=None) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            spent = await spent_this_week(now)
            if amount_usd > max(0.0, WEEKLY_CAP_USD - spent):
                await db.execute("COMMIT")
                return None
            cur = await db.execute(
                """
                INSERT INTO x402_spend_log
                  (resource, provider, amount_usd, status, created_at, contract, token_symbol)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
                """,
                (resource, provider, amount_usd, now.isoformat(), contract, token_symbol),
            )
            await db.execute("COMMIT")
        except Exception:
            await db.execute("ROLLBACK")
            raise
        return cur.lastrowid


async def settle(reservation_id: int, *, status: str, reason: str = "", pay_to: str = "") -> None:
    """Resolves a ``try_reserve`` reservation to its final outcome --
    ``status`` in {"ok", "failed"}, never back to "pending"."""
    await _ensure_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "UPDATE x402_spend_log SET status = ?, reason = ?, pay_to = ? WHERE id = ?",
            (status, reason, pay_to, reservation_id),
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
    async with aiosqlite.connect(str(aria_db_path())) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM x402_spend_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


# 10/08 -- operator request after the twitsh false-positive incident: every
# wallet ARIA pays should be identifiable by NAME, automatically, never a
# registry maintained by hand (the pre-existing `_THIRD_PARTY_ADDRESS_NAMES`
# in agent_wallet_monitor.py covers exactly 2 manually-added entries and
# missed twit.sh entirely despite 77+ prior real payments to it). Derived
# from the FULL history (never `list_spends`'s default 200-row window --
# a provider paid steadily for months could otherwise fall out of range),
# grouped by (pay_to, provider) so a provider using more than one address
# still gets its own entries rather than one merged/ambiguous label.
_KNOWN_PROVIDER_MIN_OK_COUNT = 3


async def known_pay_to_providers(*, min_ok_count: int = _KNOWN_PROVIDER_MIN_OK_COUNT) -> dict[str, str]:
    """pay_to address (lowercase) -> provider name, for every address with at
    least ``min_ok_count`` successful ("ok") payments in the FULL history.
    Display/diagnostic only -- like `_THIRD_PARTY_ADDRESS_NAMES`, never
    consulted to decide whether a movement is authorized, only to label it
    once a movement has already been classified."""
    await _ensure_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        rows = await (
            await db.execute(
                """
                SELECT pay_to, provider, COUNT(*) as cnt
                FROM x402_spend_log
                WHERE status = 'ok' AND pay_to != '' AND provider != ''
                GROUP BY pay_to, provider
                HAVING cnt >= ?
                ORDER BY cnt DESC
                """,
                (min_ok_count,),
            )
        ).fetchall()
    result: dict[str, str] = {}
    for pay_to, provider, _cnt in rows:
        key = str(pay_to).lower()
        # First row per key wins (already ORDER BY cnt DESC) -- the provider
        # with the most successful payments to this address, if more than
        # one has ever paired with it.
        result.setdefault(key, str(provider))
    return result
