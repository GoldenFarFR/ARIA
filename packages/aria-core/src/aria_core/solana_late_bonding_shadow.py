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
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx

from aria_core import creator_reputation, pretrade_rejection_log
from aria_core.paths import ensure_wal, shadow_db_path
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

# 21/08 -- LIQUIDITY FLOOR, aligned with FAST-DISCOVERY's own 3000$.
#
# This pocket had NO liquidity filter at all -- found while answering "70% de
# bonding, ca correspond a quel market cap ?", which surfaced entries with a
# pool holding TWO DOLLARS. Harmless while simulating (we only pretend to
# buy); impossible in reality, where a 1$ order in a 2$ pool moves the price
# 50%. The real-execution seam added the same day makes this a blocker, not a
# cosmetic gap.
#
# Measured on 1114 closures with honest fills:
#     no floor    PnL -8.3%  (without top2 -10.7%)  31% winners
#     >= 1000$        -8.5%             -10.9%      31%
#     >= 3000$        -7.3%             -10.0%      31%
#     >= 5000$        -3.0%              -6.0%      35%
# 5000$ scores clearly better AND survives the outlier test, so it is not an
# artefact -- but it cuts 28% of the >=+100% winners (their average: +313%),
# against 8% at 3000$. This dome's standing rule is that any extra entry
# filter cuts the rare winners carrying everything, so the tighter floor is
# NOT taken on backtest alone: 5000$ stays one line away if live data
# confirms it, whereas winners we stopped observing can never be recovered.
#
# Also sets the tradable position size: a 3000$ pool tolerates roughly 30$
# before price impact eats the edge (measured via Jupiter the same day).
MIN_LIQUIDITY_USD = 3000.0

# 21/08 -- CARENCE APRES SORTIE. Le defaut le plus couteux trouve ce jour-la,
# revele par une capture de l'operateur : CALLOUTS a ete achete et stoppe
# TROIS fois en huit minutes (0s, 33s, 2min), puis a fait +199% sans nous.
#
# Rien n'empechait de racheter un token qui venait de nous ejecter. Le stop
# fixe coupe a -5%, le prix remonte de 5%, on rachete, on se refait couper.
# Mesure sur 6h : 423 clotures pour seulement 192 tokens distincts, 73% des
# clotures etaient des RE-ENTREES, jusqu'a 12 positions sur un meme token.
#     tokens re-tradés   +6.7%  (79 tokens)
#     tokens tradés 1x  +30.0%
# On divisait notre propre performance par quatre en repayant la friction a
# chaque aller-retour sur le meme sous-jacent.
#
# 30 minutes plutot qu'un blocage definitif : un token qui nous a stoppe puis
# se reprend VRAIMENT reste une opportunite legitime (CALLOUTS l'a prouve),
# mais pas dans les secondes qui suivent, quand le prix oscille autour du
# seuil qui vient de nous sortir.
REENTRY_COOLDOWN_MINUTES = 30.0

# 20/08, RELAXED 0.60 -> 0.95. Kept non-1.0 on purpose: at 100% a single
# wallet is literally the only buyer, which is not a market at all. Everything
# below that is COLLECTED rather than judged -- `top_buyer_share_at_entry` is
# recorded, so the real wash-trading cutoff is measurable later.
MAX_TOP_BUYER_SHARE = 0.95

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

# 21/08 -- floor loss for the window the trailing stop does not cover (it only
# arms once the peak reaches +10% above entry). Measured on THIS pocket's own
# 304 closures at 70%+: the 142 positions whose trailing never armed averaged
# -55.5%, with no downside rule protecting any of them. Full derivation, the
# pessimistic assumption and the outlier test live next to
# `HARD_STOP_PCT_DEFAULT` in the shared exit module -- not restated here.
# Deliberately passed as an ARGUMENT to the shared `evaluate_exit` rather than
# forked into a local copy of the exit rule, so FAST-DISCOVERY stays an
# untouched control and the two pockets keep differing on one variable.
# 21/08 -- OPERATOR-DESIGNED EXIT: fixed stop from entry + staged profit
# taking, replacing the trailing stop entirely for this pocket.
#
# His reasoning, and it is the right one: a trailing stop keeps the HIGHEST
# price, so every swing during a climb eats its margin without ever giving it
# back -- a token that runs to +50%, falls to +10% and recovers to +30% ends
# up glued to a stop set on a peak it no longer trades near. A fixed stop
# never moves, so the ladder is what secures gains: the two do distinct jobs
# instead of fighting each other.
#
# Measured on 86 archived paths, 1% sell friction included:
#     ladder 50/100/200 + fixed -5%   +7.0%  (outlier-tested +3.8%)
#     free-ride variant               +5.6%  (+3.1%)
#     banded trailing                 +4.9%  (+2.1%)
# Robust across ladder SHAPES -- every configuration tested landed between
# +7.1% and +8.5% before friction -- so the principle carries it, not these
# exact thresholds. Tested against the trailing this MORNING the ladder
# degraded, because there the two overlapped; the pairing is what changed.
#
# Independently confirmed by looking at what happens after we sell: of 12
# closures above +100%, TEN collapsed afterwards (median -81.8%) and only two
# kept rising. On these tokens the peak is a point of no return, so selling
# ON THE WAY UP structurally beats a trailing stop, which by construction can
# only act once the fall has already started.
#
# The friction caveat matters: at 3% sell friction this advantage halves, at
# 5% it disappears. Rungs are small fractions of a small position, so real
# impact should stay well under that -- to be verified on live closures.
PROFIT_LADDER = ((50.0, 0.25), (100.0, 0.25), (200.0, 0.25))
FIXED_STOP_PCT = 5.0

# Kept for the record: the trailing path is no longer reached by this pocket
# (`FIXED_STOP_PCT` takes precedence), but the constant stays so the shared
# rule's signature is honoured and FAST-DISCOVERY -- which still runs the
# trailing at a flat 15% -- remains a live A/B against this design.
HARD_STOP_PCT = 20.0

# 21/08 -- this pocket uses the SHARED progressive trailing bands
# (`trailing_distance_for`), so it passes no fixed distance at all.
#
# It ran at a fixed 5% for about an hour, chosen from `exit_replay` alone.
# That was wrong and the operator caught it: replaying only sees paths up to
# the real exit, so it is blind to what a wider stop would have captured
# afterwards, and it ranked tight best because the mass of small losers
# dominates the average. Measured properly -- the largest pullback suffered
# BEFORE reaching the peak -- 78% of the >+100% winners pull back past 5% on
# their way up, while NONE pull back past 15%. A 5% stop ejects the winners
# that carry the entire pocket.
#
# FAST-DISCOVERY deliberately keeps a flat 15%, so the two pockets now A/B
# banded-vs-fixed on live data instead of both moving on one replay.

# 21/08 -- REINFORCEMENT, measured in parallel and never acted on.
#
# Operator's read of the PnL is exact: many small losers, a few explosions.
# His question was whether adding to a position that has already proven
# something amplifies the winning side. On 1013 real closures it does, and the
# decisive figure is this: capital added after a token moved returns +8.2%
# (outlier-tested) while the capital committed blind at entry returns -10.4%,
# on the SAME tokens over the SAME period.
#
# Simulated at portfolio level, return on capital actually deployed:
#     no reinforcement        -9.1%  (without top2 -11.7%)
#     reinforce at +30%       -3.1%  (-6.6%)   <-- chosen
#     reinforce at +50%       -4.5%  (-8.1%)
#     reinforce at +75%       -5.8%  (-9.3%)
# Earlier is better, and stated plainly: this REDUCES the loss by 6 points, it
# does not make the pocket profitable. The median reinforcement still LOSES
# (-8.4%) -- the gain comes from the same handful of explosions as everything
# else here, so it is another asymmetric bet, not compounding.
#
# Half at entry and half on confirmation, because doubling both stakes changes
# nothing per euro deployed: the two variants scored identically, and this one
# reaches it with 0.67 of capital per position instead of 1.34.
#
# SHADOW-ONLY AND PARALLEL: nothing about the live position changes. The
# would-be reinforcement price is recorded and a second PnL computed beside
# the real one, so both are measured on the SAME tokens without splitting the
# data rate and without any way to damage the measurement in flight.
REINFORCE_TRIGGER_PCT = 30.0
REINFORCE_ENTRY_WEIGHT = 0.5
REINFORCE_ADD_WEIGHT = 0.5

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
CONFIG_EPOCH = "2026-08-21T15:45:00+00:00"

# 20/08 -- raised with the widened band. The REAL constraint is the exit
# loop: more open positions means each one is checked less often, which is
# exactly what caused the late liquidity_collapse catches fixed earlier today
# (first check landing 32-116s after entry despite a 10s cadence). The exit
# sweep's own `limit` is raised in step below so widening collection cannot
# quietly re-create that failure.
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
                founding_tracked_at_entry INTEGER,
                founding_exited_at_entry INTEGER,
                founding_exit_ratio_at_entry REAL,
                founding_bundle_size_at_entry INTEGER,
                creator_address TEXT,
                creator_sold_at_entry INTEGER,
                has_paid_profile INTEGER,
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
                exit_price_source TEXT,
                exit_detail TEXT,
                amm_pool_address TEXT,
                sol_velocity_at_entry REAL,
                sellable_at_entry INTEGER,
                roundtrip_loss_pct_at_entry REAL,
                reinforce_price REAL,
                reinforce_at TEXT,
                reinforced_final_multiplier REAL
            )
            """
        )
        # Same index discipline as the sibling pockets: the two columns every
        # read path filters on.
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_open ON {TABLE}(exit_reason, last_checked_at)")
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_pool ON {TABLE}(pool_address)")
        # Hot idempotent ALTER, same pattern as everywhere else here -- start
        # accumulating on the live table rather than waiting for a rebuild.
        await ensure_wal(db)
        cur = await db.execute(f"PRAGMA table_info({TABLE})")
        existing = {r[1] for r in await cur.fetchall()}
        if "has_paid_profile" not in existing:
            await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN has_paid_profile INTEGER")
        if "exit_detail" not in existing:
            await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN exit_detail TEXT")
        # 21/08 -- founding-cohort columns, hot-ALTERed like the rest so the
        # live table starts accumulating immediately.
        for col, typ in (
            ("founding_tracked_at_entry", "INTEGER"),
            ("founding_exited_at_entry", "INTEGER"),
            ("founding_exit_ratio_at_entry", "REAL"),
            ("founding_bundle_size_at_entry", "INTEGER"),
            ("creator_address", "TEXT"),
            ("creator_sold_at_entry", "INTEGER"),
            # 21/08 -- the AMM pool a graduated token moved to. A pump.fun
            # position keeps its BONDING-CURVE address for life, so after
            # graduation the pool was simply unknown and pricing fell back to
            # REST -- on this pocket's best-performing segment.
            ("amm_pool_address", "TEXT"),
            ("sol_velocity_at_entry", "REAL"),
            ("sellable_at_entry", "INTEGER"),
            ("roundtrip_loss_pct_at_entry", "REAL"),
            ("reinforce_price", "REAL"),
            ("reinforce_at", "TEXT"),
            ("reinforced_final_multiplier", "REAL"),
        ):
            if col not in existing:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col} {typ}")
        await db.commit()
    _ensured_db_paths.add(path)


async def _in_reentry_cooldown(db, token_address: str, chain: str) -> bool:
    """Ce token nous a-t-il ejectes recemment ?

    Porte sur le TOKEN et non sur le pool : un meme token peut etre vu via
    plusieurs adresses au cours de sa vie (courbe puis AMM apres graduation),
    et la carence doit suivre le sous-jacent, pas l'enveloppe."""
    cur = await db.execute(
        f"SELECT 1 FROM {TABLE} WHERE token_address = ? AND chain = ? "
        f"AND exit_reason IS NOT NULL AND last_checked_at >= ? LIMIT 1",
        (token_address, chain,
         (datetime.now(timezone.utc) - timedelta(minutes=REENTRY_COOLDOWN_MINUTES)).isoformat()),
    )
    return await cur.fetchone() is not None


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
        # 21/08 -- how FAST the curve is advancing, not just where it is. See
        # `TokenTradeFlow.sol_velocity`: graduation is the only factor that
        # separates this pocket's real winners, and speed is the one signal
        # plausibly predicting it that we were not recording. COLLECTED ONLY.
        "sol_velocity": flow.sol_velocity if flow else None,
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


def _founding_snapshot(trade_stream, mint: str) -> dict:
    """The founding cohort as recorded by the live stream, or all-None when
    the mint was never seen from early enough.

    All-None rather than zeros on purpose: "no founder sold" and "we were not
    watching this token when it launched" are different facts, and collapsing
    them would quietly bias the sample the moment we start measuring it. The
    stream only knows a mint from the moment this process connected, so an
    unknown cohort is the NORMAL state right after a restart."""
    empty = {"tracked": None, "exited": None, "exit_ratio": None, "bundle_size": None}
    if trade_stream is None or not hasattr(trade_stream, "founding_cohort"):
        return empty
    try:
        return trade_stream.founding_cohort(mint) or empty
    except Exception:  # noqa: BLE001 -- an observation never blocks an entry
        return empty


def _founder_has_sold(trade_stream, mint: str, creator: str) -> bool:
    """Whether the token's own creator was seen selling. The single most
    direct rug signal there is, and free -- the stream already carries every
    seller's wallet."""
    try:
        return bool(trade_stream.founder_sold(mint, creator))
    except Exception:  # noqa: BLE001 -- an observation never blocks an entry
        return False


async def consider_candidate(
    mint: str, pool_address: str, *, chain: str = "solana", trade_stream=None,
    http_client: httpx.AsyncClient | None = None, geckoterminal_client=None,
    bonding_ws_feed=None,
    resolve_curves_fn=None, snapshot_fn=None, db_path: str | None = None,
    execute_fn=None,
) -> int | None:
    """Screens one mint and, if it passes, records an entry.
    Returns the new row id, or ``None``. Never raises into the caller.

    ``execute_fn`` -- 21/08, the seam for REAL capital, on the operator's
    explicit constraint: "il faut exactement les memes outils que la poche
    bonding". Real trading must REPLACE the execution and nothing else --
    sourcing, filters, exit rule and price feed stay this shared code. A
    second module reimplementing the strategy would diverge silently and make
    every hour of calibration worthless, which is the exact defect the shared
    `evaluate_exit` already exists to prevent.

    Signature: ``await execute_fn(mint, pool_address, chain=...,
    quoted_price=...)`` returning ``{"entry_price": float, "tx": str}`` on a
    real fill, or ``None`` if the buy failed. ``None`` ABORTS the entry
    entirely rather than recording a position that does not exist -- a shadow
    row standing in for a failed real trade would corrupt every measurement
    built on this table.

    Default ``None`` keeps the pocket in pure simulation, byte-identical to
    its behaviour before this parameter existed."""
    try:
        await _ensure_table(db_path)
        async with aiosqlite.connect(db_path or _db_path()) as db:
            if await _has_open_signal(db, pool_address, chain):
                return None
            if await _in_reentry_cooldown(db, mint, chain):
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
            elif (snapshot.reserve_usd or 0) < MIN_LIQUIDITY_USD:
                # Checked here rather than in `screen_candidate`: the reserve
                # is only known once the position has been priced. A pool this
                # thin is not a weak candidate, it is an UNEXECUTABLE one --
                # entries were found on pools holding two dollars, harmless
                # while simulating and impossible in reality.
                accepted, reason = False, f"blocked_thin_liquidity: reserve={snapshot.reserve_usd or 0:.0f}"

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
        # 21/08, operator's own idea -- who launched this, did his cohort
        # already sell, and did they arrive as one Jito bundle. Read from the
        # trade stream we already consume: no extra API call, no added entry
        # latency. COLLECTED ONLY for now, nothing rejects on it: a filter
        # would need its own forward sample first, and any new entry filter
        # risks cutting the rare winners that carry the whole PnL.
        founding = _founding_snapshot(trade_stream, mint)
        creator = getattr(account, "creator", None)
        # `creator_sold` stays NULL rather than 0 when the cohort is unknown:
        # the stream only knows a mint from the moment this process connected,
        # so "he did not sell" and "we were not watching" are different facts.
        creator_sold = None
        if creator is not None and founding.get("tracked") is not None:
            creator_sold = 1 if _founder_has_sold(trade_stream, mint, creator) else 0
        # REAL execution, when wired. The price actually paid replaces the
        # quoted one: recording the quote while the fill happened elsewhere
        # would reproduce, on real money, exactly the optimistic-fill bug
        # found in the exit rule earlier today.
        entry_price = snapshot.price_usd
        tx_hash = None
        if execute_fn is not None:
            try:
                filled = await execute_fn(
                    mint, pool_address, chain=chain, quoted_price=snapshot.price_usd,
                )
            except Exception as exc:  # noqa: BLE001 -- a failed buy is not a position
                logger.info("solana_late_bonding_shadow: execute_fn raised for %s (%s)", mint, exc)
                return None
            if not filled or not filled.get("entry_price"):
                return None
            # The real fill price becomes the entry, and the "realistic"
            # column too: a modelled impact makes no sense once a genuine
            # price has been paid.
            entry_price = filled["entry_price"]
            realistic = entry_price
            tx_hash = filled.get("tx")

        async with aiosqlite.connect(db_path or _db_path()) as db:
            cur = await db.execute(
                f"""
                INSERT INTO {TABLE}
                    (pool_address, token_address, chain, detected_at, entry_price, reserve_usd,
                     bonding_progress_at_entry, distinct_buyers_at_entry, top_buyer_share_at_entry,
                     buyer_acceleration_at_entry, peak_price, realistic_entry_price,
                     founding_tracked_at_entry, founding_exited_at_entry,
                     founding_exit_ratio_at_entry, founding_bundle_size_at_entry,
                     creator_address, creator_sold_at_entry, sol_velocity_at_entry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pool_address, mint, chain, datetime.now(timezone.utc).isoformat(),
                    entry_price, snapshot.reserve_usd, metrics.get("bonding_progress"),
                    metrics.get("distinct_buyers"), metrics.get("top_buyer_share"),
                    metrics.get("buyer_acceleration"), entry_price, realistic,
                    founding.get("tracked"), founding.get("exited"),
                    founding.get("exit_ratio"), founding.get("bundle_size"),
                    creator, creator_sold, metrics.get("sol_velocity"),
                ),
            )
            await db.commit()
            asyncio.create_task(_enrich_paid_profile(cur.lastrowid, mint, chain=chain, db_path=db_path))
            asyncio.create_task(_enrich_exit_route(cur.lastrowid, mint, db_path=db_path))
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
    # Graduated: the curve feed honestly reports unavailable, but the AMM pool
    # is on the SAME RPC -- try it before paying for a REST round trip.
    amm = row.get("amm_pool_address")
    if amm and bonding_ws_feed is not None:
        try:
            snap = bonding_ws_feed.get_snapshot(amm)
            if getattr(snap, "available", False) and snap.price_usd is not None:
                return snap
        except Exception:  # noqa: BLE001
            pass
    fn = snapshot_fn or _snapshot_with_fallback
    return await fn(None, row["pool_address"], row["token_address"], chain=chain)


async def _enrich_exit_route(row_id: int, mint: str, *, db_path: str | None = None) -> None:
    """Fire-and-forget: can this token actually be SOLD, and at what cost.

    21/08, operator's design -- "on va trader des token legerement dangereux
    (bonding), il faut le mecanisme de verification achat-vente instantanee".
    This pocket had NO scam check of any kind: no RugCheck, no honeypot
    screen, nothing, while FAST-DISCOVERY has run RugCheck since it was
    written. A token you can buy but not sell only reveals itself on the way
    out.

    Runs AFTER the entry is recorded, deliberately: the whole day was spent
    cutting milliseconds off the entry path, and this costs two HTTP calls.
    COLLECTED ONLY for now -- it becomes a pre-trade block once the data shows
    it actually separates rugs from survivors, not before.
    """
    try:
        from aria_core.services import jupiter

        # Priced at the size we would really trade, since the cost is
        # size-dependent -- a check run at a different size measures a
        # different token than the one we hold.
        out = await jupiter.roundtrip_cost_pct(mint, SIMULATED_TRADE_SIZE_USD / 92.0)
        sellable = out.get("sellable")
        async with aiosqlite.connect(db_path or _db_path()) as db:
            await db.execute(
                f"UPDATE {TABLE} SET sellable_at_entry = ?, roundtrip_loss_pct_at_entry = ? "
                f"WHERE id = ?",
                (None if sellable is None else int(sellable),
                 out.get("roundtrip_loss_pct"), row_id),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- an observation never breaks a pocket
        logger.info("solana_late_bonding_shadow: exit-route check failed for %s (%s)", mint, exc)


async def _enrich_paid_profile(row_id: int, mint: str, *, chain: str = "solana", db_path: str | None = None) -> None:
    """Fire-and-forget: records whether the token has a PAID DexScreener profile.

    21/08 -- operator's own idea, and it measured as the strongest single signal
    of the whole investigation. On 150 real closures:
        WITH a paid profile   n=63  PnL +57.4%  (+12.2% without its two best)
                                    rug 22.2%   33 winners of 63
        WITHOUT               n=87  PnL -27.7%  (-34.9% without its two best)
                                    rug 51.7%   17 winners of 87
    It stays POSITIVE after removing its two best trades, which none of the five
    segments rejected yesterday managed. The operator also raised the right
    objection -- scammers pay for profiles too -- and the data answers it: they
    do, but the rug rate is still more than halved, because a ~300$ profile
    filters out the zero-cost rugs that make up the bulk.

    REUSES `dexscreener.fetch_token_pairs`, whose `PairSnapshot.project_links`
    already extracts exactly `info.websites`/`socials` -- nothing new built.
    Called AFTER insertion, fire-and-forget, so it adds ZERO latency to the
    entry decision (same discipline as FAST-DISCOVERY's rugcheck backfill).
    COLLECTED ONLY: nothing acts on it until the pocket has its own sample.
    """
    try:
        from aria_core.services import dexscreener

        pairs = await dexscreener.fetch_token_pairs(mint, chain=chain)
        has_profile = any(getattr(p, "project_links", None) for p in (pairs or []))
        async with aiosqlite.connect(db_path or _db_path()) as db:
            await db.execute(
                f"UPDATE {TABLE} SET has_paid_profile = ? WHERE id = ?",
                (1 if has_profile else 0, row_id),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- enrichment never disturbs the pocket
        logger.info("solana_late_bonding_shadow: paid-profile enrichment failed for %s (%s)", mint, exc)


async def resolve_migrated_pools(
    http_client, *, chain: str = "solana", bonding_ws_feed=None, pumpswap_feed=None,
    limit: int = 10, db_path: str | None = None, find_pool_fn=None,
) -> int:
    """Finds the AMM pool of every open position whose curve has completed, so
    it keeps being priced on the RPC instead of falling back to REST.

    21/08, operator: "on a dit tout par le RPC Helius". Bounded on purpose --
    at most `limit` unresolved positions per pass, each costing ONE
    `getProgramAccounts`, and each resolved exactly once for the position's
    whole remaining life. A failure is not recorded, so it simply retries on
    the next pass rather than poisoning the row with a wrong address."""
    from aria_core.services.pumpswap_ws import find_pool_for_mint

    find = find_pool_fn or find_pool_for_mint
    await _ensure_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT id, pool_address, token_address FROM {TABLE} "
            f"WHERE chain = ? AND exit_reason IS NULL AND amm_pool_address IS NULL LIMIT ?",
            (chain, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    resolved = 0
    for row in rows:
        # Only spend an RPC call on positions the curve feed can no longer
        # price -- a token still ON its curve has no AMM pool to find.
        if bonding_ws_feed is not None:
            try:
                snap = bonding_ws_feed.get_snapshot(row["pool_address"])
                if getattr(snap, "available", False):
                    continue
            except Exception:  # noqa: BLE001
                continue
        try:
            pool = await find(http_client, row["token_address"])
        except Exception:  # noqa: BLE001 -- never breaks the exit loop
            continue
        if not pool:
            continue
        async with aiosqlite.connect(db_path or _db_path()) as db:
            await db.execute(
                f"UPDATE {TABLE} SET amm_pool_address = ? WHERE id = ?", (pool, row["id"]),
            )
            await db.commit()
        if pumpswap_feed is not None:
            try:
                await pumpswap_feed.add_pools([pool])
            except Exception:  # noqa: BLE001 -- subscription is an enhancement
                pass
        resolved += 1
        logger.info(
            "solana_late_bonding_shadow: migrated pool resolved for %s -> %s",
            row["token_address"], pool,
        )
    return resolved


def _reinforced_multiplier(row: dict, exit_multiplier: float | None) -> float | None:
    """PnL the position WOULD have had with half the stake at entry and half
    added once it reached ``REINFORCE_TRIGGER_PCT``.

    Returns ``None`` when the reinforcement never triggered -- the position is
    then identical to the real one and reporting the same number twice would
    silently inflate the sample of "reinforced" trades with untouched ones.

    Weighted by capital DEPLOYED, not by position count: a reinforcement that
    never fires means half the capital was never committed, and crediting it
    with the full entry's result would flatter the strategy exactly where it
    matters least."""
    price = row.get("reinforce_price")
    entry = row.get("entry_price")
    if not price or not entry or exit_multiplier is None:
        return None
    exit_price = exit_multiplier * entry
    gain = (
        REINFORCE_ENTRY_WEIGHT * (exit_price / entry - 1)
        + REINFORCE_ADD_WEIGHT * (exit_price / price - 1)
    )
    deployed = REINFORCE_ENTRY_WEIGHT + REINFORCE_ADD_WEIGHT
    return 1 + gain / deployed


async def _apply_exit_check(row: dict, snapshot, *, chain: str, db_path: str | None) -> dict:
    """Archives the path, runs the SHARED exit rule and persists the outcome
    for ONE position.

    21/08 -- extracted so the polling sweep and the event-driven handler run
    the identical code. Two call sites each carrying their own copy would mean
    the pocket quietly trading two policies at once, and the difference would
    surface as unexplainable PnL rather than as a failure."""
    # 21/08 -- archive the price path, the standing convention since 18/08
    # that this pocket never followed. Without it a position's history
    # holds only entry/peak/exit, so NO alternative exit threshold can
    # ever be measured -- only guessed at. And there is a real question
    # waiting on it: the trailing stop's fixed -15% distance captures 72%
    # of a +100% move but LOSES money on a +12% one (n=8, -3.1% average),
    # which is a calibration problem no stored closure can settle.
    # Pure local SQLite write on a WAL database, zero network cost.
    try:
        from aria_core import shadow_snapshot_archive

        await shadow_snapshot_archive.store_snapshot(
            module="solana_late_bonding", position_id=row["id"],
            pool_address=row["pool_address"], chain=chain,
            price_usd=snapshot.price_usd, reserve_usd=snapshot.reserve_usd,
            dex_id=snapshot.dex_id,
            # The extremes REACHED since the last read, not just this
            # sample -- exactly what replaying a different stop distance
            # needs, and what a point sample can never reconstruct.
            price_change_pct=None,
            transactions=None, volume_usd=None,
            # Named parameters, NOT a dict passthrough: the first attempt
            # routed these through `price_change_pct`, whose fixed key set
            # silently dropped them -- 149 rows archived with the extremes
            # missing and no error anywhere.
            window_high=getattr(snapshot, "price_high_since_last_read", None),
            window_low=getattr(snapshot, "price_low_since_last_read", None),
        )
    except Exception:  # noqa: BLE001 -- archiving never blocks an exit
        pass

    # Record the would-be reinforcement the first time the trigger is crossed.
    # Written BEFORE the exit rule runs, so a position that crosses the
    # trigger and closes in the same check still counts it -- otherwise the
    # fastest movers, which are exactly the ones worth reinforcing, would be
    # the ones systematically missed.
    if row.get("reinforce_price") is None and row.get("entry_price"):
        trigger = row["entry_price"] * (1 + REINFORCE_TRIGGER_PCT / 100.0)
        reached = max(
            snapshot.price_usd,
            getattr(snapshot, "price_high_since_last_read", None) or snapshot.price_usd,
        )
        if reached >= trigger:
            row["reinforce_price"] = trigger
            async with aiosqlite.connect(db_path or _db_path()) as db:
                await db.execute(
                    f"UPDATE {TABLE} SET reinforce_price = ?, reinforce_at = ? "
                    f"WHERE id = ? AND reinforce_price IS NULL",
                    (trigger, datetime.now(timezone.utc).isoformat(), row["id"]),
                )
                await db.commit()

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
        # 21/08 -- the extremes REACHED since the last read, not just the
        # price at the instant we happen to look. The websocket already
        # tracks them (`price_high/low_since_last_read`) and
        # FAST-DISCOVERY already passed them; this pocket did not, so a
        # stop could only ever react to a point sample. That is the real
        # reason a -20% hard stop filled at -78%: the feed HAD recorded
        # the -20% crossing, the pocket simply never read it. Fixing the
        # queue's freshness narrowed the gap; this closes it at the source
        # and is also what makes filling AT the stop legitimate rather
        # than optimistic -- we genuinely observed the crossing.
        window_high=getattr(snapshot, "price_high_since_last_read", None),
        window_low=getattr(snapshot, "price_low_since_last_read", None),
        hard_stop_pct=HARD_STOP_PCT,
        profit_ladder=PROFIT_LADDER,
        fixed_stop_pct=FIXED_STOP_PCT,
    )
    async with aiosqlite.connect(db_path or _db_path()) as db:
        await db.execute(
            f"""
            UPDATE {TABLE} SET remaining_qty = ?, realized_proceeds = ?, peak_price = ?,
                realistic_realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                realistic_final_multiplier = ?, last_price = ?, last_reserve_usd = ?,
                last_checked_at = ?, exit_price_source = ?, exit_detail = ?,
                reinforced_final_multiplier = ?
            WHERE id = ? AND exit_reason IS NULL
            """,
            (
                result.get("remaining_qty"), result.get("realized_proceeds"), result.get("peak_price"),
                result.get("realistic_realized_proceeds"), result.get("exit_reason"),
                result.get("final_multiplier"), result.get("realistic_final_multiplier"),
                snapshot.price_usd, snapshot.reserve_usd,
                datetime.now(timezone.utc).isoformat(), snapshot.dex_id,
                result.get("exit_detail"),
                _reinforced_multiplier(
                    row,
                    result.get("realistic_final_multiplier") or result.get("final_multiplier"),
                ),
                row["id"],
            ),
        )
        await db.commit()
    return {"checked": 1, "closed": 1 if result.get("exit_reason") else 0}


async def advance_position_by_pool(
    pool_address: str, *, chain: str = "solana", bonding_ws_feed=None,
    snapshot_fn=None, db_path: str | None = None,
) -> dict:
    """Evaluates the ONE open position on this pool, right now.

    21/08 -- the event-driven counterpart to `advance_exit_simulation`'s
    polling sweep. Wired to the bonding feed's `on_update` hook, it reacts to
    a price move within the websocket's own latency instead of waiting for the
    position's turn in a queue -- a measured 8s gap on a 10s cadence, which is
    enough for a collapsing curve to run from -15% to -30% and is exactly why
    a -20% stop kept filling near -30%.

    Deliberately reuses `_persist_exit_result` and the shared `evaluate_exit`
    rather than duplicating either: the polling sweep stays as the safety net
    for anything the feed never pushes (a silent pool, a reconnect), and both
    paths MUST apply the same rule or the pocket would trade two policies at
    once.

    Prices from the LOCAL feed only -- never REST. An event handler that could
    block on a throttled provider would stall the very reactivity it exists
    for, and a pool with no local price simply waits for the polling sweep."""
    if bonding_ws_feed is None:
        return {"checked": 0, "closed": 0}
    await _ensure_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {TABLE} WHERE pool_address = ? AND chain = ? AND exit_reason IS NULL "
            f"LIMIT 1", (pool_address, chain),
        )
        row = await cur.fetchone()
    if row is None:
        return {"checked": 0, "closed": 0}
    row = dict(row)

    try:
        snapshot = bonding_ws_feed.get_snapshot(pool_address)
    except Exception:  # noqa: BLE001 -- a feed hiccup is not a verdict
        return {"checked": 0, "closed": 0}
    if not getattr(snapshot, "available", False) or snapshot.price_usd is None:
        # Graduated or not yet pushed: the polling sweep owns this one.
        return {"checked": 0, "closed": 0}

    return await _apply_exit_check(row, snapshot, chain=chain, db_path=db_path)


async def advance_exit_simulation(
    geckoterminal_client=None, *, chain: str = "solana", limit: int = 200,
    snapshot_fn=None, bonding_ws_feed=None, db_path: str | None = None,
    max_rest_calls: int | None = None,
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

    # 21/08 -- LOCAL-FIRST ordering. The queue is sequential, and a single
    # MIGRATED position waiting on GeckoTerminal's adaptive throttle (measured
    # at 16.2s after a real 429) stalled every bonding-curve position behind
    # it -- whose price needs no network at all, since the websocket already
    # holds their reserves in memory.
    #
    # The cost was not theoretical. It made the average gap between two checks
    # of the SAME position 41s against a 10s nominal cadence, and that is what
    # broke the hard stop on its first live batch: 15 closures averaging -44.5%
    # against a -20% floor, with `exit_detail` reading "low touched -78.7%" on
    # a position whose peak was +0.0%. A stop cannot cut a price it never sees;
    # 41 seconds is long enough for a pump.fun rug to run to completion.
    #
    # So rows whose price is a free local read go FIRST, in one uninterrupted
    # sweep, and the network-bound ones follow. No parallelism, no new client,
    # no threshold touched -- just refusing to let the slow tail set the pace
    # for everyone.
    # READ THE FEED EXACTLY ONCE PER POSITION, then reuse that snapshot for
    # ordering, evaluation and archiving alike.
    #
    # 21/08, self-inflicted bug found by reading the archive table: this
    # ordering pass used to call `get_snapshot()` on its own, and that call
    # is NOT free of side effects -- the feed RESETS `price_high/low_since_
    # last_read` after every read, by design ("since the caller's last read").
    # So the ordering pass consumed the window and the real evaluation
    # received extremes already reset to the current price, silently undoing
    # the whole point of passing window_low to the exit rule. Two fixes that
    # each looked right in isolation cancelled each other out, and nothing
    # failed -- the columns were simply empty.
    local_snaps: dict[int, object] = {}
    if bonding_ws_feed is not None:
        for row in rows:
            try:
                snap = bonding_ws_feed.get_snapshot(row["pool_address"])
                if getattr(snap, "available", False) and snap.price_usd is not None:
                    local_snaps[row["id"]] = snap
            except Exception:  # noqa: BLE001 -- a feed hiccup is not a verdict
                continue

    rows.sort(key=lambda r: r["id"] not in local_snaps)

    # 21/08 -- REST ceiling per cycle, the guardrail the two sibling pockets
    # already had and this one did not. Measured cost of its absence: an exit
    # pass took 23.5s for 9 positions (2.6s each) because every position the
    # websocket had lost fell through to the throttled REST cascade, one after
    # another -- pushing the gap between two checks of the SAME position to
    # 60s against a 10s cadence, which is precisely why the hard stop filled
    # at -44.5% instead of -20%. Beyond this ceiling a position is simply left
    # for the next pass rather than made to wait: a stale check on ONE
    # position is far cheaper than delaying every other position behind it.
    rest_budget = max_rest_calls
    for row in rows:
        stats["checked"] += 1
        locally_priced = row["id"] in local_snaps
        if not locally_priced and rest_budget is not None:
            if rest_budget <= 0:
                stats["checked"] -= 1
                stats["deferred_no_rest_budget"] = stats.get("deferred_no_rest_budget", 0) + 1
                continue
            rest_budget -= 1
        try:
            # Reuse the single read taken above rather than asking again --
            # asking again would consume a second window and hand the exit
            # rule a flattened one.
            snapshot = local_snaps.get(row["id"]) or await _price_position(
                row, chain=chain, bonding_ws_feed=bonding_ws_feed, snapshot_fn=snapshot_fn,
            )
        except Exception:  # noqa: BLE001 -- a provider failure is not a verdict
            continue
        if not snapshot.available or snapshot.price_usd is None:
            continue

        outcome = await _apply_exit_check(row, snapshot, chain=chain, db_path=db_path)
        stats["closed"] += outcome["closed"]
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

        # NOTE on the `replace(...,' ','T')`: SQLite's `datetime()` returns
        # "YYYY-MM-DD HH:MM:SS" while these columns hold ISO strings with a
        # "T". Compared as text, "T" sorts AFTER " ", so a naive
        # `>= datetime('now','-1 hour')` matched the WHOLE day and reported
        # 911 entries/hour against a real ~78. Found before it was ever shown.
        #
        # 21/08, operator request: "le nombre de token trade par heure sur la
        # derniere heure et une moyenne comme le pnl sur 24h que je puisse voir
        # le debit en direct... et mieux me projeter". Throughput answers a
        # question the cumulative figures cannot: how long until there is
        # enough of anything to judge. Deliberately measured on a rolling
        # WALL-CLOCK window, NOT from CONFIG_EPOCH -- the epoch moves whenever
        # a parameter changes, which would make the rate collapse to zero
        # right after every reset and read as a stalled pocket.
        cur = await db.execute(
            f"SELECT COUNT(*) AS n FROM {TABLE} WHERE chain = ? "
            f"AND detected_at >= replace(datetime('now','-1 hour'),' ','T')", (chain,),
        )
        entries_1h = (await cur.fetchone())["n"]
        cur = await db.execute(
            f"SELECT COUNT(*) AS n FROM {TABLE} WHERE chain = ? AND exit_reason IS NOT NULL "
            f"AND last_checked_at >= replace(datetime('now','-1 hour'),' ','T')", (chain,),
        )
        closures_1h = (await cur.fetchone())["n"]
        # The 24h PnL spans configurations by construction, so it is a
        # THROUGHPUT-scale reading, never the figure to judge a setting on.
        cur = await db.execute(
            f"SELECT AVG(COALESCE(realistic_final_multiplier, final_multiplier)) AS m, "
            f"COUNT(*) AS n FROM {TABLE} WHERE chain = ? AND exit_reason IS NOT NULL "
            f"AND last_checked_at >= replace(datetime('now','-24 hours'),' ','T') "
            f"AND COALESCE(realistic_final_multiplier, final_multiplier) IS NOT NULL", (chain,),
        )
        row24 = await cur.fetchone()
        avg_24h = (row24["m"] - 1) * 100 if row24["m"] is not None else None
        closures_24h = row24["n"]

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
        "entries_last_hour": entries_1h,
        "closures_last_hour": closures_1h,
        "avg_pnl_pct_24h": round(avg_24h, 2) if avg_24h is not None else None,
        "closures_24h": closures_24h,
    }
