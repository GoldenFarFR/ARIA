"""Shared pure statistics helpers -- no DB, no network, no event loop.

Single source of truth for metrics computed identically for two otherwise
unrelated domains: third-party wallet-scoring (``services/smart_money.py``)
and ARIA's own paper-trading track record (``performance_breakdown.py``).
Neither domain module should import the other just to reuse a formula --
this module is the shared, dependency-free home for that formula instead."""
from __future__ import annotations

import math
from statistics import fmean

DEFAULT_MIN_TRADES_FOR_SORTINO = 5


def sortino_ratio(returns: list[float], *, min_trades: int = DEFAULT_MIN_TRADES_FOR_SORTINO) -> float | None:
    """Sortino-style ratio on a list of per-trade returns (any consistent
    unit -- fraction or percent, the ratio is scale-invariant either way).

    Below ``min_trades``, judged too noisy for an individual track record --
    unavailable, never an unreliable number presented as reliable. No loss
    observed -> ratio undefined (not an artificial infinity)."""
    if len(returns) < min_trades:
        return None
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    downside_deviation = math.sqrt(fmean([r * r for r in downside]))
    if downside_deviation == 0:
        return None
    return fmean(returns) / downside_deviation
