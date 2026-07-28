"""momentum_timing.py (20/07, revue croisée externe) -- source unique pour la
confirmation temporelle 75s, partagée par paper_trader.py et momentum_entry.py.
Avant ce module, les deux fichiers avaient chacun leur propre copie de la valeur --
rien ne garantissait qu'elles restent égales si l'une changeait sans l'autre."""
from __future__ import annotations

from aria_core import momentum_entry, momentum_timing, paper_trader


def test_shared_constant_value():
    assert momentum_timing.MOMENTUM_CONFIRMATION_SECONDS == 75.0


def test_paper_trader_sources_from_shared_module():
    assert (
        paper_trader.HIGH_WATER_CONFIRMATION_SECONDS
        is momentum_timing.MOMENTUM_CONFIRMATION_SECONDS
    )


def test_momentum_entry_sources_from_shared_module():
    assert (
        momentum_entry._WASH_TRADING_CONFIRMATION_SECONDS
        is momentum_timing.MOMENTUM_CONFIRMATION_SECONDS
    )


def test_both_modules_agree_with_each_other():
    """Le test qui aurait échoué avant ce correctif si quelqu'un avait changé une
    des deux copies sans l'autre -- désormais structurellement impossible."""
    assert (
        paper_trader.HIGH_WATER_CONFIRMATION_SECONDS
        == momentum_entry._WASH_TRADING_CONFIRMATION_SECONDS
    )


# -- Item #128, 28/07: cross-path evaluation dedup --------------------------

def setup_function(_fn):
    momentum_timing._recent_evaluations.clear()


def test_recently_evaluated_action_none_when_never_recorded():
    assert momentum_timing.recently_evaluated_action("0xabc", "base") is None


def test_record_then_recall_within_window_returns_the_action():
    momentum_timing.record_evaluation("0xAbC", "base", "HOLD", now=1000.0)
    assert momentum_timing.recently_evaluated_action("0xabc", "base", now=1000.0 + 60) == "HOLD"


def test_recall_is_case_insensitive_on_contract_and_chain():
    momentum_timing.record_evaluation("0xAbCdEf", "BASE", "BUY", now=1000.0)
    assert momentum_timing.recently_evaluated_action("0xabcdef", "base", now=1001.0) == "BUY"


def test_recall_returns_none_once_the_window_elapses():
    momentum_timing.record_evaluation("0xabc", "base", "HOLD", now=1000.0)
    window = momentum_timing._RECENT_EVALUATION_WINDOW_SECONDS
    assert momentum_timing.recently_evaluated_action("0xabc", "base", now=1000.0 + window - 1) == "HOLD"
    assert momentum_timing.recently_evaluated_action("0xabc", "base", now=1000.0 + window) is None


def test_recall_does_not_confuse_different_contracts_or_chains():
    momentum_timing.record_evaluation("0xabc", "base", "HOLD", now=1000.0)
    assert momentum_timing.recently_evaluated_action("0xdef", "base", now=1000.0) is None
    assert momentum_timing.recently_evaluated_action("0xabc", "ethereum", now=1000.0) is None


def test_record_overwrites_the_previous_verdict_for_the_same_key():
    momentum_timing.record_evaluation("0xabc", "base", "HOLD", now=1000.0)
    momentum_timing.record_evaluation("0xabc", "base", "BUY", now=1001.0)
    assert momentum_timing.recently_evaluated_action("0xabc", "base", now=1002.0) == "BUY"


def test_record_with_none_action_is_indistinguishable_from_never_evaluated():
    """Deliberate, not a gap: ``evaluate_momentum_entry`` returns ``None`` when
    it bailed out early on missing price data (before honeypot/OHLCV/LLM ever
    ran) -- there's little expensive work to protect in that case, and the
    missing data may resolve moments later (a brand-new pool), so this case
    is allowed to retry rather than being silently skipped by the periodic
    discovery. ``recently_evaluated_action`` therefore returns ``None`` here
    too, same as "never evaluated" -- ``_add_candidate`` only ever checks
    ``is not None`` on this return value, so a None-action record never
    blocks a rescan."""
    momentum_timing.record_evaluation("0xabc", "base", None, now=1000.0)
    assert momentum_timing.recently_evaluated_action("0xabc", "base", now=1000.0 + 1) is None


def test_purge_evicts_expired_entries_on_the_next_write():
    momentum_timing.record_evaluation("0xold", "base", "HOLD", now=1000.0)
    window = momentum_timing._RECENT_EVALUATION_WINDOW_SECONDS
    momentum_timing.record_evaluation("0xnew", "base", "HOLD", now=1000.0 + window + 1)
    assert ("0xold", "base") not in momentum_timing._recent_evaluations
    assert ("0xnew", "base") in momentum_timing._recent_evaluations
