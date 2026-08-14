"""Unified resource-budget ledger for API providers with a monthly/daily
quota cap (#302, Devil's Advocate 13/08) -- consolidates the near-identical
append-only log + SUM/COUNT mechanisms scattered across CoinGecko, Mobula,
Dune, Blockscout, Firecrawl, Tavily into one shared table.

Deliberately scoped to same-shape "log-based" guards only (suspend-only, no
Telegram alert, no distant balance read, one calendar window per provider).
The 4 SingleRowStore-backed state machines (goplus_quota_suspension,
coinmarketcap_quota_guard, firecrawl_overspend_suspension,
twitterapi_io_budget) have fundamentally different semantics (backoff,
hysteresis, distant balance reads, alerting) and stay separate modules on
purpose -- a naive single API across all 10 would silently break real
production behavior. Full comparative mapping of all 10 mechanisms:
docs/HANDOFF_RESOURCE_BUDGET.md.

Deliberately NOT a provider->cap registry here: ``window``/``cap`` are always
passed in explicitly by the calling module, which keeps owning its own
constant (already documented there with its real-provider context, e.g.
"95% of the real documented 2,500 executions/month"). A registry duplicated
in two places is exactly the kind of drift this consolidation exists to
remove -- one source of truth per constant, not two that could silently
diverge.

Migrated provider-by-provider, one commit each (Dune first -- the simplest,
pure execution count, no per-call cost formula), existing per-module public
functions kept as thin facades so zero consumer is touched. Each migration
copies the provider's pre-existing log table into this shared ledger FIRST,
lazily and idempotently, before the facade switches over -- resetting a
mid-month counter to zero would silently blow through the provider's REAL
external quota, not just a local bookkeeping error.

Note on ``can_spend``: this checks ``spent_in_window + cost <= cap`` (would
this specific call exceed the cap) -- for a provider whose every call has the
same fixed cost (Dune: 1/execution), this is exactly equivalent to the
pre-migration ``spent >= cap`` check it replaces. For a provider with a
variable per-call cost (e.g. Mobula's 5-credit OHLCV calls), this is
STRICTER than a pre-migration ``spent >= cap`` check that ignored the size of
the call about to be made (a real latent gap: it could authorize a 5-credit
call at spent=9499/cap=9500, landing 4 credits over) -- to note explicitly
when that provider is migrated, not a silent behavior change.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

_TABLE = "resource_budget_log"

# provider -> (legacy_table, legacy_timestamp_column, legacy_cost_expr,
# legacy_caller_expr, legacy_query_expr). Each expr is a SQL expression
# evaluated per legacy row -- "1" for a pure execution/request count, a
# column name for a stored credit cost / caller / query label, "''" if the
# legacy table never tracked that field.
_LEGACY_TABLES: dict[str, tuple[str, str, str, str, str]] = {
    "dune_execution": ("dune_execution_log", "executed_at", "1", "''", "''"),
    "coingecko": ("coingecko_request_log", "requested_at", "1", "''", "''"),
    "mobula": ("mobula_request_log", "requested_at", "credits", "''", "''"),
    "blockscout": ("blockscout_credit_log", "created_at", "credits", "endpoint", "''"),
    "firecrawl": ("firecrawl_crawl_log", "created_at", "credits", "caller", "query"),
    "tavily": ("tavily_search_log", "created_at", "credits", "caller", "query"),
}


async def _ensure_table() -> None:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                caller TEXT NOT NULL DEFAULT '',
                query TEXT NOT NULL DEFAULT '',
                cost INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_provider_recorded "
            f"ON {_TABLE} (provider, recorded_at)"
        )
        await db.commit()


async def _migrate_legacy_log_if_needed(provider: str) -> None:
    """One-time, idempotent copy of a provider's pre-existing log table into
    the shared ledger -- skipped once the provider already has any row here
    (never re-copies, never double-counts real spend)."""
    legacy = _LEGACY_TABLES.get(provider)
    if legacy is None:
        return
    legacy_table, legacy_ts_col, legacy_cost_expr, legacy_caller_expr, legacy_query_expr = legacy
    async with aiosqlite.connect(str(aria_db_path())) as db:
        cursor = await db.execute(f"SELECT 1 FROM {_TABLE} WHERE provider = ? LIMIT 1", (provider,))
        if await cursor.fetchone() is not None:
            return  # already migrated (or already has real post-migration spend)
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (legacy_table,)
        )
        if await cursor.fetchone() is None:
            return  # legacy table never created (fresh install), nothing to copy
        await db.execute(
            f"INSERT INTO {_TABLE} (provider, caller, query, cost, recorded_at) "
            f"SELECT ?, {legacy_caller_expr}, {legacy_query_expr}, {legacy_cost_expr}, {legacy_ts_col} "
            f"FROM {legacy_table}",
            (provider,),
        )
        await db.commit()


def _month_start(now: datetime) -> str:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _day_start(now: datetime) -> str:
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _window_start(window: str, now: datetime) -> str:
    if window == "monthly":
        return _month_start(now)
    if window == "daily":
        return _day_start(now)
    raise ValueError(f"unknown resource_budget window: {window!r}")


async def spent_in_window(provider: str, *, window: str = "monthly", now: datetime | None = None) -> int:
    await _ensure_table()
    await _migrate_legacy_log_if_needed(provider)
    now = now or datetime.now(timezone.utc)
    start = _window_start(window, now)
    async with aiosqlite.connect(str(aria_db_path())) as db:
        cursor = await db.execute(
            f"SELECT COALESCE(SUM(cost), 0) FROM {_TABLE} WHERE provider = ? AND recorded_at >= ?",
            (provider, start),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def can_spend(
    provider: str,
    cost: int = 1,
    *,
    cap: int,
    window: str = "monthly",
    now: datetime | None = None,
) -> bool:
    if cost <= 0:
        return False
    spent = await spent_in_window(provider, window=window, now=now)
    return spent + cost <= cap


async def record_spend(
    provider: str,
    cost: int = 1,
    *,
    caller: str = "",
    query: str = "",
    now: datetime | None = None,
) -> None:
    await _ensure_table()
    ts = (now or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            f"INSERT INTO {_TABLE} (provider, caller, query, cost, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (provider, caller, query, cost, ts),
        )
        await db.commit()


async def recent_spends(provider: str, limit: int = 20) -> list[dict]:
    """Traceability: the most recent recorded spends for one provider (caller,
    query, cost, timestamp), most recent first -- same purpose as the
    per-module ``recent_crawls()``/``recent_searches()`` helpers this
    consolidation replaces."""
    await _ensure_table()
    await _migrate_legacy_log_if_needed(provider)
    async with aiosqlite.connect(str(aria_db_path())) as db:
        cursor = await db.execute(
            f"SELECT caller, query, cost, recorded_at FROM {_TABLE} "
            f"WHERE provider = ? ORDER BY id DESC LIMIT ?",
            (provider, max(1, min(limit, 200))),
        )
        rows = await cursor.fetchall()
    return [
        {"caller": row[0], "query": row[1], "cost": row[2], "recorded_at": row[3]} for row in rows
    ]
