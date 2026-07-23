"""Liquidity-rotation signal (07/23, operator request): "on ces small-caps
sans info, l'edge n'est pas les fondamentaux -- c'est de sentir si l'argent
tourne VERS ce token maintenant." A low-info token has nothing to judge on
fundamentals (no team, no product, no audit -- see the AUTONOMOPOLY case), but
every buy/sell and every dollar of volume is fully transparent on-chain. This
module turns that transparency into a deterministic, zero-extra-network-call
signal: is fresh capital rotating IN right now, or is this stale/dying volume?

Pure and DB-free (same doctrine as performance_breakdown.py) -- takes a
``PairSnapshot`` (already fetched by the caller, services/dexscreener.py),
returns a plain dict. Trivially unit-testable, no event loop needed.

DELIBERATELY OBSERVATIONAL ONLY for this first cut (operator's own
"measure before I act" doctrine, same as the whole /performance chantier):
exposed on the momentum signal dict as informational fields, tracked by
/performance, NEVER yet used to gate or size a position. Once enough real
trades accumulate a real correlation to winrate/PnL can be measured -- wiring
it into the decision is a SEPARATE, later step, once proven."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LiquidityRotation:
    """Everything needed to explain the rotation score to a human -- never an
    opaque number alone."""
    score: float                       # 0-10, higher = stronger fresh inflow
    buy_pressure_h1: float | None       # h1 buys / (h1 buys + h1 sells), None if no h1 txns
    buy_pressure_24h: float | None      # h24 buys / (h24 buys + h24 sells), None if no h24 txns
    pressure_accelerating: bool | None  # h1 buy pressure > h24 buy pressure (fresh, not stale)
    volume_acceleration_ratio: float | None  # h1 volume, annualized to a 24h run-rate, / real 24h volume
    reasons: list[str]


def _buy_pressure(buys: int, sells: int) -> float | None:
    total = buys + sells
    if total <= 0:
        return None
    return buys / total


def compute_liquidity_rotation(
    *,
    buys_h1: int, sells_h1: int,
    buys_24h: int, sells_24h: int,
    volume_h1_usd: float, volume_24h_usd: float,
) -> LiquidityRotation:
    """Deterministic, no network call -- every input already comes from the
    SAME DexScreener pair response already fetched for the hard gates
    (``services/dexscreener.PairSnapshot``), never a dedicated extra call.

    Two independent measurements, each individually honest about missing data
    (``None`` rather than a fabricated neutral value):
      - ``pressure_accelerating``: is the LAST HOUR's buy/sell ratio higher
        than the full day's? A token that was net-selling most of the day but
        is being net-bought THIS hour is rotation happening right now -- the
        24h aggregate alone would hide it.
      - ``volume_acceleration_ratio``: if the last hour's volume, run-rated to
        24h (``volume_h1_usd * 24``), would be several times the REAL 24h
        volume, trading has clearly accelerated very recently (a fresh spike,
        not stale activity spread evenly across the day). Capped reporting
        (see below) -- the ratio itself is never clamped, only the derived
        score contribution is bounded, so the raw number stays honest for
        logging/analysis.

    ``score`` (0-10) blends both signals, capped contributions so neither one
    alone can dominate: pressure acceleration contributes up to 5 points
    (proportional to how much h1 pressure exceeds h24, capped at +40
    percentage points of difference = full credit), volume acceleration
    contributes up to 5 points (a 4x+ run-rate vs the real 24h volume = full
    credit). Missing data on one side simply halves the achievable score on
    that side (never inflates the other), never fabricates a value."""
    reasons: list[str] = []
    pressure_h1 = _buy_pressure(buys_h1, sells_h1)
    pressure_24h = _buy_pressure(buys_24h, sells_24h)

    pressure_accelerating: bool | None = None
    pressure_points = 0.0
    if pressure_h1 is not None and pressure_24h is not None:
        delta = pressure_h1 - pressure_24h
        pressure_accelerating = delta > 0
        # +40 percentage points of improvement -> full 5-point credit; a worse
        # h1 pressure than the 24h average contributes 0, never negative.
        pressure_points = max(0.0, min(5.0, (delta / 0.40) * 5.0))
        reasons.append(
            f"pression acheteuse : {pressure_h1:.0%} (1h) vs {pressure_24h:.0%} (24h) -- "
            + ("accélération fraîche" if pressure_accelerating else "pas d'accélération")
        )
    else:
        reasons.append("pression acheteuse non calculable (aucune transaction sur la fenêtre)")

    volume_acceleration_ratio: float | None = None
    volume_points = 0.0
    if volume_24h_usd > 0:
        run_rate_24h = volume_h1_usd * 24.0
        volume_acceleration_ratio = run_rate_24h / volume_24h_usd
        # 4x the real 24h volume (run-rated) -> full 5-point credit.
        volume_points = max(0.0, min(5.0, (volume_acceleration_ratio / 4.0) * 5.0))
        reasons.append(
            f"volume 1h annualisé = {volume_acceleration_ratio:.1f}x le volume 24h réel"
        )
    else:
        reasons.append("volume 24h nul ou indisponible -- accélération non calculable")

    score = round(pressure_points + volume_points, 1)
    return LiquidityRotation(
        score=score,
        buy_pressure_h1=pressure_h1,
        buy_pressure_24h=pressure_24h,
        pressure_accelerating=pressure_accelerating,
        volume_acceleration_ratio=volume_acceleration_ratio,
        reasons=reasons,
    )
