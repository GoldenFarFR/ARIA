"""Trade amount sizing for the agent-wallet pilot (#203) -- computes how much
to commit against the real balance, never a made-up value. Executes
nothing: the result is passed to ``agent_wallet_pilot.attempt_swap()``, which
independently re-checks it against the real balance AND ``MAX_TRANSACTION_USD``
before any execution (deliberate double-check, not accidental redundancy --
protects against a balance that changes between sizing and execution).

Operator decision (16/07, #203): 3% of the real balance by default, capped at
``MAX_TRANSACTION_USD``. On the pilot's target balance (10-15$), this produces
an amount on the order of 30-45 cents per trade -- intended, not a problem:
the goal of this specific pilot is an amount with no consequence if something
goes wrong (see docs/pilote-agent-wallet-10usd.md). No environment variable
to override the percentage in this V1 (minimal scope, explicit operator
decision) -- ``pct`` stays a plain Python call parameter.
"""
from __future__ import annotations

from aria_core.agent_wallet_pilot import MAX_TRANSACTION_USD, BalanceFn

DEFAULT_SIZING_PCT = 0.03  # 3% -- explicit operator decision (16/07, #203)


async def size_trade_usd(
    *,
    balance_fn: BalanceFn,
    pct: float = DEFAULT_SIZING_PCT,
    max_usd: float = MAX_TRANSACTION_USD,
) -> float | None:
    """Amount to commit = ``min(real balance * pct, max_usd)``. ``None`` if the
    balance is unavailable, zero or negative, or if ``pct`` is not positive
    (fail-closed, same doctrine as the rest of the module) -- never a made-up
    fallback amount."""
    if pct <= 0:
        return None
    try:
        balance_usd = await balance_fn()
    except Exception:
        return None
    if balance_usd is None or balance_usd <= 0:
        return None
    return min(balance_usd * pct, max_usd)
