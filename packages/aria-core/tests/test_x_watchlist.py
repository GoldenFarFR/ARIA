from aria_core.knowledge.x_watchlist import (
    all_curiosity_handles,
    operator_watch_handles,
    vc_watch_handles,
)


def test_operator_watch_handles():
    handles = operator_watch_handles()
    assert "GoldenFarFR" in handles
    assert "solvrbot" in handles
    assert "grok" in handles
    assert "aixbt_agent" in handles


def test_all_curiosity_handles_deduped():
    handles = all_curiosity_handles()
    lower = [h.lower() for h in handles]
    assert len(lower) == len(set(lower))
    assert "solvrbot" in handles
    assert "Aria_ZHC" in handles


def test_vc_watch_handles():
    handles = vc_watch_handles()
    assert "a16zcrypto" in handles
    assert "paradigm" in handles
    assert "dragonfly_xyz" in handles


def test_vc_handles_included_in_all_curiosity_handles():
    handles = {h.lower() for h in all_curiosity_handles()}
    assert "a16zcrypto" in handles
    assert "paradigm" in handles