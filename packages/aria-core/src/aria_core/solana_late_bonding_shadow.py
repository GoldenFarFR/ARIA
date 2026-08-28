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
import copy
import logging
import time
from datetime import datetime, timedelta, timezone

import dataclasses
from dataclasses import is_dataclass, replace as dataclasses_replace

from types import SimpleNamespace

import aiosqlite
import httpx

from aria_core import db_migrations

from aria_core import (
    creator_reputation,
    pretrade_rejection_log,
    shadow_candle_archive,
    shadow_discovery_only,
    shadow_pocket_cap,
)
from aria_core.paths import ensure_wal, shadow_db_path
from aria_core.skills import chain_liquidity_regime
from aria_core.services.solana_rpc_budget import Priority
from aria_core.services import solana_rpc_budget
from aria_core.services.pumpfun_bonding_ws import (
    RPC_HTTP_DEFAULT,
    bonding_progress,
    price_and_reserve_from_curve,
    resolve_bonding_curves,
)
from aria_core.solana_fresh_launch_ws_exit_shadow import evaluate_exit
from aria_core.solana_pump_shadow import (
    PoolSnapshot,
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _minutes_since,
    _snapshot_with_fallback,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())
TABLE = "solana_late_bonding_shadow_log"

# 23/08, operator-directed -- retracement-gated shadow variant. Every entry
# so far bought at the local curve-progress top: 26/26 real closures carrying
# `progress_retracement_at_entry` sat under 2% retracement (44 total
# closures, only 26 have the metric -- it was added mid-flight). This variant
# requires a minimum pullback from the local high before entering, tested
# purely in shadow against the unfiltered pocket above as the control.
#
# Own TABLE (same shadow.db, same schema, different name) rather than a
# second database file: every other shadow module in this project shares one
# physical db (`shadow_db_path()`), and `pocket_entry_sweep.POCKETS` /
# `sqlite3 -readonly` tooling assume that single file. `table`/
# `archive_module` parameters are threaded through `consider_candidate`,
# `resolve_migrated_pools`, `_apply_exit_check` and `advance_exit_simulation`
# so this variant's anti-duplicate check, entry write and exit tracking never
# read or write the primary pocket's rows -- see each parameter's own
# docstring. NOT threaded through `advance_position_by_pool` (event-driven
# exit, wired to the websocket's `on_update` hook) or `reconcile_with_chain`
# (real-capital wallet reconciliation): this variant never trades real
# capital and is only ever advanced by the periodic exit-tracking loop, so
# neither path is reachable for its rows regardless.
RETRACEMENT_TABLE = "solana_late_bonding_retracement_shadow_log"
RETRACEMENT_ARCHIVE_MODULE = "solana_late_bonding_retracement"
# Provisional, arbitrary starting point -- NOT calibrated (standing doctrine:
# never present an uncalibrated threshold as final). Recalibrate once this
# variant has accumulated a real closure sample -- see
# docs/HANDOFF_PIPELINE_MOMENTUM.md (23/08 entry).
DEFAULT_MIN_RETRACEMENT = 0.05

# 23/08 -- the independent regime sensor's own table (see REGIME_MIN_MEDIAN_PEAK_PCT
# below). Deliberately its own name/set, not reusing TABLE/_ensured_db_paths: this
# sensor must never depend on -- or be confused with -- the pocket's trade log.
REGIME_CANDIDATES_TABLE = "solana_regime_candidates_log"
_ensured_regime_candidates_db_paths: set[str] = set()

# The name this pocket goes by in the shared path archive. Matches the sibling
# pockets' convention ("solana_support_bounce" etc.) -- one table, the `module`
# column keeps them apart.
ARCHIVE_MODULE = "solana_late_bonding"

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
# 23/08 -- RAISED BACK 0.50 -> 0.70. The 22/08 test (see the retained history
# below) has answered, and the answer confirms the 21/08 measurement it set
# out to overturn: the SAME liquidity floor that was supposed to protect this
# band was ALSO lowered 5500$ -> 4000$ the same day (see MIN_LIQUIDITY_USD's
# own comment) -- so the one thing the test's justification leaned on never
# actually held. Verified directly on 578 closures (22-23/08, 2 distinct
# days): raising the floor ALONE, at any level from 4000$ to 10000$, never
# recovers a positive PnL (-8% to -13% throughout, winrate stuck at 20-23%) --
# the band itself is the problem, not just its thinnest pools. Concretely
# costly: hard_stop fills in this band average -33% to -39% (vs -20% nominal,
# -23.5% once the band clears 80%), on positions that die in well under 5s --
# a slippage the module's own honest fill rule (min(stop_price, current_price))
# cannot avoid when a pool this thin drains 50-90% in one transaction.
# Operator's own stated objective the same day ("je prefere trader moins si le
# marche le permet pas que trader pour perdre") settles the remaining
# question: the test has produced its answer, continuing to pay this cost
# would not be collecting new information, only repeating a known one -- and
# the regime gate (REGIME_MIN_MEDIAN_PEAK_PCT above) is a DIFFERENT,
# complementary mechanism (market-wide, not band-specific) that does not
# substitute for this floor. Decision made under explicit operator mandate
# ("prends tes responsabilites et fais les meilleurs choix") after the data
# above was verified, not before.
#
# History retained for context -- what the 22/08 test actually tested:
# 22/08 -- LOWERED 0.70 -> 0.50, operator's explicit test. Stated plainly
# because the 21/08 measurement above says the opposite: 40-60% was the WORST
# band on 721 closures (48.9% rugs, -4.3% PnL) and that is exactly why the
# floor was raised to 0.70 in the first place.
#
# What was believed to have changed, and made the test worth running rather
# than a repeat of a known mistake:
#   * the liquidity floor went 3000$ -> 5500$, validated at +23.34 points on
#     528 vs 830 same-day closures. Most 40-60% rugs were thin pools; that
#     floor now cuts them before the band is even reached.
#   * entries are priced from the CHAIN, not from a websocket that could be
#     36s stale, so the band's own numbers were partly measured on false
#     prices.
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
# 22/08 -- RAISED to 5500$. The live data the note above was waiting for came
# in: 1609 closures, every entry metric on the row swept for a threshold that
# separates losers from winners. The pool's reserve is the ONLY one that does,
# and it does so on all three robustness checks at once:
#
#     reserve at entry     n     avg    without top2   winners  losses <=-20%
#     2000 - 3500$        180  -13.2%      -16.5%        33%        58%
#     3500 - 5450$        370   -2.0%       -9.5%        36%        51%
#     5450 - 8000$        817  +20.2%      +17.8%        55%        24%
#     8000 - 12000$       187   +8.6%       +0.2%        43%        17%
#
#   - outlier test: kept side +15.5% without its best two, against +7.4% for
#     the unfiltered baseline -- the first entry filter tried on this pocket
#     that does not collapse once its top trades are removed;
#   - temporal stability: holds on each day taken separately (20/08 kept -6.8%
#     vs cut -49.6%; 21/08 kept +18.1% vs cut -2.0%);
#   - monotonic across bands, so it is a gradient and not one lucky bucket.
#
# The cost is paid knowingly, on the operator's explicit instruction ("meme si
# sa fait perdre 2 ou 3 trade gagnant je prefere les eviter"): of the 30 best
# trades, this floor cuts 9, including the single best (+1709%, reserve 4450$).
# Losses removed still outweigh gains removed by ~2800 points.
#
# NO upper bound is set despite the 8000$+ band scoring worse: only 31 closures
# sit above 12000$, far too few to cut on. Left to accumulate.
#
# Also sets the tradable position size: a 3000$ pool tolerates roughly 30$
# before price impact eats the edge (measured via Jupiter the same day).
# 23/08 -- RAISED BACK 4000 -> 5500. The 22/08 test (retained below) has
# answered: re-verified directly on 578 closures (22-23/08, 2 distinct days)
# that raising THIS floor alone, at any level from 4000$ to 10000$, never
# recovers a positive PnL (-8% to -13% throughout) -- confirming this floor
# was never really tested in isolation, exactly as the note below already
# suspected ("the two filters overlap, so the band cannot be evaluated while
# both stand"). MIN_BONDING_PROGRESS raised back to 0.70 the same pass, for
# the same reason -- see its own comment for the fuller writeup (hard_stop
# slippage measured, operator's stability-first objective, explicit mandate
# under which this decision was made).
#
# History retained for context -- what the 22/08 test actually tested:
# 22/08 -- LOWERED 5500 -> 4000, operator's explicit test, and stated plainly
# because the data above says the opposite: this floor is the ONE entry filter
# that survived every check this session (+23.34 points measured at CONSTANT
# DAY -- 528 closures under it at -12.29% against 830 above at +11.06%), and
# the 2000-3500$ band it partly reopens scored -13.2%.
#
# The reason to run the test anyway: lowering the entry band to 50% turned out
# to change nothing, because THIS floor already cuts that zone -- a token at
# mid-curve has not accumulated 5500$ of reserve yet. Measured live right
# after the deploy: `blocked_thin_liquidity` 5, `blocked_outside_band` 3. The
# two filters overlap, so the 50% band cannot be evaluated while both stand.
#
# What was actually being tested was therefore the PAIR (band 50% + floor
# 4000$), not the floor alone.
MIN_LIQUIDITY_USD = 5500.0

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
# 21/08 -- RE-ENTRIES BANNED OUTRIGHT, no longer merely delayed. Diagnostic
# over 206 closures: the 22 re-entries returned -4.07% at 23% winrate against
# +25.12% and 68% for first entries. Above all they held ZERO x2 winners out of
# 22 -- which is what separates this from every other filter tested the same
# day, each of which cut at least one big winner. Removing them takes the
# pocket from +21.99% to +25.12%.
#
# A token that already ejected us once does not turn good again thirty minutes
# later: it showed what it was worth. Deliberately a huge value rather than a
# boolean -- the cooldown stays the mechanism, only its duration changes, so
# nothing else needs touching and reverting is a one-line edit.
REENTRY_COOLDOWN_MINUTES = 525600.0  # un an = interdiction de fait

# 20/08, RELAXED 0.60 -> 0.95. Kept non-1.0 on purpose: at 100% a single
# wallet is literally the only buyer, which is not a market at all. Everything
# below that is COLLECTED rather than judged -- `top_buyer_share_at_entry` is
# recorded, so the real wash-trading cutoff is measurable later.
#
# 22/08 -- TIGHTENED 0.95 -> 0.40. The "measurable later" arrived: 1029 closures
# above the liquidity floor, swept at every level.
#
#     ceiling   trades cut   their average   outlier-tested PnL kept
#     95% (old)      -            -                 +13.49%
#     50%           13         +13.7%               +13.48%
#     40%           24          +5.8%               +13.67%
#     30%           42          +6.6%               +13.86%
#
# The 24 trades above 40% return +5.8% against the pocket's +17.85%: cutting
# them IMPROVES the result rather than costing anything, and five of them are
# worse than -20%.
#
# What settled the level is the winners, not the average. Of the 80 closures at
# +100% or more, this ceiling keeps 79 -- and NOT ONE of the 31 closures above
# +200% is cut. The fifteen best trades all sit under 15% concentration, which
# is the real finding: a token that runs attracts many buyers, so no single one
# dominates. High concentration marks a token carried by one actor, going
# nowhere, able to leave whenever it likes.
#
# STATED HONESTLY: the PnL gain does NOT survive cross-validation (+0.26 on one
# day, -0.25 on the next). This is kept for the STRUCTURAL reason -- an actor
# holding 40%+ of the buy volume can empty the pool at will, which is true
# regardless of what 1029 trades say -- and it is taken because it costs
# nothing, not because it earns something.
MAX_TOP_BUYER_SHARE = 0.40

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
# 22/08 -- LADDER DISABLED, operator's explicit call ("branche de facon a rater
# aucun coureur je veut voir le resultat"). Kept as an empty tuple rather than
# deleted: the note above is the measured case FOR the ladder and stays
# readable, and restoring it is one edit.
#
# What made him ask: the pocket's 100 best trades were rebuilt from minute
# candles and every exit rule replayed over the SAME paths -- the first honest
# comparison possible here, since every earlier one compared two different time
# periods. The ladder came out among the five worst of 43 variants:
#
#     rule                                  outlier-tested (pess / opti)
#     trailing 13%, whole bag                    +101.7%  /  +85.9%
#     LADDER 50/100/200 + trailing 13%            +69.8%  /  +63.1%
#     hard exit at +100%                          +58.6%  /  +55.1%
#
# Selling 75% of the position before +200% costs ~32 points on tokens that
# average +200%. Both intra-minute orderings agree, which is the check that
# killed three earlier candidates.
#
# STATED PLAINLY: that sample is the 100 BEST trades, so it answers "what
# happens on a runner", never "what happens overall". On the unbiased sample
# archived today (19 paths) the ladder and the whole bag score IDENTICALLY once
# their top five are removed (+9.81% both). The gain is real on runners and
# UNPROVEN in general -- the operator chose to stop cutting them and measure.
PROFIT_LADDER: tuple[tuple[float, float], ...] = ()

# 22/08 -- WIDENED 5% -> 12%, operator's call ("branche celle qui nous permet
# davoir 150% en moyenne sur les 100 meilleur et on teste"). It is the pairing
# that tops the 360-combination sweep on the rebuilt runner paths: trailing 8%
# with a -12% floor averages +153.7% against +148.1% at -5%.
#
# THE COST IS KNOWN AND WAS ACCEPTED, not overlooked. 418 of 1029 closures
# (41%) never reach the +10% peak that arms the trailing, so they exit on this
# floor -- widening it costs them 7 points each, about 2.8 points across the
# whole population, against roughly 1.4 points gained from the ~10% that run.
# The arithmetic says NEGATIVE by about 1.4 points overall; the operator chose
# to measure it live rather than trust the estimate, which is fair since the
# estimate assumes every unarmed trade sinks to the floor and many do not.
#
# Also fragile on its own terms: this setting swings 13 points between the two
# intra-minute orderings, far more than the 6-8 points of the -5% variant. A
# result that depends that much on an unknowable ordering is a candidate, never
# a conclusion. Revert is one line.
# 23/08 -- DISABLED (None), after 320 closures said the stop was the loss.
#
# The pocket lost -2585 points over those closures (-8.05% per trade, 18%
# winrate). 236 of them exited on this stop for -3216 points. The stop was
# set at -5% and FILLED at -13.6%: -2085 points of slippage, i.e. 80% of the
# entire loss. That is not a selection problem, it is this stop.
#
# What the stop actually did, reserve-copied rows excluded (53 rows carry
# last_reserve_usd bit-identical to entry, never re-read -- they cannot say
# anything about the pool):
#     16.6% (35)  real pool drain (<-15% reserve), filled at -33.2%
#                 -> the stop did NOT protect, it arrived far too late
#     57.8% (122) moderate leak, filled at -7.8%  -> roughly did its job
#     25.6% (54)  pool INTACT (>-5% reserve), filled at -10.5%, -565 points
#                 -> ejected on noise, nothing was actually draining
#
# And the pocket already knew: see `trailing_distance_for`'s note above --
# 78% of the >+100% winners pull back past 5% on their way up, NONE past 15%.
# A -5% stop is aimed exactly at the trades that carry the pocket.
#
# TESTED AND DROPPED, so nobody re-runs them: tightening to -2/-3% (+216 pts
# but the winrate falls 20.4% -> 14.2%, and price crosses -2% and -5% inside
# the same interval anyway); tightening the trailing (negative, -40 pts at
# 10%); a +5% take-profit (looked like +1428 pts, refuted -- the replay gave
# the TP a perfect fill while this same engine slips -8.6 pts on its stop;
# corrected to +295 pts at best, -40 pts once the 10 best improvements are
# removed); reading the price more often (the effect reverses sign once
# holding time is controlled for, so it was survivorship, not cadence).
#
# WHAT THIS IS NOT: a measured result. Archived paths stop AT the real exit,
# so no replay can say what these positions would have done without a stop --
# every "no stop" replay returns the real fill by construction. This is a
# live test, and it is one change on one mechanism so it stays attributable.
# The reference point that motivates it is another pocket, not a replay:
# robinhood_pump runs with NO fixed stop and returns +33.3% over 106 closures
# (+17.8% without its five best).
#
# The floor does not disappear: HARD_STOP_PCT (-20%) and the shared
# progressive trailing bands still close a position that genuinely collapses.
# Revert is one line -- put 5.0 back.
FIXED_STOP_PCT: float | None = None

# 23/08 -- MARKET-REGIME GATE. Operator's direction, verbatim: "je préfère
# trader moins si le marché le permet pas que trader pour perdre mon objectif
# c'est de tenir un +20% minimum toute l'année, visée la stabilité et avoir la
# possibilité de capter ceux qui pump fort".
#
# It gates on the REGIME, never on the token: when the market is hot everything
# passes, so the rare runners that carry the whole result are not filtered out.
# What it refuses is a market where nothing climbs any more.
#
# Measured causally on 2303 closures over 4 days (each decision uses only the
# closures already known at that instant, no future data):
#     no gate            2303 trades  +6.60%/trade   58% of hours positive
#     median peak >=10%  1240 trades +10.38%/trade   73%
#     median peak >=15%   956 trades +13.39%/trade   77%
#     median peak >=20%   718 trades +16.21%/trade   93%   <- chosen
#     median peak >=25%   499 trades +17.13%/trade   88%
#     median peak >=30%   297 trades +18.71%/trade   89%
# 20% is not the highest average -- it is the point where the hourly standard
# deviation is LOWEST (17.8% against 19.5-22.2% everywhere else) and the share
# of positive hours is HIGHEST. That is the operator's stated criterion, so it
# is the one that decides. It holds without the top 5 trades (+12.54%) and it
# improved every single day of the sample, refusing 100% of the losing one.
#
# HONEST LIMIT: this was validated on OUR OWN closures, which is also its
# weakness -- a shut gate starves its own sensor if it reads its own trades.
# See `docs/HANDOFF_PIPELINE_MOMENTUM.md` (23/08 entries) for the full history:
# first version self-fed on this pocket's own trade table and measured
# -0.18%/trade in production against +13.10% in a simulation that (wrongly) fed
# it every screened candidate rather than only the taken ones. Rebuilt below
# with an independent, trade-blind sensor.
REGIME_WINDOW = 30

# 23/08 -- REBUILT with an INDEPENDENT sensor, after the first version measured
# -0.18%/trade in production against +13.10% in my own (wrong) simulation. The
# defect: the sensor read this pocket's OWN trade table, so a shut gate stopped
# feeding its own input and read only its 10% probes -- a biased handful. The
# probe existed only to keep that sensor alive; it is REMOVED here, not kept,
# because the new sensor no longer has that disease.
#
# THE FIX: log every candidate that clears every filter up to this gate (bonding
# band, buyer/wash-trading screen, liquidity floor) into `REGIME_CANDIDATES_TABLE`
# -- REGARDLESS of what this gate itself decides -- then track its peak forward
# via `bonding_ws_feed.get_snapshot()`, a LOCAL, FREE, IN-MEMORY read: the pool
# is already subscribed at this point (`add_pools` runs earlier in
# `consider_candidate`, before this gate is ever reached), so tracking costs
# nothing beyond what the entry decision already pays for. No REST, no rate
# limit, no reason it should ever be starved.
#
# This is what made `pretrade_rejection_log` unusable for the same job: it
# tracks refused candidates through the REST cascade at max_rows=5 every 300s
# against ~250 live rejects, so a given reject is re-read almost never.
# Measured: refused candidates tracked 90.5 min showed +1.27% mean peak while
# taken ones tracked 4.6 min showed +16.63% -- a sampling artefact, not a
# market fact. The websocket path has no such starvation: every candidate gets
# read on the SAME cadence the exit-tracking loop already runs at.
REGIME_TRACKING_WINDOW_MINUTES = 15.0

# 23/08, LOWERED 50% -> 30% same day, operator-directed -- no new measurement
# behind this specific number, unlike the 20% -> 50% move a few hours earlier
# (which had a concrete capture-gap figure to justify it). Still on the same
# "conservative hypothesis, explicit recalibration plan" footing: 30% sits
# between the two prior values, pending the new sensor's own verdict once it
# holds >=100 rows.
#
# 23/08, RAISED 20% -> 50% same day, operator-directed: the gate's threshold
# measures the market's raw peak, but the pocket never captures the raw peak
# -- TRAILING_STOP_PCT=15.0 and the liquidity_collapse exit (checked FIRST,
# see its own comment above) both sell before the top. Measured on the same
# 44 closures this pocket already had: mean REAL peak was +16.23%, mean NET
# captured was -11.74% -- i.e. a market that clears the OLD 20% bar on
# average still lands the pocket in the red once its own exit mechanics take
# their cut. 50% was not itself calibrated on the new sensor (see history
# below) -- it was the operator's explicit correction for this capture gap,
# on the same "conservative temporary hypothesis, explicit recalibration
# plan" footing as the 20% it replaced.
#
# HISTORY -- why 20% was chosen originally (23/08, same day, RE-ARMED at a
# PROVISIONAL 20%): Doctrine d'Ingestion (CLAUDE.md), a conservative
# temporary hypothesis borrowed from adjacent logic, with an explicit
# recalibration plan, beats leaving a promising idea idle for lack of fresh
# data. Left disarmed a few hours earlier the same day (44 closures/3h
# measured meanwhile: -9.3%/trade, 25% winrate, zero market filter active),
# operator flagged the live bleed directly. 20% itself was the median-peak
# threshold found by the causal replay on the OLD, self-feeding sensor (2303
# closures, monotonic across 10/15/20%, best hourly stability at 20%) --
# unreliable in ABSOLUTE terms (that replay's population was biased toward
# candidates the gate itself had let through), but a reasonable ORDER OF
# MAGNITUDE for "the market just turned cold" until the new sensor had its
# own answer.
#
# The mechanism itself makes any value here safe to set: `regime_median_peak`
# returns None below REGIME_WINDOW=30 samples, so `regime_state()` reads OPEN
# -- i.e. this line has ZERO effect until the rebuilt, independent sensor
# (REGIME_CANDIDATES_TABLE) has actually accumulated 30 real candidates, at
# this pocket's measured ~48/hour that is roughly 35-40 minutes, not the
# hours a from-scratch mechanism would need. No risk of blocking on absent
# data, cf. the circularity guard tested in `test_below_the_window_there_is_no_verdict`.
# At 50%, expect the gate to stay shut far more often than at 20% -- the
# market rarely holds a 30-candidate median peak that high, so this pocket
# may trade much less. That is the intended effect given the capture gap
# above, not a side effect to walk back without new evidence.
#
# RECALIBRATION IS MANDATORY, NOT OPTIONAL: revisit this number with the
# operator once the new sensor holds >=100 rows (Doctrine d'Ingestion's own
# n>=100 bar) -- the real distribution of SCREENED candidates (including ones
# the old sensor never saw, since it only recorded taken trades) may differ
# from 50% in either direction, and the capture-gap math above should be
# re-measured on THIS sensor's own population once it exists.
#
# 24/08, RAISED 25% -> 40%, operator-directed same day (a few hours after
# the 30->25 lowering just above -- the causal-replay case for 25% stands on
# its own population, but the operator chose to tighten past the original
# 30% bar rather than wait for a fresh replay). Kept the 25% note rather
# than deleting it: the 20-30% band's causal case (n=804, 87.8% positive) is
# still real data, just not the bar currently enforced. Solana-only change --
# robinhood_pump_shadow.py's own REGIME_MIN_MEDIAN_PEAK_PCT stays at 25.0,
# operator-directed ("les autres tu touche pas").
#
# 26/08, LOWERED 40% -> 30%, operator-directed (specs/011-solana-regime-
# recalibration): the 40% bar closed the gate ~94-96% of the time, producing
# ZERO new closures for 15h+ despite the curve tracker actively finding
# in-band candidates -- a pocket that cannot close positions cannot validate
# or invalidate its own strategy. Real open-time trade-off recomputed
# directly against solana_regime_candidates_log this session (4602 rolling
# medians, REGIME_WINDOW=30, 2 distinct days): 20%->37.6%, 25%->19.8%,
# 30%->11.5%, 35%->6.4%, 40%->4.0%. 20% (and below) is explicitly EXCLUDED --
# it is the exact value with a measured, real, negative outcome (44 closures,
# 23/08: mean REAL peak +16.23%, mean NET captured -11.74%, i.e. the capture
# gap this pocket's exit mechanics (TRAILING_STOP_PCT, liquidity-collapse
# exit) pays on every position regardless of entry threshold).
#
# 30.0 chosen over 25.0: (1) a wider safety margin from the known-dangerous
# 20% bar (10 points vs. 5) -- the capture-gap figure above is a STRUCTURAL
# property of the exit mechanics, not a function of this threshold, and
# cannot be re-measured per-candidate-threshold today (only 12 real closures
# exist in the current epoch, far below even the n>=100 provisional bar --
# any per-threshold figure computed on 12 rows would be noise, not evidence,
# stated honestly rather than faked with false precision); (2) already a
# ~2.9x open-time gain over the current 4.0% (enough to meaningfully
# accelerate closure accumulation) without erasing most of the margin gained
# by every tightening step since 23/08 in one move; (3) consistent with this
# pocket's own recalibration history (20->50->30->25->40, always incremental,
# never a jump straight back to the most permissive prior value). 25.0 stays
# banked as the natural next step if 30.0's real closures prove safe.
#
# RECALIBRATION PROTOCOL (specs/011 User Story 3, mirrors specs/010's own):
# once n>=100 closures accumulate under 30.0, run a pocket_entry_sweep-style
# pass (outlier removal top-2/top-5, day-count coverage) and specifically
# RE-MEASURE the capture-gap (mean real peak vs. mean net captured) under
# this threshold's own population -- if it has reopened toward the pre-24/08
# defect, that is a signal to tighten back, not a silent continuation. The
# +25%/trade target itself is only considered validated, and specs/011 only
# closeable, once n>=1000 SAME-EPOCH closures confirm it (this recalibration
# starts a new epoch, see solana_late_bonding_shadow_log_archive_reset_20260826).
# If the sample stalls well below either gate for an extended period, state
# so explicitly rather than silently drawing a conclusion from too few trades.
REGIME_MIN_MEDIAN_PEAK_PCT: float | None = 30.0


async def _ensure_regime_candidates_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_regime_candidates_db_paths:
        # 26/08 -- self-healing check, same rationale as _ensure_table above.
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (REGIME_CANDIDATES_TABLE,),
            )
            if await cur.fetchone():
                return
        _ensured_regime_candidates_db_paths.discard(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {REGIME_CANDIDATES_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                mint TEXT NOT NULL,
                chain TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                reserve_usd REAL,
                peak_price REAL NOT NULL,
                last_checked_at TEXT NOT NULL,
                tracking_status TEXT NOT NULL DEFAULT 'tracking'
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REGIME_CANDIDATES_TABLE}_decided "
            f"ON {REGIME_CANDIDATES_TABLE}(decided_at)"
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REGIME_CANDIDATES_TABLE}_status "
            f"ON {REGIME_CANDIDATES_TABLE}(tracking_status)"
        )
        # Hot ALTER, Doctrine d'Ingestion -- 26/08, specs/008. `bonding_progress`
        # lets a future backtest reconstruct the entry-time curve position;
        # `peak_reached_at` is only touched when a NEW high is set (see
        # `advance_regime_candidates`), so it measures the real time-to-peak
        # instead of `last_checked_at`'s tracking-window artefact.
        cur = await db.execute(f"PRAGMA table_info({REGIME_CANDIDATES_TABLE})")
        columns = {row[1] for row in await cur.fetchall()}
        for col, decl in (("bonding_progress", "REAL"), ("peak_reached_at", "TEXT")):
            if col not in columns:
                await db.execute(
                    f"ALTER TABLE {REGIME_CANDIDATES_TABLE} ADD COLUMN {col} {decl}"
                )
        await db.commit()
    _ensured_regime_candidates_db_paths.add(path)


async def record_regime_candidate(
    *, pool_address: str, mint: str, chain: str, entry_price: float,
    reserve_usd: float | None, bonding_progress: float | None = None,
    db_path: str | None = None,
) -> None:
    """Logs one candidate that cleared every filter up to the regime gate,
    independent of what the gate itself will decide. This is the whole fix:
    the sensor's input can never be gated by the gate's own verdict.

    Called from a point where entry_price is ALREADY computed (the on-chain
    read that priced the would-be entry) -- this adds one INSERT, no network
    call. Never raises into the caller: a measurement must not cost a trade."""
    if not entry_price or entry_price <= 0:
        return
    try:
        await _ensure_regime_candidates_table(db_path)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(db_path or _db_path()) as db:
            await db.execute(
                f"INSERT INTO {REGIME_CANDIDATES_TABLE} "
                f"(pool_address, mint, chain, decided_at, entry_price, reserve_usd, "
                f" peak_price, last_checked_at, tracking_status, bonding_progress, "
                f" peak_reached_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'tracking', ?, ?)",
                (pool_address, mint, chain, now, entry_price, reserve_usd,
                 entry_price, now, bonding_progress, now),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- a sensor write never breaks a decision
        logger.info("solana_late_bonding_shadow: record_regime_candidate failed (%s)", exc)


async def advance_regime_candidates(
    *, bonding_ws_feed, max_rows: int = 100, db_path: str | None = None,
) -> dict:
    """Reads each still-tracked candidate's CURRENT peak via a LOCAL websocket
    snapshot -- no network call, the pool was already subscribed when the
    candidate was screened. Stops tracking (but never deletes) a row once
    REGIME_TRACKING_WINDOW_MINUTES has elapsed since it was logged; the row
    stays in the table for the median calculation either way.

    Safe to call on an arbitrarily irregular cadence, same discipline as
    `advance_exit_simulation`: all state lives in the row itself."""
    path = db_path or _db_path()
    await _ensure_regime_candidates_table(path)
    # 27/08 diagnostic -- real incident: peak_price has been stuck exactly at
    # entry_price for 100% of candidates since 26/08T14:00 (confirmed against
    # solana_regime_candidates_log directly, day-by-day), silently forcing the
    # regime's own median to 0% and keeping the gate permanently closed
    # regardless of the real market. `stale_reasons` aggregates get_snapshot()'s
    # own `error` field (never per-row logging, funnel/log discipline) so the
    # next observation window says WHY no fresh price arrived this cycle --
    # candidate hypothesis: "late bonding" candidates are by definition close
    # to graduation, so a high share may migrate to the AMM (get_snapshot()
    # returns `error="bonding_curve_complete_use_pumpswap_ws"` PERMANENTLY
    # after that, since this feed only ever tracks the pre-graduation curve)
    # during the 15-minute tracking window -- never confirmed empirically yet.
    stats = {"checked": 0, "updated": 0, "closed": 0, "no_fresh_data": 0, "stale_reasons": {}}
    if bonding_ws_feed is None:
        return stats
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=REGIME_TRACKING_WINDOW_MINUTES)
    ).isoformat()

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {REGIME_CANDIDATES_TABLE} WHERE tracking_status = 'tracking' "
            f"ORDER BY decided_at LIMIT ?",
            (max_rows,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for row in rows:
        stats["checked"] += 1
        try:
            snap = bonding_ws_feed.get_snapshot(row["pool_address"])
        except Exception:  # noqa: BLE001 -- a feed hiccup skips this row, never breaks the pass
            continue
        price = getattr(snap, "price_usd", None)
        high = getattr(snap, "price_high_since_last_read", None)
        if price is None and high is None:
            stats["no_fresh_data"] += 1
            reason = getattr(snap, "error", None) or "unknown"
            stats["stale_reasons"][reason] = stats["stale_reasons"].get(reason, 0) + 1
        candidates = [v for v in (price, high, row["peak_price"]) if v is not None]
        if not candidates:
            continue
        new_peak = max(candidates)
        expired = row["decided_at"] < cutoff
        status = "closed" if expired else "tracking"
        now_iso = datetime.now(timezone.utc).isoformat()
        # 26/08, specs/008 -- only a STRICT new high moves `peak_reached_at`.
        # `last_checked_at` already tracks "when we last looked" (capped by
        # REGIME_TRACKING_WINDOW_MINUTES); without this distinction the two
        # columns would be identical and time-to-peak would still be
        # unmeasurable, the exact gap this column exists to close.
        peak_moved = new_peak > (row["peak_price"] or float("-inf"))
        async with aiosqlite.connect(path) as db:
            if peak_moved:
                await db.execute(
                    f"UPDATE {REGIME_CANDIDATES_TABLE} SET peak_price = ?, "
                    f"last_checked_at = ?, tracking_status = ?, peak_reached_at = ? "
                    f"WHERE id = ?",
                    (new_peak, now_iso, status, now_iso, row["id"]),
                )
            else:
                await db.execute(
                    f"UPDATE {REGIME_CANDIDATES_TABLE} SET peak_price = ?, "
                    f"last_checked_at = ?, tracking_status = ? WHERE id = ?",
                    (new_peak, now_iso, status, row["id"]),
                )
            await db.commit()
        stats["updated"] += 1
        if status == "closed":
            stats["closed"] += 1
    return stats


def regime_median_peak(peaks: list[float]) -> float | None:
    """Median peak of the candidates handed in, or None below the window size.

    Pure so the rule can be tested without a database. Below REGIME_WINDOW
    samples it returns None, and the caller treats that as OPEN: a pocket that
    has just started has no evidence the market is bad, and refusing on absent
    data would make a fresh epoch unable to ever collect any.
    """
    usable = [p for p in peaks if p is not None]
    if len(usable) < REGIME_WINDOW:
        return None
    recent = sorted(usable[-REGIME_WINDOW:])
    mid = len(recent) // 2
    if len(recent) % 2:
        return recent[mid]
    return (recent[mid - 1] + recent[mid]) / 2.0


async def regime_state(*, db_path: str | None = None) -> dict:
    """Reads the regime from candidates the pocket SCREENED, not from trades it
    TOOK -- the fix for the 23/08 incident. Ordered by `decided_at`: what is
    knowable at the instant of a live decision is what has already been seen,
    regardless of whether it was traded."""
    await _ensure_regime_candidates_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        cur = await db.execute(
            f"SELECT (peak_price / entry_price - 1.0) * 100.0 AS peak "
            f"FROM {REGIME_CANDIDATES_TABLE} "
            f"WHERE entry_price IS NOT NULL AND entry_price > 0 "
            f"ORDER BY decided_at DESC LIMIT ?",
            (REGIME_WINDOW,),
        )
        rows = [r[0] for r in await cur.fetchall()]
    # fetched newest-first, the pure helper expects oldest-first
    peaks = list(reversed(rows))
    median = regime_median_peak(peaks)
    disarmed = REGIME_MIN_MEDIAN_PEAK_PCT is None
    return {
        "median_peak_pct": median,
        "samples": len(peaks),
        # Disarmed reads as OPEN, never as shut: a threshold of None means "no
        # opinion on the regime", and a mechanism with no opinion must not be
        # the thing that stops the pocket trading.
        "open": disarmed or median is None or median >= REGIME_MIN_MEDIAN_PEAK_PCT,
        "threshold_pct": REGIME_MIN_MEDIAN_PEAK_PCT,
        "disarmed": disarmed,
    }

# 22/08 -- BACK TO 15%, the shared default, after three hours of production
# disproved the 8% bet. Kept as an explicit constant rather than deleted so the
# next person sees WHY it is not 8.
#
# The bet was: 8% was the best of 360 combinations replayed over the 100 best
# trades, winning under both intra-minute orderings. It was written down at the
# time as a deliberate bet on half the problem, not a validated setting,
# because the sample WAS the winners -- a tight trailing shines on tokens that
# climb with shallow pullbacks.
#
# What production said, hour by hour, on the day it shipped:
#   16h  peak median +37.7%   peaks >+50%: 38.2%   PnL +15.69%
#   18h  peak median +11.8%   peaks >+50%: 20.6%   PnL +16.98%
#   19h  peak median  +2.3%   peaks >+50%:  3.7%   PnL  -5.43%   <- 8% live
#   21h  peak median  +6.6%   peaks >+50%:  0.0%   PnL  -8.76%
#
# The PEAKS collapsed, not just the PnL. A peak is the highest price seen
# BEFORE our exit, so a collapsing peak means we stop observing the rise at
# all. The market moved the other way that day (22/08 peak median +16.3%
# against +13.1% on 21/08), so this was the setting, not conditions.
#
# The arithmetic, which needed no replay: the trailing arms at +10% and sits
# `distance` below the peak. At 8%, a +12% peak puts the stop at +3.0% -- any
# breath ejects the position. At 15%, the same peak puts it at -4.8%, BELOW
# the fixed stop, so the position simply keeps running until a real move.
# 8% does not follow a trend, it exits it.
TRAILING_STOP_PCT = 15.0

# 23/08 -- this comment used to say the trailing path was unreachable
# because FIXED_STOP_PCT took precedence; that stopped being true the moment
# FIXED_STOP_PCT was disabled (see its own comment above) -- the trailing is
# live again. `hard_stopped` (in the shared `evaluate_exit`) only fires while
# the peak has never armed the trailing, so the two are not really
# competing: hard_stop catches positions that never moved, trailing catches
# the ones that did. FAST-DISCOVERY still runs the trailing at a flat 15%,
# a live A/B against this pocket's banded distance.
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
CONFIG_EPOCH = "2026-08-21T23:37:10+00:00"

# How far back reconciliation looks. Long enough to catch a swap that took
# several slots to land, short enough that an old row can never resurrect.
RECONCILE_WINDOW_MINUTES = 30.0

# 20/08 -- raised with the widened band. The REAL constraint is the exit
# loop: more open positions means each one is checked less often, which is
# exactly what caused the late liquidity_collapse catches fixed earlier today
# (first check landing 32-116s after entry despite a 10s cadence). The exit
# sweep's own `limit` is raised in step below so widening collection cannot
# quietly re-create that failure.
MAX_CONCURRENT_TRACKED = 60
# Keyed by (path, table): the retracement variant shares the same physical
# db_path as the primary pocket but a different table, so a path-only cache
# would mark the second table "ensured" the moment the first one is (and its
# CREATE TABLE would then never run).
_ensured_db_paths: set[tuple[str, str]] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_table(db_path: str | None = None, *, table: str = TABLE) -> None:
    path = db_path or _db_path()
    key = (path, table)
    if key in _ensured_db_paths:
        # 26/08 -- self-healing check, real incident: this pocket's own
        # 2026-08-26 regime-recalibration epoch-reset (RENAME TABLE
        # solana_late_bonding_shadow_log -> ..._archive_reset_20260826) ran
        # against this LIVE long-running process moments AFTER a restart
        # rather than before it -- the fresh cache had already marked the
        # table "ensured" from the brief window before the rename, so every
        # subsequent consider_candidate/exit-tracking/price-sweep call failed
        # with "no such table" for 30+ minutes until manually recreated. One
        # cheap indexed lookup on the hot path makes this self-heal instead
        # of depending on getting restart-vs-rename ordering right forever.
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            if await cur.fetchone():
                return
        _ensured_db_paths.discard(key)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
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
                sell_pressure_at_entry REAL,
                sell_pressure_slope_at_entry REAL,
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
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_open ON {table}(exit_reason, last_checked_at)")
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_pool ON {table}(pool_address)")
        # Hot idempotent ALTER, same pattern as everywhere else here -- start
        # accumulating on the live table rather than waiting for a rebuild.
        await ensure_wal(db)
        cur = await db.execute(f"PRAGMA table_info({table})")
        existing = {r[1] for r in await cur.fetchall()}
        if "has_paid_profile" not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN has_paid_profile INTEGER")
        if "exit_detail" not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN exit_detail TEXT")
        # 21/08 -- founding-cohort columns, hot-ALTERed like the rest so the
        # live table starts accumulating immediately.
        # Concurrency-safe: this table is opened by BOTH the API container and
        # the standalone shadow process, so read-then-ALTER is a genuine race.
        await db_migrations.ensure_columns(db, table, (
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
            ("sell_pressure_at_entry", "REAL"),
            ("sell_pressure_slope_at_entry", "REAL"),
            ("sellable_at_entry", "INTEGER"),
            ("roundtrip_loss_pct_at_entry", "REAL"),
            ("reinforce_price", "REAL"),
            ("reinforce_at", "TEXT"),
            ("reinforced_final_multiplier", "REAL"),
            # Makes distinct_buyers_at_entry interpretable -- see the metric's
            # own comment. A count without its observation window measures the
            # listening strategy, not the token.
            ("observation_seconds_at_entry", "REAL"),
            ("seconds_tracked_at_entry", "REAL"),
            ("top_buyer_address_at_entry", "TEXT"),
            # 22/08 -- WHICH source priced the entry. The pocket recorded
            # `exit_price_source` but never its counterpart, so an entry priced
            # by the REST fallback and a follow-up priced by the curve looked
            # identical in the table -- and their disagreement looked like a
            # price move. Real case (id 1772): entry at a 5941$ implied mcap,
            # first tracking point at 13127$ 1.4s later, logged as a +121% peak
            # that DexScreener's own 1-second chart shows never happened. The
            # subscription had just 429'd, so the entry fell through to REST
            # while the follow-up came from the websocket. Until this column
            # has data, a "peak" on this pocket cannot be told apart from a
            # change of source.
            ("entry_price_source", "TEXT"),
            ("progress_retracement_at_entry", "REAL"),
            # 22/08 -- stop level and fill, to the decimal, as NUMBERS.
            # Both lived only inside `exit_detail` as prose rounded to one
            # decimal, which cannot be averaged, bucketed or swept.
            ("stop_level_pct", "REAL"),
            ("fill_level_pct", "REAL"),
            ("trades_per_second_at_entry", "REAL"),
            ("distinct_sellers_at_entry", "INTEGER"),
            ("trades_total_at_entry", "INTEGER"),
            ("buy_sol_volume_at_entry", "REAL"),
            # Transactions seen at entry. Meaningful only RELATIVE to size --
            # divide by reserve_usd for an intensity comparable across tokens.
            ("trade_count_at_entry", "INTEGER"),
            ("buy_count_at_entry", "INTEGER"),
            # Wallets both buying and selling -- the clean wash-trading marker.
            ("round_trip_wallets_at_entry", "INTEGER"),
            ("round_trip_share_at_entry", "REAL"),
            # Highest profit rung already taken. Added 2026.08.21: the exit rule
            # read it from day one but nothing ever wrote it and the column did
            # not exist, so every rung re-fired on each ~10s evaluation and sold
            # the whole position at the first rung price.
            ("ladder_done", "REAL"),
            # 22/08 -- the buy's real transaction hash, and the ONLY reliable
            # marker that a position was actually purchased. It used to be
            # inferred from `exit_detail` containing "real_tx", which is
            # written at the SELL: an open real position therefore carried no
            # marker at all, and the closure UPDATE overwrites exit_detail
            # anyway. That made a real position and a simulated one
            # indistinguishable while open -- the exact condition under which
            # a shadow row was retried for sale several times per second.
            ("buy_tx", "TEXT"),
        ))
        await db.commit()
    _ensured_db_paths.add(key)


async def _in_reentry_cooldown(db, token_address: str, chain: str, *, table: str = TABLE) -> bool:
    """Ce token nous a-t-il ejectes recemment ?

    Porte sur le TOKEN et non sur le pool : un meme token peut etre vu via
    plusieurs adresses au cours de sa vie (courbe puis AMM apres graduation),
    et la carence doit suivre le sous-jacent, pas l'enveloppe."""
    cur = await db.execute(
        f"SELECT 1 FROM {table} WHERE token_address = ? AND chain = ? "
        f"AND exit_reason IS NOT NULL AND last_checked_at >= ? LIMIT 1",
        (token_address, chain,
         (datetime.now(timezone.utc) - timedelta(minutes=REENTRY_COOLDOWN_MINUTES)).isoformat()),
    )
    return await cur.fetchone() is not None


async def _has_open_signal(db, pool_address: str, chain: str, *, table: str = TABLE) -> bool:
    cur = await db.execute(
        f"SELECT 1 FROM {table} WHERE pool_address = ? AND chain = ? AND exit_reason IS NULL LIMIT 1",
        (pool_address, chain),
    )
    return await cur.fetchone() is not None


async def screen_candidate(
    mint: str, pool_address: str, *, trade_stream, curve: dict | None,
    token_decimals: int | None = None, curve_tracker=None,
) -> tuple[bool, str, dict]:
    """``(accepted, reason, metrics)``. Pure: no DB, no network -- everything
    it needs is already in hand, which is what lets the whole screen run
    without adding a single call to the entry path."""
    progress = bonding_progress(curve, token_decimals=token_decimals)
    flow = trade_stream.get_flow(mint) if trade_stream is not None else None
    metrics = {
        "bonding_progress": progress,
        "distinct_buyers": flow.distinct_buyers if flow else None,
        # 21/08 -- HOW LONG this token has been observed when the count above
        # was taken. Without it `distinct_buyers` is uninterpretable: it counts
        # buyers seen SINCE THE STREAM SUBSCRIBED, not since the token existed.
        # Proof from the same day: shortening the listening window from
        # "program-wide, always on" to "pre-armed ~26s" divided the average
        # from 196 to 34 with no market change whatsoever. Any analysis mixing
        # the two regimes measures its own methodology, which is exactly what
        # made a buyers-vs-outcome study read as pure noise.
        # 21/08, operator insight: raw counts are not comparable between tokens
        # of different sizes. A $257K token with 220 transactions is MORE active
        # than a $3M one with 1906 -- 0.86 vs 0.64 per $1k. Storing the counts
        # next to the liquidity already recorded makes that ratio computable
        # afterwards, at zero API cost since the stream holds them in memory.
        # 21/08 -- wash-trading signature. A wallet that both buys and sells the
        # same token inflates volume and transaction count without bringing any
        # demand. The diagnostic that same evening could NOT settle whether wash
        # trading explained the collapses: the transactions/buyers ratio showed
        # no gradient and capped at 5.3, where real wash trading shows 20-100.
        # This is the missing measurement -- collected only, never a filter,
        # until it has proven itself on data it did not influence.
        "round_trip_wallets": (
            trade_stream.round_trip_wallets(mint) if trade_stream is not None
            and hasattr(trade_stream, "round_trip_wallets") else None
        ),
        "round_trip_share": (
            trade_stream.round_trip_share(mint) if trade_stream is not None
            and hasattr(trade_stream, "round_trip_share") else None
        ),
        "trade_count": (
            (flow.buy_count + flow.sell_count)
            if flow is not None and getattr(flow, "buy_count", None) is not None else None
        ),
        "buy_count": getattr(flow, "buy_count", None) if flow is not None else None,
        # 26/08 -- the sell-side twin of buy_count, same source (`flow`),
        # never read before: `pretrade_rejection_log.sells_observed` existed
        # in the schema but was always NULL for this pocket (specs/008).
        "sell_count": getattr(flow, "sell_count", None) if flow is not None else None,
        "observation_seconds": (
            (time.time() - getattr(flow, "first_trade_at", None))
            if (flow is not None and getattr(flow, "first_trade_at", None)) else None
        ),
        # 22/08 -- HOW LONG THIS MINT TOOK TO QUALIFY, the strongest signal
        # found on this pocket so far: closures qualifying in under 60s
        # returned +29.06% after the outlier test, against +10.53% for those
        # taking over 180s (282 archived paths).
        #
        # Read from the CURVE TRACKER, not from the trade stream. The stream
        # version above is missing on a quarter of entries, and the ratio built
        # on it (`sol_velocity`) inverted sign depending on how long we had
        # been watching -- the duration WAS the signal and dividing by it threw
        # it away. Every candidate passes through the tracker, so this one is
        # measurable on all of them.
        "seconds_tracked": (
            curve_tracker.seconds_tracked(mint) if curve_tracker is not None else None
        ),
        # 22/08, operator's hypothesis -- how far the curve has fallen BACK
        # from its own high when we buy. On a bonding curve the price is a
        # deterministic function of progress, so this is the pullback from the
        # local top, on the one axis wash trading cannot fake. 0.0 = buying at
        # the top, 0.25 = a quarter of the ground given back.
        #
        # Recorded rather than acted on: the idea is that entering at a local
        # top is what gets the trailing stop hit immediately, which the 8%
        # episode made concrete. Nothing filters on it until the data says it
        # separates winners from losers.
        "progress_retracement": (
            curve_tracker.progress_retracement_of(mint)
            if curve_tracker is not None
            and hasattr(curve_tracker, "progress_retracement_of") else None
        ),
        "top_buyer_share": flow.top_buyer_share if flow else None,
        # WHO the concentration belongs to, not just how much (22/08).
        "top_buyer_address": getattr(flow, "top_buyer_address", None) if flow else None,
        # 22/08, operator's idea ("meme pas un volume minimum ? ou un ratio
        # volume/nombre de transaction sur 1 minutes ?"). The pocket had NO
        # volume column at all -- one of the plainest measures of real interest
        # in a token, never captured.
        #
        # What suggested it: the derived trades-per-second rate separates on the
        # 18% of closures that happen to carry it (+19.6% / +23.9% / +26.7% by
        # tercile, monotonic). Same direction as time-to-qualify. Worth having
        # at full coverage rather than on a fifth of the rows.
        #
        # Two sources on purpose, because their coverage differs: the trade
        # stream knows the SOL that actually changed hands, the bonding snapshot
        # is present on every candidate. Neither is derived from the other.
        "buy_sol_volume": getattr(flow, "buy_sol_volume", None) if flow else None,
        # 22/08, operator: "meme le nombre de unique wallet ?". The flow counts
        # distinct SELLERS and the pocket never stored it -- so we knew how many
        # wallets were arriving and never how many were leaving. `sell_pressure`
        # is derived from it but is a RATIO: a token with 3 buyers and 3 sellers
        # and one with 300 of each read identically, and they are not the same
        # token. Both raw counts are kept.
        "distinct_sellers": getattr(flow, "distinct_sellers", None) if flow else None,
        # `trades_total`/`trades_per_second` are NOT set here: the screen runs
        # BEFORE the position is priced, so no snapshot exists yet. They are
        # filled in right after pricing -- see `_add_snapshot_metrics`.
        "buyer_acceleration": (
            trade_stream.buyer_acceleration(mint) if trade_stream is not None else None
        ),
        # 21/08 -- how FAST the curve is advancing, not just where it is. See
        # `TokenTradeFlow.sol_velocity`: graduation is the only factor that
        # separates this pocket's real winners, and speed is the one signal
        # plausibly predicting it that we were not recording. COLLECTED ONLY.
        "sol_velocity": flow.sol_velocity if flow else None,
        # 21/08 -- PRESSION VENDEUSE, quatrieme signal disponible et jamais lu
        # de la journee. Le flux la calcule deja en memoire et la poche temoin
        # s'en sert; celle-ci ne lisait que l'accélération des ACHETEURS.
        #
        # Cas fondateur, capture par l'operateur : une position morte a -28.6%
        # en DEUX SECONDES, un seul point de trajectoire, plus bas vu -42.9%.
        # Aucune vitesse d'observation n'aurait pu l'intercepter -- il n'y a
        # eu aucun prix intermediaire. La seule prevention possible etait de
        # ne pas entrer, et le signal qui l'aurait dit etait deja la.
        #
        # `sell_pressure` = vendeurs distincts / acheteurs distincts. Au-dessus
        # de 1.0, plus de monde sort qu'il n'entre. COLLECTE SEULEMENT : cela
        # ne devient un filtre que si les donnees montrent qu'il separe
        # vraiment, et tout filtre d'entree risque de couper les rares
        # gagnants qui portent tout.
        "sell_pressure": flow.sell_pressure if flow else None,
        "sell_pressure_slope": (
            trade_stream.sell_pressure_slope(mint) if trade_stream is not None else None
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



def _add_snapshot_metrics(metrics: dict, snapshot) -> None:
    """Fills the metrics that only exist once the position has been PRICED.

    22/08 -- the screen runs before pricing, so anything read from the snapshot
    has to be added afterwards. Written as its own step because the alternative
    (passing a snapshot that does not exist yet) is exactly how the
    time-to-qualify metric ended up at 21% coverage earlier today: instrumented
    against a source that had already let go of the mint."""
    if snapshot is None:
        return
    total = getattr(snapshot, "trades_total", None)
    seconds = getattr(snapshot, "seconds_tracked", None)
    metrics["trades_total"] = total
    metrics["trades_per_second"] = (total / seconds) if (total and seconds) else None


def _unsubscribe_rejected_candidate(bonding_ws_feed, pool_address: str) -> None:
    """28/08 -- a candidate subscribed via ``bonding_ws_feed.add_pools()``
    that is then rejected before ever becoming an open position must be
    unsubscribed too, same doctrine as ``advance_position_by_pool``'s own
    26/08 fix for a closed position. Best-effort: an unsubscribe failure
    must never block the rejection itself."""
    if bonding_ws_feed is None:
        return
    try:
        bonding_ws_feed.remove_pools([pool_address])
    except Exception:  # noqa: BLE001 -- unsubscribe is an enhancement, not the write
        logger.info("solana_late_bonding_shadow: remove_pools failed for %s", pool_address)


async def consider_candidate(
    mint: str, pool_address: str, *, chain: str = "solana", trade_stream=None,
    http_client: httpx.AsyncClient | None = None, geckoterminal_client=None,
    bonding_ws_feed=None,
    resolve_curves_fn=None, snapshot_fn=None, db_path: str | None = None,
    execute_fn=None, curve_tracker=None,
    table: str = TABLE, archive_module: str = ARCHIVE_MODULE,
    min_retracement: float | None = None,
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
    its behaviour before this parameter existed.

    ``table``/``archive_module`` -- 23/08, the seam for the retracement-gated
    shadow variant (see ``RETRACEMENT_TABLE``). Default is the primary
    pocket's own table/archive name, so a caller not passing these gets
    behaviour byte-identical to before they existed. NEVER combine a non-default
    ``table`` with a real ``execute_fn``: the retracement variant this seam
    exists for is pure shadow, always ``execute_fn=None``.

    ``min_retracement`` -- 23/08, operator-directed ("ok pars sur le
    retracement en shadow"). When set, a candidate must ALSO show at least
    this much pullback from its local curve-progress high
    (``metrics["progress_retracement"]``, see ``screen_candidate``) to be
    accepted. Fail-CLOSED: an unknown retracement (``None``) never qualifies
    while this is active -- a missing measurement is never treated as a pass.
    Checked right after ``screen_candidate`` and before the position is
    priced, so a rejection here costs no RPC/snapshot call. Default ``None``
    disables the check entirely, byte-identical to before this parameter
    existed."""
    try:
        await _ensure_table(db_path, table=table)
        async with aiosqlite.connect(db_path or _db_path()) as db:
            if await _has_open_signal(db, pool_address, chain, table=table):
                return None
            if await _in_reentry_cooldown(db, mint, chain, table=table):
                return None
            # 28/08 -- shadow-wide resource cap, see shadow_pocket_cap.py's
            # module docstring. Never a trading gate: purely a ceiling on how
            # many concurrent open positions this pocket may hold. Checked
            # here, before the RPC curve resolver below, so a candidate
            # rejected by the cap costs no Chainstack budget either (same
            # funnel doctrine as every other cheap-filter-first check in this
            # function). Scoped to the SAME ``table`` the caller passed, so
            # the retracement-gated variant (its own table) gets its own
            # independent cap, never shared with the primary pocket's.
            if await shadow_pocket_cap.at_capacity(db, table, open_clause="exit_reason IS NULL"):
                return None

        resolver = resolve_curves_fn or resolve_bonding_curves
        client = http_client or httpx.AsyncClient(timeout=15.0)
        owns_client = http_client is None
        try:
            # 24/08 fix -- this call and pumpfun_curve_tracker's own batched
            # polling both hit Chainstack's real 5 req/s Solana ceiling with
            # no coordination between them: the tracker had its own throttle,
            # this call had none. Found live the moment the tracker's polling
            # resumed (24/08 shadow_pause fix) -- every candidate started
            # failing with 429. Routed through the shared budget so the two
            # callers share the one real limit instead of adding past it.
            await solana_rpc_budget.acquire(Priority.NORMAL, caller="solana_late_bonding_shadow")
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
            mint, pool_address, trade_stream=trade_stream, curve=curve,
            token_decimals=decimals, curve_tracker=curve_tracker,
        )

        # 23/08 -- the retracement gate, checked BEFORE pricing so a rejection
        # here costs no RPC/snapshot call (funnel doctrine: cheapest filter
        # first). Fail-CLOSED: an unmeasured retracement never qualifies while
        # `min_retracement` is active, same discipline as every other gate in
        # this function -- a missing measurement is a block, never a pass.
        if accepted and min_retracement is not None:
            retracement = metrics.get("progress_retracement")
            if retracement is None or retracement < min_retracement:
                accepted, reason = (
                    False, f"blocked_insufficient_retracement: retracement={retracement}",
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
            # 22/08 -- price the entry from the curve WE JUST READ ON-CHAIN,
            # not from whatever the websocket last happened to hear. The
            # resolver above already fetched and decoded this exact account to
            # check the bonding progress, so the reserves that define the price
            # are in hand at this point and cost nothing more. An account read
            # cannot be stale: it IS the state at that slot.
            #
            # This is what the pocket got wrong. Position 1772 was recorded at
            # a 5941$ implied mcap while the chain said 13260$ at that same
            # second; the genuine quote arriving 1.4s later was then logged as
            # a +121% PEAK and its disappearance as a collapse, so the trailing
            # stop closed a position that was still climbing (the token went on
            # to 22K, +66% above the true entry). Neither number existed. Both
            # were the same instant priced twice, by two sources that disagreed.
            #
            # The websocket path stays exactly as it was for TRACKING an open
            # position, where its last-known-state semantics are correct.
            onchain_price, onchain_reserve = (
                price_and_reserve_from_curve(
                    curve, token_decimals=decimals,
                    sol_usd=getattr(bonding_ws_feed, "sol_usd", None),
                )
                if curve and decimals is not None else (None, None)
            )
            snapshot = await _price_position(
                {"pool_address": pool_address, "token_address": mint},
                chain=chain, bonding_ws_feed=bonding_ws_feed, snapshot_fn=snapshot_fn,
                geckoterminal_client=geckoterminal_client,
            )
            if onchain_price:
                # Keep the snapshot's other fields (dex_id, availability) and
                # override only what the chain answers authoritatively. Frozen
                # dataclasses need `replace`; anything else is copied and
                # mutated, so a caller passing a plain object is not silently
                # ignored -- which is exactly how the first version of this
                # left the entry priced by the feed while the test claimed
                # otherwise.
                overrides = {
                    "price_usd": onchain_price,
                    "reserve_usd": onchain_reserve or getattr(snapshot, "reserve_usd", None),
                }
                # `stale` only exists on the websocket snapshot; PoolSnapshot
                # (the REST/on-chain shape) has no such field, and passing it
                # to `replace` raises. 22/08: that raise was swallowed by
                # consider_candidate's own guard, so 28 candidates in 20
                # minutes were dropped in silence -- the pocket looked simply
                # idle rather than broken.
                if any(f.name == "stale" for f in dataclasses.fields(snapshot)) \
                        if is_dataclass(snapshot) and not isinstance(snapshot, type) \
                        else hasattr(snapshot, "stale"):
                    overrides["stale"] = False
                if is_dataclass(snapshot) and not isinstance(snapshot, type):
                    snapshot = dataclasses_replace(snapshot, **overrides)
                else:
                    snapshot = copy.copy(snapshot)
                    for field_name, value in overrides.items():
                        try:
                            setattr(snapshot, field_name, value)
                        except Exception:  # noqa: BLE001 -- immutable stand-in, keep what we have
                            pass
            if not snapshot.available or snapshot.price_usd is None:
                accepted, reason = False, "blocked_no_price"
            elif getattr(snapshot, "stale", False):
                # 22/08 -- a price older than the feed's staleness threshold is
                # fine for WATCHING a position (no new trade genuinely means
                # the price has not moved, cf. pumpfun_bonding_ws's 19/08
                # comment) but never for DECIDING a buy. A pool subscribed
                # seconds ago has no recent trades in memory yet, so its "last
                # known state" can predate us by more than the window.
                #
                # Real case, id 1772: entry recorded at a 5941$ implied mcap
                # while DexScreener's 1-second chart shows 13260$ at that exact
                # second -- a ~36s old price. The real quote arriving 1.4s later
                # was then logged as a +121% PEAK, and the return to the stale
                # value as a collapse, so the trailing stop sold a position that
                # was in fact still climbing (the token went on to 22K, +66%
                # above the true entry). Both the peak and the loss were
                # artefacts of comparing two different prices of the same
                # instant.
                accepted, reason = False, "blocked_stale_price"
            elif (snapshot.reserve_usd or 0) < MIN_LIQUIDITY_USD:
                # Checked here rather than in `screen_candidate`: the reserve
                # is only known once the position has been priced. A pool this
                # thin is not a weak candidate, it is an UNEXECUTABLE one --
                # entries were found on pools holding two dollars, harmless
                # while simulating and impossible in reality.
                accepted, reason = False, f"blocked_thin_liquidity: reserve={snapshot.reserve_usd or 0:.0f}"
            else:
                # 23/08 -- every candidate reaching this point cleared the
                # bonding band, buyer/wash-trading screen, AND the liquidity
                # floor: exactly the population the regime sensor needs, and
                # logged UNCONDITIONALLY before the verdict below -- a gate
                # must never be the thing that decides its own sensor's input,
                # which is exactly the bug that made the first version of this
                # gate measure negative in production (see REGIME_WINDOW).
                await record_regime_candidate(
                    pool_address=pool_address, mint=mint, chain=chain,
                    entry_price=snapshot.price_usd, reserve_usd=snapshot.reserve_usd,
                    bonding_progress=metrics.get("bonding_progress"),
                    db_path=db_path,
                )
                if not (await regime_state(db_path=db_path))["open"]:
                    # Last link on purpose: every cheaper filter has already had
                    # its say, so this only ever refuses candidates that were
                    # otherwise GOOD -- it is the market being refused, not the
                    # token.
                    accepted, reason = False, "blocked_regime_closed"
                else:
                    # 25/08 -- exogenous confirmation, see
                    # skills/chain_liquidity_regime.py's module docstring. The
                    # gate above is ENDOGENOUS and blind right after a reset,
                    # this one reads DefiLlama's real chain TVL/volume instead.
                    # Fail-open on anything but a CONFIRMED toxic spike.
                    chain_regime = await chain_liquidity_regime.latest_regime(chain)
                    if chain_regime and chain_regime["regime"] == chain_liquidity_regime.REGIME_TOXIC_SPIKE:
                        accepted, reason = False, f"blocked_regime_defillama: {chain_regime['detail']}"
                    elif shadow_discovery_only.is_discovery_only():
                        # 25/08 -- last link on purpose, same reasoning as the
                        # regime gates above. See shadow_discovery_only.py's
                        # module docstring -- discovery/rejection-logging/
                        # regime-candidate tracking above are all unaffected.
                        accepted, reason = False, "blocked_discovery_only"

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
                sell_pressure_slope=metrics.get("sell_pressure_slope"),
                buys_observed=metrics.get("buy_count"),
                sells_observed=metrics.get("sell_count"),
                bonding_progress=metrics.get("bonding_progress"),
            ),
            db_path=db_path,
        )
        if not accepted or snapshot is None:
            # 28/08 -- same leak class fixed 26/08 for a closed POSITION
            # (see advance_position_by_pool's own comment), but that fix only
            # covers a candidate that made it all the way to an open row.
            # add_pools() above ran for every candidate reaching this branch,
            # including one rejected here (blocked_regime_closed/
            # blocked_regime_defillama/blocked_discovery_only) -- without an
            # unsubscribe on THIS path too, it stays subscribed on
            # pumpfun_bonding_ws.py's WS feed forever, exactly the "pool
            # count climbs and never plateaus" symptom, just one step
            # earlier in the funnel. Confirmed live 28/08: 335 accounts
            # tracked while 0 positions were open anywhere in this pocket.
            _unsubscribe_rejected_candidate(bonding_ws_feed, pool_address)
            return None

        _add_snapshot_metrics(metrics, snapshot)

        if await creator_reputation.is_factory(getattr(account, "creator", None), db_path=db_path):
            _unsubscribe_rejected_candidate(bonding_ws_feed, pool_address)
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
                _unsubscribe_rejected_candidate(bonding_ws_feed, pool_address)
                return None
            if not filled or not filled.get("entry_price"):
                _unsubscribe_rejected_candidate(bonding_ws_feed, pool_address)
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
                INSERT INTO {table}
                    (pool_address, token_address, chain, detected_at, entry_price, reserve_usd,
                     bonding_progress_at_entry, distinct_buyers_at_entry, top_buyer_share_at_entry,
                     buyer_acceleration_at_entry, peak_price, realistic_entry_price, buy_tx,
                     founding_tracked_at_entry, founding_exited_at_entry,
                     founding_exit_ratio_at_entry, founding_bundle_size_at_entry,
                     creator_address, creator_sold_at_entry, sol_velocity_at_entry,
                     sell_pressure_at_entry, sell_pressure_slope_at_entry,
                     observation_seconds_at_entry, seconds_tracked_at_entry,
                     entry_price_source, progress_retracement_at_entry,
                     top_buyer_address_at_entry, buy_sol_volume_at_entry,
                     trades_total_at_entry, trades_per_second_at_entry,
                     distinct_sellers_at_entry,
                     trade_count_at_entry, buy_count_at_entry,
                     round_trip_wallets_at_entry, round_trip_share_at_entry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pool_address, mint, chain, datetime.now(timezone.utc).isoformat(),
                    entry_price, snapshot.reserve_usd, metrics.get("bonding_progress"),
                    metrics.get("distinct_buyers"), metrics.get("top_buyer_share"),
                    metrics.get("buyer_acceleration"), entry_price, realistic, tx_hash,
                    founding.get("tracked"), founding.get("exited"),
                    founding.get("exit_ratio"), founding.get("bundle_size"),
                    creator, creator_sold, metrics.get("sol_velocity"),
                    metrics.get("sell_pressure"), metrics.get("sell_pressure_slope"),
                    metrics.get("observation_seconds"),
                    metrics.get("seconds_tracked"),
                    # Which source actually priced this entry -- see the
                    # column's own comment for the +121% that was really a
                    # switch from REST to the curve.
                    getattr(snapshot, "dex_id", None),
                    metrics.get("progress_retracement"),
                    metrics.get("top_buyer_address"),
                    metrics.get("buy_sol_volume"),
                    metrics.get("trades_total"),
                    metrics.get("trades_per_second"),
                    metrics.get("distinct_sellers"),
                    metrics.get("trade_count"), metrics.get("buy_count"),
                    metrics.get("round_trip_wallets"), metrics.get("round_trip_share"),
                ),
            )
            await db.commit()
            # 22/08 -- the BEFORE half of the standing convention (18/08):
            # "je veut les bougies avant et apres le point dachat a chaque
            # futur shadow". This pocket honoured only the AFTER half -- 3859
            # archived rows, zero before -- so no question about the ENTRY
            # POINT could be answered: whether we buy at a local top or on a
            # pullback was simply not recorded anywhere.
            #
            # Reconstructed from the tracker's progress history rather than
            # from candles we do not have: on a bonding curve the price IS
            # `virtual_quote / virtual_token`, so progress and price carry the
            # same information, and the tracker has been watching this mint
            # since it appeared on the creation feed.
            asyncio.create_task(_archive_pre_entry_path(
                cur.lastrowid, mint, pool_address, chain=chain,
                curve_tracker=curve_tracker, entry_price=entry_price,
                entry_progress=metrics.get("bonding_progress"),
                archive_module=archive_module,
            ))
            asyncio.create_task(_enrich_paid_profile(
                cur.lastrowid, mint, chain=chain, db_path=db_path, table=table,
            ))
            asyncio.create_task(_enrich_exit_route(
                cur.lastrowid, mint, db_path=db_path, table=table,
            ))
            logger.info(
                "solana_late_bonding_shadow: ENTRY %s progress=%.2f buyers=%s",
                pool_address, metrics.get("bonding_progress") or -1, metrics.get("distinct_buyers"),
            )
            return cur.lastrowid
    except Exception as exc:  # noqa: BLE001 -- a shadow pocket never breaks its caller
        if "429" in str(exc):
            # 22/08: 472 candidates were rejected here on a rate limit while a
            # working second provider sat unused, because this path never told
            # the shared resolver the primary was exhausted. Discovery is where
            # an exhausted provider hurts most -- it stops the pocket entirely.
            from aria_core.services import pumpswap_ws

            pumpswap_ws.note_rpc_http_exhausted()
        logger.info("solana_late_bonding_shadow: consider_candidate failed for %s (%s)", mint, exc)
        return None


async def _price_from_chain(row: dict, *, http_client, bonding_ws_feed):
    """Reads this position's bonding curve on-chain and prices it.

    Used only when the feed's own value is stale: an account read cannot be,
    since it IS the state at that slot. Returns ``None`` rather than a guess
    whenever anything is missing -- a stale price is bad, a fabricated one is
    worse.
    """
    try:
        resolved = await resolve_bonding_curves(
            http_client, [(row["pool_address"], row["token_address"])],
            rpc_http_url=RPC_HTTP_DEFAULT,
        )
    except Exception as exc:  # noqa: BLE001 -- never break an exit check
        logger.info("solana_late_bonding_shadow: on-chain refresh failed for %s (%s)",
                    row.get("pool_address"), exc)
        return None
    account = resolved.get(row["pool_address"]) if resolved else None
    curve = getattr(account, "curve", None)
    decimals = getattr(account, "token_decimals", None)
    if not curve or decimals is None:
        return None
    price, reserve = price_and_reserve_from_curve(
        curve, token_decimals=decimals, sol_usd=getattr(bonding_ws_feed, "sol_usd", None),
    )
    if not price:
        return None
    return PoolSnapshot(
        pool_address=row["pool_address"], price_usd=price, reserve_usd=reserve,
        available=True, dex_id="pumpfun-onchain",
    )


async def _price_position(row: dict, *, chain: str, bonding_ws_feed, snapshot_fn,
                          geckoterminal_client=None, http_client=None):
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
                # 22/08 -- a STALE price may not decide an exit. The feed's
                # last-known-state semantics are right when no trade happened
                # (see pumpfun_bonding_ws's 19/08 comment), but a stop firing
                # on a price older than the staleness window sells against a
                # number nobody is trading at. Two-stage on purpose: the fresh
                # case stays free, and only the doubtful one pays a read --
                # `getMultipleAccounts` is 1 credit for up to 100 accounts, so
                # even a large open book costs a handful per pass, and only
                # for the positions actually in doubt.
                if getattr(snap, "stale", False) and http_client is not None:
                    fresh = await _price_from_chain(
                        row, http_client=http_client, bonding_ws_feed=bonding_ws_feed,
                    )
                    if fresh is not None:
                        return fresh
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
    # 22/08 -- this used to pass a literal None as the client. DexScreener is
    # tried first and never touches it, so the bug stayed invisible until the
    # GeckoTerminal fallback was actually needed -- i.e. exactly when the
    # primary source was already failing. 7838 swallowed AttributeErrors since
    # 20/08, each one a candidate dropped by a bug rather than by a filter.
    return await fn(geckoterminal_client, row["pool_address"], row["token_address"],
                    chain=chain)


async def _enrich_exit_route(row_id: int, mint: str, *, db_path: str | None = None, table: str = TABLE) -> None:
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
                f"UPDATE {table} SET sellable_at_entry = ?, roundtrip_loss_pct_at_entry = ? "
                f"WHERE id = ?",
                (None if sellable is None else int(sellable),
                 out.get("roundtrip_loss_pct"), row_id),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- an observation never breaks a pocket
        logger.info("solana_late_bonding_shadow: exit-route check failed for %s (%s)", mint, exc)


async def _archive_pre_entry_path(
    row_id: int, mint: str, pool_address: str, *, chain: str,
    curve_tracker, entry_price: float, entry_progress: float | None,
    archive_module: str = ARCHIVE_MODULE,
) -> None:
    """Archives the curve's climb BEFORE the buy, as phase="before".

    Prices are derived from progress: on a bonding curve the two are the same
    information, and the tracker has been sampling progress since the mint
    appeared on the creation feed. The scale is anchored on the entry, so the
    stored path is exactly comparable to the "after" rows written by the exit
    loop -- an absolute price rebuilt from a curve formula would not be.

    Fire-and-forget and fully guarded: archiving never delays or breaks an
    entry.
    """
    if curve_tracker is None or not entry_progress:
        return
    try:
        history = curve_tracker.progress_history_of(mint)
    except Exception:  # noqa: BLE001 -- a tracker without history is not an error
        return
    if len(history) < 2:
        return
    candles = []
    for ts, progress in history:
        if not progress:
            continue
        # progress is monotone in price on the curve, so the ratio to the
        # entry progress IS the ratio to the entry price
        price = entry_price * (progress / entry_progress)
        candles.append(SimpleNamespace(
            ts=int(ts), open=price, high=price, low=price, close=price, volume=0.0,
        ))
    if not candles:
        return
    try:
        await shadow_candle_archive.store_candles(
            module=archive_module, position_id=row_id, pool_address=pool_address,
            chain=chain, phase="before", candles=candles,
        )
    except Exception as exc:  # noqa: BLE001 -- never breaks an entry
        logger.info("solana_late_bonding_shadow: pre-entry archive failed for %s (%s)",
                    pool_address, exc)


async def _enrich_paid_profile(
    row_id: int, mint: str, *, chain: str = "solana", db_path: str | None = None,
    table: str = TABLE,
) -> None:
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
                f"UPDATE {table} SET has_paid_profile = ? WHERE id = ?",
                (1 if has_profile else 0, row_id),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- enrichment never disturbs the pocket
        logger.info("solana_late_bonding_shadow: paid-profile enrichment failed for %s (%s)", mint, exc)


async def resolve_migrated_pools(
    http_client, *, chain: str = "solana", bonding_ws_feed=None, pumpswap_feed=None,
    limit: int = 10, db_path: str | None = None, find_pool_fn=None,
    table: str = TABLE,
) -> int:
    """Finds the AMM pool of every open position whose curve has completed, so
    it keeps being priced on the RPC instead of falling back to REST.

    21/08, operator: "on a dit tout par le RPC Helius" -- STALE as of 26/08,
    corrected after being cited by mistake as current: `find_pool_for_mint`'s
    `RPC_HTTP_DEFAULT` reads `ARIA_SOLANA_RPC_HTTP`, which now resolves to
    Chainstack (verified live against the real backend/.env), not Helius.
    Helius stays available as a reinforcement once its own credit quota
    resets, not the default path. Bounded on purpose --
    at most `limit` unresolved positions per pass, each costing ONE
    `getProgramAccounts`, and each resolved exactly once for the position's
    whole remaining life. A failure is not recorded, so it simply retries on
    the next pass rather than poisoning the row with a wrong address."""
    from aria_core.services.pumpswap_ws import find_pool_for_mint

    find = find_pool_fn or find_pool_for_mint
    await _ensure_table(db_path, table=table)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT id, pool_address, token_address FROM {table} "
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
                f"UPDATE {table} SET amm_pool_address = ? WHERE id = ?", (pool, row["id"]),
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


# A sell that just failed is not retried for this long. Without it, a position
# that cannot be exited is retried by every loop on every pass -- which on
# 22/08 produced 847 rate-limit errors and took the whole pocket down for 3
# hours, discovery included. The exit is not abandoned, only paced.
SELL_COOLDOWN_SECONDS = 30.0
_sell_failed_at: dict[int, float] = {}


def _sell_on_cooldown(row_id: int) -> bool:
    last = _sell_failed_at.get(row_id)
    return last is not None and (time.monotonic() - last) < SELL_COOLDOWN_SECONDS


def _mark_sell_failed(row_id: int) -> None:
    _sell_failed_at[row_id] = time.monotonic()
    if len(_sell_failed_at) > 500:
        cutoff = time.monotonic() - SELL_COOLDOWN_SECONDS * 4
        for key in [k for k, v in _sell_failed_at.items() if v < cutoff]:
            _sell_failed_at.pop(key, None)



async def _apply_exit_check(
    row: dict, snapshot, *, chain: str, db_path: str | None, sell_fn=None,
    table: str = TABLE, archive_module: str = ARCHIVE_MODULE, bonding_ws_feed=None,
) -> dict:
    """Archives the path, runs the SHARED exit rule and persists the outcome
    for ONE position.

    21/08 -- extracted so the polling sweep and the event-driven handler run
    the identical code. Two call sites each carrying their own copy would mean
    the pocket quietly trading two policies at once, and the difference would
    surface as unexplainable PnL rather than as a failure.

    ``sell_fn`` -- 22/08, the exit counterpart of ``consider_candidate``'s
    ``execute_fn``, and the reason real trading could not be switched on
    before: the buy seam existed, the sell seam did not. A pocket that buys
    for real but only SIMULATES its exits would leave real tokens stranded
    while this table records them as sold -- the position collapses to zero
    on-chain while the row claims a clean stop.

    Signature: ``await sell_fn(mint, fraction, chain=...)`` where ``fraction``
    is the share of the CURRENT holding to sell, returning the fill or
    ``None``. ``None`` aborts the write entirely and leaves the position open,
    so the table can never claim an exit that did not happen.

    Default ``None`` keeps the pocket in pure simulation, byte-identical to
    its behaviour before this parameter existed."""
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
            # 23/08 -- was the literal string "solana_late_bonding" (a
            # duplicate of ARCHIVE_MODULE's own value, not a reference to it).
            # Switched to the parameter so the retracement variant's snapshots
            # land under its own module name instead of colliding with the
            # primary pocket's on the same (module, position_id) key -- two
            # different tables can both produce row id 5.
            module=archive_module, position_id=row["id"],
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
                    f"UPDATE {table} SET reinforce_price = ?, reinforce_at = ? "
                    f"WHERE id = ? AND reinforce_price IS NULL",
                    (trigger, datetime.now(timezone.utc).isoformat(), row["id"]),
                )
                await db.commit()

    # 22/08 -- ARCHIVE THE PATH, standing convention since 18/08 that this
    # pocket alone never honoured. Written BEFORE the exit verdict on purpose:
    # the point that triggers a close must be in the archive too, otherwise
    # every path stops one step short of the thing being studied.
    #
    # What it unblocks, measured: `liquidity_collapse` carries 40.6% of this
    # pocket's remaining loss (87 trades, -63.98% each, reserve down 65% by the
    # time we sold). The exit fires at a 50% reserve drop and looks far too
    # late -- but the row only ever kept the reserve at entry and the last one
    # seen, so no tighter threshold could be tested without inventing the data
    # in between. Self-throttled and failure-swallowing: it must never slow or
    # break a real exit.
    await shadow_candle_archive.store_observation(
        module=archive_module, position_id=row["id"], pool_address=row["pool_address"],
        chain=chain, price_usd=snapshot.price_usd, reserve_usd=snapshot.reserve_usd,
        # 22/08 -- the snapshot itself, for its RAW curve fields and its own
        # timestamp. A live closure showed price -18.4% with the reserve
        # unchanged to the cent across 425ms, which a bonding curve cannot do:
        # price comes from the VIRTUAL reserves and reserve_usd from the REAL
        # ones, and the derived pair cannot say which of the two is wrong.
        snapshot=snapshot,
    )

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
        # This pocket's own distance, never the shared default -- see
        # TRAILING_STOP_PCT's own comment for why 8% and why it is a bet.
        trailing_stop_pct=TRAILING_STOP_PCT,
        fixed_stop_pct=FIXED_STOP_PCT,
    )
    # 22/08, caught live within three minutes of enabling real trading: a
    # SHADOW position (never really bought) hit its stop, sell_fn found no
    # tokens, refused, and the row stayed open -- so every loop retried it,
    # several times per second, forever. Only a row carrying a real buy can be
    # sold; a simulated one closes in the table as it always did.
    is_real_position = bool(row.get("buy_tx"))
    if sell_fn is not None and is_real_position:
        # How much of the CURRENT holding this verdict sells. Expressed as a
        # share of what is still held rather than of the original position,
        # because the wallet's real balance already reflects every earlier
        # partial exit -- passing a share of the original would oversell on
        # the second rung.
        held_before = float(row.get("remaining_qty") or 0.0)
        held_after = float(result.get("remaining_qty") or 0.0)
        if held_before > 0 and held_after < held_before:
            closing = result.get("exit_reason") not in (None, "")
            fraction = 1.0 if closing else (held_before - held_after) / held_before
            # 22/08, real outage: seven positions that could not be sold were
            # retried on EVERY pass of EVERY loop. 847 rate-limit errors in
            # minutes, and the provider then refused everything -- including
            # discovery, so the pocket stopped trading entirely for 3 hours.
            # A failing sell must back off, not hammer.
            if _sell_on_cooldown(row["id"]):
                return {"checked": 1, "closed": 0}
            try:
                fill = await sell_fn(row["token_address"], fraction, chain=chain)
            except Exception as exc:  # noqa: BLE001 -- a failed sell is not an exit
                _mark_sell_failed(row["id"])
                logger.warning(
                    "solana_late_bonding_shadow: sell_fn raised for %s (%s) -- retry in %.0fs",
                    row["token_address"], exc, SELL_COOLDOWN_SECONDS,
                )
                return {"checked": 1, "closed": 0}
            if not fill:
                _mark_sell_failed(row["id"])
                logger.warning(
                    "solana_late_bonding_shadow: real sell refused for %s -- retry in %.0fs",
                    row["token_address"], SELL_COOLDOWN_SECONDS,
                )
                return {"checked": 1, "closed": 0}
            # 22/08 -- a sell that only moved PART of the holding must not close
            # the row. Found by the operator against his own wallet: a
            # liquidation reported every token sold while three were still held,
            # two of them already marked closed here. The tokens were stranded
            # and the recorded PnL was fiction, because the sell path reported
            # what it had REQUESTED rather than what the chain showed.
            #
            # Only an explicit False blocks the closure. `None` means the chain
            # could not be read in time, and treating unknown as failure would
            # reopen good exits forever -- reconciliation already owns that case
            # and re-checks it every 45s with no deadline.
            if fill.get("partial") is True:
                _mark_sell_failed(row["id"])
                logger.warning(
                    "solana_late_bonding_shadow: PARTIAL exit on %s -- %s units still "
                    "held, row stays open (retry in %.0fs)",
                    row["token_address"], fill.get("units_remaining"), SELL_COOLDOWN_SECONDS,
                )
                return {"checked": 1, "closed": 0}
            # The real proceeds replace the modelled ones: once genuine money
            # has changed hands, a simulated fill is fiction.
            if fill.get("exit_price"):
                result["exit_price_real"] = fill["exit_price"]
            result["exit_detail"] = (
                f"{result.get('exit_detail') or ''} real_tx={fill.get('tx') or ''}"
            ).strip()

    async with aiosqlite.connect(db_path or _db_path()) as db:
        await db.execute(
            f"""
            UPDATE {table} SET remaining_qty = ?, realized_proceeds = ?, peak_price = ?,
                realistic_realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                realistic_final_multiplier = ?, last_price = ?, last_reserve_usd = ?,
                last_checked_at = ?, exit_price_source = ?, exit_detail = ?,
                reinforced_final_multiplier = ?, ladder_done = ?,
                stop_level_pct = ?, fill_level_pct = ?
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
                    result.get("realistic_final_multiplier")
                    if result.get("realistic_final_multiplier") is not None
                    else result.get("final_multiplier"),
                ),
                # Without this the rung marker never survives the call and every
                # rung re-fires ~every 10s, selling the whole position at the
                # first rung price (16 closures stuck at exactly 1.5).
                result.get("ladder_done") or 0.0,
                result.get("stop_level_pct"), result.get("fill_level_pct"),
                row["id"],
            ),
        )
        await db.commit()
    closed = 1 if result.get("exit_reason") else 0
    if closed:
        # The path is written; the throttle state for it is now dead weight on a
        # process that runs for weeks.
        shadow_candle_archive.forget_position(
            module=archive_module, position_id=row["id"],
        )
        # 26/08 -- this pocket never called remove_pools() on exit, unlike its
        # two siblings (FAST discovery, ws_exit shadow). Every closed position
        # stayed subscribed on pumpfun_bonding_ws.py's WS feed forever, which is
        # why T004's live measurement saw the pool count climb 13->63 without
        # ever plateauing -- a leak, not real trading volume, driving most of
        # the ~540k RU/day that made this feed look too expensive to migrate
        # off Helius. Best-effort: an unsubscribe failure must never block a
        # position from being recorded as closed.
        if bonding_ws_feed is not None:
            try:
                bonding_ws_feed.remove_pools([row["pool_address"]])
            except Exception:  # noqa: BLE001 -- unsubscribe is an enhancement, not the write
                logger.info(
                    "solana_late_bonding_shadow: remove_pools failed for %s",
                    row["pool_address"],
                )
    return {"checked": 1, "closed": closed}


async def advance_position_by_pool(
    pool_address: str, *, chain: str = "solana", bonding_ws_feed=None,
    snapshot_fn=None, db_path: str | None = None, sell_fn=None, http_client=None,
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

    # 22/08 -- same rule as the polling path, and it matters MORE here: this
    # loop fires on every websocket trade and closes most positions, so a
    # stale price deciding an exit does the damage here first. This function
    # reads the feed DIRECTLY rather than through `_price_position`, so the
    # guard has to be repeated -- fixing only the other path would have left
    # the busier one untouched.
    if getattr(snapshot, "stale", False) and http_client is not None:
        fresh = await _price_from_chain(
            row, http_client=http_client, bonding_ws_feed=bonding_ws_feed,
        )
        if fresh is not None:
            snapshot = fresh

    return await _apply_exit_check(
        row, snapshot, chain=chain, db_path=db_path, sell_fn=sell_fn,
        bonding_ws_feed=bonding_ws_feed,
    )


async def reconcile_with_chain(
    *, holdings_fn, verify_buy_fn=None, chain: str = "solana",
    db_path: str | None = None,
) -> dict:
    """Makes the table agree with the wallet. The safety net that lets both
    legs skip confirmation (22/08).

    Real trades no longer wait for a Solana slot before returning -- that wait
    bought nothing on the buy side and only blocked the exit loop on the sell
    side. Skipping it means the table can briefly disagree with the chain, in
    exactly two ways, and both are repaired here:

      * a position recorded OPEN whose token is not held  -- the buy never
        landed. The row is cancelled, not left to be exited later at a price
        for tokens nobody owns.
      * a position recorded CLOSED whose token is STILL held -- the sell never
        landed. The row is REOPENED so the exit rule sells it again on the next
        pass. This is the FOMO failure of 22/08, which stranded real tokens
        under a row claiming a clean stop.

    `holdings_fn` returns `{mint: amount}` read from the chain. It is injected
    rather than imported so this module keeps no wallet dependency -- the
    pocket must stay runnable with no key at all.

    Never touches a row whose token it cannot resolve: an empty or failed
    holdings read must reconcile NOTHING rather than cancel every open
    position at once.
    """
    try:
        holdings = await holdings_fn()
    except Exception as exc:  # noqa: BLE001 -- an unreadable wallet reconciles nothing
        logger.warning("solana_late_bonding_shadow: reconcile skipped (%s)", exc)
        return {"cancelled": 0, "reopened": 0, "skipped": True}
    if holdings is None:
        return {"cancelled": 0, "reopened": 0, "skipped": True}

    stats = {"cancelled": 0, "reopened": 0, "skipped": False}
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""SELECT id, token_address, exit_reason, detected_at, buy_tx
                FROM {TABLE}
                WHERE chain = ?
                  AND (buy_tx IS NOT NULL OR exit_detail LIKE '%real_tx%')
                  AND last_checked_at > ?""",
            (chain, _RECONCILE_WINDOW_START()),
        )
        rows = [dict(r) for r in await cur.fetchall()]

        for row in rows:
            held = float(holdings.get(row["token_address"], 0.0) or 0.0)
            closed = bool(row["exit_reason"])

            # 22/08, found by the operator: absence of tokens does NOT mean the
            # buy failed. A position already sold looks exactly the same, and
            # 67% of real buys were being cancelled although their transaction
            # had SUCCEEDED on-chain (verified: the wallet really received the
            # tokens). Deducing failure from an empty wallet destroyed real
            # trade history.
            #
            # So a buy is only declared failed when the CHAIN says so. When the
            # transaction is unknown or unverifiable, the row is left alone --
            # an untouched row is recoverable, a wrongly cancelled one is not.
            if not closed and held <= 0 and row.get("buy_tx") and verify_buy_fn:
                try:
                    landed = await verify_buy_fn(row["buy_tx"])
                except Exception:  # noqa: BLE001 -- unknown is not failed
                    landed = None
                if landed is not False:
                    continue

            if not closed and held <= 0:
                await db.execute(
                    f"""UPDATE {TABLE}
                        SET exit_reason = ?, exit_detail = COALESCE(exit_detail,'') || ?,
                            remaining_qty = 0.0, final_multiplier = NULL,
                            realistic_final_multiplier = NULL, last_checked_at = ?
                        WHERE id = ? AND (exit_reason IS NULL OR exit_reason = '')""",
                    ("buy_never_landed",
                     " | cancelled: recorded open, nothing held on-chain", now, row["id"]),
                )
                stats["cancelled"] += 1
                logger.warning(
                    "solana_late_bonding_shadow: #%s cancelled, buy never landed", row["id"],
                )
            elif closed and held > 0:
                await db.execute(
                    f"""UPDATE {TABLE}
                        SET exit_reason = NULL, exit_detail = COALESCE(exit_detail,'') || ?,
                            remaining_qty = 1.0, final_multiplier = NULL,
                            realistic_final_multiplier = NULL, ladder_done = 0.0,
                            last_checked_at = ?
                        WHERE id = ?""",
                    (" | reopened: recorded closed but tokens still held", now, row["id"]),
                )
                stats["reopened"] += 1
                logger.warning(
                    "solana_late_bonding_shadow: #%s reopened, sell never landed", row["id"],
                )
        await db.commit()
    return stats


def _RECONCILE_WINDOW_START() -> str:
    """Only recent rows are reconciled. An old closed position whose token was
    later airdropped back must not reopen itself years afterwards."""
    return (datetime.now(timezone.utc) - timedelta(minutes=RECONCILE_WINDOW_MINUTES)).isoformat()


class _PricedSnapshot:
    """Minimal snapshot built from an external price quote.

    Carries the pocket's LAST KNOWN reserve and venue rather than inventing
    them: a price feed says what something is worth, not how deep the pool is,
    and a fabricated reserve would feed the liquidity-collapse exit a number
    nobody measured.
    """

    __slots__ = ("available", "price_usd", "reserve_usd", "dex_id",
                 "price_high_since_last_read", "price_low_since_last_read")

    def __init__(self, price_usd, *, reserve_usd, dex_id):
        self.available = True
        self.price_usd = price_usd
        self.reserve_usd = reserve_usd
        self.dex_id = dex_id
        # A point quote knows no high or low: reporting the price as both would
        # claim we observed a range we never saw. The websocket owns that.
        self.price_high_since_last_read = None
        self.price_low_since_last_read = None


async def advance_exit_by_prices(
    prices: dict, *, chain: str = "solana", db_path: str | None = None, sell_fn=None,
) -> dict:
    """Re-evaluates open positions against externally supplied prices (22/08).

    The websocket only speaks when a pool MOVES, which left a quiet token
    unpriced for as long as it stayed quiet -- a 5.9s median between checks,
    measured. A stop cannot fire on a price nobody looked at, so this is the
    ACTIVE half: it asks rather than waiting to be told.

    Deliberately routed through `_apply_exit_check` like every other path, so
    the single-writer guarantee holds and no third exit policy can drift into
    existence.
    """
    if not prices:
        return {"checked": 0, "closed": 0}
    await _ensure_table(db_path)
    stats = {"checked": 0, "closed": 0}
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""SELECT * FROM {TABLE}
                WHERE chain = ? AND (exit_reason IS NULL OR exit_reason = '')""",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for row in rows:
        price = prices.get(row["token_address"])
        if not price:
            continue
        snapshot = _PricedSnapshot(
            float(price),
            reserve_usd=row.get("last_reserve_usd") or row.get("reserve_usd"),
            dex_id=row.get("exit_price_source") or "pumpfun",
        )
        try:
            outcome = await _apply_exit_check(
                row, snapshot, chain=chain, db_path=db_path, sell_fn=sell_fn,
            )
        except Exception as exc:  # noqa: BLE001 -- one position never stops the sweep
            logger.info(
                "solana_late_bonding_shadow: price-driven exit failed for %s (%s)",
                row["token_address"], exc,
            )
            continue
        stats["checked"] += outcome.get("checked", 0)
        stats["closed"] += outcome.get("closed", 0)
    return stats


async def advance_exit_simulation(
    geckoterminal_client=None, *, chain: str = "solana", limit: int = 200,
    snapshot_fn=None, bonding_ws_feed=None, db_path: str | None = None,
    max_rest_calls: int | None = None, sell_fn=None, http_client=None,
    table: str = TABLE, archive_module: str = ARCHIVE_MODULE,
) -> dict:
    """Advances every open position using the SAME exit rule as WS-EXIT --
    imported, never reimplemented, so the two pockets differ on ENTRY only and
    stay comparable.

    ``table``/``archive_module`` -- 23/08, same seam as ``consider_candidate``
    (see ``RETRACEMENT_TABLE``). Default reproduces the primary pocket's
    behaviour exactly."""
    stats = {"checked": 0, "closed": 0}
    await _ensure_table(db_path, table=table)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {table} WHERE chain = ? AND exit_reason IS NULL "
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
                geckoterminal_client=geckoterminal_client, http_client=http_client,
            )
        except Exception:  # noqa: BLE001 -- a provider failure is not a verdict
            continue
        if not snapshot.available or snapshot.price_usd is None:
            continue

        outcome = await _apply_exit_check(
            row, snapshot, chain=chain, db_path=db_path, sell_fn=sell_fn,
            table=table, archive_module=archive_module, bonding_ws_feed=bonding_ws_feed,
        )
        stats["closed"] += outcome["closed"]
    return stats


_EFFECTIVE_REALISTIC_MULT_SQL = (
    "CASE "
    "WHEN realistic_final_multiplier IS NOT NULL THEN realistic_final_multiplier "
    "WHEN realistic_entry_price IS NOT NULL "
    "THEN COALESCE(realistic_realized_proceeds, 0.0) / realistic_entry_price "
    "ELSE NULL END"
)


async def summary(*, chain: str = "solana", since: str | None = None, db_path: str | None = None) -> dict:
    """Same shape as the sibling pockets' own summary, so the comparative
    report can treat all three identically.

    Reports from ``CONFIG_EPOCH`` by default -- closures from an earlier
    configuration are still in the table but are not averaged in. Pass
    ``since`` explicitly to read any other window, including the full history.

    **25/08, real bug found live** (operator question: "are rugs counted as a
    total loss?" -- cross-pocket recheck of shadow_notify.py's own 23/08 fix
    surfaced this twin). The old query used ``COALESCE(realistic_final_
    multiplier, final_multiplier)``: a position that was genuinely bought
    (``realistic_entry_price`` NOT NULL) but whose pool then dried up mid-hold
    (``realistic_final_multiplier`` NULL, this pocket's own dominant
    ``liquidity_collapse`` exit path) silently fell back to the NON-realistic,
    zero-friction ``final_multiplier`` -- i.e. a real rug reported at whatever
    optimistic price the last spot tick showed, not the salvaged-vs-entry loss
    it actually was. ``_EFFECTIVE_REALISTIC_MULT_SQL`` now mirrors
    ``chain_pnl_summary_realistic``'s own ``stranded`` handling in
    robinhood_pump_shadow.py/base_momentum_shadow.py: a stranded row scores
    ``realistic_realized_proceeds / realistic_entry_price`` (0 proceeds if
    nothing was ever sold -> -100%), while a row whose ENTRY itself was never
    fillable (``realistic_entry_price`` NULL -- this trade could never have
    happened at all) stays excluded exactly as before."""
    await _ensure_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT {_EFFECTIVE_REALISTIC_MULT_SQL} AS m, "
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
            f"SELECT AVG({_EFFECTIVE_REALISTIC_MULT_SQL}) AS m, "
            f"COUNT(*) AS n FROM {TABLE} WHERE chain = ? AND exit_reason IS NOT NULL "
            f"AND last_checked_at >= replace(datetime('now','-24 hours'),' ','T') "
            f"AND {_EFFECTIVE_REALISTIC_MULT_SQL} IS NOT NULL", (chain,),
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
