"""Persistent log of PRE-TRADE rejections (20/08, operator-directed).

**Why this table has to exist at all.** The pre-trade holder gate's whole
value is that a blocked candidate never becomes a position -- no row in the
pocket's own table, no entry fee, no loss. But that also means the rejection
leaves ZERO trace: without this log, every blocked candidate's top holder
concentration, the gate's real latency, and the price we would have paid are
lost the instant the coroutine returns. Nothing could then answer the only
question that matters -- "is the filter actually saving money, or is it
cutting the winners?".

This is the proactive-ingestion doctrine applied at the moment the mechanism
is built rather than after: the filter and its measurement ship together.

**What "avoided PnL" means here, honestly.** Storing the price we would have
entered at is not by itself a measurement. `advance_avoided_tracking()` polls
each rejected candidate's price afterwards, exactly as if a position had been
opened, so the counterfactual is measured on real subsequent prices rather
than assumed. Two deliberate honesty constraints:
  - The would-be entry price is the SLIPPAGE-ADJUSTED one (same
    ``_apply_price_impact_and_fee`` the real pockets use). Comparing a raw
    mid price against a real exit would flatter the filter.
  - Tracking is bounded (``_tracking_window_minutes()``, which READS the
    pocket's own MAX_HOLD_MINUTES rather than restating it) and rejections whose
    price can no longer be resolved are marked ``unresolvable`` rather than
    silently dropped -- a pool that went dark IS the outcome, and dropping
    those would bias the result toward whichever tokens survived.

ACCEPTED candidates are logged too, not just rejections. A filter can only be
judged against what it let through; a table of rejections alone cannot tell a
good cut from an indiscriminate one.
"""
from __future__ import annotations

import hashlib

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from .paths import shadow_db_path

# One band reject in this many is followed -- see the sampling block in
# `record_decision` for why a fraction rather than all or nothing.
BAND_SAMPLE_ONE_IN = 50

TABLE = "fresh_launch_pretrade_gate_log"

def _tracking_window_minutes() -> float:
    """How long a rejected candidate's counterfactual is tracked: EXACTLY the
    horizon a real position would have lived, so the avoided PnL is never
    credited with a collapse the position would not have been exposed to.

    20/08 -- was hardcoded to 180.0 with a comment claiming it matched the
    pocket's MAX_HOLD_MINUTES. It did NOT: that constant is 60.0, so every
    counterfactual was being measured over 3x the real horizon. Found by
    auditing this dome's own modules against the architectural-coherence rule
    the same day. Read lazily to avoid an import cycle (the pocket imports
    this module).
    """
    from .solana_fresh_launch_ws_exit_shadow import MAX_HOLD_MINUTES

    return float(MAX_HOLD_MINUTES)

# 20/08, security/robustness pass -- this table grows on EVERY gate decision
# (accepted ones included), not just on entries, so at the measured rate it
# adds thousands of rows a day and nothing ever removed them: an unbounded
# table on a VPS whose disk also holds the real trading DB. Rows past this
# horizon are purged, and only ONCE their counterfactual has been resolved
# (`tracking_status` no longer 'tracking') -- never a blind age-based delete
# that could drop a still-open measurement. 30 days is far beyond the 180-min
# tracking window, so a purged row has always long finished being useful.
RETENTION_DAYS = 30

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return str(shadow_db_path())


@dataclass
class GateDecision:
    """One gate verdict, accepted or rejected."""

    pocket: str
    chain: str
    mint: str
    pool_address: str | None
    blocked: bool
    reason: str | None
    top_holder_pct: float | None
    gate_latency_ms: float | None
    would_be_entry_price: float | None
    would_be_reserve_usd: float | None
    realistic_would_be_entry_price: float | None
    # 20/08 -- the real concentration among actual wallets, pool excluded.
    # Defaulted so existing call sites keep working; see the gate's own
    # `top_holder_excluding_pool_pct` docstring for why the two are measured
    # side by side before either threshold is touched.
    top_holder_excluding_pool_pct: float | None = None
    # 20/08 -- live trade flow observed while tracking, from the
    # bonding-curve subscription. Logged on EVERY decision so the
    # provisional >=1-buy rule can be recalibrated on real history.
    buys_observed: int | None = None
    sells_observed: int | None = None
    # 20/08 -- LURE-PHASE metrics from services/pumpfun_trade_stream.py (real
    # buyer identities, decoded free from the program logs). Logged on every
    # decision so the N-distinct-buyers threshold is calibrated on real
    # history instead of guessed -- nothing acts on them yet.
    distinct_buyers: int | None = None
    top_buyer_share: float | None = None
    buyer_acceleration: float | None = None
    sell_pressure_slope: float | None = None
    # 26/08 -- the curve position itself, already computed by every caller's
    # own screen but silently dropped before reaching this log. Without it,
    # a candidate's entry-time bonding progress can never be reconstructed
    # for a future backtest (specs/008-solana-regime-macro-gate).
    bonding_progress: float | None = None


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pocket TEXT NOT NULL,
                chain TEXT NOT NULL,
                mint TEXT NOT NULL,
                pool_address TEXT,
                decided_at TEXT NOT NULL,
                blocked INTEGER NOT NULL,
                reason TEXT,
                top_holder_pct REAL,
                top_holder_excluding_pool_pct REAL,
                buys_observed INTEGER,
                sells_observed INTEGER,
                distinct_buyers INTEGER,
                top_buyer_share REAL,
                buyer_acceleration REAL,
                sell_pressure_slope REAL,
                gate_latency_ms REAL,
                would_be_entry_price REAL,
                would_be_reserve_usd REAL,
                realistic_would_be_entry_price REAL,
                last_price REAL,
                last_checked_at TEXT,
                peak_price REAL,
                avoided_multiplier REAL,
                tracking_status TEXT
            )
            """
        )
        # Indexed on the two columns every read path filters by -- this table
        # grows by every gate decision, not just entries, so a scan would get
        # expensive fast.
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_decided ON {TABLE}(decided_at)")
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_status ON {TABLE}(tracking_status)")
        # Hot ALTER for a table that already exists in prod -- proactive-
        # ingestion doctrine: add the column and start accumulating now,
        # never wait for a rebuild. A PRAGMA guard keeps it idempotent.
        cur = await db.execute(f"PRAGMA table_info({TABLE})")
        columns = {row[1] for row in await cur.fetchall()}
        for col, decl in (("top_holder_excluding_pool_pct", "REAL"),
                          ("buys_observed", "INTEGER"), ("sells_observed", "INTEGER"),
                          ("distinct_buyers", "INTEGER"), ("top_buyer_share", "REAL"),
                          ("buyer_acceleration", "REAL"), ("sell_pressure_slope", "REAL"),
                          ("bonding_progress", "REAL")):
            if col not in columns:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col} {decl}")
        await db.commit()
    _ensured_db_paths.add(path)


async def record_decision(decision: GateDecision, *, db_path: str | None = None) -> int | None:
    """Best-effort write. NEVER raises into the trading loop: losing a log row
    is bad, blocking or crashing an entry decision because logging failed
    would be worse."""
    try:
        path = db_path or _db_path()
        await _ensure_table(path)
        # Only a genuine reject with a real price is worth tracking forward --
        # a fail-closed "unavailable" has no holder data to validate, and an
        # accepted candidate becomes a real position tracked by the pocket
        # itself (double-tracking it here would double the API load).
        # 22/08, operator: "verifie si on a rater des courreur depuis le
        # lancement de cette epoque". The answer was unobtainable: this list
        # only ever matched the HOLDER-concentration reasons, and the pocket
        # that actually trades emits none of them -- 1615 of its rejects sat at
        # `not_tracked`, so the mechanism built to measure what we decline had
        # never measured anything on the only live pocket.
        #
        # Tracked now: any reject carrying a JUDGEMENT we could be wrong about.
        # Deliberately NOT `blocked_outside_band`, which is 98% of the volume
        # and is not a judgement at all -- a token at 4% of its curve was never
        # a candidate, and following thousands of them would spend the price
        # cascade's whole budget re-pricing tokens nobody considered.
        TRACKED_REJECTS = (
            "blocked_holder_concentration",
            "blocked_wallet_concentration",
            "blocked_thin_liquidity",     # the 5500$ floor: is it too high?
            "blocked_wash_trading",       # the concentration ceiling
            "blocked_no_sell_route",
            "blocked_creator",
            # 23/08 -- the regime gate. Tracked for a reason the others do not
            # have: these candidates passed EVERY other filter and were refused
            # only because the market was judged cold, so their forward path is
            # the direct measurement of whether that judgement was right. It is
            # the gate's own control group.
            "blocked_regime_closed",
            # 22/08 -- how much a stale entry price was really costing. This
            # one is tracked to answer a question the refusal itself creates:
            # are we now dropping candidates that would have run?
            "blocked_stale_price",
        )
        trackable = (
            decision.blocked
            and decision.reason is not None
            and decision.reason.startswith(TRACKED_REJECTS)
            and decision.would_be_entry_price
        )
        # 22/08 -- a SAMPLE of the band rejects, on the operator's question:
        # "what if we had bought at 10% of the curve instead of 70%?". The
        # answer does not exist today -- 22549 band rejects carry zero tracked
        # outcome -- and the reason it was excluded stands: following all of
        # them would spend the entire price budget on tokens nobody
        # considered. A fixed fraction answers the question at 1/50th of that
        # cost.
        #
        # Deterministic on the mint, never random: the same token is always
        # in or out of the sample, so a restart cannot half-follow one, and
        # the sample carries no bias toward tokens seen at a particular
        # moment.
        if (
            not trackable
            and decision.blocked
            and decision.reason is not None
            and decision.reason.startswith("blocked_outside_band")
            and decision.would_be_entry_price
            and decision.mint
        ):
            digest = hashlib.sha256(decision.mint.encode()).digest()
            trackable = (digest[0] % BAND_SAMPLE_ONE_IN) == 0
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                f"""
                INSERT INTO {TABLE}
                    (pocket, chain, mint, pool_address, decided_at, blocked, reason,
                     top_holder_pct, top_holder_excluding_pool_pct, gate_latency_ms,
                     would_be_entry_price, would_be_reserve_usd, realistic_would_be_entry_price,
                     buys_observed, sells_observed, distinct_buyers, top_buyer_share,
                     buyer_acceleration, sell_pressure_slope, bonding_progress, peak_price,
                     tracking_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.pocket, decision.chain, decision.mint, decision.pool_address,
                    datetime.now(timezone.utc).isoformat(), 1 if decision.blocked else 0,
                    decision.reason, decision.top_holder_pct,
                    decision.top_holder_excluding_pool_pct, decision.gate_latency_ms,
                    decision.would_be_entry_price, decision.would_be_reserve_usd,
                    decision.realistic_would_be_entry_price,
                    decision.buys_observed, decision.sells_observed,
                    decision.distinct_buyers, decision.top_buyer_share,
                    decision.buyer_acceleration, decision.sell_pressure_slope,
                    decision.bonding_progress,
                    decision.would_be_entry_price if trackable else None,
                    "tracking" if trackable else "not_tracked",
                ),
            )
            await db.commit()
            return cur.lastrowid
    except Exception:  # noqa: BLE001 -- logging must never break a trade decision
        return None


async def advance_avoided_tracking(
    *, resolve_price_fn, max_rows: int = 40, db_path: str | None = None,
) -> dict:
    """Polls each still-tracked rejection's real current price and updates its
    counterfactual. ``resolve_price_fn(pool_address, mint, chain)`` must return
    ``(price_usd, reserve_usd)`` or ``(None, None)``.

    ``max_rows`` bounds the API cost per pass -- this table can grow much
    faster than the position tables it shadows, so an unbounded sweep would
    quietly become the dome's biggest consumer."""
    path = db_path or _db_path()
    await _ensure_table(path)
    stats = {"checked": 0, "updated": 0, "closed": 0, "unresolvable": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=_tracking_window_minutes())).isoformat()

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {TABLE} WHERE tracking_status = 'tracking' ORDER BY decided_at LIMIT ?",
            (max_rows,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for row in rows:
        stats["checked"] += 1
        try:
            price_usd, _reserve_usd = await resolve_price_fn(row["pool_address"], row["mint"], row["chain"])
        except Exception:  # noqa: BLE001 -- a provider failure is not a verdict
            price_usd = None

        expired = row["decided_at"] < cutoff
        if price_usd is None:
            if expired:
                # Marked, never deleted: a pool that went dark IS the outcome.
                # Dropping these would bias the result toward survivors.
                await _set_status(path, row["id"], "unresolvable")
                stats["unresolvable"] += 1
            continue

        entry = row["realistic_would_be_entry_price"] or row["would_be_entry_price"]
        peak = max(row["peak_price"] or price_usd, price_usd)
        multiplier = (price_usd / entry) if entry else None
        status = "closed" if expired else "tracking"

        async with aiosqlite.connect(path) as db:
            await db.execute(
                f"""
                UPDATE {TABLE} SET last_price = ?, last_checked_at = ?, peak_price = ?,
                    avoided_multiplier = ?, tracking_status = ?
                WHERE id = ?
                """,
                (price_usd, datetime.now(timezone.utc).isoformat(), peak, multiplier, status, row["id"]),
            )
            await db.commit()
        stats["updated"] += 1
        if status == "closed":
            stats["closed"] += 1

    return stats


async def advance_avoided_tracking_cycle(*, max_rows: int = 40, db_path: str | None = None) -> dict:
    """Ready-to-call wrapper for the standalone shadow process: resolves prices
    through the SAME REST cascade the pockets themselves use, so the
    counterfactual is measured on the same data source as the real positions
    (a different source would make the comparison meaningless) and shares that
    cascade's own throttles and circuit breakers rather than adding a parallel,
    uncoordinated load on the same providers.

    Kept here, in the tracked repo and under test, precisely because its only
    caller lives OUTSIDE the repo (`shadow_persistent.py`) where nothing is
    covered by CI -- the call site there stays a single line."""
    from .solana_fresh_launch_ws_exit_shadow import _snapshot_with_fallback, geckoterminal_client

    async def _resolve(pool_address, mint, chain):
        snapshot = await _snapshot_with_fallback(geckoterminal_client, pool_address, mint, chain=chain)
        if not snapshot.available or snapshot.price_usd is None:
            return (None, None)
        return (snapshot.price_usd, snapshot.reserve_usd)

    stats = await advance_avoided_tracking(resolve_price_fn=_resolve, max_rows=max_rows, db_path=db_path)
    # Housekeeping rides this cycle rather than getting its own loop.
    purged = await purge_expired(db_path=db_path)
    if purged:
        stats["purged"] = purged
    return stats


async def purge_expired(*, db_path: str | None = None) -> int:
    """Removes decisions older than ``RETENTION_DAYS`` whose counterfactual is
    already resolved. Returns how many rows went. Best-effort: a purge failure
    must never disturb the loop that calls it."""
    try:
        path = db_path or _db_path()
        await _ensure_table(path)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                f"DELETE FROM {TABLE} WHERE decided_at < ? AND tracking_status != 'tracking'",
                (cutoff,),
            )
            await db.commit()
            return cur.rowcount or 0
    except Exception:  # noqa: BLE001 -- housekeeping never breaks the caller
        return 0


async def _set_status(path: str, row_id: int, status: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(f"UPDATE {TABLE} SET tracking_status = ? WHERE id = ?", (status, row_id))
        await db.commit()


async def avoided_pnl_summary(*, pocket: str | None = None, db_path: str | None = None) -> dict:
    """What the filter actually saved (or cost), on real subsequent prices.

    A POSITIVE ``avoided_pnl_pct`` means the blocked candidates would have
    LOST that much on average -- the filter saved it. A negative one means the
    filter is cutting winners, which is exactly the failure mode this table
    exists to make visible rather than assumable."""
    path = db_path or _db_path()
    await _ensure_table(path)
    where = "WHERE avoided_multiplier IS NOT NULL"
    params: list = []
    if pocket:
        where += " AND pocket = ?"
        params.append(pocket)

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT avoided_multiplier AS m FROM {TABLE} {where}", params)
        multipliers = [r["m"] for r in await cur.fetchall() if r["m"] is not None]
        cur = await db.execute(
            f"SELECT tracking_status AS s, COUNT(*) AS n FROM {TABLE} GROUP BY tracking_status"
        )
        by_status = {r["s"]: r["n"] for r in await cur.fetchall()}
        cur = await db.execute(
            f"SELECT reason AS r, COUNT(*) AS n, AVG(gate_latency_ms) AS lat FROM {TABLE} "
            f"WHERE blocked = 1 GROUP BY reason"
        )
        by_reason = [
            {"reason": r["r"], "n": r["n"], "avg_latency_ms": round(r["lat"], 1) if r["lat"] else None}
            for r in await cur.fetchall()
        ]

    if not multipliers:
        return {
            "n_measured": 0, "avoided_pnl_pct": None, "would_have_won_pct": None,
            "by_status": by_status, "by_reason": by_reason,
        }

    avg = sum(multipliers) / len(multipliers)
    winners = sum(1 for m in multipliers if m > 1.0)
    return {
        "n_measured": len(multipliers),
        # Sign flipped on purpose: a blocked candidate that would have lost 30%
        # means the filter AVOIDED +30%.
        "avoided_pnl_pct": round(-(avg - 1.0) * 100.0, 2),
        "would_have_won_pct": round(100.0 * winners / len(multipliers), 1),
        "by_status": by_status,
        "by_reason": by_reason,
    }
