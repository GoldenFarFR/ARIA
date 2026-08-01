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
    if len(candles) < _MIN_CANDLES_FOR_SIGNAL:
        return None, [], _hold(
            chain, best_pair.base_symbol, best_pair.price_usd,
            f"historique de bougies insuffisant ({len(candles)} < {_MIN_CANDLES_FOR_SIGNAL})",
            "insufficient_candle_history",
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
    *, pair, chain: str, entry: float, stop: float, target: float | None, reason: str, variant: str,
) -> dict:
    entry_atr_pct = None
    return {
        "action": "BUY", "chain": chain, "symbol": pair.base_symbol, "price": entry,
        "target": target, "invalidation": stop,
        "rr": _rr(entry, stop, target) if target is not None else None,
        "mode": "scalping", "strategy": "momentum",
        "reasons": [f"[{variant}] {reason}"],
        "liquidity_usd": pair.liquidity_usd, "entry_atr_pct": entry_atr_pct,
        # 08/01 -- market cap at entry, same purely-observational field as the
        # standard momentum pipeline (see momentum_entry.py's own comment).
        "market_cap_usd": pair.market_cap_usd,
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
        pair=pair, chain=chain, entry=entry, stop=stop, target=target,
        reason=f"sortie de survente %B confirmée ({percent_b[-2]:.2f} -> {percent_b[-1]:.2f})",
        variant="V1 Bollinger",
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
        pair=pair, chain=chain, entry=entry, stop=stop, target=target,
        reason=f"sortie de survente VWAP confirmée (Z={zscore[-2]:.2f} -> {zscore[-1]:.2f})",
        variant="V2 VWAP institutionnel",
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
        pair=pair, chain=chain, entry=entry, stop=stop, target=target,
        reason=f"sortie de survente %K confirmée ({k[-2]:.1f} -> {k[-1]:.1f})",
        variant="V3 Stochastique ultra-réactif",
    )


# ── V4 -- Combo sec (%B ET %K) ───────────────────────────────────────────────
# Entrée : LES DEUX conditions réunies (double confirmation) -- moins de
# trades, taux de réussite visé maximal. Stop = 2xATR (large). TP = ratio 1:1
# (conservateur).

_V4_STOP_ATR_MULT = 2.0
_V4_TP_RR_RATIO = 1.0


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
        pair=pair, chain=chain, entry=entry, stop=stop, target=target,
        reason=f"double confirmation Bollinger+Stochastique (%B {percent_b[-2]:.2f}->{percent_b[-1]:.2f}, "
               f"%K {k[-2]:.1f}->{k[-1]:.1f})",
        variant="V4 Combo sec",
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
        pair=pair, chain=chain, entry=entry, stop=stop, target=None,
        reason=f"sortie de survente VWAP confirmée (Z={zscore[-2]:.2f} -> {zscore[-1]:.2f}), sans TP fixe",
        variant="V5 VWAP trailing",
    )


VARIANT_ANALYZERS = {
    "scalping_v1": evaluate_v1_bollinger,
    "scalping_v2": evaluate_v2_vwap_institutional,
    "scalping_v3": evaluate_v3_stochastic,
    "scalping_v4": evaluate_v4_combo,
    "scalping_v5": evaluate_v5_vwap_trailing,
}
