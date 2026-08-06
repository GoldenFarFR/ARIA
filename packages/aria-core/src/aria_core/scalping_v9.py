"""Scalping_v9 -- fixed-watchlist RSI+MFI synchronized-oversold engine
(06/08, full operator spec, every parameter operator-provided from their own
manual simulation on the SPX 5-min chart -- never re-derived or "improved"
here without an explicit operator decision):

  - WATCHLIST of fixed tokens (SPX first; the operator will add ~4 more) --
    never the momentum discovery stream, no liquidity/volume/other floor.
    The GoPlus honeypot check stays (CLAUDE.md absolute: the one hard
    guardrail, never weakened for a new pocket).
  - TIMEFRAME: 5-minute candles ONLY (mode="scalping_5m" OHLCV ladder,
    single rung, no coarser fallback).
  - ENTRY: RSI(18) < 21 AND MFI(10) < 20 on the SAME closed candle --
    "quand les deux en même temps le sont". One candle below on its own, or
    the two dipping 2-3 candles apart, is explicitly NOT a signal. One buy
    per synchronized episode (re-arms once at least one indicator closes
    back above its limit); buy immediately on detection, no confirmation
    wait ("achat sans analyse... il faut être rapide, le signal est rare").
  - SIZING: every buy = 3% of the wallet's REMAINING cash. Positions stack
    (several concurrent SPX positions are legitimate, one per episode) --
    the allow_multiple seam in paper_trader.open_position exists for this.
  - FILL SIMULATION: buy at spot * (1 + 0.3% fee + 1% impact); sell at
    spot * (1 - 0.3% - 1%), symmetric (operator-confirmed). Modeled HERE
    explicitly -- positions are opened mode="standard" with
    pool_liquidity_usd=None so paper_trader's own scalping fee/impact
    machinery never double-counts.
  - EXIT: flat -5% trailing stop from the SPOT high-water mark, the ONLY
    exit (no TP, no overbought exit, no stagnation timeout -- operator
    choice) + the standard weekly reset (V9_WALLET rides
    all_pocket_wallets(), the heartbeat weekly loop covers it).
    The generic position-management loop explicitly SKIPS this wallet
    (see _run_paper_cycle_locked) -- this module is the single manager.

Deterministic, no LLM call. Own 5-min heartbeat cycle (scalping_v9_cycle)
-- API cost is negligible by design: 1 DexScreener pair fetch + 1
GeckoTerminal OHLCV call per watchlist token per 5 min."""
from __future__ import annotations

import logging

from aria_core import paper_trader

logger = logging.getLogger(__name__)

# Watchlist -- operator-provided. Append new entries here (contract, chain,
# label); no other code change needed to feed more tokens to the engine.
V9_WATCHLIST: tuple[dict, ...] = (
    {
        "contract": "0x50dA645f148798F68EF2d7dB7C1CB22A6819bb2C",
        "chain": "base",
        "symbol": "SPX",
    },
)

# Operator-charted indicator settings (TradingView screenshots, 06/08):
# RSI Length 18 (raw value -- the SMA smoothing line is unchecked on the
# chart, so the smoothed series is deliberately NOT what the limit applies
# to), LowerLimit 21. MFI Length 10, LowerLimit 20.
RSI_PERIOD = 18
RSI_LOWER_LIMIT = 21.0
MFI_PERIOD = 10
MFI_LOWER_LIMIT = 20.0

BUY_PCT_OF_REMAINING_CASH = 0.03
TRAIL_STOP_PCT = 0.05
# "le prix d'achat c'est le prix net + 0.3% + 1% pour simuler le price
# impact" -- 0.3% pool fee + 1% impact, applied symmetrically on the sell.
SWAP_FEE_PCT = 0.003
PRICE_IMPACT_PCT = 0.01
_TOTAL_FEE_PCT = SWAP_FEE_PCT + PRICE_IMPACT_PCT

# Warmup: RSI(18) needs 19 closes, MFI(10) needs 11 candles -- generous
# margin so a thin history degrades to "no signal" rather than a
# partially-warmed read (same doctrine as scalping_variants).
_MIN_CANDLES_FOR_SIGNAL = 40

# Don't open dust: below this allocation the paper position teaches nothing.
_MIN_ALLOC_USD = 10.0

# One-buy-per-episode dedup across cycles: the transition is detected on the
# last 1-2 closed candles (2, not 1: a cycle firing just before a candle
# close would otherwise permanently miss the transition it was about to
# see). In-memory marker of the last bought episode per contract (transition
# candle ts); belt-and-suspenders on restart: a fresh open position less
# than _EPISODE_GUARD_SECONDS old on the same contract also blocks a re-buy.
_last_buy_episode_ts: dict[str, float] = {}
_EPISODE_GUARD_SECONDS = 15 * 60.0


def _both_below(rsi_v: float | None, mfi_v: float | None) -> bool | None:
    """None while either indicator is still warming up -- never a guess."""
    if rsi_v is None or mfi_v is None:
        return None
    return rsi_v < RSI_LOWER_LIMIT and mfi_v < MFI_LOWER_LIMIT


def _find_entry_transition(rsi: list, mfi: list) -> int | None:
    """Index (into the candle list) of a fresh synchronized-oversold
    TRANSITION on one of the last 2 closed candles: both below now, NOT both
    below on the candle before. Returns None when there is no fresh episode
    -- including the both-below-for-a-while case (episode already bought or
    already stale, per the one-buy-per-episode spec)."""
    n = len(rsi)
    for idx in (n - 1, n - 2):
        if idx < 1:
            continue
        now = _both_below(rsi[idx], mfi[idx])
        prev = _both_below(rsi[idx - 1], mfi[idx - 1])
        if now is True and prev is False:
            # a transition at n-2 only counts if the episode is still live
            # on the last candle (still both below) -- "be fast" never means
            # buying an episode that already ended.
            if idx == n - 2 and _both_below(rsi[-1], mfi[-1]) is not True:
                return None
            return idx
    return None


def _degraded_buy_price(spot: float) -> float:
    return spot * (1.0 + _TOTAL_FEE_PCT)


def _degraded_sell_price(spot: float) -> float:
    return spot * (1.0 - _TOTAL_FEE_PCT)


async def _manage_positions(contract: str, spot: float, notifier=None) -> list[dict]:
    """Flat -5% trailing stop from the SPOT high-water mark -- the pocket's
    ONLY exit. High water lives on SPOT (chart) prices, never the degraded
    fill (same doctrine as the 08/05 high-water-vs-fill fix in
    paper_trader)."""
    closed: list[dict] = []
    contract_lower = contract.lower()
    for p in await paper_trader.get_open_positions(wallet=paper_trader.V9_WALLET):
        if (p.get("contract") or "").lower() != contract_lower:
            continue
        high_water = max(p.get("high_water_price") or 0.0, spot)
        if high_water > (p.get("high_water_price") or 0.0):
            await paper_trader._update_high_water(p["id"], high_water)
        if spot <= high_water * (1.0 - TRAIL_STOP_PCT):
            result = await paper_trader.close_position(
                contract,
                _degraded_sell_price(spot),
                position_id=p["id"],
                reason="trailing -5% (v9)",
                notes=(
                    f"spot {spot:.6g} <= plus haut {high_water:.6g} -5% ; "
                    f"sortie simulée -{_TOTAL_FEE_PCT * 100:.1f}% (frais+impact)"
                ),
            )
            if result:
                closed.append(result)
                if notifier is not None:
                    try:
                        await notifier(paper_trader.format_sell_alert(result))
                    except Exception as exc:  # noqa: BLE001 -- alert must never block the cycle
                        logger.info("scalping_v9: sell alert failed (%s)", exc)
    return closed


async def _recent_position_guard(contract: str) -> bool:
    """True when an open v9 position on this contract is younger than the
    episode guard -- restart-safe belt against a double buy on the SAME
    still-running episode (the in-memory marker dies with the process)."""
    from datetime import datetime, timezone

    contract_lower = contract.lower()
    for p in await paper_trader.get_open_positions(wallet=paper_trader.V9_WALLET):
        if (p.get("contract") or "").lower() != contract_lower:
            continue
        opened_at = p.get("opened_at")
        if not opened_at:
            continue
        try:
            opened = datetime.fromisoformat(opened_at)
            age = (datetime.now(timezone.utc) - opened).total_seconds()
        except ValueError:
            continue
        if age < _EPISODE_GUARD_SECONDS:
            return True
    return False


async def run_v9_cycle(*, notifier=None) -> dict:
    """One full pass over the watchlist: manage open positions (trailing)
    then evaluate the entry signal. Called by the heartbeat every 5 min
    (scalping_v9_cycle); double-gated there (ARIA_PAPER_TRADING_ENABLED +
    ARIA_SCALPING_V9_ENABLED), re-checked here (defence in depth)."""
    from aria_core import paper_pause

    actions: dict = {"opened": [], "closed": [], "checked": 0, "holds": []}
    if not paper_trader.scalping_v9_enabled() or paper_pause.is_paused():
        return actions

    from aria_core import momentum_entry
    from aria_core.services.geckoterminal import geckoterminal_client
    from aria_core.skills import indicators
    from aria_core.skills.entry_signals import rsi_series

    for token in V9_WATCHLIST:
        contract, chain, label = token["contract"], token["chain"], token["symbol"]
        actions["checked"] += 1
        try:
            pair = await paper_trader._default_pair_lookup(contract, chain=chain)
        except Exception as exc:  # noqa: BLE001 -- one token's failure never blocks the rest
            logger.info("scalping_v9[%s]: pair lookup failed (%s)", label, exc)
            continue
        if pair is None or not pair.price_usd or pair.price_usd <= 0:
            actions["holds"].append({"symbol": label, "reason": "no_liquid_pair"})
            continue
        spot = pair.price_usd

        actions["closed"].extend(await _manage_positions(contract, spot, notifier=notifier))

        try:
            ohlcv = await geckoterminal_client.get_ohlcv(
                pair.pair_address, network=chain, mode="scalping_5m",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("scalping_v9[%s]: OHLCV failed (%s)", label, exc)
            continue
        if not ohlcv.available or not ohlcv.candles:
            actions["holds"].append({"symbol": label, "reason": "ohlcv_unavailable"})
            continue
        # Last candle is the one still forming (standard real-time OHLCV
        # behavior) -- never compute the signal on an unclosed candle, same
        # centralized-trim doctrine as scalping_variants.
        candles = ohlcv.candles[:-1]
        if len(candles) < _MIN_CANDLES_FOR_SIGNAL:
            actions["holds"].append({"symbol": label, "reason": "insufficient_candles"})
            continue

        rsi = rsi_series([c.close for c in candles], period=RSI_PERIOD)
        mfi = indicators.mfi_series(candles, period=MFI_PERIOD)
        transition_idx = _find_entry_transition(rsi, mfi)
        if transition_idx is None:
            actions["holds"].append({"symbol": label, "reason": "no_signal"})
            continue

        transition_ts = float(getattr(candles[transition_idx], "ts", 0) or 0)
        if _last_buy_episode_ts.get(contract.lower()) == transition_ts:
            actions["holds"].append({"symbol": label, "reason": "episode_already_bought"})
            continue
        if await _recent_position_guard(contract):
            actions["holds"].append({"symbol": label, "reason": "episode_guard_recent_position"})
            continue

        # The one hard guardrail (CLAUDE.md absolute) -- fail-closed.
        clear, hp_reason, _hp_code = await momentum_entry._check_honeypot(
            contract, chain, liquidity_usd=pair.liquidity_usd,
        )
        if not clear:
            actions["holds"].append({"symbol": label, "reason": f"honeypot:{hp_reason}"})
            logger.info("scalping_v9[%s]: honeypot gate refused (%s)", label, hp_reason)
            continue

        cash = await paper_trader.cash_available(wallet=paper_trader.V9_WALLET)
        alloc = cash * BUY_PCT_OF_REMAINING_CASH
        if alloc < _MIN_ALLOC_USD:
            actions["holds"].append({"symbol": label, "reason": "insufficient_cash"})
            continue

        rsi_shown = rsi[transition_idx]
        mfi_shown = mfi[transition_idx]
        pos = await paper_trader.open_position(
            contract,
            pair.base_symbol or label,
            _degraded_buy_price(spot),
            wallet=paper_trader.V9_WALLET,
            alloc_usd=alloc,
            invalidation_price=spot * (1.0 - TRAIL_STOP_PCT),
            chain=chain,
            mode="standard",
            strategy="momentum",
            discovery_channel="v9_watchlist",
            entry_market_cap_usd=pair.market_cap_usd,
            allow_multiple=True,
            thesis=(
                f"[V9] Survente synchronisée sur bougie 5min : RSI(18)="
                f"{rsi_shown:.1f} < {RSI_LOWER_LIMIT:.0f} ET MFI(10)={mfi_shown:.1f} "
                f"< {MFI_LOWER_LIMIT:.0f} en même temps. Achat immédiat 3% du cash "
                f"restant, sortie unique : stop suiveur -5% du plus haut spot."
            ),
        )
        if pos is None:
            actions["holds"].append({"symbol": label, "reason": "open_refused"})
            continue
        # Trailing runs on SPOT ("prix net réel sur le graphique") -- reseed
        # the high-water at spot, not the degraded fill open_position stored.
        await paper_trader._update_high_water(pos["id"], spot)
        _last_buy_episode_ts[contract.lower()] = transition_ts
        actions["opened"].append(pos)
        if notifier is not None:
            try:
                await notifier(paper_trader.format_buy_alert(pos))
            except Exception as exc:  # noqa: BLE001 -- alert must never block the cycle
                logger.info("scalping_v9: buy alert failed (%s)", exc)

    return actions
