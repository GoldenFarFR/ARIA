"""Scalping variant engines (V1-V5, 08/01) -- 5 independent mean-reversion
strategies compared side by side on the SAME micro-cap sourcing/hard-gate
pipeline already used by the RSI-divergence scalping mode. Operator-provided
spec (%B Bollinger / VWAP Z-score / fast Stochastic %K, ATR-based risk
management) -- cross-checked against ``indicators.py``'s own conventions
before wiring, never taken at face value without verifying the formulas
against the real code first.

Each variant shares:
  - the SAME hard gates as every other momentum entry (blacklist/honeypot/
    liquidity/wash-trading/B20 -- ``momentum_entry.evaluate_hard_gates``,
    ``mode="scalping"``) -- never a weaker guardrail for a new variant.
  - the SAME anti-dump doctrine (operator spec): never buy WHILE the
    indicator is still IN its oversold zone (a falling knife) -- only on the
    CONFIRMED EXIT from oversold (oversold on the second-to-last candle,
    back above the threshold on the last one).
  - the SAME micro-cap sourcing as today's scalping mode -- deliberately NOT
    the mid/large-cap (50M-1B$) filter also proposed in the same spec
    (operator's explicit scoping decision, 08/01): these 5 variants test
    SIGNALS, not a different market-cap universe.

Each variant differs on: entry signal, initial stop-loss, initial take-
profit target. Deliberate simplification, stated honestly rather than
silently: position MANAGEMENT after entry (trailing stop, TP-tier ladder,
stagnation timeout) reuses paper_trader.py's existing generic engine as-is
for all 5 variants -- V3's "exit on %K >= 85" and V5's "no fixed TP, exit on
reversal" are therefore approximated via the initial target/invalidation
feeding that generic ATR-adaptive trailing-stop/TP-ladder machinery, not a
bespoke per-variant exit loop (a 5x reimplementation of exit management would
be a disproportionate chantier for a first comparative test -- revisit if
the comparison shows this approximation actually matters).

Deterministic, no LLM call -- consistent with the spec's own intent (a fast,
mechanical scalping engine, "ARIA must be first") and with how today's
RSI-divergence scalping direct-buy path already works (mode == "scalping"
skips the LLM confirmation entirely when R/R and alignment are strong)."""
from __future__ import annotations

import logging
import time

from aria_core import momentum_entry
from aria_core.chasing_filter_shadow import (
    RECENT_LOW_WINDOW_BOLLINGER_VWAP,
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
    contract: str, chain: str,
) -> tuple["momentum_entry.PairSnapshot | None", list[Candle], dict | None]:
    """Shared plumbing for all 5 variants: hard gates (same as the RSI
    scalping mode, mode="scalping") then real 15-30min candles. Returns
    ``(None, [], hold_dict)`` on any hard rejection or missing data --
    caller returns that dict as-is, never guesses a signal without data.

    08/01 -- cached (see _gates_cache's own comment): the 5 variants are
    evaluated on the SAME candidate independently, this used to mean 5x the
    network calls for identical data."""
    key = (contract.lower(), (chain or "").lower())
    now = time.monotonic()
    cached = _gates_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    result = await _gates_and_candles_uncached(contract, chain)
    _prune_gates_cache(now)
    _gates_cache[key] = (now + _GATES_CACHE_TTL_SECONDS, result)
    return result


async def _gates_and_candles_uncached(
    contract: str, chain: str,
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
    # 10-candle average, floor $2,500 on the triggering candle) -- a signal
    # that CAN react on the same candle the bounce forms on, unlike a lagging
    # moving average. Same 3-state doctrine reused as-is: "not_confirmed"
    # (real data, bounce not backed by capital) rejects; "unknown" (no real
    # per-candle volume on this data source, e.g. a synthesis fallback) never
    # rejects -- same fail-open doctrine as everywhere else in this pipeline,
    # never confusing "this source doesn't provide this data" with "this
    # signal is false". align_score itself is UNCHANGED as an
    # informational/sizing signal (still computed and propagated in
    # _buy_result below, still feeds risk_guard's conviction sizing) -- only
    # its use as a HARD GATE here is removed.
    volume_status, volume_reason, _rvol = momentum_entry._check_volume_confirmation(candles)
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
    volume_status, _volume_reason, rvol_multiple = momentum_entry._check_volume_confirmation(candles)
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


# ── V1 -- Pure Bollinger (%B) ────────────────────────────────────────────────
# Entrée : %B <= 0 puis confirmation de sortie (%B repasse > 0). Stop = 1.5xATR.
# TP = ratio fixe 1:2.

_V1_OVERSOLD_THRESHOLD = 0.0
_V1_STOP_ATR_MULT = 1.5
_V1_TP_RR_RATIO = 2.0


async def evaluate_v1_bollinger(contract: str, chain: str) -> dict | None:
    pair, candles, hold = await _gates_and_candles(contract, chain)
    if hold is not None:
        return hold
    if pair is None:
        return None
    closes = [c.close for c in candles]
    percent_b = indicators.bollinger_percent_b(closes)
    if percent_b[-1] is None or percent_b[-2] is None:
        return _hold(chain, pair.base_symbol, pair.price_usd, "%B non calculable (warmup)", "indicator_unavailable")
    was_oversold = percent_b[-2] <= _V1_OVERSOLD_THRESHOLD
    confirmed_exit = percent_b[-1] > _V1_OVERSOLD_THRESHOLD
    if not (was_oversold and confirmed_exit):
        return _hold(
            chain, pair.base_symbol, pair.price_usd,
            f"pas de sortie de survente confirmée (%B={percent_b[-1]:.2f})", "no_signal",
        )
    entry = pair.price_usd
    atr = _atr_value(candles)
    if not atr or atr <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "ATR non calculable", "indicator_unavailable")
    stop = entry - _V1_STOP_ATR_MULT * atr
    if stop <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "stop ATR invalide (<=0)", "invalid_stop")
    target = entry + _V1_TP_RR_RATIO * (entry - stop)
    return _buy_result(
        pair=pair, chain=chain, contract=contract, entry=entry, stop=stop, target=target,
        reason=f"sortie de survente %B confirmée ({percent_b[-2]:.2f} -> {percent_b[-1]:.2f})",
        variant="V1 Bollinger",
    candles=candles, recent_low_window=RECENT_LOW_WINDOW_BOLLINGER_VWAP,
    )


# ── V2 -- VWAP Z-score "institutionnel" ──────────────────────────────────────
# Entrée : Z-score VWAP <= -2.5 puis confirmation de sortie. Stop = 1.5xATR.
# TP = ratio fixe 1:1.5 (sortie plus rapide, conviction volume).

_V2_OVERSOLD_ZSCORE = -2.5
_V2_STOP_ATR_MULT = 1.5
_V2_TP_RR_RATIO = 1.5


async def evaluate_v2_vwap_institutional(contract: str, chain: str) -> dict | None:
    pair, candles, hold = await _gates_and_candles(contract, chain)
    if hold is not None:
        return hold
    if pair is None:
        return None
    zscore = indicators.vwap_zscore_series(candles)
    if zscore[-1] is None or zscore[-2] is None:
        return _hold(
            chain, pair.base_symbol, pair.price_usd, "Z-score VWAP non calculable (warmup)", "indicator_unavailable",
        )
    was_oversold = zscore[-2] <= _V2_OVERSOLD_ZSCORE
    confirmed_exit = zscore[-1] > _V2_OVERSOLD_ZSCORE
    if not (was_oversold and confirmed_exit):
        return _hold(
            chain, pair.base_symbol, pair.price_usd,
            f"pas de sortie de survente VWAP confirmée (Z={zscore[-1]:.2f})", "no_signal",
        )
    entry = pair.price_usd
    atr = _atr_value(candles)
    if not atr or atr <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "ATR non calculable", "indicator_unavailable")
    stop = entry - _V2_STOP_ATR_MULT * atr
    if stop <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "stop ATR invalide (<=0)", "invalid_stop")
    target = entry + _V2_TP_RR_RATIO * (entry - stop)
    return _buy_result(
        pair=pair, chain=chain, contract=contract, entry=entry, stop=stop, target=target,
        reason=f"sortie de survente VWAP confirmée (Z={zscore[-2]:.2f} -> {zscore[-1]:.2f})",
        variant="V2 VWAP institutionnel",
    candles=candles, recent_low_window=RECENT_LOW_WINDOW_BOLLINGER_VWAP,
    )


# ── V3 -- Stochastique rapide, ultra-réactif ─────────────────────────────────
# Entrée : %K <= 15 puis confirmation de sortie. Stop = plus bas de la bougie
# précédente - 0.5% (structurel, pas ATR). TP dynamique visé (%K >= 85) --
# approximé ici par un target initial au ratio 1:2 (voir docstring du module :
# la gestion de sortie post-entrée reste le moteur générique existant).

_V3_OVERSOLD_K = 15.0
_V3_STOP_SLIPPAGE_PCT = 0.005
_V3_TP_RR_RATIO = 2.0  # approximation du "sort sur %K>=85", voir docstring module


async def evaluate_v3_stochastic(contract: str, chain: str) -> dict | None:
    pair, candles, hold = await _gates_and_candles(contract, chain)
    if hold is not None:
        return hold
    if pair is None:
        return None
    k = indicators.stochastic_k_series(candles)
    if k[-1] is None or k[-2] is None:
        return _hold(
            chain, pair.base_symbol, pair.price_usd, "%K non calculable (warmup)", "indicator_unavailable",
        )
    was_oversold = k[-2] <= _V3_OVERSOLD_K
    confirmed_exit = k[-1] > _V3_OVERSOLD_K
    if not (was_oversold and confirmed_exit):
        return _hold(chain, pair.base_symbol, pair.price_usd, f"pas de sortie confirmée (%K={k[-1]:.1f})", "no_signal")
    entry = pair.price_usd
    previous_low = candles[-2].low
    stop = previous_low * (1.0 - _V3_STOP_SLIPPAGE_PCT)
    if stop <= 0 or stop >= entry:
        return _hold(chain, pair.base_symbol, pair.price_usd, "stop structurel invalide", "invalid_stop")
    target = entry + _V3_TP_RR_RATIO * (entry - stop)
    return _buy_result(
        pair=pair, chain=chain, contract=contract, entry=entry, stop=stop, target=target,
        reason=f"sortie de survente %K confirmée ({k[-2]:.1f} -> {k[-1]:.1f})",
        variant="V3 Stochastique ultra-réactif",
    candles=candles, recent_low_window=RECENT_LOW_WINDOW_STOCHASTIC,
    )


# ── V4 -- Combo sec (%B ET %K) ───────────────────────────────────────────────
# Entrée : LES DEUX conditions réunies (double confirmation) -- moins de
# trades, taux de réussite visé maximal. Stop = 2xATR (large). TP = ratio 1:1.3
# (relevé le 08/02, voir le commentaire ci-dessous -- 1:1 était mathématiquement
# invendable).

_V4_STOP_ATR_MULT = 2.0
# 08/02 -- vrai bug critique trouvé en direct (workflow d'audit + contre-
# vérification adversariale, feu vert opérateur) : à 1.0 exactement, ce ratio
# était EXACTEMENT égal à risk_guard.PRICE_IMPACT_MIN_RR (1.0) -- une preuve
# algébrique montre que cap_alloc_to_price_impact retourne alors TOUJOURS 0.0,
# quelle que soit la liquidité ou la volatilité, dès que le frais de swap
# scalping (1%, apply_swap_fee=True) est appliqué : `target_degraded_entry`
# se simplifie exactement à `entry_price`, tandis que `fee_adjusted_entry` est
# strictement supérieur (`entry_price*1.01`) -- la condition de rejet immédiat
# `target_degraded_entry <= fee_adjusted_entry` est donc TOUJOURS vraie.
# Confirmé en données réelles de prod : 7/7 signaux BUY de V4 rejetés
# (`buy_refused`) depuis sa création, ZÉRO position jamais ouverte en 17h30+.
# Relevé à 1.3 (au-dessus du plancher, y compris du nouveau plancher scalping
# PRICE_IMPACT_MIN_RR_SCALPING=0.5 introduit le même jour) -- reste le ratio
# le plus conservateur des 5 variantes (V1=2.0, V2=1.5, V3 structurel), cohérent
# avec la conception "double confirmation, taux de réussite visé maximal".
_V4_TP_RR_RATIO = 1.3


async def evaluate_v4_combo(contract: str, chain: str) -> dict | None:
    pair, candles, hold = await _gates_and_candles(contract, chain)
    if hold is not None:
        return hold
    if pair is None:
        return None
    closes = [c.close for c in candles]
    percent_b = indicators.bollinger_percent_b(closes)
    k = indicators.stochastic_k_series(candles)
    if any(v is None for v in (percent_b[-1], percent_b[-2], k[-1], k[-2])):
        return _hold(
            chain, pair.base_symbol, pair.price_usd, "indicateurs non calculables (warmup)", "indicator_unavailable",
        )
    bollinger_signal = percent_b[-2] <= _V1_OVERSOLD_THRESHOLD and percent_b[-1] > _V1_OVERSOLD_THRESHOLD
    stochastic_signal = k[-2] <= _V3_OVERSOLD_K and k[-1] > _V3_OVERSOLD_K
    if not (bollinger_signal and stochastic_signal):
        return _hold(
            chain, pair.base_symbol, pair.price_usd,
            f"double confirmation absente (%B={percent_b[-1]:.2f}, %K={k[-1]:.1f})", "no_signal",
        )
    entry = pair.price_usd
    atr = _atr_value(candles)
    if not atr or atr <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "ATR non calculable", "indicator_unavailable")
    stop = entry - _V4_STOP_ATR_MULT * atr
    if stop <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "stop ATR invalide (<=0)", "invalid_stop")
    target = entry + _V4_TP_RR_RATIO * (entry - stop)
    return _buy_result(
        pair=pair, chain=chain, contract=contract, entry=entry, stop=stop, target=target,
        reason=f"double confirmation Bollinger+Stochastique (%B {percent_b[-2]:.2f}->{percent_b[-1]:.2f}, "
               f"%K {k[-2]:.1f}->{k[-1]:.1f})",
        variant="V4 Combo sec",
    candles=candles, recent_low_window=RECENT_LOW_WINDOW_BOLLINGER_VWAP,
    )


# ── V5 -- VWAP + stop suiveur, pas de TP fixe ────────────────────────────────
# Entrée : même signal que V2 (Z-score VWAP). Sortie : stop suiveur pur
# (réutilise le moteur ATR-adaptatif générique déjà en place), jamais de TP
# fixe -- vise à maximiser le potentiel sur une grosse bougie. ``target=None``
# ici (le champ existe pour rester compatible avec le format sig, mais aucun
# palier de TP fixe n'est calculé -- la gestion générique retombe alors sur
# le stop suiveur ATR seul, voir paper_trader.py's ``_effective_tp_stages``
# fallback quand ``target_price`` est absent).

_V5_STOP_ATR_MULT = 1.5

# 08/02 -- real bug found (adversarial cross-review workflow): V5's rr was
# always None (no fixed TP by design), which made risk_guard's conviction
# sizing treat it as "no signal supplied at all" and always grant the
# MAXIMUM allocation (5%/$50k), on every single buy, with zero risk
# discrimination -- see _buy_result's own comment on sizing_rr for the full
# explanation. Set to V2's OWN TP_RR_RATIO (1.5), not an arbitrary number:
# V5 shares the exact same entry signal and stop width as V2 (same Z-score
# VWAP oversold-bounce trigger, same _STOP_ATR_MULT=1.5), it only differs on
# the EXIT (V2 takes profit at a fixed level, V5 lets the ATR trailing stop
# run) -- 1.5 is the R/R V5's setup would have measured at entry if it had a
# TP, same as its sister variant. Below MODERATE_RR_THRESHOLD (2.0,
# risk_guard.py), so V5 can never reach the STRONG/MODERATE conviction
# tiers this way -- deliberate: a design with no measurable upside target
# has no real basis to claim a higher conviction tier, this only stops it
# from defaulting to the WORST case (unconditional MAX) while still letting
# the ATR-based sizing discriminate by this token's real volatility.
_V5_SIZING_RR = _V2_TP_RR_RATIO


async def evaluate_v5_vwap_trailing(contract: str, chain: str) -> dict | None:
    pair, candles, hold = await _gates_and_candles(contract, chain)
    if hold is not None:
        return hold
    if pair is None:
        return None
    zscore = indicators.vwap_zscore_series(candles)
    if zscore[-1] is None or zscore[-2] is None:
        return _hold(
            chain, pair.base_symbol, pair.price_usd, "Z-score VWAP non calculable (warmup)", "indicator_unavailable",
        )
    was_oversold = zscore[-2] <= _V2_OVERSOLD_ZSCORE
    confirmed_exit = zscore[-1] > _V2_OVERSOLD_ZSCORE
    if not (was_oversold and confirmed_exit):
        return _hold(
            chain, pair.base_symbol, pair.price_usd,
            f"pas de sortie de survente VWAP confirmée (Z={zscore[-1]:.2f})", "no_signal",
        )
    entry = pair.price_usd
    atr = _atr_value(candles)
    if not atr or atr <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "ATR non calculable", "indicator_unavailable")
    stop = entry - _V5_STOP_ATR_MULT * atr
    if stop <= 0:
        return _hold(chain, pair.base_symbol, pair.price_usd, "stop ATR invalide (<=0)", "invalid_stop")
    return _buy_result(
        pair=pair, chain=chain, contract=contract, entry=entry, stop=stop, target=None,
        reason=f"sortie de survente VWAP confirmée (Z={zscore[-2]:.2f} -> {zscore[-1]:.2f}), sans TP fixe",
        variant="V5 VWAP trailing",
    candles=candles, sizing_rr=_V5_SIZING_RR, recent_low_window=RECENT_LOW_WINDOW_BOLLINGER_VWAP,
    )


VARIANT_ANALYZERS = {
    "scalping_v1": evaluate_v1_bollinger,
    "scalping_v2": evaluate_v2_vwap_institutional,
    "scalping_v3": evaluate_v3_stochastic,
    "scalping_v4": evaluate_v4_combo,
    "scalping_v5": evaluate_v5_vwap_trailing,
}
