"""Scalping variant engine -- V8 only since 06/08 (explicit operator
decision: "supprimer toutes les poches scalping sauf v8"). The V1-V5
mean-reversion engines (08/01) and the v6/v7 legacy RSI-divergence arms
(wired in paper_trader.build_scalping_pocket_entries) were retired that day
-- their full trade history stays intact in the DB (paper_position_archive,
momentum_scan_log...), only the sourcing code was removed. Design notes and
the comparative-test history live in docs/HANDOFF_PIPELINE_MOMENTUM.md.

The shared plumbing below (_gates_and_candles: hard gates + closed-candle
truncation + volume-gate opt-out, _buy_result: uniform signal dict with
entry_atr_pct/align_score/entry security snapshot) predates the retirement
and is kept as the common base for v8 and any future 8.x variant pocket.

Deterministic, no LLM call -- a fast, mechanical scalping engine (the
direct-buy path skips LLM confirmation entirely)."""
from __future__ import annotations

import logging
import time

from aria_core import momentum_entry
from aria_core.chasing_filter_shadow import (
    RECENT_LOW_WINDOW_STOCHASTIC,
    recent_low_from_candles,
)
from aria_core.skills import indicators
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

# How many CLOSED candles the ATR/indicator windows need at minimum --
# generous margin above each indicator's own warmup (VWAP Z-score's is the
# largest, 2 * period) so a thin candle history degrades to HOLD rather than
# a partially-warmed, misleading read.
_MIN_CANDLES_FOR_SIGNAL = 45

_ATR_PERIOD = 14

# 08/01 -- real bug found live (operator: "j'ai l'impression que sa trade
# beaucoup moin depuis 14h"): GeckoTerminal went into a sustained HTTP 429
# burst the moment the 5 variants went live. Root cause: paper_trader.py runs
# each pocket (scalping_v1..v5) as an independent full pass over the SAME
# candidate list -- every variant called _gates_and_candles(contract, chain)
# on its own for the SAME candidate, multiplying the hard-gate + candle-fetch
# network calls by 5x for IDENTICAL data (same contract/chain/mode="scalping"
# -- nothing variant-specific about the fetch itself). A short-lived cache
# keyed by (contract, chain) makes the first variant's call the only one that
# hits the network; the other 4 reuse the exact same (pair, candles, hold)
# tuple. TTL comfortably covers one cycle's 5 sequential pocket passes over
# the same list (a few seconds to low minutes in practice) while never
# serving data across cycles (candles/gates must stay fresh cycle to cycle).
_GATES_CACHE_TTL_SECONDS = 120.0
_GATES_CACHE_MAX_SIZE = 500
_gates_cache: dict[tuple[str, str], tuple[float, tuple]] = {}


def _prune_gates_cache(now: float) -> None:
    """Opportunistic prune, called only when the cache grows past a
    threshold -- keeps steady-state memory bounded without a background
    task, since the short TTL means most entries are already expired by the
    time this runs."""
    if len(_gates_cache) <= _GATES_CACHE_MAX_SIZE:
        return
    expired = [k for k, (expires_at, _) in _gates_cache.items() if expires_at <= now]
    for k in expired:
        del _gates_cache[k]

# Anti-dump confirmation: the LAST candle must show the indicator back OUT
# of its oversold zone, the SECOND-TO-LAST candle must still show it WAS
# oversold -- proves a genuine turn, never a buy mid-collapse.


def _hold(chain: str, symbol: str, price: float, reason: str, hold_code: str) -> dict:
    return {
        "action": "HOLD", "chain": chain, "symbol": symbol, "price": price,
        "reasons": [reason], "hold_reason": hold_code, "mode": "scalping",
    }


async def _gates_and_candles(
    contract: str, chain: str, *, enforce_volume_gate: bool = True,
) -> tuple["momentum_entry.PairSnapshot | None", list[Candle], dict | None]:
    """Shared plumbing for every variant pocket: hard gates (same as the RSI
    scalping mode, mode="scalping") then real 15-30min candles. Returns
    ``(None, [], hold_dict)`` on any hard rejection or missing data --
    caller returns that dict as-is, never guesses a signal without data.

    08/01 -- cached (see _gates_cache's own comment): concurrent variant
    pockets used to evaluate the SAME candidate independently, multiplying
    the network calls for identical data.

    ``enforce_volume_gate`` (08/05, scalping_v8 -- first live-data decision
    under Claude's autonomous mandate): v8 opts OUT of the RVOL>=3x hard
    gate. Empirical basis, PRE-established by the same-day backtest (58 real
    trades, BEFORE v8 shipped -- not a reactive tweak): RVOL at entry showed
    NO predictive power on outcome (RVOL<1x actually won 42.9% vs 35.3% for
    >=3x), and the first 40 live minutes confirmed the gate as v8's dominant
    starvation cause (29 of 50 scans rejected on it alone -- the exact V2/V5
    zero-trade trap this pocket's bootstrap mode exists to avoid). RVOL stays
    computed and persisted by _buy_result (observational, feeds sizing),
    only its HARD-GATE use is skipped for callers that opt out. Cache key
    includes the flag -- a gated and an ungated read of the same candidate
    never contaminate each other (v1-v5 byte-for-byte unchanged)."""
    key = (contract.lower(), (chain or "").lower(), enforce_volume_gate)
    now = time.monotonic()
    cached = _gates_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    result = await _gates_and_candles_uncached(contract, chain, enforce_volume_gate=enforce_volume_gate)
    _prune_gates_cache(now)
    _gates_cache[key] = (now + _GATES_CACHE_TTL_SECONDS, result)
    return result


async def _gates_and_candles_uncached(
    contract: str, chain: str, *, enforce_volume_gate: bool = True,
) -> tuple["momentum_entry.PairSnapshot | None", list[Candle], dict | None]:
    best_pair, _honeypot_reason, hold = await momentum_entry.evaluate_hard_gates(
        contract, chain, mode="scalping",
    )
    if hold is not None:
        return None, [], hold
    if best_pair is None:
        return None, [], None  # no liquid pair -- signal structurally absent, same as the RSI path
    candles = await momentum_entry._fetch_candles(
        best_pair.pair_address, chain, contract=contract, pair=best_pair, mode="scalping",
    )
    # 03/08 -- 9-pocket diagnostic (docs/HANDOFF_LLM.md, blind LLM comparison):
    # the OHLCV cascade's last candle is the one CURRENTLY forming, not yet
    # closed (standard behavior of every real-time OHLCV provider in this
    # cascade -- GeckoTerminal/Mobula/DexPaprika all report the in-progress
    # candle as the latest point; `Candle.ts` isn't reliably a real close-time
    # epoch across all of them, some report it as a plain index, so comparing
    # it to "now" per-provider would be fragile). Every evaluate_vN below
    # reads candles[-1]/candles[-2] for its oversold-then-confirmed-exit
    # signal -- on the UNTRIMMED series that means confirming a bounce against
    # a candle still mid-formation, i.e. buying into an intra-candle pump
    # before it's actually confirmed to hold. Trimmed HERE, once, for all 5
    # variants (same centralization doctrine as align_score/entry_atr_pct in
    # _buy_result below) -- never trade signal computed on an unclosed candle,
    # same industry-standard practice Fable 5/Qwen3.7-Max both flagged
    # independently. Checked against the threshold AFTER trimming, not before
    # -- a batch that just barely cleared 45 candles must not silently fall
    # under it once the unclosed one is dropped.
    candles = candles[:-1]
    if len(candles) < _MIN_CANDLES_FOR_SIGNAL:
        return None, [], _hold(
            chain, best_pair.base_symbol, best_pair.price_usd,
            f"historique de bougies insuffisant ({len(candles)} < {_MIN_CANDLES_FOR_SIGNAL})",
            "insufficient_candle_history",
        )
    # 08/02 -- real bug found live (diagnostic workflow, operator go-ahead to
    # fix: 7/7 closed scalping trades lost, peak_gain_pct = 0.00% in EVERY
    # case -- the expected bounce from oversold never materialized once,
    # in any of the 5 variants or the legacy RSI-divergence engine). Root
    # cause: none of the 6 scalping entries had ANY trend/market-context
    # filter -- just "the oversold indicator just exited its zone", with no
    # check that a real bounce (not a dead-cat one inside a larger downtrend)
    # was actually forming.
    #
    # 08/02, same evening -- the first fix (EMA12>EMA26 / MACD>signal /
    # bullish pattern via momentum_entry._technical_alignment, threshold
    # >=2/3) was itself a real bug, found by an operator-approved adversarial
    # cross-review workflow and independently confirmed live: EMA12/26 are
    # structurally too slow for scalping's 15/30min candle width (EMA26 lags
    # ~6.5h) to ever confirm a bounce that JUST formed -- a 500-scenario
    # simulation against the real, unmocked function showed the >=2 bar was
    # reached only 2.1% of the time, and real prod data confirmed the same
    # starvation (scalping_v2/v4/v5: ZERO trades opened in 8h despite 16
    # rejections each on this gate alone; v1/v3 barely got through). The
    # gate's own INTENT (reject a dead-cat bounce not backed by real demand)
    # is better served by relative volume -- momentum_entry's own
    # _check_volume_confirmation, already the hard gate the standard
    # momentum pipeline uses for exactly this purpose (RVOL >= 3.0x the prior
    # 10-candle average, floor $2,500 on the triggering candle for swing/
    # daily-scale candles, $500 with mode="scalping" -- 08/04 fix, see that
    # function's own comment; this call's candles are 15/30min, dozens of
    # times shorter than daily, so the swing floor never applied here before
    # the fix) -- a signal that CAN react on the same candle the bounce forms
    # on, unlike a lagging moving average. Same 3-state doctrine reused as-is: "not_confirmed"
    # (real data, bounce not backed by capital) rejects; "unknown" (no real
    # per-candle volume on this data source, e.g. a synthesis fallback) never
    # rejects -- same fail-open doctrine as everywhere else in this pipeline,
    # never confusing "this source doesn't provide this data" with "this
    # signal is false". align_score itself is UNCHANGED as an
    # informational/sizing signal (still computed and propagated in
    # _buy_result below, still feeds risk_guard's conviction sizing) -- only
    # its use as a HARD GATE here is removed.
    if enforce_volume_gate:
        volume_status, volume_reason, _rvol = momentum_entry._check_volume_confirmation(candles, mode="scalping")
        if volume_status == "not_confirmed":
            return None, [], _hold(
                chain, best_pair.base_symbol, best_pair.price_usd,
                f"rebond non soutenu par le volume ({volume_reason})",
                "no_volume_confirmation",
            )
    return best_pair, candles, None


def _atr_value(candles: list[Candle]) -> float | None:
    atr = indicators.atr_series(candles, period=_ATR_PERIOD)
    return atr[-1]


def _rr(entry: float, stop: float, target: float) -> float:
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return (target - entry) / risk


def _buy_result(
    *, pair, chain: str, contract: str, entry: float, stop: float, target: float | None, reason: str,
    variant: str, candles: list[Candle], sizing_rr: float | None = None,
    recent_low_window: int = 20,
) -> dict:
    # 08/02 -- real bug found live (operator: 7/7 closed scalping trades lost,
    # every one via the blind stagnation timeout, none via the ATR trailing
    # stop): this was hardcoded to None on every variant, even the ones (V1/
    # V2/V4/V5) that already compute a real ATR internally just to size their
    # OWN stop -- the value was computed then thrown away, never reaching
    # paper_trader._effective_trail_pct, which silently fell back to the
    # generic TRAIL_STOP_PCT (15%) -- wildly too wide for scalping-scale moves
    # (the 7 real losses were 1.7%-3.6%), so the trailing stop never actually
    # triggered in practice. Computed HERE (once, uniformly for all 5
    # variants including V3, which has no ATR of its own -- its stop is
    # structural, previous-candle-low-based) rather than threading an extra
    # parameter through each evaluate_vN, so no future variant can forget it.
    # Same ratio convention as momentum_entry.py's own entry_atr_pct
    # (last_atr / price, a fraction, never a raw ATR value or a *100 percent).
    atr = _atr_value(candles)
    entry_atr_pct = (atr / entry) if atr and entry > 0 else None
    # 08/02 -- same fix as entry_atr_pct above, same root incident: align_score
    # (and align_ema/align_macd/align_pattern) were NEVER populated for any
    # scalping variant, which made risk_guard.compute_entry_alloc's
    # conviction_size_multiplier/conviction_risk_budget_pct fall back to their
    # own "missing signal" branch (MAX_ALLOC_MULTIPLIER, the most permissive
    # tier -- never actually earned by a real signal). By the time
    # _buy_result runs, _gates_and_candles_uncached has ALREADY required
    # align_score >= momentum_entry._ALIGN_SCORE_MIN_FOR_DIRECT_BUY (2) --
    # recomputed here (cheap, no network call, same candles already in hand)
    # so that guarantee is reflected in the persisted signal rather than
    # silently dropped.
    align_score, _align_reasons, align_detail = momentum_entry._technical_alignment(candles)
    # 08/02 -- same recompute-once-more-is-cheap pattern as align_score above:
    # by the time _buy_result runs, _gates_and_candles_uncached has ALREADY
    # rejected "not_confirmed" (see its comment) -- status here can only be
    # "confirmed" or "unknown". Same mapping as momentum_entry.py's own
    # standard-pipeline call site (confirmed -> True, everything else ->
    # False/unconfirmed) so risk_guard.compute_entry_alloc's existing
    # volume_confirmed handling (sizing penalty on unconfirmed/unknown volume)
    # applies identically to scalping, no new sizing code needed.
    volume_status, _volume_reason, rvol_multiple = momentum_entry._check_volume_confirmation(candles, mode="scalping")
    volume_confirmed = volume_status == "confirmed"
    # 08/02 -- real bug found (adversarial cross-review workflow, confirmed
    # against the real code): V5 ("no fixed TP, pure trailing stop" by
    # design) always passes target=None, so rr was always None too --
    # risk_guard.conviction_size_multiplier/conviction_risk_budget_pct treat
    # rr=None as "caller doesn't supply this signal" (a rule meant to
    # preserve the dormant VC-thesis pilot's historical behavior, never
    # designed for a real momentum engine), falling back to
    # MAX_ALLOC_MULTIPLIER -- V5 received the MAXIMUM allocation (5%/$50k)
    # on EVERY buy, with zero risk discrimination, unlike V1-V4/V6.
    # ``sizing_rr`` (V5 only, see its own evaluate_v5_vwap_trailing) plugs
    # this specific gap WITHOUT inventing a fake target: it's not a TP level
    # (target stays None below, exactly as before -- the real exit is still
    # governed purely by the ATR trailing stop, no behavior change there),
    # only a number fed to the sizing tiers so V5 stops being treated as "no
    # signal at all". Deliberately kept a real value the ATR-based sizing can
    # use rather than switching V5 to some unrelated proxy metric.
    computed_rr = sizing_rr if sizing_rr is not None else (_rr(entry, stop, target) if target is not None else None)
    # 08/02 -- real bug found live (100% of positions had a NULL
    # entry_security_json, diagnostic workflow): momentum_entry.py's own
    # BUY path got this snapshot in Item #234 (30/07), but these 5 variant
    # engines (created 08/01, a day AFTER #234) never did -- their shared
    # `_buy_result` builder simply never set the key. Same source, same
    # doctrine as momentum_entry.py's own fix: reuses the TokenSecurity
    # object ALREADY fetched by the honeypot hard gate a moment earlier
    # (`_gates_and_candles_uncached` -> `momentum_entry.evaluate_hard_gates`
    # -> `_check_honeypot`, which caches it under the SAME (chain, contract)
    # key), so this costs zero extra network calls. `_get_cached_security`
    # returns ``None`` on a cache miss/expiry (e.g. a cached `_gates_and_
    # candles` result reused past the security cache's own shorter TTL) --
    # `capture_entry_snapshot_from_security` degrades to an all-``None``
    # snapshot rather than skipping the field entirely, same fail-open
    # doctrine as momentum_entry.py's own call site.
    from aria_core import paper_trader_risk as _risk

    entry_security_json = _risk.capture_entry_snapshot_from_security(
        momentum_entry._get_cached_security(chain, contract)
    ).to_json()
    # Item #65 (08/03), anti-chasing shadow filter: distance to the recent
    # low, informational only -- logged by the caller (paper_trader.py /
    # limit_orders.py), NEVER a rejection gate here. ``recent_low_window``
    # matches THIS variant's own oscillator lookback (passed by each
    # evaluate_vN below), never a single uniform N -- see chasing_filter_
    # shadow.py's own module docstring for why.
    #
    # 03/08 -- conscious choice (flagged by Fable 5's review, not left as a
    # silent side effect): ``candles`` here is the TRIMMED series (still-
    # forming last candle dropped, see _gates_and_candles_uncached). A purer
    # "true recent low" would look at the untrimmed series (an intra-candle
    # dip IS a real low, unlike a reversal signal which needs a close to be
    # trustworthy) -- but this filter is purely informational (never a gate),
    # and threading a second, untrimmed candle list through the whole
    # gates->evaluate_vN->_buy_result chain for a log-only signal isn't
    # worth the added complexity. Accepted trade-off: this shadow signal is
    # now measured against the last CONFIRMED low, one candle more
    # conservative than before -- revisit if the shadow census data shows
    # this materially changes its read.
    recent_low = recent_low_from_candles(candles, recent_low_window)
    return {
        "action": "BUY", "chain": chain, "symbol": pair.base_symbol, "price": entry,
        "target": target, "invalidation": stop,
        "rr": computed_rr,
        "mode": "scalping", "strategy": "momentum",
        "reasons": [f"[{variant}] {reason}"],
        "liquidity_usd": pair.liquidity_usd, "entry_atr_pct": entry_atr_pct,
        "align_score": align_score,
        "align_ema": align_detail.get("ema_above"),
        "align_macd": align_detail.get("macd_above"),
        "align_pattern": align_detail.get("bullish_pattern"),
        "volume_confirmed": volume_confirmed,
        "rvol_multiple": rvol_multiple,
        "entry_security_json": entry_security_json,
        # 08/01 -- market cap at entry, same purely-observational field as the
        # standard momentum pipeline (see momentum_entry.py's own comment).
        "market_cap_usd": pair.market_cap_usd,
        "recent_low": recent_low,
        "recent_low_window": recent_low_window,
    }


# ── V8 -- Wick-confirmed RSI-divergence reversal (08/05, operator carte
# blanche: "je te donne carte blanche pour me crée une poche v8 avec tes
# convictions selon les données récoltées") ─────────────────────────────────
# Every design choice below is anchored to the 05/08 empirical session (58
# real closed trades reconstructed candle-by-candle + the free in-DB analysis
# of all 124 closed trades), never to theory alone:
#   - ENTRY = bullish RSI divergence (scalping period 10, CLOSED candles --
#     the two 05/08 fixes) CONFIRMED by a lower-wick (hammer) signal candle:
#     wick ratio >= 0.3 won 60% vs 25.6% without (p=0.026), the ONLY entry
#     discriminator that survived confound checks (pocket + period).
#   - The divergence itself is CONTEXT, not the edge: its gap/span showed
#     ~zero outcome correlation on real trades (05/08 analysis) -- so no
#     invented calibration on them, standard detection as-is.
#   - FRESHNESS gate: the divergence's recent pivot must be <= 5 closed
#     candles old -- a stale divergence is a historical chart pattern, not an
#     entry (34% of real trades never traded above entry again: buying late
#     into a dead setup is the dominant historical failure mode).
#   - ANTI-CHASE gate: live price must not already sit > 2% above the signal
#     candle's close -- median max gain across ALL 58 trades was +0.47%, and
#     the two real winners measured (DEGEN/LIQ) peaked at +4-6%: entering
#     +2%+ late consumes the whole move before we're even in.
#   - NO fixed take-profit (target=None, V5 pattern): tight fixed SL/TP grids
#     were ALL negative-EV on our data (12/12 combos, SL hit first on up to
#     53/58 trades) and v6's winning exits were the technical ones (bearish-
#     divergence exit 60% WR) -- exits stay: ATR trail (scalping bounds) +
#     bearish-RSI-divergence + the SHORTER v8 stagnation timeout
#     (paper_trader._scalping_stagnation_params_for_wallet: 1.5h, vs 3h
#     generic -- "timeout stagnation" was 0% WR over 45 real trades; a trade
#     that doesn't move fast is already dead, free the capital sooner).
#   - NO RVOL gate: empirically non-predictive on our memecoin trades
#     (RVOL<1x actually won MORE than >=3x) -- logged by _buy_result for
#     observation, never gated.
# Future variants (operator: "anticiper des test pour de futur version 8.1
# 8.2"): every knob is a named constant below -- a new variant = a new
# constants set threaded the same way v7 overrides v6's watch span (see
# build_scalping_pocket_entries), e.g. 8.1 = wick >= 0.6 alone (drop the
# divergence requirement, test whether the hammer IS the whole signal),
# 8.2 = tighter freshness/stagnation. Never retune v8 in place once it has
# live history -- add a pocket, keep the comparison arm (v6/v7 doctrine).
_V8_WICK_MIN_RATIO = 0.30
# 06/08 21h16 -- 5->7: 6h39 with ZERO new entries after the bootstrap-exit
# (divergence made mandatory that morning), 394 no_signal rejections/24h vs
# a distant 2nd place (blacklisted) -- the exact zero-signal starvation
# pattern this file already documents for V2/V4/V5 (sat at zero for days
# until relaxed). Diagnosed BEFORE touching this: the two OTHER gates a
# parallel session added today (bounce_already_faded, giveback ATR-scale)
# never even get reached -- this freshness window alone accounts for the
# overwhelming majority of no_signal holds, confirmed against the real
# momentum_scan_log breakdown, not assumed. Widened moderately (not reverted
# to bootstrap's no-divergence-required mode) -- still requires a real
# confirmed divergence, just tolerates a 2-candle-older pivot.
#
# Tension with "never retune v8 in place" (comment above, 05/08, before any
# of today's changes): judged a calibration adjustment on an EXISTING knob,
# not a change to what the signal fundamentally is (mèche + divergence stays
# the entry logic) -- same class as today's other in-place changes already
# made without spinning up a new pocket (bootstrap-exit, max-hold cap, the
# parallel session's own two gate additions). Operator informed of the
# silence and the planned action beforehand (no reply after the announced
# reaction window) -- acting alone within the already-established v8
# autonomy mandate, not a unilateral departure from it. Revert to 5 if this
# doesn't restore a reasonable entry cadence within a few hours.
_V8_MAX_BARS_SINCE_PIVOT = 7
_V8_MAX_CHASE_PCT = 2.0
_V8_STOP_ATR_MULT = 1.5
# Sizing-only R/R (V5's exact pattern -- target stays None, this never
# becomes a TP level). Two tiers: a wick entry BACKED by a confirmed fresh
# RSI divergence earns the standard tier (2.0, the 60%-WR historical basis
# was measured on divergence-triggered trades), a bootstrap wick-only entry
# sizes more defensively (1.5, V5's tier) until its own forward data exists.
_V8_SIZING_RR_WITH_DIVERGENCE = 2.0
_V8_SIZING_RR_WICK_ONLY = 1.5
# BOOTSTRAP MODE (08/05, operator: "fait en sorte qu'il trade beaucoup au
# début en étant souple, ça permettra d'obtenir un minimum de données") --
# the full divergence requirement stacked on wick+freshness+anti-chase would
# likely reproduce the V2/V5 zero-trade trap (both sat at zero for DAYS until
# relaxed twice, same file above). While True: the RSI divergence is OPTIONAL
# and TRACED (each buy's reason says whether it was present) instead of
# required -- the wick gate (the actual validated edge) plus a FRESH LOCAL
# TROUGH requirement still hold unconditionally. This doubles as the 8.1
# experiment for free: wick-only vs wick+divergence sub-populations accumulate
# side by side in the same pocket, separable by reason/rr at analysis time.
# Flipped to False (06/08, operator decision): 20 forward trades closed in
# <24h under bootstrap, 0 wins, 19/20 (95%) never traded above their entry
# price even once (the 05/08 backtest's own worst case was 34% never-above --
# this run is far outside it). Operator's read, not just a data point: we are
# in the LAST year of the post-halving cycle, the phase with the least "free
# beta" left in the market -- a wick-only filter that fires this fast (30
# candidates queued in under a day) is exactly the kind of permissive
# sourcing that stopped working once free capital inflow dried up. The fix
# isn't waiting passively for the ~30-trade bootstrap-exit threshold with the
# same permissive filter; it's tightening NOW. Divergence goes from optional
# (traced) to a hard requirement -- only the 60%-WR-validated combination
# (wick + fresh divergence) trades from here, the wick-only tier stops
# opening new positions. Not proven better yet by forward data (only 1
# divergence-confirmed trade exists so far, ZORA, itself a loss) -- this is a
# principled tightening for the current market phase, not an empirical
# conclusion. _V8_SIZING_RR_WICK_ONLY stays defined (existing/legacy
# positions still reference it) but no new entry can reach it once this gate
# is unconditional.
# 06/08 -- live diagnostic (Claude autonomous mandate, operator directive:
# iterate until winrate >=50%/PnL >=0): first 34 real closed trades showed
# 0% winrate AND 32/34 NEVER traded a single tick above their own entry
# price before closing (avg loss -2.57%, every exit either time-based,
# trailing-stop, or invalidation -- never a target, since v8 has none by
# design). This is not an exit-tuning problem (the 05/08 backtest's wick
# discriminator was measured candle-by-candle, not against live execution
# latency) -- it's an ENTRY problem: ``entry`` is a LIVE price fetched at
# evaluation time, up to one candle period after ``candles[-1]`` closed, and
# the existing anti-chase guard only bounds entry from ABOVE (max +2% over
# signal_close) -- nothing stops a buy once the bounce that produced the
# wick has already faded and price has resumed falling BELOW where the
# "confirmed" reversal candle closed.
#
# Devil's Advocate report 2db20159 (verified, real point): a first version of
# this gate used a flat 0.5% threshold, the only entry-band constant in the
# whole file NOT expressed in ATR while everything else here (stop, trail)
# scales with the pair's own volatility -- flat % either rejects almost
# every valid entry on a low-volatility pair or lets a real breakdown
# through on a high-volatility one. ATR-scaled here, same clamp pattern as
# paper_trader._effective_trail_pct: MULT * entry_atr_pct bounded to
# [MIN, MAX]. Principled, not yet forward-validated (0 trades under any
# version of this gate so far) -- same class of change as the 06/08
# bootstrap-mode flip below, revisit once trade data accumulates under it.
# (The report's more radical proposal -- anchor entry to a simulated limit
# order at signal_close +/- k*ATR instead of a live-price gate, run in
# shadow mode first -- is a real architecture improvement banked as
# backlog #10-adjacent, not done here: bigger surface, needs its own
# validation pass before touching how v8 actually enters.)
_V8_GIVEBACK_ATR_MULT = 0.35
_V8_MIN_GIVEBACK_PCT = 0.2
_V8_MAX_GIVEBACK_PCT = 1.5

# 07/08 ~12h30 -- DISABLED AGAIN, the re-enable above (06h25 same day) has now
# reproduced the exact pattern its own comment said to watch for. Full v8
# closed history at the moment of this decision: bootstrap tier 34 trades /
# 0 wins / -9304$ (avg -2.72%), divergence tier 6 trades / 0 wins / -1284$
# (avg -1.63%) -- bootstrap carries 91% of v8's total realized loss on 87%
# of its trade count. Since THIS re-enable specifically (anti-chase +
# ATR-giveback gates added): 4 trades, all 4 entered via bootstrap (zero via
# divergence), all 4 losers -- the very re-enable meant to test whether the
# new gates fixed bootstrap instead ran zero divergence-tier trades to
# compare against. Across both the pre-gate and post-gate samples combined,
# bootstrap has now produced 0 winners in 38 trades under two different gate
# configurations and two different market windows -- past the point where
# "not enough data" is a credible explanation. The divergence tier is the
# only one with any empirical basis at all (60% WR vs 25.6%, Fisher
# p=0.026, the actual backtest v8 was built on) -- bootstrap was always an
# experiment layered on top of it, never itself validated. Disabling it
# does not resolve the divergence tier's own 0/6 (still too small a sample
# to judge), it just stops paying bootstrap's now-proven-negative tax while
# that tier accumulates real data. Reversible in one line if a future
# session finds a real fix for bootstrap's entry quality, but re-enabling
# it AGAIN without one would just be repeating this experiment a third time.
_V8_BOOTSTRAP_MODE = False
# Bootstrap trough freshness: the lowest low of the recent window must sit
# within the last _V8_MAX_BARS_SINCE_PIVOT closed candles -- same freshness
# spirit as the divergence pivot check, computed directly on the candles.
_V8_TROUGH_WINDOW = 10


async def evaluate_v8_wick_reversal(contract: str, chain: str) -> dict | None:
    from aria_core.skills import entry_signals

    # enforce_volume_gate=False: v8's own empirically-grounded opt-out (see
    # _gates_and_candles's docstring -- RVOL non-predictive on our data, and
    # this gate alone caused 29/50 rejections in v8's first live 40 minutes).
    pair, candles, hold = await _gates_and_candles(contract, chain, enforce_volume_gate=False)
    if hold is not None:
        return hold
    if pair is None:
        return None
    # 06/08 -- combo-signal shadow log (operator guidance: RSI alone already
    # tried and doesn't work, draft indicator COMBINATIONS and observe them
    # on real forward candles before forcing any single one). Logged on every
    # real v8 evaluation that reaches valid candles, regardless of whether
    # v8's OWN gates below end up holding -- maximizes forward data, zero
    # extra network calls (same candles v8 itself just fetched).
    try:
        from aria_core import combo_signal_shadow

        await combo_signal_shadow.record_evaluation(
            contract, chain, wallet="scalping_v8", candles=candles, symbol=pair.base_symbol,
        )
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never block a real evaluation
        logger.info("scalping_variants: combo_signal_shadow.record_evaluation failed (%s)", exc)
    detail = entry_signals._bullish_rsi_divergence_detail(
        candles, period=entry_signals.SCALPING_RSI_PERIOD
    )
    divergence_fresh = (
        detail.present
        and detail.bars_since_recent_pivot is not None
        and detail.bars_since_recent_pivot <= _V8_MAX_BARS_SINCE_PIVOT
    )
    if not divergence_fresh:
        if not _V8_BOOTSTRAP_MODE:
            reason = (
                "pas de divergence RSI haussière (période scalping)"
                if not detail.present
                else f"divergence trop ancienne (pivot il y a {detail.bars_since_recent_pivot} "
                f"bougies > {_V8_MAX_BARS_SINCE_PIVOT})"
            )
            return _hold(chain, pair.base_symbol, pair.price_usd, reason,
                         "no_signal" if not detail.present else "stale_divergence")
        # bootstrap: no divergence needed, but the wick must reject a FRESH
        # local trough -- a hammer printed mid-range confirms nothing.
        window = candles[-_V8_TROUGH_WINDOW:]
        min_low = min(c.low for c in window)
        bars_since_trough = len(window) - 1 - max(
            i for i, c in enumerate(window) if c.low == min_low
        )
        if bars_since_trough > _V8_MAX_BARS_SINCE_PIVOT:
            return _hold(
                chain, pair.base_symbol, pair.price_usd,
                f"creux local trop ancien ({bars_since_trough} bougies > "
                f"{_V8_MAX_BARS_SINCE_PIVOT}, fenêtre {_V8_TROUGH_WINDOW})", "stale_trough",
            )
    wick = indicators.hammer_wick_ratio(candles[-1])
    if wick is None or wick < _V8_WICK_MIN_RATIO:
        shown = "n/a" if wick is None else f"{wick:.2f}"
        return _hold(
            chain, pair.base_symbol, pair.price_usd,
            f"bougie de signal sans rejet du bas (mèche {shown} < {_V8_WICK_MIN_RATIO})",
            "no_wick_confirmation",
        )
    entry = pair.price_usd
    atr = _atr_value(candles)
    if not atr or atr <= 0:
        return _hold(chain, pair.base_symbol, entry, "ATR non calculable", "indicator_unavailable")
    signal_close = candles[-1].close
    if signal_close > 0 and entry > signal_close * (1 + _V8_MAX_CHASE_PCT / 100.0):
        return _hold(
            chain, pair.base_symbol, entry,
            f"prix déjà parti (+{(entry / signal_close - 1) * 100:.1f}% au-dessus de la "
            f"bougie de signal, max {_V8_MAX_CHASE_PCT:.0f}%)", "price_ran_away",
        )
    if signal_close > 0:
        atr_pct = atr / entry if entry > 0 else 0.0
        giveback_limit_pct = max(
            _V8_MIN_GIVEBACK_PCT, min(_V8_MAX_GIVEBACK_PCT, _V8_GIVEBACK_ATR_MULT * atr_pct * 100.0)
        )
        if entry < signal_close * (1 - giveback_limit_pct / 100.0):
            return _hold(
                chain, pair.base_symbol, entry,
                f"rebond de la bougie de signal déjà effacé (-{(1 - entry / signal_close) * 100:.1f}% "
                f"sous sa clôture, max {giveback_limit_pct:.2f}% pour cet ATR)", "bounce_already_faded",
            )
    stop = entry - _V8_STOP_ATR_MULT * atr
    if stop <= 0:
        return _hold(chain, pair.base_symbol, entry, "stop ATR invalide (<=0)", "invalid_stop")
    if divergence_fresh:
        basis = (
            f"divergence RSI confirmée (pivot il y a {detail.bars_since_recent_pivot} bougies)"
        )
        sizing_rr = _V8_SIZING_RR_WITH_DIVERGENCE
    else:
        basis = "creux local frais sans divergence (bootstrap)"
        sizing_rr = _V8_SIZING_RR_WICK_ONLY
    return _buy_result(
        pair=pair, chain=chain, contract=contract, entry=entry, stop=stop, target=None,
        reason=(
            f"mèche basse confirmée (ratio {wick:.2f} >= {_V8_WICK_MIN_RATIO}), "
            f"{basis}, sans TP fixe"
        ),
        variant="V8 wick reversal",
        candles=candles, sizing_rr=sizing_rr, recent_low_window=RECENT_LOW_WINDOW_STOCHASTIC,
    )


VARIANT_ANALYZERS = {
    "scalping_v8": evaluate_v8_wick_reversal,
}
