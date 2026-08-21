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

# 21/08, RAISED 0.40 -> 0.70 on 721 real closures. The band was widened to
# 0.40 the night before to COLLECT broadly and find out which sub-band works.
# It has now answered, and the answer is monotonic on both axes at once:
#     40-60%  n=327  rug 48.9%  win 37.0%  PnL -4.3%
#     60-70%  n=132  rug 43.2%  win 37.1%  PnL -4.0%
#     70-80%  n=140  rug 37.1%  win 42.9%  PnL +6.5%
#     80%+    n=122  rug 27.0%  win 50.0%  PnL +5.7%
# Rug risk nearly HALVES climbing the curve while the win rate rises -- a token
# that already convinced hundreds of buyers gets rug-pulled far less often than
# a fresh one. Yet 71% of entries were landing in 40-60%, the worst band of all
# (operator spotted this from a live screenshot before the data was queried).
#
# Second reason, operator's own: real on-chain execution is NOT instant. Curve
# drift during a ~5s execution window is negligible on a quiet token (+0.10%)
# but reaches several points on one actually moving (+47.5%/min average among
# risers) -- precisely the tokens worth buying. Latency therefore pushes the
# real entry UP the curve, which is the right direction, but it means a floor
# has to be set where the band is already good rather than where it is barely
# acceptable.
MIN_BONDING_PROGRESS = 0.70
# 21/08, RAISED 0.95 -> 0.985 on 676 real closures. The old ceiling existed
# because a curve past 90% can COMPLETE mid-position and migrate its liquidity
# to the AMM -- treated as a risk to avoid. The data says that is the single
# BEST outcome available:
#     stayed on the curve   n=620  winrate 32.6%  PnL  -15.93%
#     MIGRATED to PumpSwap  n= 54  winrate 87.0%  PnL +161.42%
# and the migrated figure survives the outlier test intact (+138.5% without its
# two best, 47 winners of 54). Graduation is also PREDICTABLE from entry
# position, monotonically:
#     40-60%: 2.0%   60-70%: 2.5%   70-80%: 8.3%   80-90%: 24.5%   90-95%: 50.0%
# So the ceiling was excluding exactly the band with the highest chance of the
# best outcome. Kept just below 1.0 rather than removed: at a fully complete
# curve there is no bonding liquidity left to enter against at all.
MAX_BONDING_PROGRESS = 0.985

# 20/08, RELAXED 3 -> 1 for the same collection reason. A candidate must still
# show SOME real buyer (buying what nobody buys is the behaviour the data
# condemns most clearly, -21.56% on the <30s band), but the exact N is one of
# the values this pocket exists to find -- fixing it at 3 up front would
# pre-decide the answer. `distinct_buyers_at_entry` is on every row, so the
# real threshold gets read off the data instead of guessed.
MIN_DISTINCT_BUYERS = 1

# 20/08, RELAXED 0.60 -> 0.95. Kept non-1.0 on purpose: at 100% a single
# wallet is literally the only buyer, which is not a market at all. Everything
# below that is COLLECTED rather than judged -- `top_buyer_share_at_entry` is
# recorded, so the real wash-trading cutoff is measurable later.
MAX_TOP_BUYER_SHARE = 0.95

# 20/08 -- raised with the widened band. The REAL constraint is the exit
# loop: more open positions means each one is checked less often, which is
# exactly what caused the late liquidity_collapse catches fixed earlier today
# (first check landing 32-116s after entry despite a 10s cadence). The exit
# sweep's own `limit` is raised in step below so widening collection cannot
# quietly re-create that failure.
# 21/08 -- a position whose token GRADUATED is exempt from max_hold.
# Measured on this pocket's own graduated closures: `trailing_stop` exits
# returned +228.3% (n=47, capturing 71% of a +296% peak) while `max_hold`
# exits returned -5.3% (n=12) despite having reached a +52.4% peak. Those 12
# were still alive when the clock killed them -- the trailing was armed and
# simply had not triggered, because the price had never fallen back far enough.
# A token that graduated has PROVEN its traction (87% winrate, +161% average),
# so it is handed to the trailing stop alone rather than to a timer that knows
# nothing about it. The trailing still protects the downside, and
# liquidity_collapse still applies.
EXEMPT_GRADUATED_FROM_MAX_HOLD = True

# How many of the most recent closures the 'recent' summary covers.
RECENT_WINDOW_CLOSURES = 50

# 21/08 -- CONFIG EPOCH. Everything closed before this instant was produced by
# a DIFFERENT configuration and must not be averaged with what follows: the
# 40-95% collection band, and a window where entry was priced by REST while the
# exit used the RPC (every PnL then compared two sources). Mixing them makes
# the headline meaningless, which is exactly the problem the recent-window fix
# was already treating.
#
# Deliberately an EPOCH MARKER, not a delete. The rows stay: they produced
# every finding of the last two days (the rug gradient 48.9% -> 27.0%, the
# graduation rate 2.0% -> 50.0%, the +161% on migrated positions) and this
# dome's standing rule is that real history is never destroyed. `summary()`
# reports from here; anything older is still queryable, just not averaged in.
# Move this forward on the NEXT configuration change rather than editing the
# rows.
CONFIG_EPOCH = "2026-08-21T11:05:00+00:00"

MAX_CONCURRENT_TRACKED = 60
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
    bonding_ws_feed=None,
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
        #  carries the decoded account fields the resolver used to discard
        # -- see PumpFunBondingCurveAccount. Falls back to a raw dict so an
        # injected test double can hand one directly.
        curve = getattr(account, "curve", None) or (account if isinstance(account, dict) else None)
        decimals = getattr(account, "token_decimals", None)

        accepted, reason, metrics = await screen_candidate(
            mint, pool_address, trade_stream=trade_stream, curve=curve, token_decimals=decimals,
        )

        snapshot = None
        if accepted:
            # 20/08 -- MUST price the entry from the SAME source the exit will
            # use. Real bug found live within 30 minutes of going live: the
            # entry was priced through the REST cascade while
            # `advance_exit_simulation` priced through the RPC feed, so every
            # PnL compared two different sources. It showed up as impossible
            # arithmetic -- a position whose reserve fell 53% reported a 79%
            # price drop, which a constant-product curve cannot produce.
            # Subscribing BEFORE pricing is what makes the RPC path available
            # on the very first read.
            if bonding_ws_feed is not None:
                try:
                    await bonding_ws_feed.add_pools([(pool_address, mint)])
                except Exception:  # noqa: BLE001 -- subscription is an enhancement
                    pass
            snapshot = await _price_position(
                {"pool_address": pool_address, "token_address": mint},
                chain=chain, bonding_ws_feed=bonding_ws_feed, snapshot_fn=snapshot_fn,
            )
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


async def _price_position(row: dict, *, chain: str, bonding_ws_feed, snapshot_fn):
    """Prices one open position, RPC FIRST.

    20/08 -- this pocket trades tokens that are BY DEFINITION still on their
    bonding curve, and a bonding curve's price is `virtual_quote_reserves /
    virtual_token_reserves`: the Helius websocket already pushes us those
    reserves, so the price is a local read. Going through the REST cascade
    (DexScreener -> GeckoTerminal) instead paid a rate-limited round trip for
    a number we were already being handed -- and GeckoTerminal was the only
    provider actually 429-ing under load (12 real 429s in 20 minutes,
    throttle auto-tightened 8s -> 12s).
    That cascade is NOT wrong, it is just built for MIGRATED tokens; it stays
    as the fallback for a curve that completed mid-position, whose liquidity
    has moved to the AMM and which the bonding feed then honestly reports as
    unavailable."""
    if bonding_ws_feed is not None:
        try:
            snap = bonding_ws_feed.get_snapshot(row["pool_address"])
            if getattr(snap, "available", False) and snap.price_usd is not None:
                return snap
        except Exception:  # noqa: BLE001 -- a feed hiccup falls through to REST
            pass
    fn = snapshot_fn or _snapshot_with_fallback
    return await fn(None, row["pool_address"], row["token_address"], chain=chain)


async def advance_exit_simulation(
    geckoterminal_client=None, *, chain: str = "solana", limit: int = 200,
    snapshot_fn=None, bonding_ws_feed=None, db_path: str | None = None,
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

    for row in rows:
        stats["checked"] += 1
        try:
            snapshot = await _price_position(
                row, chain=chain, bonding_ws_feed=bonding_ws_feed, snapshot_fn=snapshot_fn,
            )
        except Exception:  # noqa: BLE001 -- a provider failure is not a verdict
            continue
        if not snapshot.available or snapshot.price_usd is None:
            continue

        age = _minutes_since(row["detected_at"])
        graduated = snapshot.dex_id not in (None, "pumpfun")
        if graduated and EXEMPT_GRADUATED_FROM_MAX_HOLD:
            # Reported as age 0 so `evaluate_exit`'s max_hold branch never
            # fires -- the rule itself is left untouched and shared with the
            # sibling pockets, as the coherence rule requires.
            age = 0.0
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


async def summary(*, chain: str = "solana", since: str | None = None, db_path: str | None = None) -> dict:
    """Same shape as the sibling pockets' own summary, so the comparative
    report can treat all three identically.

    Reports from ``CONFIG_EPOCH`` by default -- closures from an earlier
    configuration are still in the table but are not averaged in. Pass
    ``since`` explicitly to read any other window, including the full history."""
    await _ensure_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT COALESCE(realistic_final_multiplier, final_multiplier) AS m, "
            f"bonding_progress_at_entry AS p FROM {TABLE} "
            f"WHERE chain = ? AND exit_reason IS NOT NULL AND detected_at >= ? "
            f"ORDER BY last_checked_at ASC", (chain, since or CONFIG_EPOCH),
        )
        closed = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            f"SELECT COUNT(*) AS n FROM {TABLE} WHERE chain = ? AND exit_reason IS NULL "
            f"AND detected_at >= ?", (chain, since or CONFIG_EPOCH),
        )
        open_n = (await cur.fetchone())["n"]

    mults = [c["m"] for c in closed if c["m"] is not None]
    wins = sum(1 for m in mults if m > 1.0)

    # 21/08 -- RECENT window alongside the cumulative one. Operator spotted the
    # real problem: the notification's PnL had not moved off -2.0% for over an
    # hour despite violent per-trade swings. The figure was correct but useless
    # -- at 775 closures each new one carries 1/776 of the average, so even a
    # +100% trade moves the headline by 0.13 points. Meanwhile the hourly
    # reality was +26.4% then -21.9%. A number that cannot move is a number
    # nobody can act on.
    recent = [c["m"] for c in closed[-RECENT_WINDOW_CLOSURES:] if c["m"] is not None]
    recent_wins = sum(1 for m in recent if m > 1.0)

    return {
        "recent_n": len(recent),
        "recent_win_rate": (recent_wins / len(recent)) if recent else None,
        "recent_avg_pnl_pct": (round((sum(recent) / len(recent) - 1) * 100, 2)) if recent else None,
        "completed": len(closed), "open": open_n,
        "win_rate": (wins / len(mults)) if mults else None,
        "avg_pnl_pct": (round((sum(mults) / len(mults) - 1) * 100, 2)) if mults else None,
        "avg_entry_progress": (
            round(sum(c["p"] for c in closed if c["p"] is not None) / max(1, sum(1 for c in closed if c["p"] is not None)), 3)
            if closed else None
        ),
    }
