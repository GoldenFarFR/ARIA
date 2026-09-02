"""REQ-0006: a price ratio is not a return a real position could have taken.

The two cases that motivated this module must fall out MECHANICALLY -- no rule
may name them. A $4 pool cannot fund a $2 position, and a x1972 cannot be
extracted from a pool of that size whatever the entry looked like.
"""
from __future__ import annotations

import pytest

from aria_core import executability_replay as er
from aria_core.solana_pump_shadow import _apply_price_impact_and_fee as impact

pytestmark = pytest.mark.asyncio


def _row(entry_price: float, reserve: float, mult: float, exit_reserve=None) -> dict:
    r = {"entry_price": entry_price, "reserve_usd": reserve, "final_multiplier": mult}
    if exit_reserve is not None:
        r["last_reserve_usd"] = exit_reserve
    return r


async def test_four_dollar_pool_is_rejected_at_every_real_size():
    """The real row: x4 booked against a pool holding four dollars.

    Production books it as a winner because it simulates a TEN CENT trade. At the
    smallest size ARIA can actually place ($2, the Robinhood per-trade cap), the
    position is half the pool. It must be rejected on entry, at every rung.
    """
    row = _row(entry_price=1e-6, reserve=4.0, mult=4.0)
    for size in er.DEFAULT_SIZES_USD:
        mult, reason = er.replay_row(row, size, impact)
        assert mult is None, f"a $4 pool absorbed a ${size:.0f} position"
        assert reason == er.REJ_ENTRY_TOO_DEEP

    # And the ten-cent question production actually asks does pass -- which is
    # precisely why the headline number is meaningless, not why it is a bug.
    mult, reason = er.replay_row(row, 0.1, impact)
    assert mult is not None and reason is None


async def test_x1972_passes_entry_and_dies_on_the_exit():
    """The exit is what disqualifies it, and the distinction matters.

    Entry reserve $8,434, so a $25 position is 0.30% of the pool and enters
    comfortably. Exiting x1972 on that $25 means extracting ~$49,300 from the
    same pool. A model that only checked the entry would call this executable.
    """
    row = _row(entry_price=8.33e-05, reserve=8434.0, mult=1972.0)
    entry_only = impact(8.33e-05, trade_size_usd=25.0, reserve_usd=8434.0, side="buy")
    assert entry_only is not None, "entry should pass -- the test is about the exit"

    mult, reason = er.replay_row(row, 25.0, impact)
    assert mult is None
    assert reason == er.REJ_EXIT_TOO_DEEP


async def test_return_degrades_monotonically_with_size():
    """Impact is monotonic in size: a bigger position never gets a better price."""
    row = _row(entry_price=1e-4, reserve=500_000.0, mult=2.0)
    got = []
    for size in (2.0, 25.0, 100.0, 1000.0):
        mult, reason = er.replay_row(row, size, impact)
        assert reason is None
        got.append(mult)
    assert got == sorted(got, reverse=True), f"return rose with size: {got}"


async def test_exit_reserve_is_used_when_the_table_has_one():
    """SPEC-0001 E2 wants depth at BOTH instants. A pool that drained between
    entry and exit must disqualify the exit even though the entry was fine."""
    deep_entry_shallow_exit = _row(
        entry_price=1e-4, reserve=500_000.0, mult=50.0, exit_reserve=10.0,
    )
    mult, reason = er.replay_row(deep_entry_shallow_exit, 25.0, impact)
    assert mult is None and reason == er.REJ_EXIT_TOO_DEEP

    # Same row without the exit-reserve column falls back to the entry reserve
    # and passes -- the substitution is optimistic, which is why `run()` reports
    # whether the column was available at all.
    no_exit_col = _row(entry_price=1e-4, reserve=500_000.0, mult=50.0)
    mult, reason = er.replay_row(no_exit_col, 25.0, impact)
    assert mult is not None and reason is None


async def test_missing_inputs_are_rejected_never_defaulted():
    """unknown != zero. A missing reserve is not a zero reserve."""
    assert er.replay_row({"entry_price": None, "reserve_usd": 10.0,
                          "final_multiplier": 2.0}, 25.0, impact)[1] == er.REJ_NO_ENTRY_PRICE
    assert er.replay_row({"entry_price": 1e-4, "reserve_usd": None,
                          "final_multiplier": 2.0}, 25.0, impact)[1] == er.REJ_NO_RESERVE
    assert er.replay_row({"entry_price": 1e-4, "reserve_usd": 10.0,
                          "final_multiplier": None}, 25.0, impact)[1] == er.REJ_NO_OUTCOME


async def test_impact_comes_from_shared_infrastructure_not_from_a_pocket():
    """Invariant deliberately CHANGED on 02/09, recorded here rather than silently.

    It used to assert the opposite: that the impact function was imported from
    each pocket. That was the defect, not the design -- the same arithmetic sat
    duplicated character-for-character in EIGHT pockets, and this research tool
    could not outlive them. It now comes from `market_impact`, shared
    infrastructure, with the chain's fee passed in since that is the only thing
    that varies.

    The pockets keep their own copies for now, deliberately: removing them would
    touch files the out-of-repo production process imports, and that decision is
    the operator's.
    """
    from aria_core import market_impact

    solana = er._impact_fn("solana_pump_shadow_archive")
    robinhood = er._impact_fn("robinhood_pump_shadow_log")

    # Same input, different chain fee -- proof the fee really is bound per chain.
    args = dict(trade_size_usd=25.0, reserve_usd=500_000.0, side="buy")
    assert solana(1e-4, **args) != robinhood(1e-4, **args)

    # And each matches the shared function called with that chain's fee.
    for fn, chain in ((solana, "solana"), (robinhood, "robinhood")):
        assert fn(1e-4, **args) == market_impact.apply_price_impact_and_fee(
            1e-4, fee_pct=market_impact.fee_pct_for(chain), **args)

    with pytest.raises(ValueError):
        er._impact_fn("some_unregistered_table")
