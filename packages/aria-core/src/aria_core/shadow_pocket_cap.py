"""Shared open-position cap for shadow pockets (28/08, operator request:
"met un filtre de cumul a 25 max par shadow pour tous"). Applies uniformly
to every shadow pocket regardless of its own schema -- never a trading
gate, purely a resource-consumption guardrail on top of whatever entry
filters each pocket already has (each pocket keeps its own funnel; this is
an additional cap, not a replacement). Real incident: one pocket
(solana_pump_shadow) accumulated 242 positions with zero cap, each costing
a live Chainstack accountSubscribe -- a real, measured RU consumption
driver found live 28/08."""

from __future__ import annotations

import aiosqlite

MAX_OPEN_POSITIONS_PER_POCKET = 25


async def open_position_count(
    db: aiosqlite.Connection, table: str, *, open_clause: str, params: tuple = ()
) -> int:
    """Counts rows matching `open_clause` (a raw SQL WHERE fragment, e.g.
    "exit_reason IS NULL" or "status = 'open'") in `table`. Each pocket's own
    schema decides what "open" means -- this helper stays schema-agnostic on
    purpose, never assumes a universal column name."""
    cur = await db.execute(f"SELECT COUNT(*) FROM {table} WHERE {open_clause}", params)  # noqa: S608 -- table/open_clause are module-internal constants, never user input
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def at_capacity(
    db: aiosqlite.Connection,
    table: str,
    *,
    open_clause: str,
    params: tuple = (),
    cap: int = MAX_OPEN_POSITIONS_PER_POCKET,
) -> bool:
    """True once `table` already holds `cap` or more open positions matching
    `open_clause` -- callers check this BEFORE inserting a new row, never
    after, so the cap is a hard ceiling, not a soft target."""
    return await open_position_count(db, table, open_clause=open_clause, params=params) >= cap
