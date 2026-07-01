from aria_core.knowledge.x_watchlist import all_curiosity_handles, operator_watch_handles


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