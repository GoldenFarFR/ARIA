"""Replay closed shadow positions at REAL capital sizes -- REQ-0006.

Why this exists. A pocket's headline PnL is computed from a price ratio, which
answers "what did the price do", never "what would a position of size S have
obtained". The two diverge violently on thin pools: `solana_pump_shadow_archive`
holds a x1972 booked against a pool with $8,434 of reserve (exiting it would
drain the pool many times over) and a x4 against a pool holding **four dollars**.
A third of that sample sits on pools where no useful size fits at all.

What this module does NOT do: it does not invent an impact model. It replays the
one each pocket already uses -- ``_apply_price_impact_and_fee``, constant product,
returning ``None`` rather than a fabricated number when depth cannot absorb the
size. That function is imported per pocket, never copied, because the DEX fee
differs by chain (Solana 1.25%, Robinhood 1.0%, Base derived from its own
constant). What changes here is the SIZE it is asked about: production runs it at
``SIMULATED_TRADE_SIZE_USD = 0.1`` -- ten cents, an operator decision of 17/08 --
which is why a $4 pool can report an "executable" x4. No capital decision is made
on a ten-cent simulation.

Read-only, zero RU, outside production. It never writes to any pocket table and
never changes any live constant.

**Reserve is a PROXY for depth, not depth** (SPEC-0001's honest limit, kept here
so no caller can miss it). On concentrated-liquidity pools the depth near the
current price is <= total reserve, often far below. The filter is therefore
OPTIMISTIC: it cannot reject a trade wrongly, only let through one that is in
reality unexecutable. Everything it rejects is certainly rejectable, and every
PnL it produces is an UPPER bound on the truth. Never present it otherwise.

Also: ``reserve_usd`` is the pool's TOTAL liquidity, both sides combined, and the
imported impact function already halves it to approximate one side. A "size <= 5%
of one side" rule is therefore 2.5% of ``reserve_usd``.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from aria_core.paths import shadow_db_path

# Real ARIA bounds, not a decimal ladder: $2 is the Robinhood per-trade cap
# (23/08 in-principle authorisation), $10-$25 the CDP pilot floor and hard cap,
# $200 the home-wallet target. $50/$100/$250 interpolate, $1000 is there to see
# where it dies.
DEFAULT_SIZES_USD: tuple[float, ...] = (2.0, 10.0, 25.0, 50.0, 100.0, 250.0, 1000.0)

# Which module owns the impact function for a given pocket table. Imported, never
# reimplemented -- the fee is chain-specific and a local copy would silently drift.
_IMPACT_MODULE_BY_PREFIX: dict[str, str] = {
    "solana_pump": "aria_core.solana_pump_shadow",
    "solana_late_bonding": "aria_core.solana_pump_shadow",
    "robinhood_pump": "aria_core.robinhood_pump_shadow",
    "base_momentum": "aria_core.base_momentum_shadow",
}

# Rejection reasons. A rejected row is never a WIN nor a LOSS -- it leaves the
# aggregate and is counted here instead (SPEC-0001, unknown != zero).
REJ_NO_ENTRY_PRICE = "no_entry_price"
REJ_NO_RESERVE = "no_reserve"
REJ_NO_OUTCOME = "no_outcome"
REJ_ENTRY_TOO_DEEP = "entry_impact_exceeds_depth"
REJ_EXIT_TOO_DEEP = "exit_impact_exceeds_depth"


def _impact_fn(table: str):
    for prefix, module in _IMPACT_MODULE_BY_PREFIX.items():
        if table.startswith(prefix):
            return importlib.import_module(module)._apply_price_impact_and_fee
    raise ValueError(f"no impact function registered for table {table!r}")


@dataclass
class SizeResult:
    """One rung of the ladder. Every field a report needs is here, so no caller
    can publish a mean without its n, its outlier tests and its day count."""
    size_usd: float
    n_total: int = 0
    n_executable: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    multipliers: list[float] = field(default_factory=list)

    def _pnl(self, drop: int = 0) -> float | None:
        if not self.multipliers:
            return None
        kept = sorted(self.multipliers, reverse=True)[drop:]
        if not kept:
            return None
        return round((sum(kept) / len(kept) - 1.0) * 100, 1)

    def summary(self) -> dict[str, Any]:
        return {
            "size_usd": self.size_usd,
            "n_total": self.n_total,
            "n_executable": self.n_executable,
            "pnl_pct": self._pnl(0),
            "pnl_without_top2": self._pnl(2),
            "pnl_without_top5": self._pnl(5),
            "winrate_pct": round(
                100.0 * sum(1 for m in self.multipliers if m > 1.0) / len(self.multipliers), 1
            ) if self.multipliers else None,
            "rejections": dict(sorted(self.rejections.items(), key=lambda kv: -kv[1])),
        }


def replay_row(
    row: dict[str, Any], size_usd: float, impact,
) -> tuple[float | None, str | None]:
    """One position at one size. Returns (multiplier, rejection_reason).

    The exit reserve is used when the table carries one; otherwise the ENTRY
    reserve stands in and that substitution is reported by the caller, never
    hidden -- it is optimistic if the pool shrank, conservative if it grew, and
    SPEC-0001 requires depth at BOTH instants.
    """
    entry_price = row.get("entry_price")
    if not entry_price or entry_price <= 0:
        return None, REJ_NO_ENTRY_PRICE
    entry_reserve = row.get("reserve_usd")
    if entry_reserve is None or entry_reserve <= 0:
        return None, REJ_NO_RESERVE
    ideal_mult = row.get("final_multiplier")
    if ideal_mult is None or ideal_mult <= 0:
        return None, REJ_NO_OUTCOME

    real_entry = impact(
        entry_price, trade_size_usd=size_usd, reserve_usd=entry_reserve, side="buy",
    )
    if real_entry is None:
        return None, REJ_ENTRY_TOO_DEEP

    exit_reserve = row.get("last_reserve_usd") or entry_reserve
    ideal_exit_price = entry_price * ideal_mult
    # The position's VALUE at exit is what must fit the pool, not the entry size:
    # a x1972 on a $25 entry means extracting $49,300, which is what disqualifies
    # it while its entry passed comfortably.
    exit_value_usd = size_usd * ideal_mult
    real_exit = impact(
        ideal_exit_price, trade_size_usd=exit_value_usd,
        reserve_usd=exit_reserve, side="sell",
    )
    if real_exit is None:
        return None, REJ_EXIT_TOO_DEEP

    return real_exit / real_entry, None


async def load_rows(table: str, db_path: str | None = None) -> list[dict[str, Any]]:
    """Closed positions only, read-only. Missing optional columns come back absent
    rather than defaulted, so `replay_row` can tell 'no data' from 'zero'."""
    path = db_path or shadow_db_path()
    async with aiosqlite.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = {r["name"] for r in await cur.fetchall()}
        wanted = [
            c for c in (
                "entry_price", "reserve_usd", "last_reserve_usd", "final_multiplier",
                "realistic_final_multiplier", "detected_at", "closed_at", "status",
                "last_checked_at", "pool_address", "last_price",
            ) if c in cols
        ]
        # CLOSED positions only, and "closed" is not "has a multiplier". Found
        # the hard way on solana_pump_shadow_archive: 156 rows carry a
        # final_multiplier while status is still 'open' -- a live valuation, not
        # a realised outcome. Filtering on the multiplier alone silently mixes
        # latent and realised PnL, which is exactly the kind of number this
        # module exists to stop. Preference order, most explicit first.
        if "closed_at" in cols:
            closed = "closed_at IS NOT NULL"
        elif "status" in cols:
            closed = "status <> 'open'"
        elif "exit_reason" in cols:
            closed = "exit_reason IS NOT NULL"
        else:
            closed = "final_multiplier IS NOT NULL"
        cur = await db.execute(
            f"SELECT {', '.join(wanted)} FROM {table} "
            f"WHERE final_multiplier IS NOT NULL AND {closed}"
        )
        return [dict(r) for r in await cur.fetchall()]


async def run(
    table: str, sizes: tuple[float, ...] = DEFAULT_SIZES_USD, db_path: str | None = None,
) -> dict[str, Any]:
    """Both readings SPEC-0001 requires.

    (a) variable population -- each rung on the trades executable at that rung.
    (b) constant population -- every rung on the subset executable at the LARGEST
        size. Without (b) a falling return is indistinguishable from a changing
        sample, and the gap between (a) and (b) measures how much of the apparent
        edge came from selecting micro-pools.
    """
    impact = _impact_fn(table)
    rows = await load_rows(table, db_path)

    variable: dict[float, SizeResult] = {}
    per_row: dict[int, dict[float, float]] = {}
    for size in sizes:
        res = SizeResult(size_usd=size, n_total=len(rows))
        for idx, row in enumerate(rows):
            mult, reason = replay_row(row, size, impact)
            if reason:
                res.rejections[reason] = res.rejections.get(reason, 0) + 1
                continue
            res.n_executable += 1
            res.multipliers.append(mult)
            per_row.setdefault(idx, {})[size] = mult
        variable[size] = res

    largest = max(sizes)
    survivors = [i for i, by_size in per_row.items() if largest in by_size]
    constant: dict[float, SizeResult] = {}
    for size in sizes:
        res = SizeResult(size_usd=size, n_total=len(survivors))
        for i in survivors:
            mult = per_row.get(i, {}).get(size)
            if mult is None:
                res.rejections[REJ_EXIT_TOO_DEEP] = res.rejections.get(REJ_EXIT_TOO_DEEP, 0) + 1
                continue
            res.n_executable += 1
            res.multipliers.append(mult)
        constant[size] = res

    day_col = "closed_at" if rows and "closed_at" in rows[0] else "last_checked_at"
    days = {
        (r.get(day_col) or "")[:10] for r in rows if r.get(day_col)
    }
    return {
        "table": table,
        "n_closed": len(rows),
        "distinct_closure_days": len(days),
        "exit_reserve_available": bool(rows and rows[0].get("last_reserve_usd") is not None),
        "variable_population": {s: r.summary() for s, r in variable.items()},
        "constant_population": {s: r.summary() for s, r in constant.items()},
        "constant_population_size": len(survivors),
    }
