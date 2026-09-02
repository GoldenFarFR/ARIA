"""The shared impact brick: it must refuse rather than fabricate, and it must
reproduce, to the bit, what the pockets' eight copies computed.

Why the identity test matters more than it looks: this function was extracted
from a pocket that is scheduled for deletion. If the extraction had drifted by a
rounding, every number `executability_replay` has produced today would silently
stop being comparable to the ones produced before it.
"""
from __future__ import annotations

import pytest

from aria_core import market_impact as mi
from aria_core.solana_pump_shadow import _apply_price_impact_and_fee as pocket_copy

SOLANA_FEE = mi.fee_pct_for("solana")


def test_extraction_is_bit_identical_to_the_pocket_copy():
    """128 combinations of price, size, depth and side. Zero tolerance."""
    for price in (1e-9, 1e-6, 8.33e-05, 0.5, 100.0):
        for size in (0.1, 2.0, 25.0, 1000.0):
            for reserve in (4.0, 8434.0, 500_000.0, None):
                for side in ("buy", "sell"):
                    old = pocket_copy(price, trade_size_usd=size,
                                      reserve_usd=reserve, side=side)
                    new = mi.apply_price_impact_and_fee(
                        price, trade_size_usd=size, reserve_usd=reserve,
                        side=side, fee_pct=SOLANA_FEE)
                    assert old == new, (price, size, reserve, side, old, new)


def test_refuses_rather_than_fabricates_when_depth_cannot_absorb():
    """The refusal IS the feature: it separates 'would have lost money' from
    'could not have happened', which one number would conflate."""
    # A $4 pool against the smallest size ARIA can really place.
    assert mi.apply_price_impact_and_fee(
        1e-6, trade_size_usd=2.0, reserve_usd=4.0, side="buy",
        fee_pct=SOLANA_FEE) is None
    # Unknown depth is unknown, never zero and never optimistic.
    assert mi.apply_price_impact_and_fee(
        1e-6, trade_size_usd=2.0, reserve_usd=None, side="buy",
        fee_pct=SOLANA_FEE) is None
    assert mi.apply_price_impact_and_fee(
        1e-6, trade_size_usd=2.0, reserve_usd=0.0, side="buy",
        fee_pct=SOLANA_FEE) is None


def test_impact_is_monotonic_in_size_and_costs_the_buyer_more():
    prices = [mi.apply_price_impact_and_fee(
        1e-4, trade_size_usd=s, reserve_usd=500_000.0, side="buy",
        fee_pct=SOLANA_FEE) for s in (2.0, 25.0, 100.0, 1000.0)]
    assert prices == sorted(prices), "a bigger buy must never get a better price"

    sells = [mi.apply_price_impact_and_fee(
        1e-4, trade_size_usd=s, reserve_usd=500_000.0, side="sell",
        fee_pct=SOLANA_FEE) for s in (2.0, 25.0, 100.0, 1000.0)]
    assert sells == sorted(sells, reverse=True), "a bigger sell must never get more"

    buy = mi.apply_price_impact_and_fee(1e-4, trade_size_usd=25.0,
                                        reserve_usd=500_000.0, side="buy",
                                        fee_pct=SOLANA_FEE)
    sell = mi.apply_price_impact_and_fee(1e-4, trade_size_usd=25.0,
                                         reserve_usd=500_000.0, side="sell",
                                         fee_pct=SOLANA_FEE)
    assert buy > 1e-4 > sell, "the round trip must cost, never pay"


def test_unknown_chain_falls_back_to_the_most_conservative_fee():
    """An unlisted chain must never look CHEAPER than a measured one -- that
    would make an unknown market seem more attractive than a known one."""
    known = [mi.fee_pct_for(c) for c in mi.DEX_FEE_PCT_BY_CHAIN]
    assert mi.fee_pct_for("some-new-chain") >= max(known)


def test_no_default_trade_size_exists_in_this_module():
    """The pockets each carried SIMULATED_TRADE_SIZE_USD = 0.1 -- ten cents,
    which is why a $4 pool could report an 'executable' x4. The size is the
    caller's question; a default here would silently restore that defect."""
    import inspect
    import re
    src = inspect.getsource(mi)
    # An ASSIGNMENT, never a mention: the module docstring explains at length why
    # that constant is absent, and grepping the raw text would flag its own
    # explanation. Asserting on prose is how a test starts lying.
    assert not re.search(r"^\s*SIMULATED_TRADE_SIZE\w*\s*=", src, re.M)
    sig = inspect.signature(mi.apply_price_impact_and_fee)
    assert sig.parameters["trade_size_usd"].default is inspect.Parameter.empty
    assert sig.parameters["fee_pct"].default is inspect.Parameter.empty
