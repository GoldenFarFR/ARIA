"""Support-bounce Solana shadow (17/08, operator-directed) -- a single new
strategy, replacing the 3-variant m5-threshold experiment: buy an
ESTABLISHED pool (age >= 70min, no upper bound -- deliberately testing
older pools after the HAROLD/EYE observation that age alone doesn't
separate outcomes, but age combined with low holder concentration might)
that is at worst mildly down over the last hour (h1 > -5%, widened 18/08 --
see ``MIN_H1_PCT``) but has pulled back close to
the LOW of its own recent 10-candle (5min each, 50min lookback) range --
a mean-reversion / "buy the dip within an uptrend" entry, deliberately
the opposite of the m5-surge momentum entries used everywhere else in this
project.

**Support tolerance is a first guess, not calibrated** (operator: "20% pour
commencer mais il faut un log avec des donnees pour voir si on peut le
calibrer mieux") -- every logged row stores the REAL distance from the
10-candle low at entry (``distance_from_support_pct``), even though only
rows within ``SUPPORT_TOLERANCE_PCT`` ever get logged at all right now.
Once enough real outcomes accumulate, a future pass can look at whether
tighter/looser tolerance would have performed better, using this column.

**Exit mechanics, deliberately simple, no scale-out ladder**: a single
-10% trailing stop from the peak price since entry (looser than the -20%
used elsewhere is WRONG -- this is TIGHTER, 10 vs 20 -- operator-specified
"stop loss suiveur -10%"), full-position exit when it fires (never partial).
``liquidity_collapse`` and ``max_hold`` (2h) survive as the two safety nets,
same doctrine and same PumpSwap-aware guard as every other shadow module in
this dome (see solana_pump_shadow.py's own comment for the full root-cause
writeup on why PumpSwap pools misreport reserve).

Same bright-line doctrine as every other module here: never trades real or
paper capital, never wired to the heartbeat, pure observation. Reuses
solana_pump_shadow.py's price-impact/fee simulation and DexScreener-primary
snapshot fallback rather than re-deriving them.

**Stop detection reads the OHLCV window, fill price stays the theoretical
threshold (18/08, MANDATORY convention for every shadow module in this
codebase)**: ``advance_exit_simulation`` also reads GeckoTerminal 5min
candles closed since the row's own ``last_checked_at`` and checks the stop
against ``effective_low = min(window_low, current_price)``, not the raw
point-sample alone -- a stop crossed then partially recovered between two
~75s polls is no longer invisible (same fix already live in
solana_pump_shadow.py/robinhood_pump_shadow.py since 16/08-17/08, propagated
here 18/08). The FILL price is a separate question and stays the stop's own
theoretical threshold (``peak_price * (1 - TRAILING_STOP_PCT/100)``) even
when the observed low is worse -- a real live incident (POE/Momota, 18/08)
prompted re-examining this, but the theoretical-fill convention is the
correct, deliberate, twice-tested one (2 real prior bugs -- a -20%-capped
stop realizing a 98% loss, SOLCATANA closing at -48.3% instead of its -20%
floor -- both from trusting the crash extreme instead of the calibrated
threshold). Never change the fill price to the observed extreme; only the
DETECTION should read the window.

Target: 50 closures before drawing any conclusion, same anti-overfitting
posture as every other shadow module here."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.services import dexpaprika, rugcheck
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    OHLCVResult,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)
from aria_core.solana_pump_shadow import (
    DEX_FEE_PCT,
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _epoch_of,
    _minutes_since,
    _snapshot_with_fallback,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())

TABLE = "solana_support_bounce_shadow_log"

# Entry criteria (operator-specified 17/08)
# 18/08, operator decision: raised from 0.0 (required h1 strictly positive)
# to -5.0 after observing the discovery funnel live -- get_trending_pools
# sorts by strongest 1h gainer, so most fetched candidates sit near the TOP
# of their own 10-candle range (still actively pumping), not the bottom,
# which structurally starves the support-distance filter downstream. Letting
# in mildly-cooling candidates (down to -5% over 1h, not just still-positive
# ones) gives more candidates a real chance of sitting near their recent low.
MIN_H1_PCT = -5.0
MIN_LIQUIDITY_USD = 5000.0
MIN_POOL_AGE_MINUTES = 70.0  # no upper bound, deliberately
SUPPORT_TOLERANCE_PCT = 20.0  # first guess, see module docstring -- to recalibrate
SUPPORT_CANDLE_COUNT = 10
SUPPORT_CANDLE_INTERVAL = "5m"
# 17/08, real bug caught live by the operator (BULLSHIT: range_high_10c was
# 7386x range_low_10c -- a near-total collapse within the 50min lookback,
# not a real consolidation. entry_price landed exactly at range_low,
# "distance 0%" was technically true but this was catching a falling knife
# after a crash, not a support bounce). No sane bound previously existed on
# how WIDE the 10-candle range itself could be. Claude's own pick (operator
# had no preference -- "je sais pas"), first guess like SUPPORT_TOLERANCE_PCT,
# to recalibrate once enough real outcomes accumulate.
MAX_RANGE_RATIO = 3.0

# Exit mechanics (operator-specified originally: "stop loss suiveur -10%",
# "aucun palier"). 18/08, second change: tightened 10.0 -> 5.0, operator-
# directed, ISOLATED single-variable test -- unlike v2 (which bundles this
# same stop change with 2 other recalibrations, MAX_RANGE_RATIO and a new
# MAX_H1_PCT ceiling), every other constant here is untouched, so a
# comparison against v2's own results can attribute any difference here
# purely to the stop width. The 160-closure 10% batch (winrate 43.1%, PnL
# +4.9% realistic) is archived, not discarded -- see the reset note on
# TARGET_CLOSURES below.
TRAILING_STOP_PCT = 5.0
MAX_HOLD_MINUTES = 120.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0

# 18/08, lowered back 150 -> 50, operator-directed, paired with the
# TRAILING_STOP_PCT change above -- same size as the very first batch this
# module ever ran (which came back net positive, winrate 45%, PnL +9.7%,
# before being raised to 150 the same day). A fresh, smaller, faster read
# on the new stop width before committing to another 150-closure run.
#
# 18/08, same day -- raised 50 -> 100, operator-directed: the first 50-batch
# read (74 closures logged before this cap fired, since record_signals only
# checks the cap BEFORE sourcing new candidates, not mid-cycle) surfaced a
# real signal worth confirming on a larger, still-fresh sample before
# touching the entry filter -- entries 10-15% into the support range
# (distance_from_support_pct) outperformed the naive "closest to support"
# 0-5% band on BOTH v1 and v2 independently, but each bucket's n (13-50) was
# too thin to trust. Never sourcing new candidates once the cap is hit means
# this run does NOT reset -- it just keeps accumulating past 74 toward 100
# on the exact same -5% stop / distance filter, so the comparison stays
# apples-to-apples. Once at 100: split train/validation, re-check the
# distance-bucket signal on each half independently before ever narrowing
# the entry filter based on it -- same anti-overfitting doctrine as the v8
# wick-gate incident (never promote a pattern mined on one un-split batch).
TARGET_CLOSURES = 100

# Columns added after the table's first version (18/08, operator-directed
# exhaustive-capture pass -- capture every already-fetched field, even ones
# that don't look useful yet, so future trades carry no analysis blind
# spots), PRAGMA-guarded ALTER TABLE so the already-existing prod DB
# migrates in place, same pattern as solana_pump_shadow.py.
_ADDED_COLUMNS: list[tuple[str, str]] = [
    # m5/h6/h24 were already fetched from DexPaprika alongside h1 (see
    # services/dexpaprika.py's price_change_pct dict) but discarded before
    # this change -- zero extra network cost, purely additive. dex_id/
    # volume_usd_24h/transactions_24h are likewise already in the raw
    # DexPaprika response (confirmed live via curl 18/08), never parsed
    # before now (see TrendingPool.dex_id's own docstring).
    ("m5_pct", "REAL"),
    ("h6_pct", "REAL"),
    ("h24_pct", "REAL"),
    ("volume_usd_24h", "REAL"),
    ("transactions_24h", "INTEGER"),
    ("dex_id", "TEXT"),
    # distance_from_support_pct (the 10-candle "official" one that gates
    # entry) stays unchanged -- these are ADDITIONAL readings computed from
    # the exact same already-fetched candle list at zero extra cost, so a
    # future pass can compare window sizes without needing to reconstruct
    # them from shadow_candle_archive (which only has candles for already-
    # ACCEPTED candidates -- see the 18/08 conversation this was born from).
    ("distance_from_support_pct_5", "REAL"),
    ("distance_from_support_pct_15", "REAL"),
    ("distance_from_support_pct_20", "REAL"),
    ("distance_from_support_pct_30", "REAL"),
]

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                token_address TEXT,
                chain TEXT NOT NULL DEFAULT 'solana',
                symbol TEXT,
                detected_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                h1_pct REAL,
                reserve_usd REAL,
                range_low_10c REAL,
                range_high_10c REAL,
                distance_from_support_pct REAL,
                pool_created_at TEXT,
                rugcheck_score INTEGER,
                rugcheck_risks TEXT,
                rugcheck_top_holder_pct REAL,
                rugcheck_creator TEXT,
                remaining_qty REAL NOT NULL DEFAULT 1.0,
                realized_proceeds REAL NOT NULL DEFAULT 0.0,
                peak_price REAL,
                exit_reason TEXT,
                final_multiplier REAL,
                last_checked_at TEXT,
                last_price REAL,
                last_reserve_usd REAL,
                realistic_entry_price REAL,
                realistic_realized_proceeds REAL NOT NULL DEFAULT 0.0,
                realistic_final_multiplier REAL
            )
            """
        )
        existing = {
            row[1] for row in await (await db.execute(f"PRAGMA table_info({TABLE})")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}")
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_lookup ON {TABLE} (pool_address, chain, exit_reason)"
        )
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_detected_at ON {TABLE} (detected_at)")
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        f"SELECT 1 FROM {TABLE} WHERE pool_address = ? AND chain = ? AND exit_reason IS NULL LIMIT 1",
        (pool_address, chain),
    )
    return (await cur.fetchone()) is not None


async def closures_so_far() -> int:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE exit_reason IS NOT NULL")
        (count,) = await cur.fetchone()
    return count


async def record_signals(pools: list[TrendingPool], *, chain: str = "solana") -> int:
    """Each candidate must pass, in order: h1 > -5% (should already be true --
    the caller is expected to have used ``dexpaprika.get_trending_pools``
    with ``order_by="price_change_percentage_1h"``, this is a defensive
    re-check, never trusted blindly), liquidity floor, age floor (no
    ceiling). Only candidates that clear ALL THREE get the extra OHLCV call
    to check the support condition -- keeps the real cost proportional to
    genuinely plausible candidates, not the full fetched list.

    **17/08, real bug found live** (recurring ``database is locked`` in
    ``shadow_persistent.py``'s exit-tracking loop right as this function was
    mid-batch) -- the SQLite connection used to stay open for the WHOLE
    candidate loop, including every slow network call (candle fetch under
    DexPaprika contention/retries, rugcheck lookup) in between. Under real
    DexPaprika rate-limiting each candidate's candle fetch can take many
    seconds (3 retries with backoff), so a batch of a dozen+ candidates held
    the write connection open for minutes -- long enough to collide with
    ``exit_tracking_loop``'s own writes on the same ``shadow.db`` file every
    60s. Fixed by splitting into three passes: (1) cheap synchronous filters
    + a SHORT dedup-check connection per candidate, (2) all network
    enrichment (candle fetch, rugcheck) with NO connection open at all, (3) a
    single short connection at the end for every INSERT, batched together."""
    logged = 0
    try:
        await _ensure_table()
        if (await closures_so_far()) >= TARGET_CLOSURES:
            return 0

        candidates: list[TrendingPool] = []
        async with aiosqlite.connect(_db_path()) as db:
            for pool in pools:
                h1 = pool.price_change_pct.get("h1")
                if h1 is None or h1 <= MIN_H1_PCT:
                    continue
                if (pool.reserve_usd or 0.0) < MIN_LIQUIDITY_USD:
                    continue
                if pool.pool_created_at is None or pool.price_usd is None:
                    continue
                age_minutes = (datetime.now(timezone.utc) - pool.pool_created_at).total_seconds() / 60.0
                if age_minutes < MIN_POOL_AGE_MINUTES:
                    continue
                if await _has_open_signal(db, pool.pool_address, chain):
                    continue
                candidates.append(pool)

        rows_to_insert: list[tuple] = []
        candles_by_row: list[list] = []
        for pool in candidates:
            h1 = pool.price_change_pct.get("h1")
            try:
                candles = await dexpaprika._fetch_one_interval(
                    pool.pool_address, chain, SUPPORT_CANDLE_INTERVAL,
                )
            except Exception as exc:  # noqa: BLE001 -- one candidate's failure never blocks the batch
                logger.info(
                    "solana_support_bounce_shadow: candle fetch failed for %s (%s)",
                    pool.pool_address, exc,
                )
                continue
            if len(candles) < SUPPORT_CANDLE_COUNT:
                continue
            last_n = candles[-SUPPORT_CANDLE_COUNT:]
            range_low = min(c.low for c in last_n)
            range_high = max(c.high for c in last_n)
            if not range_low or range_high <= range_low:
                continue  # a degenerate/flat range has no meaningful "position within it"
            if range_high / range_low > MAX_RANGE_RATIO:
                continue  # a near-total collapse within the lookback, not a real consolidation

            # 17/08, real bug caught live by the operator (Niles: distance
            # -21.6%, contradicted by the actual chart showing a recovery,
            # not a breakdown). Root cause traced from the real DB row:
            # `pool.price_usd` is a snapshot from the SINGLE broad
            # get_trending_pools() call made once at the top of the
            # discovery cycle, but under real DexPaprika contention each
            # candidate's own candle fetch can lag that snapshot by MINUTES
            # (confirmed: Niles was detected ~3min after this cycle's
            # discovery call). If price moves meaningfully in that gap, the
            # STALE scan-time price gets compared against a FRESH candle
            # range -- exactly what produced the artifact distance. Fixed by
            # using the last candle's close (the freshest price this
            # function has actually just fetched) as the reference for both
            # the support-distance check AND the simulated entry, instead of
            # the stale scan-time snapshot.
            current_price = last_n[-1].close or pool.price_usd

            # 17/08, second real bug caught live by the operator (Redbull:
            # entry_price landed EXACTLY equal to range_high, the top of its
            # own 10-candle range, not the bottom). Root cause: the original
            # formula measured distance from the low in absolute percentage
            # terms -- (price/range_low - 1)*100 -- which is trivially small
            # whenever the WHOLE range is narrow (a smooth, uninterrupted
            # climb with no real pullback), even when price sits at the very
            # top of that narrow range. Fixed by measuring where price sits
            # WITHIN the range instead: 0% = at the low, 100% = at the high,
            # regardless of how wide or narrow the range itself is -- the
            # column keeps its name (distance_from_support_pct) but now
            # means "position within the range", a strictly more correct
            # reading of "how close to support".
            distance_from_support_pct = (current_price - range_low) / (range_high - range_low) * 100.0
            # A NEGATIVE value means price is BELOW the 10-candle low -- the
            # pool breaking down through its own recent support, not
            # bouncing off it. Never accept it, same doctrine either way.
            if distance_from_support_pct > SUPPORT_TOLERANCE_PCT or distance_from_support_pct < 0:
                continue

            # 18/08, operator-directed exhaustive-capture pass: additional
            # readings at other window sizes, computed for FREE from the
            # exact same `candles` list already fetched above -- never used
            # to gate entry (only the official 10-candle distance above
            # does), purely observational so a future pass has same-cycle
            # multi-window data instead of reconstructing from the "before"
            # candle archive (which only covers candidates already accepted
            # under window=10 -- see the 18/08 conversation this was born
            # from for the full reasoning).
            distance_from_support_pct_by_n: dict[int, float | None] = {}
            for alt_n in (5, 15, 20, 30):
                if len(candles) < alt_n:
                    distance_from_support_pct_by_n[alt_n] = None
                    continue
                alt_window = candles[-alt_n:]
                alt_low = min(c.low for c in alt_window)
                alt_high = max(c.high for c in alt_window)
                if not alt_low or alt_high <= alt_low:
                    distance_from_support_pct_by_n[alt_n] = None
                else:
                    distance_from_support_pct_by_n[alt_n] = (current_price - alt_low) / (alt_high - alt_low) * 100.0

            rugcheck_score: int | None = None
            rugcheck_risks: str | None = None
            rugcheck_top_holder_pct: float | None = None
            rugcheck_creator: str | None = None
            if pool.token_address:
                try:
                    report = await rugcheck.get_token_report(pool.token_address)
                    if report.available:
                        rugcheck_score = report.score_normalised
                        rugcheck_risks = ",".join(report.risks) if report.risks else None
                        rugcheck_top_holder_pct = report.top_holder_pct
                        rugcheck_creator = report.creator
                except Exception as exc:  # noqa: BLE001 -- enrichment must never break the log pass
                    logger.info(
                        "solana_support_bounce_shadow: rugcheck lookup failed for %s (%s)",
                        pool.token_address, exc,
                    )

            realistic_entry_price = _apply_price_impact_and_fee(
                current_price, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                reserve_usd=pool.reserve_usd, side="buy",
            )

            rows_to_insert.append((
                pool.pool_address, pool.token_address, chain, pool.symbol,
                datetime.now(timezone.utc).isoformat(), current_price,
                h1, pool.reserve_usd, range_low, range_high, distance_from_support_pct,
                current_price,
                pool.pool_created_at.isoformat(),
                rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator,
                realistic_entry_price,
                pool.price_change_pct.get("m5"), pool.price_change_pct.get("h6"), pool.price_change_pct.get("h24"),
                pool.volume_usd_24h, pool.transactions_24h, pool.dex_id,
                distance_from_support_pct_by_n[5], distance_from_support_pct_by_n[15],
                distance_from_support_pct_by_n[20], distance_from_support_pct_by_n[30],
            ))
            candles_by_row.append(candles)

        if rows_to_insert:
            # 18/08, operator-directed: archive the "before" candles this
            # entry decision was actually based on -- needs the real row id
            # from lastrowid, so per-row INSERT instead of the previous
            # executemany batch (candidate lists here are always small,
            # a handful per cycle, never the large batch the 17/08
            # connection-hold-time bug was about).
            from aria_core import shadow_candle_archive

            async with aiosqlite.connect(_db_path()) as db:
                for row, candles in zip(rows_to_insert, candles_by_row):
                    cur = await db.execute(
                        f"""
                        INSERT INTO {TABLE} (
                            pool_address, token_address, chain, symbol, detected_at, entry_price,
                            h1_pct, reserve_usd, range_low_10c, range_high_10c, distance_from_support_pct,
                            remaining_qty, realized_proceeds, peak_price,
                            pool_created_at, rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct,
                            rugcheck_creator, realistic_entry_price,
                            m5_pct, h6_pct, h24_pct, volume_usd_24h, transactions_24h, dex_id,
                            distance_from_support_pct_5, distance_from_support_pct_15,
                            distance_from_support_pct_20, distance_from_support_pct_30
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        row,
                    )
                    new_id = cur.lastrowid
                    await db.commit()
                    await shadow_candle_archive.store_candles(
                        module="solana_support_bounce", position_id=new_id,
                        pool_address=row[0], chain=chain, phase="before", candles=candles,
                    )
            logged = len(rows_to_insert)
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("solana_support_bounce_shadow: record_signals failed (%s)", exc)
    return logged


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 30,
) -> dict[str, int]:
    """No scale-out ladder -- only two possible closes: ``trailing_stop``
    (-10% from the running peak, FULL position, never partial) or the two
    safety nets (``liquidity_collapse``, ``max_hold``). Priority order:
    liquidity_collapse first (unrelated to price, protects against an
    unsellable pool), then the trailing stop, then max_hold as the final
    catch-all. Same PumpSwap dex_id guard as every other module here --
    see solana_pump_shadow.py's comment for the full root-cause writeup."""
    client = client or geckoterminal_client
    counts = {"checked": 0, "closed_trailing_stop": 0, "closed_max_hold": 0, "closed_liquidity_collapse": 0}
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM {TABLE} WHERE chain = ? AND exit_reason IS NULL ORDER BY detected_at ASC LIMIT ?",
                (chain, limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]

        for row in rows:
            age_minutes = _minutes_since(row["detected_at"])
            if age_minutes is None:
                continue
            entry_price = row["entry_price"]
            if not entry_price:
                continue

            try:
                snapshot: PoolSnapshot = await _snapshot_with_fallback(
                    client, row["pool_address"], row["token_address"], chain=chain,
                )
            except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                logger.info(
                    "solana_support_bounce_shadow: snapshot failed for %s (%s)", row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue
            counts["checked"] += 1
            current_price = snapshot.price_usd

            # 18/08, operator-directed exhaustive-capture pass: archive the
            # FULL snapshot this check just fetched anyway (zero extra
            # network cost), not just the price_usd/reserve_usd this
            # function already reads. Best-effort, never blocks exit logic.
            # NOTE: `_snapshot_with_fallback` tries DexScreener FIRST (see
            # its own docstring) -- that path only ever fills price_usd/
            # reserve_usd/dex_id, price_change_pct/transactions/volume_usd
            # stay empty unless the GeckoTerminal fallback was actually
            # used. Honest gap, not a bug: DexScreener genuinely doesn't
            # expose those fields the way GeckoTerminal's pool endpoint does.
            from aria_core import shadow_snapshot_archive

            await shadow_snapshot_archive.store_snapshot(
                module="solana_support_bounce", position_id=row["id"],
                pool_address=row["pool_address"], chain=chain,
                price_usd=snapshot.price_usd, reserve_usd=snapshot.reserve_usd,
                dex_id=snapshot.dex_id, price_change_pct=snapshot.price_change_pct,
                transactions=snapshot.transactions, volume_usd=snapshot.volume_usd,
            )

            # 18/08 -- same window-detection fix already live in
            # solana_pump_shadow.py/robinhood_pump_shadow.py since 16/08-17/08
            # (2 real live bugs there: a -20%-capped stop realizing a 98%
            # loss, SOLCATANA closing at -48.3% instead of its -20% floor --
            # both caused by a point-sample-only poll MISSING a stop that had
            # already been crossed-then-crashed-further between two checks).
            # This module was still point-sample-only, exposed to the same
            # detection gap. Reads candles closed since this row's own
            # `last_checked_at` and folds the window low with the current
            # spot -- a stop crossed and not yet re-polled is no longer
            # invisible. The FILL price is UNCHANGED (still the stop's own
            # theoretical threshold, never the crash extreme -- that
            # limit-order-style fill is the deliberate, already-tested
            # convention this codebase settled on, not a bug).
            window_high = current_price
            window_low = current_price
            try:
                ohlcv: OHLCVResult = await client.get_ohlcv(
                    row["pool_address"], network=chain, mode="scalping_5m",
                )
            except Exception as exc:  # noqa: BLE001 -- OHLCV is an enhancement, never a hard requirement
                logger.info(
                    "solana_support_bounce_shadow: advance_exit_simulation get_ohlcv failed for %s (%s)",
                    row["pool_address"], exc,
                )
                ohlcv = None
            if ohlcv is not None and ohlcv.available and ohlcv.candles:
                boundary_epoch = _epoch_of(row.get("last_checked_at") or row["detected_at"])
                new_candles = [
                    c for c in ohlcv.candles if boundary_epoch is None or c.ts > boundary_epoch
                ]
                if new_candles:
                    window_high = max(c.high for c in new_candles)
                    window_low = min(c.low for c in new_candles)
                    from aria_core import shadow_candle_archive

                    await shadow_candle_archive.store_candles(
                        module="solana_support_bounce", position_id=row["id"],
                        pool_address=row["pool_address"], chain=chain, phase="after",
                        candles=new_candles,
                    )
            effective_high = max(window_high, current_price)
            effective_low = min(window_low, current_price)

            peak_price = row["peak_price"] or entry_price
            peak_price = max(peak_price, effective_high)
            remaining_qty = row["remaining_qty"] if row["remaining_qty"] is not None else 1.0
            realized_proceeds = row["realized_proceeds"] or 0.0

            realistic_entry_price = row.get("realistic_entry_price")
            realistic_realized_proceeds = row.get("realistic_realized_proceeds") or 0.0
            realistic_unreachable = realistic_entry_price is None

            def _realistic_sell(qty_fraction: float, ideal_price: float) -> None:
                nonlocal realistic_realized_proceeds, realistic_unreachable
                if realistic_unreachable:
                    return
                impacted = _apply_price_impact_and_fee(
                    ideal_price, trade_size_usd=qty_fraction * SIMULATED_TRADE_SIZE_USD,
                    reserve_usd=snapshot.reserve_usd, side="sell",
                )
                if impacted is None:
                    realistic_unreachable = True
                    return
                realistic_realized_proceeds += qty_fraction * impacted

            is_pumpswap = snapshot.dex_id == "pumpswap"
            entry_reserve = row.get("reserve_usd")
            liquidity_collapsed = (
                not is_pumpswap
                and entry_reserve is not None and entry_reserve > 0
                and snapshot.reserve_usd is not None
                and snapshot.reserve_usd < entry_reserve * (1 - LIQUIDITY_COLLAPSE_EXIT_PCT / 100.0)
            )
            trailing_stop_hit = effective_low <= peak_price * (1 - TRAILING_STOP_PCT / 100.0)

            exit_reason: str | None = None
            if liquidity_collapsed:
                _realistic_sell(remaining_qty, current_price)
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "liquidity_collapse"
            elif trailing_stop_hit:
                stop_price = peak_price * (1 - TRAILING_STOP_PCT / 100.0)
                _realistic_sell(remaining_qty, stop_price)
                realized_proceeds += remaining_qty * stop_price
                remaining_qty = 0.0
                exit_reason = "trailing_stop"
            elif age_minutes >= MAX_HOLD_MINUTES:
                _realistic_sell(remaining_qty, current_price)
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "max_hold"

            final_multiplier = (realized_proceeds / entry_price) if exit_reason else None
            realistic_final_multiplier = (
                realistic_realized_proceeds / realistic_entry_price
                if exit_reason and not realistic_unreachable and realistic_entry_price
                else None
            )

            async with aiosqlite.connect(_db_path()) as db:
                await db.execute(
                    f"""
                    UPDATE {TABLE} SET
                        peak_price = ?, remaining_qty = ?, realized_proceeds = ?, exit_reason = ?,
                        final_multiplier = ?, last_checked_at = ?, last_price = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?, last_reserve_usd = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, remaining_qty, realized_proceeds, exit_reason, final_multiplier,
                        datetime.now(timezone.utc).isoformat(), current_price,
                        realistic_realized_proceeds, realistic_final_multiplier, snapshot.reserve_usd,
                        row["id"],
                    ),
                )
                await db.commit()

            if exit_reason == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif exit_reason == "max_hold":
                counts["closed_max_hold"] += 1
            elif exit_reason == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("solana_support_bounce_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def chain_pnl_summary_realistic(chain: str = "solana") -> dict:
    """17/08, real gap found live by the operator ("sur bounce je ne vois
    pas le pnl") -- this pocket's notify functions mirrored the retired
    3-variant experiment's pattern (progress/winrate only, no dollar PnL),
    which was fine when a main pocket already showed the $ figure elsewhere.
    Now that support-bounce is the ONLY active pocket, that gap is real.
    Same aggregate as solana_pump_shadow.chain_pnl_summary_realistic,
    ported here since this table carries the same realistic_* columns
    (liquidity-aware: a row whose realistic_entry_price is NULL means the
    entry itself was already too shallow to fill a SIMULATED_TRADE_SIZE_USD
    trade -- counted in unreachable_liquidity, never silently dropped)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT realistic_entry_price, remaining_qty, realistic_realized_proceeds, "
            f"realistic_final_multiplier, last_price, exit_reason FROM {TABLE} WHERE chain = ?",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    total_pnl_units = 0.0
    closed = 0
    open_valued = 0
    pending_price = 0
    unreachable_liquidity = 0
    stranded = 0
    for r in rows:
        entry = r["realistic_entry_price"]
        if entry is None:
            unreachable_liquidity += 1
            continue
        if r["exit_reason"] is not None:
            if r["realistic_final_multiplier"] is not None:
                closed += 1
                total_pnl_units += r["realistic_final_multiplier"] - 1.0
            else:
                # Bought-then-stranded capital (pool drained mid-flight) is a
                # LOSS, never an unmeasurable event -- see solana_pump_shadow's
                # own comment for the full survivorship-bias writeup this
                # guards against.
                stranded += 1
                salvaged = r["realistic_realized_proceeds"] or 0.0
                total_pnl_units += salvaged / entry - 1.0
            continue
        if r["last_price"] is None:
            pending_price += 1
            continue
        open_valued += 1
        remaining = r["remaining_qty"] if r["remaining_qty"] is not None else 1.0
        realized = r["realistic_realized_proceeds"] or 0.0
        current_value = realized + remaining * r["last_price"]
        total_pnl_units += current_value / entry - 1.0

    positions_funded = closed + stranded + open_valued + pending_price
    capital_deployed_usd = positions_funded * SIMULATED_TRADE_SIZE_USD
    total_pnl_usd = total_pnl_units * SIMULATED_TRADE_SIZE_USD
    return {
        "total_pnl_units": total_pnl_units,
        "total_pnl_usd": total_pnl_usd,
        "capital_deployed_usd": capital_deployed_usd,
        "return_on_deployed_pct": (
            total_pnl_usd / capital_deployed_usd * 100.0 if capital_deployed_usd else 0.0
        ),
        "closed": closed,
        "stranded": stranded,
        "open_valued": open_valued,
        "pending_price": pending_price,
        "unreachable_liquidity": unreachable_liquidity,
    }


async def summary(*, chain: str = "solana") -> dict:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT exit_reason, final_multiplier, distance_from_support_pct FROM {TABLE} "
            "WHERE chain = ? AND final_multiplier IS NOT NULL",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    wins = sum(1 for r in rows if r["final_multiplier"] > 1.0)
    by_exit_reason: dict[str, int] = {}
    for r in rows:
        by_exit_reason[r["exit_reason"]] = by_exit_reason.get(r["exit_reason"], 0) + 1
    return {
        "completed": len(rows),
        "target": TARGET_CLOSURES,
        "wins": wins,
        "win_rate": (wins / len(rows)) if rows else None,
        "avg_multiplier": (sum(r["final_multiplier"] for r in rows) / len(rows)) if rows else None,
        "by_exit_reason": by_exit_reason,
    }
