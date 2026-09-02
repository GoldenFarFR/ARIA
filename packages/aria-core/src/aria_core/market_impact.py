"""Constant-product price impact and DEX fee -- the shared measurement brick.

**Why this file exists (02/09).** This function was written inside a shadow
pocket and then copied, character for character, into EIGHT of them. That was an
architectural mistake from the start: it measures nothing about a strategy, it
measures what a REAL trade of a given size would achieve against a pool of a
given depth. It is infrastructure, not a pocket's business logic, and pockets
come and go while the question "what would this have actually cost" does not.

Operator's framing of the sort that produced this move: keep what MEASURES,
discard what DECIDES. This measures.

**What was deliberately NOT carried over.** Each pocket also held
``SIMULATED_TRADE_SIZE_USD = 0.1`` -- ten cents, an operator decision of 17/08 to
make more Solana signals reachable. That constant is the reason a pool holding
four dollars could report an "executable" x4, and it stays behind: the size is
the CALLER's question, never this module's default. There is no default size here
on purpose.

**Honest limit, restated here so no caller can miss it.** ``reserve_usd`` is a
PROXY for depth, not depth. On concentrated-liquidity pools the depth available
near the current price is <= total reserve, often far below, so this function is
OPTIMISTIC: it cannot reject a trade wrongly, only let through one that is in
reality unexecutable. Everything it rejects is certainly rejectable, and every
number it returns is an upper bound on the truth.

``reserve_usd`` is the pool's TOTAL liquidity, both sides combined; ``depth =
reserve_usd / 2`` approximates one side for a roughly balanced pool. A "size at
most 5% of one side" rule is therefore 2.5% of ``reserve_usd``.
"""
from __future__ import annotations

# Per-chain DEX fee, in percent. Sourced 17/08 against the real launchpads, never
# assumed: Robinhood Chain's pools.trade charges 0.25% (Uniswap v4 base) and
# Robinpad 1% (Uniswap v3 LP), so 1.0% is the conservative middle; Solana's
# pump.fun-family pools measured at 1.25%. Base derives its own from
# _BASE_DEX_SWAP_FEE_FRACTION. A caller passing an explicit fee overrides these.
DEX_FEE_PCT_BY_CHAIN: dict[str, float] = {
    "solana": 1.25,
    "robinhood": 1.0,
}
_FALLBACK_FEE_PCT = 1.25  # the most conservative of the measured ones


def fee_pct_for(chain: str) -> float:
    """Fee for a chain, falling back to the most conservative measured value
    rather than to zero -- an unknown chain must never look cheaper than a
    known one."""
    return DEX_FEE_PCT_BY_CHAIN.get(chain, _FALLBACK_FEE_PCT)


def apply_price_impact_and_fee(
    price: float,
    *,
    trade_size_usd: float,
    reserve_usd: float | None,
    side: str,
    fee_pct: float,
) -> float | None:
    """Price a REAL trade of ``trade_size_usd`` would achieve against a pool
    holding ``reserve_usd``, constant-product approximation.

    Returns ``None`` -- never a fabricated number -- when the pool is too shallow
    to absorb the size at all (``depth <= trade_size_usd``, where the price would
    move toward infinity). That refusal is the point: it is what lets a caller
    distinguish "this trade would have lost money" from "this trade could not
    have happened", two things a single number would conflate.

    ``side="buy"`` raises the effective price paid, ``side="sell"`` lowers the
    price received, and the fee is applied in the same direction in both cases:
    a buyer pays more, a seller receives less.
    """
    if reserve_usd is None or reserve_usd <= 0:
        return None
    depth = reserve_usd / 2.0
    if depth <= trade_size_usd:
        return None
    if side == "buy":
        impacted = price * (depth + trade_size_usd) / depth
        return impacted * (1 + fee_pct / 100.0)
    impacted = price * depth / (depth + trade_size_usd)
    return impacted * (1 - fee_pct / 100.0)
