"""Replays the REAL exit rule against REAL archived price paths.

**Why.** The trailing stop's distance is fixed at 15% below the running peak,
and measured on this dome's own closures that distance is plainly miscalibrated:
it captures 72% of a +100% move but LOSES money on a +12% one (n=8, -3.1%
average). Choosing a better number by judgement is exactly how five "profitable
segments" were announced and retracted in two days. This picks it from data.

**How it stays honest.** It calls `evaluate_exit` itself -- the same function
production trades on, with its distances passed as arguments -- rather than a
second implementation that would drift. A replay measuring a copy of the rule
measures nothing about the rule.

**The limit you must not forget.** A replay can only see what was recorded.
  - A TIGHTER stop than the one that ran is measured honestly: it would have
    fired at or before the real exit, and every point up to there is archived.
  - A WIDER stop is TRUNCATED: the position was closed when it was, so the
    path simply stops. Its result is a LOWER BOUND, never a verdict, and every
    result carries `truncated` saying so.
This asymmetry is the whole reason the archive was wired before any threshold
was touched, and ignoring it would turn a measurement back into a guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import aiosqlite

from .paths import shadow_db_path
from .solana_fresh_launch_ws_exit_shadow import evaluate_exit

ARCHIVE_TABLE = "shadow_snapshot_archive"

# A path with fewer points than this cannot distinguish one stop distance from
# another -- replaying it would add noise wearing the costume of evidence.
MIN_PATH_POINTS = 3


@dataclass
class ReplayResult:
    positions: int = 0
    truncated: int = 0
    pnl_pct: float = 0.0
    pnl_pct_without_top2: float = 0.0
    winners: int = 0
    by_reason: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "positions": self.positions,
            "truncated": self.truncated,
            "pnl_pct": round(self.pnl_pct, 2),
            "pnl_pct_without_top2": round(self.pnl_pct_without_top2, 2),
            "winners": self.winners,
            "win_rate": round(self.winners / self.positions, 3) if self.positions else None,
            "by_reason": self.by_reason,
        }


async def load_paths(module: str, *, db_path: str | None = None, limit: int = 500) -> dict[int, list[dict]]:
    """Archived price path per position, oldest point first."""
    async with aiosqlite.connect(db_path or str(shadow_db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT position_id, price_usd, reserve_usd, dex_id, window_high, window_low, checked_at "
            f"FROM {ARCHIVE_TABLE} WHERE module = ? AND price_usd IS NOT NULL "
            f"ORDER BY position_id ASC, id ASC",
            (module,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    paths: dict[int, list[dict]] = {}
    for r in rows:
        paths.setdefault(r["position_id"], []).append(r)
    # Keep the most recent positions when capped, not an arbitrary slice.
    if len(paths) > limit:
        for pid in sorted(paths)[:len(paths) - limit]:
            paths.pop(pid)
    return paths


async def load_entries(table: str, position_ids: list[int], *, db_path: str | None = None) -> dict[int, dict]:
    if not position_ids:
        return {}
    async with aiosqlite.connect(db_path or str(shadow_db_path())) as db:
        db.row_factory = aiosqlite.Row
        marks = ",".join("?" * len(position_ids))
        cur = await db.execute(
            f"SELECT id, entry_price, reserve_usd, realistic_entry_price, exit_reason "
            f"FROM {table} WHERE id IN ({marks})",
            position_ids,
        )
        return {r["id"]: dict(r) for r in await cur.fetchall()}


def replay_one(entry: dict, path: list[dict], **params) -> tuple[str, float, bool] | None:
    """Walks one archived path through the real rule.

    Returns ``(exit_reason, pnl_pct, truncated)`` or ``None`` when the path is
    too short to mean anything. ``truncated`` is True when the rule never
    fired: the position outlived its own archive, so this PnL is a floor."""
    if len(path) < MIN_PATH_POINTS or not entry.get("entry_price"):
        return None

    state = {
        "entry_price": entry["entry_price"],
        "reserve_usd": entry.get("reserve_usd"),
        "peak_price": entry["entry_price"],
        "remaining_qty": 1.0,
        "realized_proceeds": 0.0,
        "realistic_entry_price": entry.get("realistic_entry_price"),
        "realistic_realized_proceeds": 0.0,
        "pool_address": "replay",
    }
    for i, point in enumerate(path):
        result = evaluate_exit(
            state,
            current_price=point["price_usd"],
            reserve_usd=point.get("reserve_usd"),
            dex_id=point.get("dex_id"),
            # Elapsed time is approximated by the sampling index rather than
            # read from the clock: the archive stores when we LOOKED, and
            # replaying max_hold on look-times would measure our own cadence
            # rather than the rule. Kept explicit so nobody reads these
            # max_hold numbers as precise.
            age_minutes=float(i),
            window_high=point.get("window_high"),
            window_low=point.get("window_low"),
            **params,
        )
        if result.get("skipped"):
            continue
        state["peak_price"] = result["peak_price"]
        if result.get("exit_reason"):
            mult = result.get("realistic_final_multiplier") or result.get("final_multiplier")
            if mult is None:
                return None
            return result["exit_reason"], (mult - 1) * 100, False

    # Never fired: value it at the last observed price, and flag it.
    last = path[-1]["price_usd"]
    return "still_open", (last / entry["entry_price"] - 1) * 100, True


async def replay(
    module: str, table: str, *, db_path: str | None = None, limit: int = 500, **params,
) -> ReplayResult:
    paths = await load_paths(module, db_path=db_path, limit=limit)
    entries = await load_entries(table, list(paths), db_path=db_path)

    pnls: list[float] = []
    out = ReplayResult()
    for pid, path in paths.items():
        entry = entries.get(pid)
        if entry is None:
            continue
        got = replay_one(entry, path, **params)
        if got is None:
            continue
        reason, pnl, truncated = got
        pnls.append(pnl)
        out.positions += 1
        out.truncated += 1 if truncated else 0
        out.winners += 1 if pnl > 0 else 0
        out.by_reason[reason] = out.by_reason.get(reason, 0) + 1

    if pnls:
        ordered = sorted(pnls)
        out.pnl_pct = sum(ordered) / len(ordered)
        # The mandate's outlier test, computed here rather than left to the
        # reader: a grid of averages invites picking the highest one, which is
        # precisely how a handful of trades gets mistaken for an edge.
        out.pnl_pct_without_top2 = (
            sum(ordered[:-2]) / (len(ordered) - 2) if len(ordered) > 2 else out.pnl_pct
        )
    return out


async def sweep(
    module: str, table: str, *, values: list[float], param: str = "trailing_stop_pct",
    db_path: str | None = None, limit: int = 500, **fixed,
) -> list[dict]:
    """Same positions, one parameter varied. Ranked by the OUTLIER-TESTED
    figure, never by the raw mean."""
    out = []
    for v in values:
        res = await replay(module, table, db_path=db_path, limit=limit, **{param: v}, **fixed)
        row = res.as_dict()
        row[param] = v
        out.append(row)
    return sorted(out, key=lambda r: r["pnl_pct_without_top2"], reverse=True)
