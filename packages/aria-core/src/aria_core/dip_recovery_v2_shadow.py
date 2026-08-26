"""Dip-recovery shadow tracker, v2 -- Base/Robinhood market-cap-bounded
variant (26/08, operator-proposed: "sa serait interessant de lui brancher
base et robinhood comme noeud mais sur des tokens entre 50k et 1 milly post
bonding" / clarified: "oui la capitalisation, ducoup c'est sans bonding
disons un truc simple comme un filtre de 50k a 1 milly de market cap, 25k de
liquidite, et l'objectif c'est d'acheter tout se qui fait -30% sur 24h
minimum et de le revendre avec 25% de benef, un truc simpliste pour tester").

Distinct from dip_recovery_shadow.py (v1: Base-only via the shared
candle_history watchlist, fixed -5% stop, no take-profit, never activated
since 13/08 -- see docs/HANDOFF_AUDIT_LIVRAISON.md's own entry on that).
Same "-30%/24h" entry idea, everything else genuinely different: a
market-cap-bounded universe (50k-1M), a real liquidity floor (25k), BOTH
Base and Robinhood, and a fixed +25% take-profit instead of a stop-loss.
Built as a full separate module/table so it never touches v1's own episode
state or (nonexistent) sample -- same precedent as every other vN variant
in this project (robinhood_pump_v2_shadow.py, solana_support_bounce_v2_
shadow.py): a full independent copy, never a parameter bolted onto the
original.

**Sourcing, and why it can't reuse v1's candle_history/watchlist path**:
that path is Base-only today (`goplus_watchlist` holds 2000 Base rows, zero
Robinhood, verified live 26/08) and reading it would mean either extending
a SHARED 2000-slot watchlist other consumers depend on, or building a
second Robinhood-only watchlist -- both heavier than this test warrants.
Instead: `dexpaprika.get_trending_pools(chain, order_by=
"price_change_percentage_24h", sort="asc", min_liquidity_usd=
MIN_LIQUIDITY_USD)` -- a server-side liquidity floor plus a real "worst 24h
performers first" ordering (`sort="asc"`, verified live 26/08 against
docs.dexpaprika.com/tutorials/pool-filtering) -- gives a genuinely different
population from base_momentum_shadow.py/robinhood_pump_shadow.py's own
m5-surge-sorted fetch (which is sorted for PUMPING tokens and would never
surface a -30% dip). This module makes its OWN independent DexPaprika call
per chain rather than reusing theirs.

**Market cap, the one thing DexPaprika's pool search does not return**
(verified against its own docs, 26/08): resolved via ONE bounded
`dexscreener.fetch_token_pairs()` call per candidate that has ALREADY
cleared the -30%/24h + liquidity floor server-side -- the funnel doctrine
(cheap filter first, a paid call only on the surviving handful), same
"never a linear/unbounded resource pattern" bar CLAUDE.md sets. DexScreener
already carries `market_cap_usd`/`liquidity_usd`/`price_usd` for free in
that same response, so this call ALSO supplies the real entry price and a
second liquidity read (cross-checked against DexPaprika's own, never
blindly trusted from a single source).

**Take-profit is a FIXED +25%** (not trailing, no scale-out ladder) --
matches the operator's own words verbatim, no invented mechanic. A timeout
safety net (`MAX_HOLD_HOURS`) exists purely as a technical guard against a
position drifting open in this table forever absent any exit signal -- the
operator did not ask for a stop-loss this time (unlike v1's -5%), so NONE
is added; a losing position simply stays open and times out. Same "never
invent an unrequested rule" doctrine as v1's own docstring.

**Deduplication deliberately differs from v1's episode-state table** (caught
and fixed before shipping, 26/08): v1 dedupes on a recovery-triggered
episode flag because its `record_evaluation` sees EVERY watchlist token
EVERY cycle, recovered or not. v2's own discovery call only ever returns
the chain's current worst-24h performers -- a token that recovers simply
stops appearing in that feed, so a recovery-triggered episode flag would
latch `in_episode=True` forever with no path back to `False` (verified:
this exact bug existed in the first draft, caught by
`test_discover_rearms_after_recovery_above_threshold`). Dedup here is
therefore a direct check against an existing OPEN row for (contract,
chain) -- simpler, and correct for this sourcing shape: a fresh dip can
always open a new position once the previous one has actually closed
(take-profit or timeout), never while one is still open."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services import dexpaprika, dexscreener
# Reused rather than duplicated (architectural-coherence doctrine) -- these
# two formatting helpers are generic (a timestamp, a duration), not specific
# to shadow_notify.py's own scale-out-ladder shape which this pocket has
# none of (see the module's own notification section further below).
from aria_core.shadow_notify import _format_hold_duration, _local_hms

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

CHAINS = ("base", "robinhood")

# Candidate thresholds under shadow evaluation -- verbatim from the
# operator's own proposal (26/08), not independently tuned. Shadow mode
# exists precisely so these can be validated (or not) against real forward
# data before ever considering a live paper pocket.
DIP_THRESHOLD_PCT = -30.0
TAKE_PROFIT_PCT = 25.0
MIN_MARKET_CAP_USD = 50_000.0
MAX_MARKET_CAP_USD = 1_000_000.0
MIN_LIQUIDITY_USD = 25_000.0

# 26/08, operator-added requirement (session follow-up): "post bonding" is
# enforced not just by the market-cap floor but by a real minimum pair/pool
# age too -- a token that only just cleared 50k market cap hours after
# creation is a different (already-covered-elsewhere) risk profile from one
# that has traded for weeks. Sourced from TrendingPool.pool_created_at,
# already populated by dexpaprika.get_trending_pools at zero extra network
# cost (see research.md Decision 3) -- a candidate with no age data is never
# treated as qualifying by default.
MIN_POOL_AGE_DAYS = 14.0

# Safety net only, never an invented stop-loss -- see module docstring.
MAX_HOLD_HOURS = 168.0  # 7 days, same default as v1

# 26/08, Decision 2 (research.md) -- a guard against the same failure class
# confirmed live THE SAME DAY on base_momentum_shadow.py (a corrupted AMM
# reserve-ratio price read as "+707006.8% nominal, never executable"). Both
# pockets read prices from the same provider (DexScreener), so the risk is
# not hypothetical here either. Set at 50x rather than base_momentum's 1000x:
# that pocket's own scale-out tests legitimately simulate jumps up to 1000x,
# a mechanic this pocket has none of -- this pocket closes immediately once
# pnl_pct clears +25%, so a genuinely legitimate multi-hundred-x read should
# never be sitting in an open position here. 50x matches the precedent
# already used by solana_support_bounce_shadow.py, whose take-profit-style
# (non-scale-out) exit shape is the closer match.
EXIT_PRICE_SANITY_MULTIPLE = 50.0

# How many DexPaprika candidates to fetch per chain per pass -- the
# server-side liquidity floor already does most of the filtering, this just
# bounds how many surviving candidates get a DexScreener call.
DISCOVERY_LIMIT = 20

# 26/08, specs/013-dip-recovery-entry-sanity -- real incident: position id=13
# (contract 0x23acfab04106a21af0ae1643b74cfec3c9aac181, chain=robinhood)
# opened on a DexPaprika var_24h_pct of -31.9487%, the pocket's ONLY source
# for this field, with zero independent cross-check. Within minutes both
# DexScreener's live card and DexPaprika's own live lookup agreed the token
# was actually +29% -- the entry-time reading was never checked against
# anything outside DexPaprika itself before triggering a (shadow) buy, the
# same "a system's own data can never validate its own prices" gap already
# closed on the exit side (EXIT_PRICE_SANITY_MULTIPLE above). Rejects only a
# flat SIGN disagreement (DexPaprika strongly negative AND DexScreener
# strongly positive) -- see research.md Decision 1 for why a magnitude-delta
# threshold was rejected (it would collapse into "DexScreener must also show
# a big dip", rejecting the ordinary same-direction drift between two
# providers sampled moments apart). RECALIBRATE once enough real
# rejected/closed candidates accumulate to measure typical provider drift.
ENTRY_SANITY_MIN_CONFLICT_PCT = 10.0

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_tables() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        # 26/08 -- self-healing check, same rationale as the shadow pockets'
        # own _ensure_table fix the same day (an epoch-reset rename against
        # a live process left their cache stale): re-verify the table
        # actually exists even on a cache hit.
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dip_recovery_v2_shadow'"
            )
            if await cur.fetchone():
                return
        _ensured_db_paths.discard(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS dip_recovery_v2_shadow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                pool_address TEXT,
                symbol TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                entry_price REAL NOT NULL,
                entry_var_24h_pct REAL,
                entry_market_cap_usd REAL,
                entry_liquidity_usd REAL,
                entry_pool_age_days REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                exit_price REAL,
                close_reason TEXT,
                pnl_pct REAL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dip_recovery_v2_shadow_lookup "
            "ON dip_recovery_v2_shadow (contract, chain, status)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


def _hours_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


# Same realism doctrine as every other pocket in this dome (risk_guard's own
# DEX_SWAP_FEE_PCT, never a second-guessed figure restated here).
def _realistic_fill_price(price: float) -> float:
    from aria_core import risk_guard

    return price * (1.0 + risk_guard.DEX_SWAP_FEE_PCT)


def _realistic_exit_price(price: float) -> float:
    from aria_core import risk_guard

    return price * (1.0 - risk_guard.DEX_SWAP_FEE_PCT)


async def _resolve_market_cap_and_price(
    contract: str, chain: str, pool_address: str | None,
) -> dexscreener.PairSnapshot | None:
    """The one bounded, paid call this module makes per surviving candidate
    -- never fabricated, ``None`` on any failure or empty response (same
    never-fabricate dome doctrine as every sibling shadow module)."""
    try:
        pairs = await dexscreener.fetch_token_pairs(contract, chain=chain)
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the batch
        logger.info("dip_recovery_v2_shadow: fetch_token_pairs failed for %s (%s)", contract, exc)
        return None
    if not pairs:
        return None
    if pool_address:
        for p in pairs:
            if p.pair_address.lower() == pool_address.lower():
                return p
    # No exact pool match (or none supplied) -- the deepest pair for this
    # token is the most representative single read, never an arbitrary pick.
    return max(pairs, key=lambda p: p.liquidity_usd)


async def discover_and_record(chain: str) -> int:
    """One independent DexPaprika call for this chain (worst 24h performers
    first, server-side liquidity floor), one bounded DexScreener call per
    surviving candidate to resolve market cap. Returns the number of NEW
    shadow positions opened. Best-effort throughout: a single candidate's
    failure never blocks the rest of the batch."""
    opened = 0
    try:
        result = await dexpaprika.get_trending_pools(
            chain, order_by="price_change_percentage_24h", sort="asc",
            min_liquidity_usd=MIN_LIQUIDITY_USD, limit=DISCOVERY_LIMIT,
        )
    except Exception as exc:  # noqa: BLE001 -- one chain's outage never blocks the other
        logger.info("dip_recovery_v2_shadow: discovery failed for %s (%s)", chain, exc)
        return 0
    if not result.available:
        return 0

    await _ensure_tables()
    for pool in result.pools:
        var_24h = pool.price_change_pct.get("h24")
        if var_24h is None or var_24h > DIP_THRESHOLD_PCT:
            continue
        if not pool.token_address:
            continue
        # 26/08, Decision 3 -- a pool age we can't determine is never treated
        # as "old enough" by default (never-fabricate dome doctrine). Checked
        # here, before the paid DexScreener call, same funnel placement as
        # the var_24h check above.
        if pool.pool_created_at is None:
            continue
        age_days = (datetime.now(timezone.utc) - pool.pool_created_at).total_seconds() / 86400.0
        if age_days < MIN_POOL_AGE_DAYS:
            continue
        try:
            opened += await _maybe_open_position(
                chain, pool.token_address, pool.pool_address, var_24h, age_days,
            )
        except Exception as exc:  # noqa: BLE001 -- one candidate's failure never blocks the batch
            logger.info(
                "dip_recovery_v2_shadow: candidate advance failed for %s (%s)",
                pool.token_address, exc,
            )
    return opened


async def _has_open_position(db: aiosqlite.Connection, contract: str, chain: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM dip_recovery_v2_shadow WHERE contract = ? AND chain = ? AND status = 'open'",
        (contract, chain),
    )
    return await cur.fetchone() is not None


async def _maybe_open_position(
    chain: str, contract: str, pool_address: str | None, var_24h_pct: float, age_days: float,
) -> int:
    """26/08, Decision 1 -- dedup is a direct check against an already-open
    row, NOT a recovery-triggered episode flag (the flag approach, copied
    from v1, cannot re-arm here: this pocket's own discovery feed never
    re-surfaces a token once it recovers, so the flag could only ever latch
    True and never observe the transition back to False -- caught by
    test_discover_rearms_after_recovery_above_threshold against the
    original draft). A fresh position is allowed to open on this
    (contract, chain) any time no position for it is currently open."""
    async with aiosqlite.connect(_db_path()) as db:
        if await _has_open_position(db, contract, chain):
            return 0

        # Fresh, currently-unheld qualifying dip -- resolve market cap (the
        # one paid call) only now, on a candidate that has already cleared
        # every free/server-side filter.
        snapshot = await _resolve_market_cap_and_price(contract, chain, pool_address)
        if snapshot is None or not snapshot.price_usd:
            return 0
        # specs/013 -- cross-provider entry sanity guard (research.md Decision
        # 1): DexPaprika says a big dip AND DexScreener independently says a
        # big gain for the same candidate is never trusted as-is.
        if var_24h_pct <= DIP_THRESHOLD_PCT and snapshot.price_change_24h >= ENTRY_SANITY_MIN_CONFLICT_PCT:
            logger.info(
                "dip_recovery_v2_shadow: entry sanity guard rejected %s "
                "(dexpaprika=%.2f%%, dexscreener=%.2f%%)",
                contract, var_24h_pct, snapshot.price_change_24h,
            )
            return 0
        market_cap = snapshot.market_cap_usd
        if market_cap is None or not (MIN_MARKET_CAP_USD <= market_cap <= MAX_MARKET_CAP_USD):
            return 0
        if snapshot.liquidity_usd < MIN_LIQUIDITY_USD:
            return 0
        cur = await db.execute(
            """
            INSERT INTO dip_recovery_v2_shadow (
                contract, chain, pool_address, symbol, status, entry_price,
                entry_var_24h_pct, entry_market_cap_usd, entry_liquidity_usd,
                entry_pool_age_days, opened_at
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
            """,
            (
                contract, chain, snapshot.pair_address, snapshot.base_symbol or None,
                _realistic_fill_price(snapshot.price_usd), var_24h_pct,
                market_cap, snapshot.liquidity_usd, age_days, datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
        new_id = cur.lastrowid
        new_pool_address = snapshot.pair_address

    # 26/08, operator-directed ("un max de sqlite avec les log de suivi des
    # positions ouvertes") -- same standing convention as every other shadow
    # module since 18/08: archive the "before" candles this entry was based
    # on, so an alternate parameter set (different market-cap band, dip
    # threshold, take-profit level) can be honestly re-simulated against the
    # real price path later, however the "factory" defaults chosen today
    # turn out to perform. Done AFTER the DB connection above has closed
    # (same connection-hold-time avoidance as robinhood_pump_shadow.py's own
    # wiring) -- a genuinely new network call, not a free by-product, since
    # this pocket's entry signal itself needs only a spot price.
    try:
        before_ohlcv = await dexpaprika.get_ohlcv(new_pool_address, network=chain)
        if before_ohlcv.available and before_ohlcv.candles:
            from aria_core import shadow_candle_archive

            await shadow_candle_archive.store_candles(
                module="dip_recovery_v2", position_id=new_id,
                pool_address=new_pool_address, chain=chain, phase="before",
                candles=before_ohlcv.candles,
            )
    except Exception as exc:  # noqa: BLE001 -- archiving must never break the entry
        logger.info(
            "dip_recovery_v2_shadow: before-candle archive failed for %s (%s)",
            new_pool_address, exc,
        )
    return 1


async def advance_open_positions(chain: str) -> dict:
    """Checks every open position for this chain against the fixed +25%
    take-profit or the MAX_HOLD_HOURS timeout. Best-effort: one position's
    failure never blocks the rest."""
    counts = {"checked": 0, "closed_take_profit": 0, "closed_timeout": 0}
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM dip_recovery_v2_shadow WHERE chain = ? AND status = 'open'",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for row in rows:
        try:
            await _advance_one_position(row)
            counts["checked"] += 1
        except Exception as exc:  # noqa: BLE001 -- one position's failure never blocks the batch
            logger.info(
                "dip_recovery_v2_shadow: advance failed for %s (%s)", row["contract"], exc,
            )
            continue
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT close_reason FROM dip_recovery_v2_shadow WHERE id = ?", (row["id"],))
            updated = await cur.fetchone()
        if updated and updated["close_reason"] == "take_profit_25pct":
            counts["closed_take_profit"] += 1
        elif updated and updated["close_reason"] == "timeout_max_hold":
            counts["closed_timeout"] += 1
    return counts


async def _advance_one_position(row: dict) -> None:
    entry_price = row["entry_price"]
    if not entry_price:
        return

    # 26/08, operator-directed ("un max de sqlite avec les log de suivi des
    # positions ouvertes") -- archive the real price path for every open
    # position on every pass, not just at entry/close, so any alternate
    # exit rule (different take-profit level, a trailing stop, etc.) can be
    # honestly re-simulated later against real data regardless of how the
    # "factory" parameters shipped today end up performing. Best-effort,
    # never blocks the actual exit-check logic below.
    if row.get("pool_address"):
        try:
            after_ohlcv = await dexpaprika.get_ohlcv(row["pool_address"], network=row["chain"])
            if after_ohlcv.available and after_ohlcv.candles:
                from aria_core import shadow_candle_archive

                await shadow_candle_archive.store_candles(
                    module="dip_recovery_v2", position_id=row["id"],
                    pool_address=row["pool_address"], chain=row["chain"], phase="after",
                    candles=after_ohlcv.candles,
                )
        except Exception as exc:  # noqa: BLE001 -- archiving must never block the exit check
            logger.info(
                "dip_recovery_v2_shadow: after-candle archive failed for %s (%s)",
                row["pool_address"], exc,
            )

    snapshot = await _resolve_market_cap_and_price(row["contract"], row["chain"], row["pool_address"])
    age_hours = _hours_since(row["opened_at"]) or 0.0
    close_reason: str | None = None
    realistic_exit: float | None = None
    pnl_pct: float | None = None
    if snapshot is not None and snapshot.price_usd:
        if snapshot.price_usd <= entry_price * EXIT_PRICE_SANITY_MULTIPLE:
            realistic_exit = _realistic_exit_price(snapshot.price_usd)
            pnl_pct = (realistic_exit / entry_price - 1.0) * 100.0
            if pnl_pct >= TAKE_PROFIT_PCT:
                close_reason = "take_profit_25pct"
        else:
            logger.info(
                "dip_recovery_v2_shadow: implausible exit price for %s "
                "(quote=%.10g, entry=%.10g) -- skipping this pass's take-profit check",
                row["contract"], snapshot.price_usd, entry_price,
            )
    if close_reason is None and age_hours >= MAX_HOLD_HOURS:
        close_reason = "timeout_max_hold"
        if realistic_exit is None:
            # Timeout fires purely on age. If no plausible price was
            # available this pass (missing or rejected as implausible),
            # never fabricate a PnL from a bad quote -- close at entry_price
            # so realized PnL reads as the round-trip fee only.
            realistic_exit = _realistic_exit_price(entry_price)
            pnl_pct = (realistic_exit / entry_price - 1.0) * 100.0
    if close_reason is None:
        return
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE dip_recovery_v2_shadow SET status = 'closed', closed_at = ?,
                exit_price = ?, close_reason = ?, pnl_pct = ? WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), realistic_exit, close_reason, pnl_pct, row["id"]),
        )
        await db.commit()


async def run_cycle() -> dict:
    """Called once per heartbeat passage -- both chains, discovery then
    exit-tracking. Never touches the real $1M paper portfolio or any real
    capital, purely observational."""
    stats = {"opened": 0, "checked": 0, "closed_take_profit": 0, "closed_timeout": 0}
    for chain in CHAINS:
        stats["opened"] += await discover_and_record(chain)
        chain_stats = await advance_open_positions(chain)
        stats["checked"] += chain_stats["checked"]
        stats["closed_take_profit"] += chain_stats["closed_take_profit"]
        stats["closed_timeout"] += chain_stats["closed_timeout"]
    return stats


async def summary() -> dict:
    """Aggregate read for session/monitoring use -- never called from a real
    trading path."""
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT status, chain, contract, pnl_pct FROM dip_recovery_v2_shadow")
        rows = [dict(r) for r in await cur.fetchall()]
    closed = [r for r in rows if r["status"] == "closed"]
    wins = sum(1 for r in closed if (r["pnl_pct"] or 0) > 0)
    return {
        "open": sum(1 for r in rows if r["status"] == "open"),
        "closed": len(closed),
        "wins": wins,
        "distinct_tokens": len({(r["contract"], r["chain"]) for r in rows}),
        "avg_pnl_pct": (sum(r["pnl_pct"] or 0 for r in closed) / len(closed)) if closed else None,
    }


# --- Telegram open/close notifications (26/08, operator-directed: "je veux
# toutes les meme notif a l'identique sur telegram achat et vente") --------
#
# shadow_notify.py's own notify_pocket() is NOT reused here: it is built for
# Robinhood/Base's scale-out-ladder shape (SCALE_OUT_STEP_PCT, next_scale_
# level, m5/m15 surge fields) and is called from shadow_persistent.py, the
# standalone OUT-OF-REPO process -- this pocket runs in-process via
# heartbeat.py instead (same as v1, dip_recovery_shadow.py), never in that
# process. Same visual shape (OUVERTURE/CLOTURE, DexScreener link, a rolling
# aggregate) rebuilt against this pocket's OWN real fields (market cap,
# liquidity, 24h dip, pool age, fixed take-profit/timeout -- no scale-out,
# no m5/m15 surge data to report). The two small formatting helpers used
# below (_local_hms/_format_hold_duration) are imported from shadow_notify
# at the top of this file rather than duplicated here.

_last_notified_open_id: int | None = None
_notified_closed_ids: set[int] = set()
_NOTIFIED_CLOSED_MAX = 500


async def _dip_v2_aggregate() -> str:
    """Recent-30 then cumulative then 1h debit -- same reading order as
    shadow_notify.aggregate(), adapted to this pocket's own schema (no
    realistic/nominal distinction to make: pnl_pct here is ALREADY the
    fee-adjusted, realistic figure on every row, never a fictional one)."""
    try:
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT COUNT(*) n, AVG(pnl_pct) pnl, SUM(pnl_pct > 0) wins "
                "FROM (SELECT * FROM dip_recovery_v2_shadow WHERE status = 'closed' "
                "ORDER BY id DESC LIMIT 30)"
            )
            rec = dict(await cur.fetchone())
            cur = await db.execute(
                "SELECT COUNT(*) n, AVG(pnl_pct) pnl, SUM(pnl_pct > 0) wins "
                "FROM dip_recovery_v2_shadow WHERE status = 'closed'"
            )
            cum = dict(await cur.fetchone())
            cur = await db.execute("SELECT COUNT(*) n FROM dip_recovery_v2_shadow WHERE status = 'open'")
            ouvertes = (await cur.fetchone())["n"]
            depuis = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            cur = await db.execute(
                "SELECT SUM(opened_at >= ?) ouv, SUM(closed_at >= ?) clo FROM dip_recovery_v2_shadow",
                (depuis, depuis),
            )
            debit = dict(await cur.fetchone())
    except Exception:  # noqa: BLE001 -- the aggregate must never kill the notification
        return ""

    out = ""
    if rec.get("n"):
        wr = f"{100.0 * (rec['wins'] or 0) / rec['n']:.0f}%"
        out += f"\n{rec['n']} dernieres: winrate {wr}, PnL {rec['pnl'] or 0:+.1f}%"
    if cum.get("n"):
        wr = f"{100.0 * (cum['wins'] or 0) / cum['n']:.0f}%"
        out += f"\nCumul: {cum['n']} clot., winrate {wr}, {ouvertes} ouv., PnL {cum['pnl'] or 0:+.1f}%"
    out += f"\nDebit 1h: {debit.get('ouv') or 0} ouv., {debit.get('clo') or 0} clot."
    return out


def _dexscreener_link(row: dict) -> str:
    pool = row.get("pool_address") or ""
    return f"https://dexscreener.com/{row['chain']}/{pool}" if pool else ""


async def pending_notifications() -> list[str]:
    """Diff-based, same approach as shadow_notify.notify_pocket(): compares
    row ids between passes rather than the pocket module tracking its own
    "already notified" flag. Called once per heartbeat pass (heartbeat.py),
    after run_cycle() -- a failure here must never affect the pocket
    itself, so this never raises."""
    global _last_notified_open_id
    texts: list[str] = []
    try:
        await _ensure_tables()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            if _last_notified_open_id is None:
                cur = await db.execute("SELECT COALESCE(MAX(id), 0) FROM dip_recovery_v2_shadow")
                _last_notified_open_id = (await cur.fetchone())[0]
                opened_rows: list[dict] = []  # this pass only anchors, never replays history
            else:
                cur = await db.execute(
                    "SELECT * FROM dip_recovery_v2_shadow WHERE id > ? ORDER BY id ASC",
                    (_last_notified_open_id,),
                )
                opened_rows = [dict(r) for r in await cur.fetchall()]

            cur = await db.execute(
                "SELECT * FROM dip_recovery_v2_shadow WHERE status = 'closed' AND closed_at >= ? "
                "ORDER BY id ASC",
                ((datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),),
            )
            closed_rows = [dict(r) for r in await cur.fetchall()]

        agg = await _dip_v2_aggregate()

        for row in opened_rows:
            _last_notified_open_id = max(_last_notified_open_id, row["id"])
            texts.append(
                f"DIP-RECOVERY v2 ({row['chain']}) ({row.get('symbol') or row['contract'][:10]})\n"
                f"OUVERTURE\n"
                f"Market cap: ${(row.get('entry_market_cap_usd') or 0):.0f} "
                f"[bande ${MIN_MARKET_CAP_USD:.0f}-${MAX_MARKET_CAP_USD:.0f}]\n"
                f"Liquidite: ${(row.get('entry_liquidity_usd') or 0):.0f}\n"
                f"Variation 24h a l'entree: {row.get('entry_var_24h_pct'):.1f}%\n"
                f"Age du pool: {(row.get('entry_pool_age_days') or 0):.1f} j "
                f"[MIN {MIN_POOL_AGE_DAYS:.0f} j]\n"
                f"Entree: ${(row.get('entry_price') or 0):.10g} a {_local_hms(row.get('opened_at'))}\n"
                f"Sortie: take-profit fixe +{TAKE_PROFIT_PCT:.0f}% "
                f"| timeout {MAX_HOLD_HOURS / 24.0:.0f}j (pas de stop-loss)\n"
                + _dexscreener_link(row) + agg
            )

        for row in closed_rows:
            if row["id"] in _notified_closed_ids:
                continue
            _notified_closed_ids.add(row["id"])
            if len(_notified_closed_ids) > _NOTIFIED_CLOSED_MAX:
                for old_id in sorted(_notified_closed_ids)[:100]:
                    _notified_closed_ids.discard(old_id)
            pnl = row.get("pnl_pct")
            pnl_txt = f"{pnl:+.1f}%" if pnl is not None else "n/a"
            duree = _format_hold_duration(row.get("opened_at"), row.get("closed_at"))
            texts.append(
                f"DIP-RECOVERY v2 ({row['chain']}) ({row.get('symbol') or row['contract'][:10]})\n"
                f"CLOTURE -- {row.get('close_reason') or 'n/a'}\n"
                f"PnL: {pnl_txt}\n"
                f"Duree: {duree}\n"
                + _dexscreener_link(row) + agg
            )
    except Exception as exc:  # noqa: BLE001 -- notifications must never break the caller
        logger.info("dip_recovery_v2_shadow: pending_notifications failed (%s)", exc)
    return texts
