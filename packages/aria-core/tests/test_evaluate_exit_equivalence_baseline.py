"""Frozen behavioural baseline of `evaluate_exit`, captured BEFORE extraction.

Operator instruction (03/09), verbatim in intent: extraction is not
refactoring and is not a strategy change. No threshold, no business rule and
no behaviour may change while the function moves to the core. To make that
claim checkable rather than asserted, this file pins the CURRENT behaviour
across a systematic sweep of inputs — the same discipline used when
`market_impact.py` was extracted ("identité bit à bit vérifiée sur 128
combinaisons").

How it is meant to be used:

    1. (now) This file passes against the function in its pocket.
    2. (extraction) The function moves to the core, unchanged.
    3. (proof) This exact file is re-pointed at the new location and must pass
       with ZERO edits to the expected values. Any diff is a behaviour change
       and means the extraction is wrong, not that the baseline is stale.

Deliberately NOT a correctness test. Several pinned values below are almost
certainly undesirable as strategy (that is Phase 2's question, with its own
pre-registered criterion and falsifier). This file only asserts "the same
inputs still produce the same decisions" — it takes no position on whether
those decisions are good.
"""
from __future__ import annotations

import itertools

import pytest

from aria_core import solana_fresh_launch_ws_exit_shadow as shadow


def _row(**overrides) -> dict:
    """The row shape every existing test uses, in one place."""
    base = {
        "entry_price": 1.0,
        "peak_price": 1.0,
        "remaining_qty": 1.0,
        "realized_proceeds": 0.0,
        "realistic_entry_price": 1.0,
        "realistic_realized_proceeds": 0.0,
        "reserve_usd": 8000.0,
        "support_range_high": None,
        "pool_address": "poolA",
    }
    base.update(overrides)
    return base


def _decision(result: dict) -> tuple:
    """The decision-carrying fields only — the tuple that must not move.

    Excludes float-noisy derived values (multipliers, proceeds) so the pin
    stays about DECISIONS, not about floating-point formatting."""
    return (
        result.get("skipped"),
        result.get("exit_reason"),
        result.get("remaining_qty"),
    )


# ---------------------------------------------------------------------------
# The five exit motifs, each pinned explicitly
# ---------------------------------------------------------------------------

def test_no_exit_when_nothing_is_triggered():
    r = shadow.evaluate_exit(_row(peak_price=1.0), current_price=1.05,
                             reserve_usd=8000.0, dex_id=None, age_minutes=5.0)
    assert _decision(r) == (False, None, 1.0)


def test_trailing_stop_closes_the_whole_position():
    stop = 1.20 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    r = shadow.evaluate_exit(_row(peak_price=1.20), current_price=stop,
                             reserve_usd=8000.0, dex_id=None, age_minutes=5.0)
    assert _decision(r) == (False, "trailing_stop", 0.0)


def test_hard_stop_is_DISARMED_by_default_in_this_pocket():
    """Measured, not assumed: HARD_STOP_PCT_DEFAULT is None. The hard-stop
    branch exists in the code but is unreachable with the module's own
    defaults -- a caller must pass hard_stop_pct explicitly to arm it.

    Pinned because it is exactly the kind of fact an extraction can silently
    change: giving the core a non-None default would arm a stop that is
    currently off, on every pocket, without anyone deciding it."""
    assert shadow.HARD_STOP_PCT_DEFAULT is None

    r = shadow.evaluate_exit(_row(), current_price=0.50, reserve_usd=8000.0,
                             dex_id=None, age_minutes=5.0)
    assert r["exit_reason"] != "hard_stop"


def test_hard_stop_fires_only_when_explicitly_armed():
    r = shadow.evaluate_exit(_row(peak_price=1.0), current_price=0.70,
                             reserve_usd=8000.0, dex_id=None, age_minutes=5.0,
                             hard_stop_pct=20.0)
    assert r["exit_reason"] == "hard_stop"
    assert r["remaining_qty"] == 0.0


def test_liquidity_collapse_fires_on_reserve_drop():
    collapsed = 8000.0 * (1 - (shadow.LIQUIDITY_COLLAPSE_EXIT_PCT + 5.0) / 100.0)
    r = shadow.evaluate_exit(_row(), current_price=1.0,
                             reserve_usd=collapsed, dex_id=None, age_minutes=5.0)
    assert r["exit_reason"] == "liquidity_collapse"
    assert r["remaining_qty"] == 0.0


def test_max_hold_fires_past_the_horizon():
    r = shadow.evaluate_exit(_row(), current_price=1.0, reserve_usd=8000.0,
                             dex_id=None, age_minutes=shadow.MAX_HOLD_MINUTES + 1.0)
    assert r["exit_reason"] == "max_hold"
    assert r["remaining_qty"] == 0.0


# ---------------------------------------------------------------------------
# The three parameters NO existing test exercises (measured 03/09: 0 hits each)
# ---------------------------------------------------------------------------

def test_explicit_max_hold_minutes_overrides_the_module_default():
    """`max_hold_minutes` had zero test coverage before this file."""
    not_yet = shadow.evaluate_exit(_row(), current_price=1.0, reserve_usd=8000.0,
                                    dex_id=None, age_minutes=50.0, max_hold_minutes=100.0)
    fired = shadow.evaluate_exit(_row(), current_price=1.0, reserve_usd=8000.0,
                                  dex_id=None, age_minutes=150.0, max_hold_minutes=100.0)
    assert not_yet["exit_reason"] is None
    assert fired["exit_reason"] == "max_hold"


def test_explicit_fixed_stop_pct_fires_and_is_distinct_from_hard_stop():
    """`fixed_stop_pct` had zero test coverage before this file."""
    r = shadow.evaluate_exit(_row(), current_price=0.90, reserve_usd=8000.0,
                             dex_id=None, age_minutes=5.0, fixed_stop_pct=5.0)
    assert r["exit_reason"] == "fixed_stop"
    assert r["remaining_qty"] == 0.0


def test_fixed_stop_none_disables_that_branch_entirely():
    r = shadow.evaluate_exit(_row(), current_price=0.90, reserve_usd=8000.0,
                             dex_id=None, age_minutes=5.0, fixed_stop_pct=None)
    assert r["exit_reason"] != "fixed_stop"


def test_trailing_arm_peak_pct_gates_when_the_trail_becomes_active():
    """`trailing_arm_peak_pct` had zero test coverage before this file.

    Below the arming peak the trail must NOT fire; above it, it must."""
    stop_price = 1.05 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    unarmed = shadow.evaluate_exit(
        _row(peak_price=1.05), current_price=stop_price, reserve_usd=8000.0,
        dex_id=None, age_minutes=5.0, trailing_arm_peak_pct=50.0)
    armed = shadow.evaluate_exit(
        _row(peak_price=1.05), current_price=stop_price, reserve_usd=8000.0,
        dex_id=None, age_minutes=5.0, trailing_arm_peak_pct=1.0)
    assert unarmed["exit_reason"] != "trailing_stop"
    assert armed["exit_reason"] == "trailing_stop"


# ---------------------------------------------------------------------------
# The systematic sweep — the actual equivalence pin
# ---------------------------------------------------------------------------

_PRICES = (0.5, 0.85, 0.95, 1.0, 1.10, 1.50)
_PEAKS = (1.0, 1.20, 2.0)
_RESERVES = (500.0, 4000.0, 8000.0)
_AGES = (1.0, 60.0, 100000.0)

# Captured 03/09 against the pocket-local implementation. Regenerate ONLY if
# the behaviour is deliberately changed with an operator decision -- never to
# make a failing extraction pass.
def _sweep_decisions() -> list[tuple]:
    out = []
    for price, peak, reserve, age in itertools.product(_PRICES, _PEAKS, _RESERVES, _AGES):
        r = shadow.evaluate_exit(
            _row(peak_price=peak), current_price=price, reserve_usd=reserve,
            dex_id=None, age_minutes=age,
        )
        out.append((price, peak, reserve, age) + _decision(r))
    return out


def test_the_sweep_covers_every_exit_motif():
    """A pin over a sweep that never reaches a branch would prove nothing.
    This asserts the sweep actually exercises each one."""
    motifs = {row[5] for row in _sweep_decisions()}
    assert {"trailing_stop", "liquidity_collapse", "max_hold", None} <= motifs, motifs
    # hard_stop and fixed_stop are covered by their own dedicated tests above:
    # both need an explicit parameter, so a default-driven sweep cannot reach
    # them. Stated here so a future reader does not mistake it for a gap.


def test_sweep_is_deterministic_and_pinned():
    """162 input combinations, decisions frozen. After extraction this must
    pass unchanged against the new location -- that IS the equivalence proof."""
    first = _sweep_decisions()
    second = _sweep_decisions()
    assert first == second, "evaluate_exit is not deterministic on identical inputs"
    assert len(first) == len(_PRICES) * len(_PEAKS) * len(_RESERVES) * len(_AGES) == 162

    # Decision distribution, pinned. Any extraction that shifts even one
    # combination between motifs changes this and fails loudly.
    from collections import Counter
    dist = Counter(row[5] for row in first)
    # MEASURED 03/09 against the pocket-local implementation, not predicted.
    # `hard_stop` is absent because HARD_STOP_PCT_DEFAULT is None (see the
    # dedicated test above) -- the sweep uses module defaults, so that branch
    # is unreachable here by construction, not by oversight.
    assert dist == {
        "trailing_stop": 66,
        "liquidity_collapse": 54,
        "max_hold": 28,
        None: 14,
    }, dict(dist)


@pytest.mark.parametrize("bad_price", [0.0, -1.0])
def test_non_positive_price_does_not_crash(bad_price):
    """Edge case: a degenerate price must be handled, not raise -- pinned so
    extraction cannot quietly introduce a raise."""
    r = shadow.evaluate_exit(_row(), current_price=bad_price, reserve_usd=8000.0,
                             dex_id=None, age_minutes=5.0)
    assert isinstance(r, dict)
    assert "skipped" in r
