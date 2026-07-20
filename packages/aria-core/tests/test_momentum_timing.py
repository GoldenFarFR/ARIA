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
