"""Solana LATE-BONDING shadow pocket (20/08, operator-directed).

**The band nobody measured.** Mapping every closure of the two existing
fresh-launch pockets onto bonding-curve progress showed the winrate DOUBLES
as a token advances along its curve -- and that past 50% the dome has almost
no data at all:

    <30% of curve   n=1277   winrate  9.9%   PnL -4.39%
    30-50%          n= 239   winrate 20.9%   PnL -2.53%
    50-75%          n=   4   unmeasured
    75-100%         n=   4   unmeasured

That gap is structural, not accidental: WS-EXIT abandons any candidate
reaching ``MAX_LIQUIDITY_USD_ENTRY``, and FAST-DISCOVERY enters at the first
liquidity confirmation. Both are built to buy tokens that were just born.
This pocket does the opposite and buys tokens that have ALREADY PROVEN
traction -- a curve at 70-80% means real people bought their way there.

**Why this is not just another threshold tweak.** Every other lever tried on
20/08 (scale-out ladders, scalping, age bands, liquidity floors, market-cap
bands) was tested and rejected on real closures, and the dome's PnL is carried
by 1.8% of trades. A late-bonding entry changes the POPULATION being traded
rather than filtering the same one harder, which is the only move that can
escape that regime.

**What is REUSED, never reimplemented** (architectural-coherence rule):
  - exit rule: ``evaluate_exit`` imported from the WS-EXIT pocket, as-is
  - fills/fees: ``_apply_price_impact_and_fee``/``SIMULATED_TRADE_SIZE_USD``
  - price fallback: ``_snapshot_with_fallback``
  - curve state: ``resolve_bonding_curves``/``bonding_progress``
  - discovery: the SHARED ``PumpFunTradeStream`` -- its program-wide feed
    already sees every actively-traded mint, so no new subscription and no
    scanning loop is needed to find candidates
  - creator screen: ``creator_reputation`` (4+ tokens from one wallet =
    4.7% winrate vs 15.5%)
  - decision log: ``pretrade_rejection_log``

Same bright line as every shadow module here: never opens a real or paper
position, never touches wallet_guard/agent_wallet/paper_trader. Read, log,
simulate.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite
import httpx

from aria_core import creator_reputation, pretrade_rejection_log
from aria_core.paths import shadow_db_path
from aria_core.services.pumpfun_bonding_ws import (
    RPC_HTTP_DEFAULT,
    bonding_progress,
    resolve_bonding_curves,
)
from aria_core.solana_fresh_launch_ws_exit_shadow import evaluate_exit
from aria_core.solana_pump_shadow import (
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _minutes_since,
    _snapshot_with_fallback,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())
TABLE = "solana_late_bonding_shadow_log"

# The band this pocket exists to measure. Lower bound is where the measured
# winrate trend was still climbing (20.9% at 30-50%); upper bound stops short
# of graduation itself, since a curve at >90% can complete mid-tracking and
# migrate its liquidity to the AMM under us.
MIN_BONDING_PROGRESS = 0.70
MAX_BONDING_PROGRESS = 0.90

# A candidate must show real trade flow, not just a high curve position -- a
# curve can sit at 75% for hours after its buyers left. Reuses the shared
# trade stream's distinct-buyer counting (velocity alone is forgeable by one
# wallet; distinct wallets are not).
MIN_DISTINCT_BUYERS = 3

# Above this, one wallet supplies most of the buy volume: wash trading rather
# than demand. Same provisional-threshold discipline as everywhere else here.
MAX_TOP_BUYER_SHARE = 0.60

MAX_CONCURRENT_TRACKED = 10
_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                token_address TEXT NOT NULL,
                chain TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                entry_price REAL,
                reserve_usd REAL,
                bonding_progress_at_entry REAL,
                distinct_buyers_at_entry INTEGER,
                top_buyer_share_at_entry REAL,
                buyer_acceleration_at_entry REAL,
                remaining_qty REAL NOT NULL DEFAULT 1.0,
                realized_proceeds REAL NOT NULL DEFAULT 0.0,
                peak_price REAL,
                realistic_entry_price REAL,
                realistic_realized_proceeds REAL DEFAULT 0.0,
                exit_reason TEXT,
                final_multiplier REAL,
                realistic_final_multiplier REAL,
                last_price REAL,
                last_reserve_usd REAL,
                last_checked_at TEXT,
                exit_price_source TEXT
            )
            """
        )
        # Same index discipline as the sibling pockets: the two columns every
        # read path filters on.
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_open ON {TABLE}(exit_reason, last_checked_at)")
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_pool ON {TABLE}(pool_address)")
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        f"SELECT 1 FROM {TABLE} WHERE pool_address = ? AND chain = ? AND exit_reason IS NULL LIMIT 1",
        (pool_address, chain),
    )
    return await cur.fetchone() is not None


async def screen_candidate(
    mint: str, pool_address: str, *, trade_stream, curve: dict | None, token_decimals: int | None = None,
) -> tuple[bool, str, dict]:
    """``(accepted, reason, metrics)``. Pure: no DB, no network -- everything
    it needs is already in hand, which is what lets the whole screen run
    without adding a single call to the entry path."""
    progress = bonding_progress(curve, token_decimals=token_decimals)
    flow = trade_stream.get_flow(mint) if trade_stream is not None else None
    metrics = {
        "bonding_progress": progress,
        "distinct_buyers": flow.distinct_buyers if flow else None,
        "top_buyer_share": flow.top_buyer_share if flow else None,
        "buyer_acceleration": (
            trade_stream.buyer_acceleration(mint) if trade_stream is not None else None
        ),
    }

    if progress is None:
        # Fail CLOSED: this pocket's entire premise is the curve position, so
        # not knowing it means there is nothing to act on.
        return (False, "blocked_progress_unknown", metrics)
    if not (MIN_BONDING_PROGRESS <= progress <= MAX_BONDING_PROGRESS):
        return (False, f"blocked_outside_band: progress={progress:.2f}", metrics)

    buyers = metrics["distinct_buyers"]
    if buyers is None or buyers < MIN_DISTINCT_BUYERS:
        # A curve can sit high for hours after its buyers left -- position on
        # the curve is history, trade flow is the present.
        return (False, f"blocked_no_traction: buyers={buyers}", metrics)

    share = metrics["top_buyer_share"]
    if share is not None and share > MAX_TOP_BUYER_SHARE:
        return (False, f"blocked_wash_trading: top_buyer={share:.2f}", metrics)

    return (True, "accepted", metrics)


async def consider_candidate(
    mint: str, pool_address: str, *, chain: str = "solana", trade_stream=None,
    http_client: httpx.AsyncClient | None = None, geckoterminal_client=None,
    resolve_curves_fn=None, snapshot_fn=None, db_path: str | None = None,
) -> int | None:
    """Screens one mint and, if it passes, records a simulated entry.
    Returns the new row id, or ``None``. Never raises into the caller."""
    try:
        await _ensure_table(db_path)
        async with aiosqlite.connect(db_path or _db_path()) as db:
            if await _has_open_signal(db, pool_address, chain):
                return None

        resolver = resolve_curves_fn or resolve_bonding_curves
        client = http_client or httpx.AsyncClient(timeout=15.0)
        owns_client = http_client is None
        try:
            resolved = await resolver(client, [(pool_address, mint)], rpc_http_url=RPC_HTTP_DEFAULT)
        finally:
            if owns_client:
                await client.aclose()

        account = resolved.get(pool_address) if resolved else None
        curve = getattr(account, "raw", None) or (account if isinstance(account, dict) else None)
        decimals = getattr(account, "token_decimals", None)

        accepted, reason, metrics = await screen_candidate(
            mint, pool_address, trade_stream=trade_stream, curve=curve, token_decimals=decimals,
        )

        snapshot = None
        if accepted:
            fn = snapshot_fn or _snapshot_with_fallback
            snapshot = await fn(geckoterminal_client, pool_address, mint, chain=chain)
            if not snapshot.available or snapshot.price_usd is None:
                accepted, reason = False, "blocked_no_price"

        # Logged on BOTH branches, same discipline as the other pockets: a
        # filter can only be judged against what it let through.
        await pretrade_rejection_log.record_decision(
            pretrade_rejection_log.GateDecision(
                pocket="late_bonding", chain=chain, mint=mint, pool_address=pool_address,
                blocked=not accepted, reason=None if accepted else reason,
                top_holder_pct=None, gate_latency_ms=None,
                would_be_entry_price=snapshot.price_usd if snapshot else None,
                would_be_reserve_usd=snapshot.reserve_usd if snapshot else None,
                realistic_would_be_entry_price=None,
                distinct_buyers=metrics.get("distinct_buyers"),
                top_buyer_share=metrics.get("top_buyer_share"),
                buyer_acceleration=metrics.get("buyer_acceleration"),
            ),
            db_path=db_path,
        )
        if not accepted or snapshot is None:
            return None

        if await creator_reputation.is_factory(getattr(account, "creator", None), db_path=db_path):
            return None

        realistic = _apply_price_impact_and_fee(
            snapshot.price_usd, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
            reserve_usd=snapshot.reserve_usd, side="buy",
        )
        async with aiosqlite.connect(db_path or _db_path()) as db:
            cur = await db.execute(
                f"""
                INSERT INTO {TABLE}
                    (pool_address, token_address, chain, detected_at, entry_price, reserve_usd,
                     bonding_progress_at_entry, distinct_buyers_at_entry, top_buyer_share_at_entry,
                     buyer_acceleration_at_entry, peak_price, realistic_entry_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pool_address, mint, chain, datetime.now(timezone.utc).isoformat(),
                    snapshot.price_usd, snapshot.reserve_usd, metrics.get("bonding_progress"),
                    metrics.get("distinct_buyers"), metrics.get("top_buyer_share"),
                    metrics.get("buyer_acceleration"), snapshot.price_usd, realistic,
                ),
            )
            await db.commit()
            logger.info(
                "solana_late_bonding_shadow: ENTRY %s progress=%.2f buyers=%s",
                pool_address, metrics.get("bonding_progress") or -1, metrics.get("distinct_buyers"),
            )
            return cur.lastrowid
    except Exception as exc:  # noqa: BLE001 -- a shadow pocket never breaks its caller
        logger.info("solana_late_bonding_shadow: consider_candidate failed for %s (%s)", mint, exc)
        return None


async def advance_exit_simulation(
    geckoterminal_client=None, *, chain: str = "solana", limit: int = 50,
    snapshot_fn=None, db_path: str | None = None,
) -> dict:
    """Advances every open position using the SAME exit rule as WS-EXIT --
    imported, never reimplemented, so the two pockets differ on ENTRY only and
    stay comparable."""
    stats = {"checked": 0, "closed": 0}
    await _ensure_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {TABLE} WHERE chain = ? AND exit_reason IS NULL "
            # Never-checked rows first, exactly the ordering defect fixed on the
            # sibling pockets the same day (a fresh row sorted to the BACK).
            f"ORDER BY (last_checked_at IS NOT NULL) ASC, "
            f"COALESCE(last_checked_at, detected_at) ASC LIMIT ?",
            (chain, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    fn = snapshot_fn or _snapshot_with_fallback
    for row in rows:
        stats["checked"] += 1
        try:
            snapshot = await fn(geckoterminal_client, row["pool_address"], row["token_address"], chain=chain)
        except Exception:  # noqa: BLE001 -- a provider failure is not a verdict
            continue
        if not snapshot.available or snapshot.price_usd is None:
            continue

        age = _minutes_since(row["detected_at"])
        result = evaluate_exit(
            row, current_price=snapshot.price_usd, reserve_usd=snapshot.reserve_usd,
            dex_id=snapshot.dex_id, age_minutes=age if age is not None else 0.0,
        )
        async with aiosqlite.connect(db_path or _db_path()) as db:
            await db.execute(
                f"""
                UPDATE {TABLE} SET remaining_qty = ?, realized_proceeds = ?, peak_price = ?,
                    realistic_realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                    realistic_final_multiplier = ?, last_price = ?, last_reserve_usd = ?,
                    last_checked_at = ?, exit_price_source = ?
                WHERE id = ? AND exit_reason IS NULL
                """,
                (
                    result.get("remaining_qty"), result.get("realized_proceeds"), result.get("peak_price"),
                    result.get("realistic_realized_proceeds"), result.get("exit_reason"),
                    result.get("final_multiplier"), result.get("realistic_final_multiplier"),
                    snapshot.price_usd, snapshot.reserve_usd,
                    datetime.now(timezone.utc).isoformat(), snapshot.dex_id, row["id"],
                ),
            )
            await db.commit()
        if result.get("exit_reason"):
            stats["closed"] += 1
    return stats


async def summary(*, chain: str = "solana", db_path: str | None = None) -> dict:
    """Same shape as the sibling pockets' own summary, so the comparative
    report can treat all three identically."""
    await _ensure_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT COALESCE(realistic_final_multiplier, final_multiplier) AS m, "
            f"bonding_progress_at_entry AS p FROM {TABLE} "
            f"WHERE chain = ? AND exit_reason IS NOT NULL", (chain,),
        )
        closed = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            f"SELECT COUNT(*) AS n FROM {TABLE} WHERE chain = ? AND exit_reason IS NULL", (chain,),
        )
        open_n = (await cur.fetchone())["n"]

    mults = [c["m"] for c in closed if c["m"] is not None]
    wins = sum(1 for m in mults if m > 1.0)
    return {
        "completed": len(closed), "open": open_n,
        "win_rate": (wins / len(mults)) if mults else None,
        "avg_pnl_pct": (round((sum(mults) / len(mults) - 1) * 100, 2)) if mults else None,
        "avg_entry_progress": (
            round(sum(c["p"] for c in closed if c["p"] is not None) / max(1, sum(1 for c in closed if c["p"] is not None)), 3)
            if closed else None
        ),
    }
