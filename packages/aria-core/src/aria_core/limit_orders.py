"""Limit-order mechanism for the momentum paper-trading pipeline (07/23,
operator-designed and cross-reviewed before implementation).

The problem it solves: between signal detection and execution, a candidate
goes through honeypot/OHLCV/LLM analysis -- on a volatile token, the price can
drift upward enough that the R/R at execution no longer clears the entry bar
(``paper_trader._execution_rr_still_valid``). Until now this was a plain
reject (``funnel["price_stale_at_execution"]``), discarding a setup that only
got MORE EXPENSIVE, not a dead one -- the real CHECK case (0.038 signal price
-> 0.044 execution price, R/R degraded from 3.9 to 1.52).

Instead of rejecting outright, a limit order is placed at the ORIGINAL signal
price and watched by ``momentum_websocket._drain_once()`` (already polling
prices every 30s) until the price comes back down to it, the structure
breaks (invalidation crossed), or it expires (``LIMIT_ORDER_EXPIRY_HOURS``).

Two cases are drawn explicitly, never conflated:
  (a) structure already broken (fresh price through the invalidation, or a
      security re-check fails) -> reject outright, exactly as before this
      mechanism existed. A limit order is NEVER placed on a dead setup.
  (b) the setup only drifted upward, structure still intact -> a limit order
      is worth placing, waiting for a pullback to the original price.

State machine: ``pending`` (just placed, price still far above target) ->
``watching`` (price within ``LIMIT_ORDER_WATCH_TRIGGER_MULT`` of target, one
re-analysis performed at this transition) -> ``triggered`` (bought) /
``cancelled`` (invalidation crossed, or the re-analysis failed) / ``expired``
(silent, just logged -- never a Telegram alert for a setup that simply never
came back).

27/07 -- 3-pocket architecture plan, Phase 2 (see paper_trader.py's own
``multi_pocket_sourcing_enabled()``/``_open_new_entries_for_wallet``): every
pending order now remembers which pocket ("swing"/"scalping"/"vc") placed it
(``wallet`` column, additive hot migration -- default 'swing', same migration
decision as ``paper_position.wallet``) and executes into that SAME pocket,
never a hardcoded one. ``has_active_order``/``create_pending_order`` default
to ``wallet="swing"`` -- unchanged behavior for any caller that doesn't pass
it explicitly, i.e. every caller while ``paper_trader.
multi_pocket_sourcing_enabled()`` is OFF (the only caller today,
``paper_trader._open_new_entries_for_wallet``, always passes its own
``wallet=`` explicitly)."""
from __future__ import annotations

import html
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core import rsi_divergence_log
from aria_core.paths import aria_db_path
from aria_core.services.dexscreener import token_url

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Explicit operator decisions, 07/23 (design cross-reviewed before coding).
LIMIT_ORDER_WATCH_TRIGGER_MULT = 1.10  # enters "watching" once price <= target * 1.10
LIMIT_ORDER_EXPIRY_HOURS = 3.0  # short-lived -- momentum setups go stale fast

# Item #158, 28/07: a bonding-curve token still sitting near
# bonding_entry._MIN_LIQUIDITY_USD (5,000$, #167) moves too erratically for a
# "wait for the price to come back down" mechanism to mean anything -- the
# whole premise of a limit order (a pullback to a still-valid original setup)
# assumes some baseline stability this thin a market doesn't have yet.
# liquidity_usd is used as the market-cap proxy here, same doctrine already
# established in bonding_entry.py ("liquidité quasiment 1 pour 1 avec le
# market cap" on a bonding curve) -- never a separate $VIRTUAL->USD mcap
# conversion just for this gate. Starting value, to recalibrate once real
# bonding limit orders accumulate outcomes.
BONDING_LIMIT_ORDER_MIN_LIQUIDITY_USD = 20_000.0

# 01/08 -- real bug found live (Workflow audit triggered by a GeckoTerminal
# 429 rate that stayed flat, ~59-60/15min, even after 3 unrelated
# uncoordinated-client fixes landed the same day): EVERY "watching" order
# whose resolution goes through check_rsi_divergence_watching_order (the
# rsi_divergence_pending reason, or any swing order per the 31/07 fine-grained
# WATCH decision above) re-fetched FRESH candles on EVERY drain pass -- no
# cache, no cap -- unlike every other GeckoTerminal-heavy path in this
# pipeline (_gates_cache in scalping_variants.py, MAX_CANDIDATES_PER_DRAIN,
# MAX_EVALUATIONS_PER_HOUR). Measured live: 104 of 105 watching orders used
# this path, drained every 30s -> ~208 calls/min of DEMAND against a
# throttle-imposed ~21 req/min SUPPLY (services/geckoterminal.py's shared
# lock) -- the lock caps the outgoing rate correctly, but the queue behind it
# never empties, a saturated-queue 429 pattern rather than a raw-overshoot
# one.
#
# Fix: cap how many rsi-divergence watching orders get a fresh candle re-check
# per drain pass, rotating oldest-checked-first (_rsi_watch_check_last_at
# below) so every order still eventually gets re-checked, just not every 30s
# -- the underlying candles (15-30min scalping-scale, or the standard
# day/4h/1h ladder for VC) don't carry new information anywhere near that
# often anyway. A hard per-pass cap (not a fixed time TTL) was chosen over a
# cooldown window because it bounds worst-case load regardless of how many
# orders accumulate -- more orders means a slower rotation, never more
# requests, mirroring MAX_MANUAL_CANDIDATES_PER_CYCLE's own reasoning in
# momentum_entry.py. Starting value: 10/drain * 2 drains/min = ~20/min, at
# today's ~105-order backlog a full rotation completes roughly every 5min --
# comfortably inside even the fastest (15min) candle granularity. Recalibrate
# if the watching backlog keeps growing (it has been, per the same audit) or
# if the other GeckoTerminal-heavy sources (discovery cycle, websocket drain
# on NEW candidates) leave less headroom than assumed here.
MAX_RSI_DIVERGENCE_WATCH_CHECKS_PER_DRAIN = 10

# order_id -> time.monotonic() of its last check_rsi_divergence_watching_order
# call. Pruned to only currently-active order ids at the top of every
# process_active_orders pass (an order leaves 'watching' for good --
# triggered/cancelled/expired -- so its entry would otherwise linger forever).
_rsi_watch_check_last_at: dict[int, float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Item #227 (30/07), operator request ("je veut une probabilité sur les
# ordre limite, le taux de chance de reussite que la divergence apparaisse")
# -- below this many resolved orders of the SAME reason, a raw ratio is too
# noisy to show honestly (e.g. 1/2 = 50% means nothing) -- degrades to "not
# enough history" rather than a fabricated-looking precise percentage.
_MIN_HISTORICAL_TRIGGER_SAMPLE = 10


async def historical_trigger_rate(reason: str | None) -> tuple[float | None, int]:
    """Real historical rate (Item #227, 30/07) at which a pending limit order
    tagged ``limit_order_reason == reason`` (``None`` for the price-drift
    path, #175, which never tags one) went on to actually TRIGGER, among
    every order of that SAME reason already resolved (``triggered``,
    ``cancelled``, or ``expired`` -- never counting ``pending``/``watching``,
    still-undecided orders would bias the ratio). This is a plain historical
    average across every past candidate, NOT a per-candidate prediction (no
    model conditions it on THIS specific setup's own features) -- displayed
    as exactly that, an honest base rate, never framed as a forecast for the
    order it's shown on.

    Returns ``(None, sample_size)`` if the sample is below
    ``_MIN_HISTORICAL_TRIGGER_SAMPLE`` -- never a rate computed on too few
    data points to mean anything.

    Item #250 (30/07): orders resolved before the last
    ``reset_historical_trigger_rate()`` call (if any) are excluded -- see
    that function's own docstring for why."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cutoff = None
        async with db.execute("SELECT reset_at FROM trigger_rate_reset_marker WHERE id = 1") as cur:
            row = await cur.fetchone()
            if row:
                cutoff = row[0]

        query = "SELECT state, signal_json FROM pending_limit_order WHERE state IN ('triggered', 'cancelled', 'expired')"
        params: tuple = ()
        if cutoff:
            query += " AND resolved_at >= ?"
            params = (cutoff,)
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()

    triggered = 0
    total = 0
    for state, signal_json in rows:
        try:
            sig = json.loads(signal_json) if signal_json else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if sig.get("limit_order_reason") != reason:
            continue
        total += 1
        if state == "triggered":
            triggered += 1

    if total < _MIN_HISTORICAL_TRIGGER_SAMPLE:
        return None, total
    return triggered / total, total


async def reset_historical_trigger_rate() -> None:
    """Item #250 (30/07), operator request ("reset les taux de déclenchement
    historique") after several same-day changes to the limit-order pipeline
    (the #231 R/R floor removed then restored, Items #245/#248; the 24h
    volume floor removed, Item #246) made the displayed historical trigger
    rate a mix of regimes no longer representative of the pipeline's current
    behavior.

    Never deletes the underlying ``pending_limit_order`` rows -- nothing
    else reads a resolved order's history (verified: ``historical_trigger_
    rate`` is the sole consumer), so they remain harmless, and still useful
    for manual debugging. Just moves a cutoff marker so ``historical_
    trigger_rate`` only counts orders resolved AFTER this call, starting the
    displayed stat fresh."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO trigger_rate_reset_marker (id, reset_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET reset_at = excluded.reset_at",
            (_now(),),
        )
        await db.commit()


# Item #231 (30/07) added an R/R floor here (scalping 1.25 / swing 2.0),
# motivated by a real incident (wIRON limit order, R/R=0.3). Removed then
# restored the SAME DAY (Items #245/#248) -- see git history for that
# back-and-forth's full context. REMOVED AGAIN 31/07, Item #252 -- operator's
# explicit call ("enleve le"), after a live case (DRV, R/R 0.066 at entry)
# went on to +18.3% -- reached far beyond its original technical target once
# the trailing-stop/staged-TP exit mechanism took over. Operator's read: the
# entry R/R doesn't bound the exit's upside, so filtering on it may be
# discarding trades with real "run further than planned" potential.
#
# Disclosed, accepted tradeoff (flagged directly by this session before the
# operator's final call): only 3 limit-order triggers exist in the entire
# history at the time of this decision, none yet closed (1 latent gain,
# 2 latent small losses) -- too small a sample to prove entry R/R predicts
# exit upside either way. The wIRON-style setup (mathematically indefensible
# at entry) can recur. If the floor needs to come back again,
# `docs/HANDOFF_PIPELINE_MOMENTUM.md`'s Item #231 entry has the full original
# calibration (67 resolved scalping orders, median R/R 1.75).


# 27/07 -- 3-pocket architecture plan, Phase 2: additive hot-migration list,
# same idempotent idiom as paper_trader.py's own ``_ADDED_COLUMNS`` (see its
# comment for why -- SQLite doesn't add a column to an already-existing table
# just because ``CREATE TABLE IF NOT EXISTS`` changed). Default 'swing' for
# every order placed before this work, and for every order placed while
# ``paper_trader.multi_pocket_sourcing_enabled()`` is OFF.
_ADDED_COLUMNS = [
    ("wallet", "TEXT NOT NULL DEFAULT 'swing'"),
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_limit_order (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                symbol TEXT,
                target_price REAL NOT NULL,
                signal_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                watch_entered_at TEXT,
                resolved_at TEXT,
                cancel_reason TEXT
            )
            """
        )
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(pending_limit_order)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE pending_limit_order ADD COLUMN {name} {ddl}")
        # Item #250 (30/07), operator request ("reset les taux de
        # déclenchement historique") -- see reset_historical_trigger_rate's
        # own docstring below for the full rationale. Single-row marker
        # table (id always 1), created lazily here alongside the main table
        # since both are only ever touched together.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trigger_rate_reset_marker (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                reset_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def should_place_limit_order(
    signal_price: float | None, fresh_price: float | None, invalidation_price: float | None,
    *, chain: str | None = None, liquidity_usd: float | None = None,
) -> bool:
    """True only for case (b): the setup drifted upward since the signal
    (``fresh_price`` above ``signal_price``) but the structure is still
    intact (``fresh_price`` still above ``invalidation_price``). False for
    case (a) -- the structure already broke (price at or below the
    invalidation) -- a dead setup is rejected outright, never turned into a
    limit order. Fail-closed (``False``) on any missing input.

    ``chain``/``liquidity_usd`` (Item #158, 28/07): for a bonding-curve
    candidate specifically (``chain == bonding_entry.CHAIN_MARKER``), an
    ADDITIONAL market-cap-proxy floor applies (``BONDING_LIMIT_ORDER_MIN_
    LIQUIDITY_USD``) -- see that constant's own comment for why. Both
    ``None`` (the default, every non-bonding caller) -- unchanged behavior."""
    from aria_core.bonding_entry import CHAIN_MARKER

    if not signal_price or not fresh_price or not invalidation_price:
        return False
    if fresh_price <= invalidation_price:
        return False  # structure already broken -- dead setup
    if chain == CHAIN_MARKER and (
        liquidity_usd is None or liquidity_usd < BONDING_LIMIT_ORDER_MIN_LIQUIDITY_USD
    ):
        return False
    return fresh_price > signal_price


def should_enter_watching(target_price: float, current_price: float | None) -> bool:
    """True once ``current_price`` has come down to within
    ``LIMIT_ORDER_WATCH_TRIGGER_MULT`` of the target -- worth a re-analysis
    (honeypot + invalidation) before committing to close, active monitoring."""
    if not current_price or current_price <= 0:
        return False
    return current_price <= target_price * LIMIT_ORDER_WATCH_TRIGGER_MULT


def check_watching_order(
    target_price: float, invalidation_price: float | None, current_price: float | None,
) -> str:
    """Decision for an order already in ``watching`` state: ``'trigger'``
    (price reached the target -- buy now), ``'cancel'`` (invalidation
    crossed during the watch -- the setup died while ARIA was waiting for a
    pullback), or ``'wait'`` (still watching). Missing price -> ``'wait'``,
    never a decision on unknown data."""
    if not current_price or current_price <= 0:
        return "wait"
    if invalidation_price and current_price <= invalidation_price:
        return "cancel"
    if current_price <= target_price:
        return "trigger"
    return "wait"


# ``is_market_dead`` (30/07, real operator gap: a ``watching`` order's whole
# premise -- price will eventually reach a meaningful level -- requires a
# pool that's still genuinely trading; a token whose market goes quiet AFTER
# the order was placed, e.g. AQUARI/WMTX-style pools with $0 volume in the
# last hour, used to just sit there until its own wall-clock expiry, up to
# 71h later) REMOVED 30/07, Item #251 -- operator's explicit call (screenshot
# of a real "marché devenu illiquide" cancellation, believed already gone
# along with the #246 24h volume floor -- a DIFFERENT mechanism: that one
# gates a NEW entry, this one cancelled an ALREADY-PLACED watching order).
# No other caller ever used this function (verified: only this module's own
# now-removed call site and its own now-removed tests) -- deleted outright
# rather than left as dead code. If a dead-market cancellation is wanted
# again, ``momentum_entry._MIN_VOLUME_24H_USD`` (the floor it reused) is
# still in place, still read by Birdeye's own discovery-side pre-filter.


async def check_rsi_divergence_watching_order(order: dict, sig: dict) -> str:
    """Item #183 (28/07), watch-RSI-divergence: decision for a ``watching``
    order tagged ``limit_order_reason == "rsi_divergence_pending"`` -- unlike
    ``check_watching_order`` (a plain price comparison), this re-fetches
    FRESH candles and re-runs the divergence detection itself, since the
    entire premise here is "the divergence hasn't formed yet", not a price
    level to wait for.

    Returns ``'trigger'`` (a divergence just confirmed WITH a span inside
    the operator-validated window, ``momentum_entry.RSI_WATCH_MIN_SPAN``-
    ``RSI_WATCH_MAX_SPAN`` -- never a looser span, even if one forms first),
    ``'cancel'`` (price broke below the invalidation -- the golden pocket
    setup itself died while waiting), ``'expire'`` (the candle-count horizon
    elapsed with no qualifying divergence -- ``RSI_WATCH_MAX_HORIZON_
    CANDLES`` new candles observed since the order's own ``last_candle_ts``,
    silent by design like every other expiry in this module, see
    ``sweep_expired``), or ``'wait'`` (still forming, or data unresolved --
    fail-open, never a cancel on a transient network failure)."""
    from aria_core import momentum_entry, paper_trader

    invalidation = sig.get("invalidation")
    last_candle_ts = sig.get("last_candle_ts")

    try:
        pairs = await momentum_entry.fetch_token_pairs(order["contract"], chain=order["chain"])
    except Exception as exc:  # noqa: BLE001 -- fail-open, a transient lookup failure just waits
        logger.info("limit_orders: rsi-divergence pair lookup failed for %s (%s)", order["contract"], exc)
        return "wait"
    pair = momentum_entry._best_pair(pairs, order["contract"])
    if pair is None:
        return "wait"

    if invalidation and pair.price_usd is not None and pair.price_usd <= invalidation:
        return "cancel"

    # Item #199 (29/07): re-fetch at the SAME timeframe the order was placed
    # under -- ``order["wallet"]`` ("scalping"/"swing"/"vc", already stored on
    # every pending order, see this module's own header comment) is the
    # canonical source, never re-derived or guessed. A scalping order re-
    # checked with standard-mode (1h+) candles would silently corrupt the
    # divergence detection's own timeframe, defeating the whole premise of
    # ``mode=`` being timeframe-aware in the first place.
    #
    # 31/07 -- explicit operator decision: swing's own WATCH phase (once an
    # order is placed and price is in the targeted golden-pocket zone) now
    # ALSO uses fine-grained (15-30min) scalping-scale candles here, never
    # the standard day/4h/1h ladder the original swing SIGNAL was detected
    # on. Rationale (operator's own words): swing finds the setup on big
    # timeframes, then "scalping" (this fine-grained lens, not the separate
    # scalping WALLET/capital -- that pocket's own independent trading is
    # entirely unaffected) confirms the precise entry timing within the
    # already-targeted zone. VC unaffected (falls through to "standard").
    #
    # 08/02 -- real bug found live (adversarial cross-review workflow): this
    # tested wallet == "scalping" literally, which stopped matching anything
    # once scalping_variants_enabled() migrated that pocket's history to
    # "scalping_v6" alongside 5 new scalping_v1..v5 pockets the same day --
    # every scalping-variant order silently fell through to "standard" (1h+
    # candles) instead of the intended fine-grained (15-30min) re-fetch, the
    # exact defeat this timeframe-aware mode= was built to prevent. Now uses
    # paper_trader.is_scalping_pocket(), the single source of truth that
    # covers both the legacy "scalping" name (gate off) and scalping_v1..v6
    # (gate on).
    wallet = order.get("wallet") or ""
    # 02/08 -- "megacap" pocket added to the OR chain, deliberately NOT a
    # replacement of this condition by uses_fine_rsi_confirmation(): this
    # line already carries the is_scalping_pocket() fix from earlier the
    # same day (see comment above), and a blind textual substitution here
    # would have silently dropped that clause again, reintroducing the exact
    # regression it just fixed for scalping_v1..v6.
    watch_mode = "scalping" if (
        wallet == "swing" or wallet == "megacap" or paper_trader.is_scalping_pocket(wallet)
    ) else "standard"
    try:
        candles = await momentum_entry._fetch_candles(
            pair.pair_address, order["chain"], contract=order["contract"], pair=pair, mode=watch_mode,
        )
    except Exception as exc:  # noqa: BLE001 -- fail-open, never a cancel on a data hiccup
        logger.info("limit_orders: rsi-divergence candle refresh failed for %s (%s)", order["contract"], exc)
        return "wait"
    if not candles:
        return "wait"

    # 03/08 -- real bug found live (operator: "jai limpression que dautre
    # token le font aussi dans megacap" -- confirmed empirically: LINK/WETH/
    # cbBTC/cbETH churned a fresh order every ~15 minutes, dozens of times/
    # day, across swing AND megacap, never a real trigger). Root cause: the
    # golden-pocket signal that CREATED this order was detected on the
    # standard-mode ladder (day/4h/1h, ``last_candle_ts`` set from THAT
    # granularity -- see ``_rsi_divergence_watch_candidate``'s own comment on
    # why it can be ~20 DAYS wide), but the watch phase above always refetches
    # fine-grained 15-30min candles (``watch_mode``, operator decision 31/07).
    # Counting "new candles since last_candle_ts" against a timestamp from a
    # coarser resolution is comparing apples to oranges: verified live on
    # LINK, a single 30min-candle refetch already had 55 candles past a
    # `last_candle_ts` set from a daily close -- 2.75x past the 20-candle
    # horizon on the VERY FIRST watch check, regardless of real elapsed time.
    # Fixed by re-anchoring ONCE, on the first check at this (possibly new)
    # resolution -- ``watch_candle_ts_aligned`` marks that it's already been
    # done, so every later check counts real newly-elapsed FINE candles, the
    # comparison the horizon was always meant to measure. Self-healing for
    # every order already stuck in the loop (missing key on an old order ==
    # not yet aligned), no migration needed.
    if not sig.get("watch_candle_ts_aligned"):
        sig["last_candle_ts"] = candles[-1].ts
        sig["watch_candle_ts_aligned"] = True
        try:
            await _persist_signal_json(order["id"], sig)
        except Exception as exc:  # noqa: BLE001 -- best-effort, retried next check if it fails
            logger.info("limit_orders: rsi-divergence candle-ts realign failed for %s (%s)", order["contract"], exc)
        return "wait"

    if last_candle_ts is not None:
        new_candles = sum(1 for c in candles if c.ts > last_candle_ts)
        if new_candles > momentum_entry.RSI_WATCH_MAX_HORIZON_CANDLES:
            logger.info(
                "limit_orders: rsi-divergence watch expired for %s -- %d new candles observed "
                "(horizon %d) with no qualifying divergence",
                order["contract"], new_candles, momentum_entry.RSI_WATCH_MAX_HORIZON_CANDLES,
            )
            return "expire"

    from aria_core.skills.entry_signals import _bullish_rsi_divergence_detail

    detail = _bullish_rsi_divergence_detail(candles)
    if (
        detail.present and detail.span is not None
        and momentum_entry.RSI_WATCH_MIN_SPAN <= detail.span <= momentum_entry.RSI_WATCH_MAX_SPAN
    ):
        logger.info(
            "limit_orders: rsi-divergence CONFIRMED for %s -- span=%d gap=%s (window %d-%d)",
            order["contract"], detail.span, detail.gap,
            momentum_entry.RSI_WATCH_MIN_SPAN, momentum_entry.RSI_WATCH_MAX_SPAN,
        )
        # 29/07 -- real bug found via operator screenshot comparison (chart
        # vs. buy thesis): ``sig["reasons"]`` still held the ORIGINAL
        # watch-creation wording ("divergence RSI pas encore confirmée"),
        # persisted as-is into the BUY thesis by ``_execute_trigger`` since
        # this function only ever returned a bare decision string -- the
        # thesis of an executed buy said the opposite of what just happened.
        # ``sig`` is the SAME dict object ``_execute_trigger`` reads right
        # after (mutation here, not a return value, is what the caller sees)
        # -- replaced wholesale (not appended) so the thesis never reads as
        # a self-contradicting mix of the stale "pending" reason and the
        # fresh "confirmed" one.
        gap_str = f"{detail.gap:.1f}" if detail.gap is not None else "n/a"
        sig["reasons"] = [
            f"Divergence RSI haussière CONFIRMÉE (span {detail.span} bougies, "
            f"force {gap_str} points RSI, fenêtre {momentum_entry.RSI_WATCH_MIN_SPAN}-"
            f"{momentum_entry.RSI_WATCH_MAX_SPAN}) -- prix déjà dans la golden pocket."
        ]
        # Item #247 (30/07): the numeric gap/span of the divergence that just
        # confirmed -- same mutate-in-place doctrine as the reasons text
        # above (this IS the fresh, re-checked divergence, not the original
        # watch-creation one). Lets ``process_active_orders`` log this
        # trigger's real "steepness" without re-deriving it.
        sig["rsi_gap"] = detail.gap
        sig["rsi_span"] = detail.span
        # Item #253 (08/02) -- entry_atr_pct RE-computed here on the SAME fresh,
        # mode-aware candles the divergence re-check just ran on -- more temporally
        # faithful than the watch-candidate's own snapshot (up to RSI_WATCH_MAX_
        # HORIZON_CANDLES old by now). `sig` is the SAME dict object _execute_
        # trigger reads right after (see this function's own mutate-in-place
        # doctrine above for reasons/rsi_gap/rsi_span) -- OVERWRITES whatever the
        # watch-candidate builder set, never additive. Falls back to that earlier
        # value if ATR isn't computable on this fresh set (insufficient candles) --
        # never erases a real prior value with None.
        #
        # Note: this uses candles[-1].close, a DIFFERENT price reference than the
        # `current_price` _execute_trigger later persists as entry_price -- a
        # separate DexScreener fetch, captured BEFORE this function even runs (see
        # process_active_orders' top-of-loop `price`). Deliberately not
        # reconciled: current_price is itself stale by several MORE network calls
        # (pair re-fetch, holder-concentration re-check) by the time it's actually
        # applied, so chasing exact match here would relocate staleness, not
        # remove it -- current_price is the OLDEST of the three fetches in this
        # call chain, not the freshest. entry_atr_pct is a ratio -- the
        # sub-few-second drift between these fetches (same synchronous drain
        # pass, no sleep) moves numerator and denominator together, a
        # proportionally negligible shift well inside the clamp bounds
        # (MIN/MAX_ATR_TRAIL_PCT, 5%-40%) and the observed production range
        # (~0.4%-28%) -- dwarfed by the multi-hour creation-to-trigger price
        # drift (-0.1% to -19% observed) this system already tolerates by design.
        from aria_core.skills.indicators import atr_series

        atr_values = atr_series(candles)
        last_atr = atr_values[-1] if atr_values else None
        if last_atr is not None and candles[-1].close:
            sig["entry_atr_pct"] = last_atr / candles[-1].close
        # Item #65 (08/03), anti-chasing shadow filter: same "recompute on
        # fresh candles, never erase a real prior value with None" doctrine
        # as entry_atr_pct just above -- this is the STATISTICALLY DOMINANT
        # execution path (most watching orders trigger through here, not a
        # direct buy, per the 08/03 workflow review), so it gets its own
        # shadow observation, distinct from paper_trader.py's direct-buy one.
        from aria_core.chasing_filter_shadow import (
            RECENT_LOW_WINDOW_GOLDEN_POCKET,
            recent_low_from_candles,
        )

        # 08/03 -- adversarial workflow review (post-deploy audit): the
        # recent_low recompute itself sat OUTSIDE any try/except here, the
        # one asymmetry vs. every other call in this function (pair/price
        # lookups above are all wrapped). A raise here would abort this
        # whole 30s drain pass (caught one level up by momentum_websocket.
        # _drain_once's own wrapper, never crashing the service) but would
        # also prevent the very trigger it was meant to observe -- a narrow
        # but real contradiction of "shadow logging never blocks a trade".
        # Now wrapped like its neighbors, same doctrine.
        try:
            from aria_core import chasing_filter_shadow

            fresh_recent_low = recent_low_from_candles(candles, RECENT_LOW_WINDOW_GOLDEN_POCKET)
            if fresh_recent_low is not None:
                sig["recent_low"] = fresh_recent_low
                sig["recent_low_window"] = RECENT_LOW_WINDOW_GOLDEN_POCKET
            await chasing_filter_shadow.record_check(
                order["contract"], order.get("chain") or "base",
                wallet=order.get("wallet") or "swing", source="limit_order_trigger",
                recent_low=sig.get("recent_low"), recent_low_window=sig.get("recent_low_window"),
                execution_price=candles[-1].close, symbol=sig.get("symbol"),
                variant=(sig.get("reasons") or [None])[0],
            )
        except Exception as exc:  # noqa: BLE001 -- shadow logging must never block a real trigger
            logger.info("limit_orders: chasing_filter_shadow.record_check failed (%s)", exc)
        return "trigger"
    return "wait"


async def has_active_order(contract: str, chain: str, *, wallet: str = "swing") -> bool:
    """True if a ``pending`` or ``watching`` order already exists for this
    contract IN THIS POCKET -- never stacks a second limit order on the same
    candidate within the same pocket.

    ``wallet`` (27/07, 3-pocket architecture plan): defaults to ``"swing"`` --
    unchanged behavior for any caller that doesn't pass it (gate OFF, or any
    caller predating this work). Scoped like ``paper_trader.has_open(...,
    wallet=...)`` -- a pending order already placed by a DIFFERENT pocket on
    the same contract must never block this one (the whole point of 3
    concurrent pockets: each independently detects/watches its own setup)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM pending_limit_order WHERE contract = ? AND chain = ? "
            "AND wallet = ? AND state IN ('pending', 'watching') LIMIT 1",
            (contract, chain, wallet),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def create_pending_order(
    contract: str, chain: str, symbol: str, target_price: float, sig: dict, *,
    wallet: str = "swing", expiry_hours: float | None = None,
) -> dict:
    """Places a new limit order at ``target_price`` (the signal's original
    price, before it drifted) -- ``sig`` is the FULL evaluated signal,
    serialized as-is so a later trigger never needs to re-scan from scratch.
    Every field of the caller's real signal dicts is already a plain
    str/float/int/bool/None (verified against ``momentum_entry``'s BUY
    returns) -- ``default=str`` below is a defensive fallback only, never
    relied on in practice.

    ``wallet`` (27/07, 3-pocket architecture plan): which pocket this order
    belongs to -- persisted as-is, read back by ``_execute_trigger`` so the
    eventual buy books into the SAME pocket that detected the setup, never a
    hardcoded one. Defaults to ``"swing"`` -- unchanged behavior (implicit
    single pocket) for any caller that doesn't pass it, i.e. every caller
    while ``paper_trader.multi_pocket_sourcing_enabled()`` is OFF.

    ``expiry_hours`` (Item #183, 28/07): overrides the flat
    ``LIMIT_ORDER_EXPIRY_HOURS`` -- used by the RSI-divergence watch
    (``momentum_entry._rsi_divergence_watch_candidate``'s own
    ``watch_expiry_hours``), whose real horizon is a CANDLE COUNT, not a
    fixed duration (see that function's docstring: the candle granularity
    varies, so a fixed 3h would be meaningless on daily candles). ``None``
    (default) keeps the original flat TTL -- unchanged behavior for every
    other caller."""
    await _ensure_table()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=expiry_hours if expiry_hours is not None else LIMIT_ORDER_EXPIRY_HOURS)).isoformat()
    signal_json = json.dumps(sig, default=str)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO pending_limit_order
              (contract, chain, symbol, target_price, signal_json, state, created_at, expires_at, wallet)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (contract, chain, symbol or "", target_price, signal_json, now.isoformat(), expires_at, wallet),
        )
        await db.commit()
        order_id = cur.lastrowid
    return {
        "id": order_id, "contract": contract, "chain": chain, "symbol": symbol or "",
        "target_price": target_price, "signal_json": signal_json, "state": "pending",
        "created_at": now.isoformat(), "expires_at": expires_at,
        "watch_entered_at": None, "resolved_at": None, "cancel_reason": None,
        "wallet": wallet,
    }


async def get_active_orders() -> list[dict]:
    """Every order still ``pending`` or ``watching`` -- what
    ``momentum_websocket._drain_once()`` must check on every pass."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_limit_order WHERE state IN ('pending', 'watching') "
            "ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _set_state(order_id: int, state: str, *, cancel_reason: str | None = None) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        if state == "watching":
            await db.execute(
                "UPDATE pending_limit_order SET state = ?, watch_entered_at = ? WHERE id = ?",
                (state, _now(), order_id),
            )
        else:
            await db.execute(
                "UPDATE pending_limit_order SET state = ?, resolved_at = ?, cancel_reason = ? WHERE id = ?",
                (state, _now(), cancel_reason, order_id),
            )
        await db.commit()


async def transition_to_watching(order_id: int) -> None:
    await _set_state(order_id, "watching")


async def _persist_signal_json(order_id: int, sig: dict) -> None:
    """03/08 -- introduced for the ``watch_candle_ts_aligned`` realignment
    below (see ``check_rsi_divergence_watching_order``'s own comment): the
    only mutation to an order's ``signal_json`` after creation, previously
    unnecessary since ``sig["reasons"]`` mutations (Item #199, 29/07)
    surface only through the in-memory dict a caller already holds, never
    re-read from disk within the same cycle."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pending_limit_order SET signal_json = ? WHERE id = ?",
            (json.dumps(sig), order_id),
        )
        await db.commit()


async def mark_triggered(order_id: int) -> None:
    await _set_state(order_id, "triggered")


async def mark_cancelled(order_id: int, reason: str) -> None:
    await _set_state(order_id, "cancelled", cancel_reason=reason)


async def sweep_expired() -> list[dict]:
    """Marks every ``pending``/``watching`` order past ``expires_at`` as
    ``expired`` -- silent by design (never a Telegram alert, see module
    docstring), only returned here for logging by the caller."""
    await _ensure_table()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_limit_order WHERE state IN ('pending', 'watching') "
            "AND expires_at < ?",
            (now,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            await db.execute(
                "UPDATE pending_limit_order SET state = 'expired', resolved_at = ? "
                "WHERE id IN ({})".format(",".join("?" * len(rows))),
                (now, *[r["id"] for r in rows]),
            )
            await db.commit()
    return rows


async def _reanalyze_bonding_for_watching(order: dict) -> bool:
    """Item #158, 28/07: GoPlus (the standard honeypot re-check below) is
    structurally inapplicable to a bonding-curve token -- no separate DEX
    pool/token contract to exploit beyond the protocol's own, see
    bonding_entry.py's own docstring. This is the bonding-native equivalent:
    re-checks the SAME structural hard gates ``evaluate_bonding_entry``
    itself enforces at signal time (dev-rug guard + liquidity floor) -- never
    the composite score itself (already judged once, this is a structural
    safety re-check before committing to watch closely, same scope/intent as
    the honeypot re-check it mirrors). Fail-closed on any missing/unresolved
    data, same doctrine as the rest of this module."""
    from aria_core import bonding_entry
    from aria_core.services.virtuals import virtuals_client

    try:
        token = await virtuals_client.fetch_by_address(order["contract"], chain="BASE")
    except Exception as exc:  # noqa: BLE001 -- fail-closed, never an unguarded watch
        logger.info(
            "limit_orders: bonding re-analysis failed for %s (%s) -- cancelling", order["contract"], exc,
        )
        return False
    if token is None:
        return False
    if token.dev_holding_pct is None or token.dev_holding_pct > bonding_entry._MAX_DEV_HOLDING_PCT:
        return False
    if token.liquidity_usd is None or token.liquidity_usd < bonding_entry._MIN_LIQUIDITY_USD:
        return False
    return True


async def _reanalyze_dex_quality_for_watching(order: dict) -> bool:
    """Item #182, 28/07: golden-pocket liberation -- an order placed because
    ``momentum_entry._golden_pocket_watch_candidate`` confirmed a high DEX
    composite score while the golden pocket zone hadn't formed yet rests
    ENTIRELY on that score, unlike the standard price-drift case (an
    already-confirmed golden pocket + RSI setup, where only the honeypot
    needs a fresh look) -- there is no already-validated technical setup to
    fall back on here. Re-checks honeypot FIRST (the one hard guardrail this
    pipeline always enforces, never skipped for any order type), then
    recomputes the composite score fresh via
    ``momentum_entry.refresh_dex_composite_score`` -- cancels if either
    degrades. Fail-closed on any missing/unresolved data (score no longer
    computable, security/pair no longer resolvable), same doctrine as the
    rest of this module: a newly-appeared weakness is worse than a missed
    entry."""
    from aria_core import risk_guard
    from aria_core.momentum_entry import check_honeypot, refresh_dex_composite_score

    try:
        clear, _reason, _code = await check_honeypot(order["contract"], order["chain"])
    except Exception as exc:  # noqa: BLE001 -- fail-closed, never an unguarded watch
        logger.info(
            "limit_orders: golden-pocket re-analysis (honeypot) failed for %s (%s) -- cancelling",
            order["contract"], exc,
        )
        return False
    if not clear:
        logger.info(
            "limit_orders: golden-pocket re-analysis cancelling %s -- honeypot no longer clear (%s)",
            order["contract"], _reason,
        )
        return False

    try:
        dex_score = await refresh_dex_composite_score(order["contract"], order["chain"])
    except Exception as exc:  # noqa: BLE001 -- fail-closed, this order's whole premise is the score
        logger.info(
            "limit_orders: golden-pocket re-analysis (composite score) failed for %s (%s) -- cancelling",
            order["contract"], exc,
        )
        return False
    if dex_score is None or dex_score.score is None:
        logger.info(
            "limit_orders: golden-pocket re-analysis cancelling %s -- score unresolved on re-check "
            "(fail-closed, this order's only reason to exist was the score)", order["contract"],
        )
        return False
    ok = dex_score.score >= risk_guard.DEX_QUALITY_WATCH_THRESHOLD
    logger.info(
        "limit_orders: golden-pocket re-analysis for %s -- fresh score=%.1f threshold=%.1f -> %s",
        order["contract"], dex_score.score, risk_guard.DEX_QUALITY_WATCH_THRESHOLD,
        "confirmed, entering watching" if ok else "degraded, cancelling",
    )
    return ok


async def _reanalyze_holder_concentration(order: dict) -> bool:
    """08/02 -- real security gap found: ``momentum_entry.evaluate_hard_
    gates``'s holder-concentration check (``_check_holder_concentration``,
    top 10 EOA holders excluding pool/burn/verified contracts >= 80%) is
    DEFERRED (``defer_holder_concentration=True``) for every candidate that
    reaches ``evaluate_momentum_entry``, then only re-run once
    ``signal.present`` is True (a confirmed golden-pocket + RSI setup, buy-
    now path). A candidate routed to the limit-order path instead
    (``signal.present`` is False by construction -- the whole premise of a
    limit order is that the setup ISN'T confirmed yet) returns straight from
    ``evaluate_momentum_entry`` before ever reaching that check -- neither
    ``reanalyze_for_watching`` (honeypot-only re-check) nor
    ``_execute_trigger`` (buy execution) ever called it. A token could
    concentrate >= 80% in insider hands anywhere between signal detection and
    trigger (up to 30 days for an ``rsi_divergence_pending`` order,
    ``watch_expiry_hours``) and still get bought -- this closes that gap by
    re-running the SAME guardrail (not a copy) at both re-analysis points.

    Never applied to a bonding-curve order (``chain == bonding_entry.
    CHAIN_MARKER``) -- pre-graduation there is no separate DEX pool/holder
    set to rank, ``_reanalyze_bonding_for_watching`` already covers the
    structural equivalent (``dev_holding_pct``) for that path.

    Re-fetches a FRESH pair (never the signal's original, possibly stale,
    pool_address) via the exact same pattern already used by
    ``check_rsi_divergence_watching_order`` (``fetch_token_pairs`` +
    ``_best_pair``) -- a pair that no longer resolves degrades fail-open
    (below), never treated as "still concentrated".

    Doctrine: FAIL-OPEN on any missing/unresolved data, matching
    ``_check_holder_concentration`` itself (its own docstring: "only the
    honeypot check is fail-closed in this pipeline") -- NOT the fail-closed
    doctrine the rest of this module uses for honeypot/dex-quality re-checks.
    Copying the honeypot's fail-closed behavior onto this specific guardrail
    would be inventing a new, undiscussed behavior change; this function only
    reuses ``_check_holder_concentration`` as-is. Returns ``True`` (safe to
    proceed) or ``False`` (>= 80% confirmed, cancel/block).

    03/08 -- real bug found live (operator: "regarde kaito", 16 pending orders
    created/cancelled for KAITO in a single day, all ``reanalysis_failed``):
    the "megacap" pocket (``fixed_watchlist.py``, a HAND-CURATED list of
    already-established tokens like LINK/KAITO/ICP/ENA, never a raw scanned
    candidate) structurally fails this guardrail. Real Blockscout data on
    KAITO: its top 2 EOA holders alone hold ~55% of supply (almost certainly
    CEX hot wallets / treasury, not memecoin insiders) -- this check's
    "verified contract" exemption only covers a multisig/staking CONTRACT,
    never an EOA, so a legitimately concentrated established token can never
    pass it. Waived for ``wallet == "megacap"`` only -- every other guardrail
    in this pipeline (honeypot, blacklist, liquidity floor) still applies
    unchanged, this concentration check alone is the mismatched one for an
    already hand-vetted watchlist of established tokens."""
    from aria_core import momentum_entry
    from aria_core.bonding_entry import CHAIN_MARKER

    if order["chain"] == CHAIN_MARKER:
        return True
    if order.get("wallet") == "megacap":
        return True

    try:
        pairs = await momentum_entry.fetch_token_pairs(order["contract"], chain=order["chain"])
    except Exception as exc:  # noqa: BLE001 -- fail-open, matches _check_holder_concentration's own doctrine
        logger.info(
            "limit_orders: holder-concentration pair lookup failed for %s (%s) -- fail-open",
            order["contract"], exc,
        )
        return True

    pair = momentum_entry._best_pair(pairs, order["contract"])
    pool_address = pair.pair_address if pair is not None else ""
    too_concentrated, reason = await momentum_entry._check_holder_concentration(
        order["contract"], order["chain"], pool_address,
    )
    if too_concentrated:
        logger.info(
            "limit_orders: holder-concentration re-check cancelling %s -- %s",
            order["contract"], reason,
        )
        return False
    return True


async def reanalyze_for_watching(order: dict) -> bool:
    """Single re-analysis performed ONCE, at the ``pending`` -> ``watching``
    transition (never repeated on every tick while watching -- see module
    docstring): re-checks the honeypot guard (the only hard guardrail this
    pipeline enforces) since it's been up to ``LIMIT_ORDER_EXPIRY_HOURS``
    since the original scan. ``True`` -> safe to start watching closely,
    ``False`` -> cancel immediately (a newly-appeared trap is worse than a
    missed entry).

    Item #158, 28/07: a bonding-curve order (``order["chain"] ==
    bonding_entry.CHAIN_MARKER``) is routed to ``_reanalyze_bonding_for_
    watching`` instead -- calling GoPlus with this marker as a "chain" would
    either error out or silently check the wrong thing.

    Item #182, 28/07: an order tagged ``limit_order_reason ==
    "golden_pocket_pending"`` (``signal_json``) is routed to
    ``_reanalyze_dex_quality_for_watching`` instead -- its premise is the DEX
    composite score, not an already-confirmed golden pocket, so honeypot
    alone isn't enough.

    08/02 -- real gap found (adversarial cross-review workflow): neither
    non-bonding branch below ever re-checked holder concentration
    (``_reanalyze_holder_concentration``, see its own docstring) -- added
    AFTER the honeypot/dex-quality check in both branches (cheapest/hardest
    guardrail first, same ordering doctrine ``evaluate_hard_gates`` already
    applies), never spent on an order that's about to be cancelled for a
    cheaper reason anyway. Bonding orders remain untouched -- routed away
    above before reaching either branch."""
    from aria_core.bonding_entry import CHAIN_MARKER

    if order["chain"] == CHAIN_MARKER:
        return await _reanalyze_bonding_for_watching(order)

    # .get(..., "{}") rather than a strict [] index: real rows always carry
    # signal_json (NOT NULL), but several existing unit tests build a minimal
    # order dict by hand without it -- never a reason to fail this guardrail.
    sig = json.loads(order.get("signal_json") or "{}")
    if sig.get("limit_order_reason") == "golden_pocket_pending":
        if not await _reanalyze_dex_quality_for_watching(order):
            return False
        return await _reanalyze_holder_concentration(order)

    from aria_core.momentum_entry import check_honeypot

    try:
        clear, _reason, _code = await check_honeypot(order["contract"], order["chain"])
    except Exception as exc:  # noqa: BLE001 -- fail-closed, never an unguarded watch
        logger.info(
            "limit_orders: re-analysis failed for %s (%s) -- cancelling", order["contract"], exc,
        )
        return False
    if not clear:
        return False
    return await _reanalyze_holder_concentration(order)


def _order_uses_rsi_divergence_check(order: dict) -> bool:
    """Same gate ``process_active_orders`` applies inline for a ``watching``
    order -- pulled out standalone so the per-drain selection below (which
    needs to know this BEFORE deciding whether to spend one of its
    ``MAX_RSI_DIVERGENCE_WATCH_CHECKS_PER_DRAIN`` slots on it) doesn't
    duplicate the condition.

    02/08 -- ``wallet == "swing"`` replaced by ``uses_fine_rsi_confirmation()``
    (full substitution, safe here: this site never had an ``is_scalping_
    pocket()`` clause to preserve, unlike ``check_rsi_divergence_watching_
    order``'s ``watch_mode``). Local import (module-level would create a
    cycle, same doctrine as every other ``paper_trader`` import in this
    file)."""
    from aria_core import paper_trader

    sig = json.loads(order.get("signal_json") or "{}")
    return sig.get("limit_order_reason") == "rsi_divergence_pending" or paper_trader.uses_fine_rsi_confirmation(
        order.get("wallet") or "swing"
    )


def _select_due_rsi_watch_order_ids(orders: list[dict], now: float) -> set[int]:
    """Which ``watching`` orders get a fresh ``check_rsi_divergence_watching_
    order`` re-check THIS drain pass -- see ``MAX_RSI_DIVERGENCE_WATCH_CHECKS_
    PER_DRAIN``'s own comment for why this is a hard per-pass cap (not a
    cooldown window): oldest-checked-first (an order never checked yet, i.e.
    absent from ``_rsi_watch_check_last_at``, sorts first via the ``0.0``
    default -- a freshly-entered watching order gets checked promptly rather
    than waiting a full rotation). Also prunes ``_rsi_watch_check_last_at``
    down to only the order ids still present in ``orders`` -- an order that
    left 'watching' for good (triggered/cancelled/expired) no longer appears
    in ``get_active_orders()``'s result, so its stale entry would otherwise
    never get cleaned up."""
    watching_rsi_ids = [
        o["id"] for o in orders if o["state"] == "watching" and _order_uses_rsi_divergence_check(o)
    ]
    live_ids = {o["id"] for o in orders}
    for stale_id in [k for k in _rsi_watch_check_last_at if k not in live_ids]:
        del _rsi_watch_check_last_at[stale_id]

    watching_rsi_ids.sort(key=lambda oid: _rsi_watch_check_last_at.get(oid, 0.0))
    return set(watching_rsi_ids[:MAX_RSI_DIVERGENCE_WATCH_CHECKS_PER_DRAIN])


async def process_active_orders(price_lookup, notifier=None, pair_lookup=None) -> dict:
    """Orchestrates every active limit order for one pass of the caller's
    drain loop (``momentum_websocket._drain_once()``): expires stale orders,
    advances ``pending`` orders toward ``watching`` (with the one-time
    re-analysis), and resolves ``watching`` orders (trigger the buy, or
    cancel on a broken structure). ``price_lookup(contract, chain=...)``
    matches the same contract already used everywhere else in this pipeline.
    Never raises -- a failure on one order never blocks the others or the
    caller's own drain.

    ``pair_lookup(contract, chain=...)`` (optional, 30/07) returns the full
    ``PairSnapshot`` instead of a bare price -- SAME network call as
    ``price_lookup`` under the hood (both ultimately fetch one DexScreener
    pair), just not throwing away the volume field. Kept as an optional
    parameter for any future volume-aware check, but the dead-market
    cancellation that originally used it (``is_market_dead``) was removed
    30/07, Item #251 (operator's explicit call) -- see that function's own
    former docstring, still in git history, for the removed rationale."""
    from aria_core import paper_trader

    actions: dict = {"expired": 0, "entered_watching": 0, "cancelled": 0, "triggered": []}

    expired = await sweep_expired()
    actions["expired"] = len(expired)

    active_orders = await get_active_orders()
    now = time.monotonic()
    due_rsi_watch_ids = _select_due_rsi_watch_order_ids(active_orders, now)

    for order in active_orders:
        pair = None
        if pair_lookup is not None:
            try:
                pair = await pair_lookup(order["contract"], chain=order["chain"])
            except Exception as exc:  # noqa: BLE001 -- one failed lookup never blocks the others
                logger.info("limit_orders: pair lookup failed for %s (%s)", order["contract"], exc)
                continue
            price = pair.price_usd if pair is not None and pair.price_usd > 0 else None
        else:
            try:
                price = await price_lookup(order["contract"], chain=order["chain"])
            except Exception as exc:  # noqa: BLE001 -- one failed lookup never blocks the others
                logger.info("limit_orders: price lookup failed for %s (%s)", order["contract"], exc)
                continue
        if not price or price <= 0:
            continue

        sig = json.loads(order["signal_json"])

        if order["state"] == "pending":
            if not should_enter_watching(order["target_price"], price):
                continue
            if await reanalyze_for_watching(order):
                await transition_to_watching(order["id"])
                actions["entered_watching"] += 1
                # 29/07 -- operator question ("plus le temps d'expiration est
                # petit plus on est proche du point d'achat ?"): expiry is a
                # pure wall-clock countdown, NEVER a proximity gauge -- THIS
                # transition (pending -> watching, price pulled back within
                # LIMIT_ORDER_WATCH_TRIGGER_MULT of target) is the real signal
                # that ARIA is getting close. Skipped for
                # ``rsi_divergence_pending`` orders: their target_price
                # equals the price AT CREATION time (see
                # momentum_entry._rsi_divergence_watch_candidate), so they
                # enter "watching" almost immediately -- a notification here
                # would just duplicate the "ORDRE LIMITE POSÉ" alert with no
                # new information.
                if notifier and sig.get("limit_order_reason") != "rsi_divergence_pending":
                    try:
                        await notifier(format_limit_order_watching_alert(order, price))
                    except Exception:  # noqa: BLE001
                        pass
            else:
                await mark_cancelled(order["id"], "reanalysis_failed")
                actions["cancelled"] += 1
                if notifier:
                    try:
                        await notifier(format_limit_order_cancelled_alert(order, "reanalysis_failed"))
                    except Exception:  # noqa: BLE001
                        pass
            continue

        # order["state"] == "watching"
        # is_market_dead cancellation removed 30/07, Item #251 -- see this
        # function's own docstring and limit_orders.py's former is_market_
        # dead comment (git history) for the removed rationale.

        # Item #183 (28/07): an order whose whole premise is a still-forming
        # RSI divergence (never an already-confirmed golden pocket + RSI
        # setup) can't be resolved by a plain price comparison -- see
        # check_rsi_divergence_watching_order's own docstring for the extra
        # 'expire' outcome (candle-count horizon elapsed, silent by design).
        #
        # 31/07 -- explicit operator decision: EVERY swing order in "watching"
        # now resolves via this same fresh-divergence re-check (fine-grained
        # candles, see check_rsi_divergence_watching_order's own watch_mode
        # comment), regardless of limit_order_reason -- not just the
        # rsi_divergence_pending case. A price-drift/golden-pocket-not-yet-
        # formed swing order that pulls back into its target zone no longer
        # buys on price alone; it waits for a genuine fine-grained divergence
        # to confirm within that zone. Scalping's own orders are UNCHANGED
        # (still gated on limit_order_reason exactly as before).
        uses_rsi_divergence_check = _order_uses_rsi_divergence_check(order)
        # 08/02 -- real bug found live (adversarial cross-review workflow):
        # the 3 logging call sites below tested wallet == "scalping" literally,
        # which stopped matching once scalping_variants_enabled() migrated
        # that pocket's history to "scalping_v6" alongside scalping_v1..v5 --
        # every real scalping-variant order was logged mode="standard",
        # corrupting the v1..v6 comparison log. Computed once here (same
        # value reused at all 3 sites) via paper_trader.is_scalping_pocket(),
        # the single source of truth.
        _log_wallet = order.get("wallet") or ""
        # 02/08 -- "megacap" added to the OR chain, same reasoning as
        # check_rsi_divergence_watching_order's own comment: never a blind
        # replacement of this condition, is_scalping_pocket() stays.
        log_mode = "scalping" if (
            _log_wallet == "swing" or _log_wallet == "megacap" or paper_trader.is_scalping_pocket(_log_wallet)
        ) else "standard"
        if uses_rsi_divergence_check:
            # 01/08 -- MAX_RSI_DIVERGENCE_WATCH_CHECKS_PER_DRAIN's own comment:
            # not this pass's turn in the rotation -- skip (same as 'wait',
            # order stays 'watching' unchanged) rather than spending a fresh
            # GeckoTerminal candle re-fetch on it every single 30s drain.
            if order["id"] not in due_rsi_watch_ids:
                continue
            decision = await check_rsi_divergence_watching_order(order, sig)
            _rsi_watch_check_last_at[order["id"]] = now
        else:
            decision = check_watching_order(order["target_price"], sig.get("invalidation"), price)
        is_rsi_divergence_watch = uses_rsi_divergence_check
        if decision == "expire":
            await mark_cancelled(order["id"], "rsi_horizon_expired")
            actions["expired"] += 1
            # Item #247 (30/07): only ever reached for a genuine RSI-
            # divergence watch (check_watching_order, the non-divergence
            # path, never returns "expire") -- no gate needed, but kept
            # explicit for symmetry with the other outcomes here.
            await rsi_divergence_log.record_divergence(
                order["contract"], order["chain"], symbol=order.get("symbol"),
                wallet=order.get("wallet"),
                mode=log_mode,
                outcome="expired_unconfirmed",
            )
            continue
        if decision == "cancel":
            await mark_cancelled(order["id"], "invalidation_crossed")
            actions["cancelled"] += 1
            if is_rsi_divergence_watch:
                await rsi_divergence_log.record_divergence(
                    order["contract"], order["chain"], symbol=order.get("symbol"),
                    wallet=order.get("wallet"),
                    mode=log_mode,
                    outcome="cancelled_unconfirmed",
                )
            if notifier:
                try:
                    await notifier(format_limit_order_cancelled_alert(order, "invalidation_crossed"))
                except Exception:  # noqa: BLE001
                    pass
        elif decision == "trigger":
            # 02/08 -- real race found live (dedicated audit): unlike EVERY
            # other capital-allocating entrypoint (run_paper_cycle,
            # run_daily_trade_floor_cycle, _drain_multi_pocket), this trigger
            # path never acquired paper_trader._run_cycle_lock -- the 30s
            # websocket drain and a slower heartbeat cycle could both read
            # has_open/position-cap/equity before either commits its
            # open_position write. Scoped tightly to _execute_trigger alone
            # (never the whole process_active_orders loop above/below) so a
            # pending->watching transition or a cancel/expire decision --
            # neither touches capital -- is never held up waiting on a
            # possibly-long-running heartbeat cycle. `_execute_trigger`
            # re-validates price freshness internally, so this added
            # latency never risks a stale-price buy.
            #
            # Known, NOT closed by this lock (documented, not fixed here):
            # this is an in-process asyncio.Lock -- it offers ZERO
            # protection during a blue-green deploy's overlap window, where
            # two full Python processes briefly run against the same
            # bind-mounted SQLite file. Closing that gap needs a DB-level
            # guard (unique constraint + explicit transaction), a separate,
            # larger chantier -- see docs/HANDOFF_PIPELINE_MOMENTUM.md.
            async with paper_trader._run_cycle_lock:
                pos = await _execute_trigger(order, sig, price, notifier)
            if pos:
                actions["triggered"].append(pos)
                await mark_triggered(order["id"])
                # Item #247 (30/07): only a genuine RSI-divergence trigger
                # carries a real gap/span (set by check_rsi_divergence_
                # watching_order right before returning "trigger") -- a
                # price-drift/golden-pocket trigger has neither, this is
                # scoped to the divergence watch specifically.
                if is_rsi_divergence_watch:
                    await rsi_divergence_log.record_divergence(
                        order["contract"], order["chain"], symbol=order.get("symbol"),
                        wallet=order.get("wallet"),
                        mode=log_mode,
                        gap=sig.get("rsi_gap"), span=sig.get("rsi_span"),
                        outcome="bought_via_limit_order",
                    )
            # A failed trigger (open_position refused -- cap reached, cash
            # short, etc.) leaves the order in "watching": it may still fill
            # on the next pass if conditions change, rather than being lost
            # silently on a transient portfolio-level constraint.

    return actions


def _wallet_position_cap(paper_trader_module, wallet: str) -> int | None:
    """27/07 -- 3-pocket architecture plan, Phase 2: the position-count cap a
    TRIGGERED limit order must respect, mirroring ``paper_trader.
    _run_paper_cycle_locked``'s own multi-pocket branch (its "pocket_cap"
    tuple). ``paper_trader_module`` is the already-imported module reference
    from the caller (``_execute_trigger``'s own deferred import) -- avoids a
    module-level import of ``paper_trader`` here, which would create a
    circular import (``paper_trader.py`` itself imports this module locally).

    Gate OFF: byte-for-byte unchanged legacy behavior -- the flat
    ``MAX_POSITIONS`` (30) this function has always used, regardless of
    wallet (always "swing" while the gate is off, see
    ``create_pending_order``'s default). Gate ON: the REAL per-pocket cap
    (5/15/unlimited)."""
    if not paper_trader_module.multi_pocket_sourcing_enabled():
        return paper_trader_module.MAX_POSITIONS
    # 08/02 -- real bug found live (adversarial cross-review workflow): the
    # dict below only ever matched the literal "scalping" key, which stopped
    # matching once scalping_variants_enabled() migrated that pocket's
    # history to "scalping_v6" alongside scalping_v1..v5 -- every real
    # scalping-variant order fell back to the generic MAX_POSITIONS (30)
    # instead of the intended unlimited scalping cap. is_scalping_pocket()
    # checked first (covers both the legacy "scalping" name and
    # scalping_v1..v6) before the swing/vc lookup.
    if paper_trader_module.is_scalping_pocket(wallet):
        return paper_trader_module.MAX_POSITIONS_SCALPING
    return {
        "swing": paper_trader_module.MAX_POSITIONS_SWING,
        "vc": paper_trader_module.MAX_POSITIONS_VC,
        # 02/08 -- "megacap" pocket, same doctrine as swing/vc above.
        "megacap": paper_trader_module.MAX_POSITIONS_MEGACAP,
    }.get(wallet, paper_trader_module.MAX_POSITIONS)


async def _execute_trigger(order: dict, sig: dict, current_price: float, notifier) -> dict | None:
    """Buys at the limit-order trigger -- same pipeline as a direct buy
    (``paper_trader.open_position``/``format_buy_alert``), sizing recomputed
    with FRESH context (regime/risk_state/weekly may have moved since the
    order was placed) via the exact same ``compute_entry_alloc`` formula.
    ``current_price`` (the real spot price, NOT pre-degraded) is handed to
    ``open_position`` as-is -- it already applies its own risk cap,
    price-impact cap, and ``simulated_fill_price`` internally (same as a
    direct buy in ``_run_paper_cycle_locked``); computing them here too would
    apply the price-impact model TWICE on an already-degraded price, silently
    collapsing the allocation to zero (real bug found while testing this
    function).

    ``order['wallet']`` (27/07, 3-pocket architecture plan): the pocket THIS
    order was placed for (see ``create_pending_order``) -- every check below
    (duplicate guard, position cap, starting capital, weekly pacing context)
    is scoped to THIS SAME pocket, and the resulting buy books into it, never
    a hardcoded "swing". Falls back to "swing" if absent (an order row from
    before this work, or created while the gate was OFF)."""
    from aria_core import bonding_entry, paper_trader, risk_guard
    from aria_core.skills import market_sentiment

    wallet = order.get("wallet") or "swing"

    if await paper_trader.has_open(order["contract"], wallet=wallet):
        return None  # already bought some other way in the meantime -- never a duplicate

    max_positions_cap = _wallet_position_cap(paper_trader, wallet)
    if (
        max_positions_cap is not None
        and len(await paper_trader.get_open_positions(wallet=wallet)) >= max_positions_cap
    ):
        return None

    # 27/07 -- 3-pocket architecture plan, Phase 3: risk_guard's circuit
    # breaker is now per-pocket -- checked against THIS order's OWN pocket
    # (``wallet``, resolved above), never a stale unscoped call that would
    # let a different pocket's drawdown wrongly block/allow this trigger.
    risk_state = await risk_guard.evaluate_portfolio_risk(wallet)
    if risk_state.blocked:
        return None  # this pocket's circuit breaker armed since the order was placed

    start = await paper_trader.starting_capital(wallet=wallet)
    weekly_context = None
    try:
        cap = start
        target = paper_trader.weekly_target_equity(cap)
        started_dt = datetime.fromisoformat(await paper_trader.cycle_started_at(wallet=wallet))
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        elapsed_days = (datetime.now(timezone.utc) - started_dt).total_seconds() / 86400.0
        progress_pct = (risk_state.equity / cap - 1.0) * 100.0 if cap else 0.0
        target_pct = (paper_trader.WEEKLY_TARGET_MULTIPLIER - 1.0) * 100.0
        weekly_context = {
            "cycle_number": await paper_trader.get_current_cycle_number(wallet=wallet),
            "day": min(paper_trader.WEEKLY_CYCLE_DAYS, int(elapsed_days) + 1),
            "days_total": paper_trader.WEEKLY_CYCLE_DAYS,
            "equity": risk_state.equity,
            "target_equity": target,
            "progress_pct": progress_pct,
            "remaining_pct": target_pct - progress_pct,
        }
    except Exception as exc:  # noqa: BLE001 -- never blocking, degrades to no pacing context
        logger.info("limit_orders: weekly context unavailable at trigger (%s)", exc)
        weekly_context = None

    entry_alloc_usd, conviction_tier = paper_trader.compute_entry_alloc(
        sig, start, weekly_context, risk_state,
    )
    # Item #158, 28/07: a bonding trigger must go through the SAME extra
    # sizing steps as a direct bonding buy in paper_trader.py's own
    # _open_new_entries_for_wallet (BONDING_SIZE_REDUCTION + the #156
    # supply-proportion cap) -- without this, a limit-order trigger on a
    # bonding candidate would silently skip both, a real gap this closes.
    if order["chain"] == bonding_entry.CHAIN_MARKER:
        entry_alloc_usd *= bonding_entry.BONDING_SIZE_REDUCTION
        entry_alloc_usd = bonding_entry.cap_alloc_to_supply_pct(
            entry_alloc_usd, current_price, sig.get("total_supply"), conviction_tier,
        )
        # Item #165, 28/07: same tighten-only long-cycle macro lever as the
        # direct-buy path (paper_trader.py) -- best-effort, degrades to no
        # change on any failure.
        try:
            from aria_core.skills import btc_cycles

            btc_phase = await btc_cycles.fetch_current_macro_phase()
            btc_phase_label = btc_phase.get("label") if btc_phase else None
        except Exception as exc:  # noqa: BLE001 -- never blocking
            logger.info("limit_orders: btc_cycles macro phase unavailable (%s)", exc)
            btc_phase_label = None
        entry_alloc_usd *= bonding_entry.late_cycle_size_multiplier(btc_phase_label)

    try:
        current_regime = await market_sentiment.resolve_meta_regime()
    except Exception:  # noqa: BLE001
        current_regime = market_sentiment.META_REGIME_NEUTRAL

    thesis_prefix = (sig.get("these") or "; ".join(sig.get("reasons") or []) or "").strip()
    thesis = (
        thesis_prefix
        + f" [ordre limite -- placé à {order['target_price']:.6g}, "
        f"déclenché à {current_price:.6g}]"
    ).strip()
    # 29/07 -- real bug found while the operator checked a triggered position
    # (wstETH, id 11): open_position's own ``mode`` default ("standard") was
    # never overridden here, so a scalping-pocket order's trigger silently
    # persisted mode="standard" despite wallet="scalping". This isn't cosmetic
    # -- ``mode`` (not ``wallet``) is what paper_trader.py's position-
    # management loop reads to decide whether the scalping-specific bearish-
    # RSI-divergence exit (#105) applies, and whether the scalping DEX swap
    # fee (#101) is simulated on close/reduce. A mismatched position would
    # silently be governed by the STANDARD/swing exit discipline (fixed TP
    # tiers, no scalping exit signal) despite its capital, sizing, and risk
    # budget all being scalping-pocket. Mirrors the exact wallet->mode mapping
    # the direct-buy 3-pocket loop already uses (paper_trader.py's own
    # ``pocket_mode`` tuple: "scalping" for that one pocket, "standard" for
    # swing/vc) -- never a third, independently-invented mapping.
    #
    # 08/02 -- real bug found live (adversarial cross-review workflow): this
    # tested wallet == "scalping" literally -- exactly the SAME bug as the
    # 29/07 fix above, reintroduced the same day scalping_variants_enabled()
    # migrated that pocket's history to "scalping_v6" alongside 5 new
    # scalping_v1..v5 pockets. Every scalping-variant limit-order trigger
    # was persisting mode="standard" again. Now uses
    # paper_trader.is_scalping_pocket(), the single source of truth.
    mode = "scalping" if paper_trader.is_scalping_pocket(wallet) else "standard"

    # 08/02 -- real security gap found (adversarial cross-review workflow):
    # this trigger never re-checked holder concentration
    # (``_reanalyze_holder_concentration``, see its own docstring) -- the
    # ``watching`` state can persist up to 30 days (``rsi_divergence_pending``
    # orders) after ``reanalyze_for_watching``'s own one-time re-check, wide
    # enough for a distribution to concentrate >= 80% in insider hands after
    # that single check without ARIA ever noticing before buying. Placed
    # AFTER sizing (never wasted on an order the has_open/position-cap/
    # risk_state checks above already discarded for free) and after all
    # duplicate/cap/circuit-breaker guards, right before the buy itself --
    # the last, hardest-to-bypass point in this pipeline.
    if not await _reanalyze_holder_concentration(order):
        return None

    pos = await paper_trader.open_position(
        order["contract"],
        order["symbol"],
        current_price,
        # 27/07 -- 3-pocket architecture plan (Phase 2): books into the SAME
        # pocket that placed this order (see docstring above) -- "swing" under
        # gate OFF (unchanged historical behavior, every order created there
        # implicitly belongs to "swing").
        wallet=wallet,
        mode=mode,
        target_price=sig.get("target"),
        invalidation_price=sig.get("invalidation"),
        alloc_usd=entry_alloc_usd,
        category=sig.get("category", ""),
        entry_security_json=sig.get("entry_security_json", ""),
        chain=order["chain"],
        thesis=thesis,
        pool_liquidity_usd=sig.get("liquidity_usd"),
        entry_atr_pct=sig.get("entry_atr_pct"),
        strategy=sig.get("strategy") or "momentum",
        entry_regime=current_regime,
        entry_dev_sold_pct=sig.get("dev_sold_pct"),
        rr=sig.get("rr"),
        align_score=sig.get("align_score"),
        conviction_tier=conviction_tier,
        rvol_multiple=sig.get("rvol_multiple"),
        discovery_channel="limit_order",
        conviction_process_trail=sig.get("conviction_process_trail"),
        conviction_website_corroborated=sig.get("conviction_website_corroborated"),
        conviction_posting_cadence=sig.get("conviction_posting_cadence"),
        liquidity_rotation_score=sig.get("liquidity_rotation_score"),
        liquidity_rotation_accelerating=sig.get("liquidity_rotation_accelerating"),
        liquidity_rotation_volume_ratio=sig.get("liquidity_rotation_volume_ratio"),
        # 08/02 -- real gap found: these 6 fields were already present on
        # ``sig`` for the momentum path (``evaluate_momentum_entry``'s
        # unconditional return dict, or the merged watch/pending dict built
        # from it in paper_trader.py's create_pending_order call sites) but
        # were never forwarded here -- every limit-order-triggered position
        # persisted them as None regardless of what the signal actually had,
        # a pure wiring omission (identical source expressions as the two
        # direct-buy call sites in paper_trader.py). Legitimately still None
        # for a bonding trigger (bonding_entry.py has no golden-pocket/EMA-
        # MACD-pattern/market-cap concept) or a golden_pocket_pending order
        # triggered before the zone had formed at signal-creation time (sig
        # is never recomputed at trigger) -- never fabricated here either way.
        entry_market_cap_usd=sig.get("market_cap_usd"),
        gp_low=sig.get("gp_low"),
        gp_high=sig.get("gp_high"),
        align_ema=sig.get("align_ema"),
        align_macd=sig.get("align_macd"),
        align_pattern=sig.get("align_pattern"),
    )
    if pos and notifier:
        try:
            await notifier(paper_trader.format_buy_alert(pos))
        except Exception:  # noqa: BLE001
            pass
    return pos


_POCKET_LABEL = {"swing": "SWING", "scalping": "SCALPING", "vc": "VC", "megacap": "MEGACAP"}

# 29/07 -- operator request: the alert never stated which candle timeframe
# the setup was analyzed on -- ``momentum_entry.evaluate_momentum_entry``'s
# own docstring is the source of truth (mode="scalping" -> GeckoTerminal's
# dedicated 15-30min ladder, no fallback; mode="standard" -> the day/4h/1h
# cascade, never below 1h). Keyed on ``order["wallet"]`` (the pocket), the
# one field always correctly synced to the real analysis mode since the
# 29/07 mode-sync fix (see limit_orders._execute_trigger's own comment).
_TIMEFRAME_LABEL = {
    "scalping": "bougies 15-30min (mode scalping)",
    "swing": "bougies 1h+ (repli jour/4h/1h selon disponibilité, mode standard)",
    "vc": "bougies 1h+ (repli jour/4h/1h selon disponibilité, mode standard)",
    # 02/08 -- "megacap" pocket, mode="standard" like swing/vc -- without this
    # entry, .get() (no default) silently drops the "Analyse sur ..." line
    # from the alert (found by a validation workflow, ronde 6).
    "megacap": "bougies 1h+ (repli jour/4h/1h selon disponibilité, mode standard)",
}


def format_limit_order_placed_alert(order: dict) -> str:
    """29/07 -- operator feedback: this alert only showed the target price,
    never the current price nor any context on the setup itself (R/R,
    invalidation) -- unlike the position-tracking alert, which shows both.
    ``price_at_order_placed`` (both call sites in paper_trader.py now inject
    it) is the real observed price at the moment this order was created --
    never re-derived from ``target_price`` (that would silently invent a
    number when the field is genuinely missing, e.g. an order created before
    this fix).

    29/07, second pass (operator feedback, same day) -- two more real gaps
    found while the operator watched a live flood of scalping-pocket orders:
    (1) the pocket (``order["wallet"]``) was never shown -- indistinguishable
    from a swing/VC order in the alert itself. (2) for a
    ``rsi_divergence_pending`` order specifically (``_rsi_divergence_watch_
    candidate``), ``target_price`` is set to the price AT DETECTION TIME (the
    golden pocket is already reached, only the RSI divergence hasn't
    confirmed yet, see that function's docstring) -- the generic "cible X,
    expire si le prix ne redescend jamais" wording is factually wrong here
    (there is no pullback to wait for, X IS the current price) and was the
    literal source of the operator's "elle cible le prix actuel, étrange"
    confusion. Also, this order type's real expiry is a CANDLE-COUNT horizon
    (``watch_expiry_hours``, often several hours, never the flat
    ``LIMIT_ORDER_EXPIRY_HOURS``) -- the alert used to hardcode the flat
    constant regardless, so it displayed "3h" on orders whose real
    ``expires_at`` was 10-15h out. Both now read the order's OWN
    ``created_at``/``expires_at`` instead of assuming any fixed duration.

    29/07, third pass -- operator request: bold the title line so it stands
    out at a glance. Sent via ``telegram_bot.send_trading_notification``,
    which switches to HTML parse mode the moment it sees a literal ``<b>``
    (see that function's own docstring) -- ``name`` (token symbol, on-chain
    metadata an attacker can set freely) MUST be HTML-escaped since an
    unescaped ``<``/``>``/``&`` anywhere in the text would break Telegram's
    HTML parser for the whole message, not just the bolded title.

    02/08 -- local ``paper_trader`` import added (this function never had one
    before -- module-level would create a cycle, same doctrine as every other
    ``paper_trader`` import in this file)."""
    from aria_core import paper_trader

    name = html.escape(order.get("symbol") or (order.get("contract") or "")[:10], quote=False)
    target = order["target_price"]
    try:
        sig = json.loads(order.get("signal_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        sig = {}

    pocket_label = _POCKET_LABEL.get(order.get("wallet"), (order.get("wallet") or "swing").upper())

    try:
        created = datetime.fromisoformat(order["created_at"])
        expires = datetime.fromisoformat(order["expires_at"])
        expiry_hours = (expires - created).total_seconds() / 3600.0
    except (KeyError, ValueError, TypeError):
        expiry_hours = LIMIT_ORDER_EXPIRY_HOURS

    reason = sig.get("limit_order_reason")
    if reason == "rsi_divergence_pending":
        lines = [
            f"<b>🎯 ORDRE LIMITE POSÉ ({pocket_label}, portefeuille papier, aucun argent réel)</b>",
            f"{name} -- déjà dans la golden pocket ({target:.6g}), divergence RSI pas encore confirmée",
            "ARIA surveille la formation de la divergence (pas un niveau de prix à atteindre) avant d'acheter.",
        ]
        # Item #234 (30/07) added a "Zone à tenir pendant la formation"
        # line here (gp_low/gp_high, the golden-pocket range the price must
        # hold while the RSI divergence forms). REMOVED 30/07, Item #249 --
        # operator's explicit call after seeing it in a real alert
        # screenshot and understanding what it meant ("j'ai compris supprime
        # la zone a tenir"). gp_low/gp_high are still stored on the order's
        # own signal_json (unchanged, still read by _rsi_divergence_watch_
        # candidate/check_rsi_divergence_watching_order) -- only this display
        # line in the Telegram alert is gone.
        expiry_line = f"Expire dans {expiry_hours:.0f}h si la divergence ne se confirme jamais."
    else:
        lines = [
            f"<b>🎯 ORDRE LIMITE POSÉ ({pocket_label}, portefeuille papier, aucun argent réel)</b>",
            f"{name} -- cible {target:.6g}",
        ]
        current_price = sig.get("price_at_order_placed")
        if isinstance(current_price, (int, float)) and current_price > 0:
            gap_pct = (current_price / target - 1.0) * 100.0
            lines.append(f"Prix actuel : {current_price:.6g} (+{gap_pct:.1f}% au-dessus de la cible)")
        # 31/07 -- explicit operator decision: once a SWING order reaches its
        # target zone, ARIA no longer buys on price alone -- she waits for a
        # fine-grained (15-30min) RSI divergence to confirm within that zone
        # (see check_rsi_divergence_watching_order's own watch_mode comment).
        # Without this line, the alert would silently claim a plain price
        # trigger, same misleading gap already fixed once for the
        # rsi_divergence_pending case (29/07, "elle cible le prix actuel,
        # étrange").
        if paper_trader.uses_fine_rsi_confirmation(order.get("wallet") or "swing"):
            lines.append(
                "Une fois dans cette zone, ARIA cherche une divergence RSI sur bougies fines "
                "(15-30 min) avant d'acheter -- pas un simple niveau de prix atteint."
            )
        expiry_line = f"Expire dans {expiry_hours:.0f}h si le prix ne redescend jamais à ce niveau."

    # 29/07 -- operator feedback ("la cible doit apparaître aussi sur l'ordre
    # limite et rajouter le pourcentage en face du prix en usdc"): ``target``
    # above (order["target_price"]) is the BUY trigger level, never the
    # eventual profit-taking level -- ``sig["target"]`` (the original
    # signal's real target, e.g. the golden pocket's range_high) was never
    # shown at all. Percentage computed from the buy trigger, the one fixed
    # reference both branches share (current price keeps moving).
    sell_target = sig.get("target")
    if isinstance(sell_target, (int, float)) and sell_target > 0 and target:
        gain_pct = (sell_target / target - 1.0) * 100.0
        lines.append(f"Cible de vente : {sell_target:.6g} (+{gain_pct:.1f}% depuis le prix d'achat visé)")

    invalidation = sig.get("invalidation")
    if isinstance(invalidation, (int, float)) and invalidation > 0:
        lines.append(f"Invalidation : {invalidation:.6g} (annule l'ordre si le prix casse ce niveau)")

    rr = sig.get("rr")
    if isinstance(rr, (int, float)) and rr > 0:
        lines.append(f"R/R du signal : {rr:.1f}")

    # Item #227 (30/07), operator request ("je veut une probabilité sur les
    # ordre limite, le taux de chance de reussite que la divergence
    # apparaisse") -- a plain historical base rate across every PAST order of
    # this SAME reason (never a per-candidate forecast, explicitly worded as
    # such). None (sig.get returns None on a missing key, e.g. an order
    # placed before this fix, or historical_trigger_rate itself returning
    # None below _MIN_HISTORICAL_TRIGGER_SAMPLE) -> omitted, never a
    # fabricated percentage.
    hist_rate = sig.get("historical_trigger_rate")
    hist_sample = sig.get("historical_trigger_sample")
    if isinstance(hist_rate, (int, float)):
        lines.append(
            f"Taux de déclenchement historique : {hist_rate:.0%} (sur {hist_sample} ordres similaires résolus)"
        )
    elif isinstance(hist_sample, int) and hist_sample > 0:
        lines.append(f"Taux de déclenchement historique : pas assez d'historique ({hist_sample} ordres résolus)")

    timeframe = _TIMEFRAME_LABEL.get(order.get("wallet"))
    if timeframe:
        lines.append(f"Analyse sur {timeframe}")

    # 29/07 -- operator feedback ("ordre limite ne montre pas la taille de la
    # future position"): an ESTIMATE at the current risk_state/weekly_context
    # (paper_trader.py's two creation sites, compute_entry_alloc) -- the real
    # size is only known at trigger time (limit_orders._execute_trigger
    # recomputes with FRESH context), so this is explicitly labeled as such,
    # never presented as a locked-in number.
    est_alloc = sig.get("estimated_alloc_usd")
    est_pct = sig.get("estimated_alloc_pct")
    if isinstance(est_alloc, (int, float)) and est_alloc > 0:
        line = f"Taille estimée : {est_alloc:,.0f} $"
        if isinstance(est_pct, (int, float)):
            line += f" ({est_pct:.1f}% du capital de départ)"
        line += " -- recalculée au déclenchement"
        lines.append(line)

    lines.append(expiry_line)
    if order.get("contract"):
        lines.append(f"DexScreener : {token_url(order['contract'], chain=order.get('chain') or 'base')}")
    return "\n".join(lines)


def format_limit_order_watching_alert(order: dict, current_price: float) -> str:
    """29/07 -- operator question ("plus le temps d'expiration est petit plus
    on est proche du point d'achat ?"): the answer is no -- expiry is a pure
    wall-clock countdown, unrelated to price proximity. THIS transition
    (``pending`` -> ``watching``, ``process_active_orders``) is the real
    signal: the price pulled back within ``LIMIT_ORDER_WATCH_TRIGGER_MULT``
    of the target AND the structure re-check (honeypot/DEX quality) just
    passed again. Never sent for a ``rsi_divergence_pending`` order -- see
    the caller's own comment (that transition happens almost instantly,
    duplicating the "ORDRE LIMITE POSÉ" alert with no new information)."""
    name = html.escape(order.get("symbol") or (order.get("contract") or "")[:10], quote=False)
    target = order["target_price"]
    pocket_label = _POCKET_LABEL.get(order.get("wallet"), (order.get("wallet") or "swing").upper())
    gap_pct = (current_price / target - 1.0) * 100.0 if target else 0.0
    lines = [
        f"<b>👁️ ARIA se rapproche ({pocket_label})</b>",
        f"{name} -- surveillance active, structure re-vérifiée et toujours propre",
        f"Prix actuel : {current_price:.6g} (à {gap_pct:+.1f}% de la cible {target:.6g})",
        "Achat dès que le prix atteint la cible (ou annulation si l'invalidation casse avant).",
    ]
    if order.get("contract"):
        lines.append(f"DexScreener : {token_url(order['contract'], chain=order.get('chain') or 'base')}")
    return "\n".join(lines)


def format_limit_order_cancelled_alert(order: dict, reason: str) -> str:
    name = order.get("symbol") or (order.get("contract") or "")[:10]
    reason_label = {
        "invalidation_crossed": "le prix a cassé l'invalidation pendant l'attente",
        # Item #182, 28/07: generalized wording -- this code now also covers a
        # golden-pocket-pending order whose DEX composite score no longer
        # confirms high quality (_reanalyze_dex_quality_for_watching), not
        # just a honeypot re-check (reanalyze_for_watching's standard path).
        "reanalysis_failed": "re-vérification échouée (sécurité ou qualité DEX)",
    }.get(reason, reason)
    return (
        f"❌ Ordre limite annulé {name} -- {reason_label}. "
        f"Cible {order['target_price']:.6g} jamais atteinte dans de bonnes conditions."
    )
