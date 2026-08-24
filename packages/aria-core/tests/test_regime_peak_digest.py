"""Periodic 30-min regime-peak digest (24/08) -- record_peak/recent_trend
history plus build_regime_peak_digest's message shape, never raises into
the caller."""
from __future__ import annotations

import pytest

from aria_core import regime_peak_digest as digest


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "DB_PATH", str(tmp_path / "shadow.db"))
    digest._ensured_db_paths.clear()
    await digest._ensure_table()
    yield
    digest._ensured_db_paths.clear()


async def test_record_and_read_back_a_single_reading():
    await digest.record_peak(
        "solana", {"median_peak_pct": 14.2, "threshold_pct": 25.0, "open": False, "samples": 30}
    )
    trend = await digest.recent_trend("solana")
    assert trend == [14.2]


async def test_trend_keeps_only_the_last_n_points_oldest_first():
    for value in [10.0, 14.2, 17.6, 25.4]:
        await digest.record_peak(
            "solana", {"median_peak_pct": value, "threshold_pct": 25.0, "open": False, "samples": 30}
        )
    trend = await digest.recent_trend("solana", limit=3)
    assert trend == [14.2, 17.6, 25.4]


async def test_trend_skips_null_readings_below_the_regime_window():
    await digest.record_peak(
        "solana", {"median_peak_pct": None, "threshold_pct": 25.0, "open": True, "samples": 5}
    )
    await digest.record_peak(
        "solana", {"median_peak_pct": 20.0, "threshold_pct": 25.0, "open": False, "samples": 30}
    )
    trend = await digest.recent_trend("solana")
    assert trend == [20.0]


async def test_trend_is_per_chain_independent():
    await digest.record_peak(
        "solana", {"median_peak_pct": 10.0, "threshold_pct": 25.0, "open": False, "samples": 30}
    )
    await digest.record_peak(
        "base", {"median_peak_pct": 40.0, "threshold_pct": 25.0, "open": True, "samples": 30}
    )
    assert await digest.recent_trend("solana") == [10.0]
    assert await digest.recent_trend("base") == [40.0]


async def test_build_regime_peak_digest_covers_all_three_chains(monkeypatch):
    async def _fake_state(median, threshold, is_open, samples):
        return {"median_peak_pct": median, "threshold_pct": threshold, "open": is_open, "samples": samples}

    import aria_core.solana_late_bonding_shadow as solana_mod
    import aria_core.robinhood_pump_shadow as rh_mod
    import aria_core.base_momentum_shadow as base_mod

    monkeypatch.setattr(solana_mod, "regime_state", lambda: _fake_state(14.2, 25.0, False, 30))
    monkeypatch.setattr(rh_mod, "regime_state", lambda: _fake_state(30.0, 25.0, True, 54))
    monkeypatch.setattr(base_mod, "regime_state", lambda: _fake_state(None, 25.0, True, 10))

    text = await digest.build_regime_peak_digest()

    assert "Solana" in text and "14.2%" in text and "🔒" in text
    assert "Robinhood" in text and "30.0%" in text and "✅" in text
    assert "Base" in text and "pas assez de données" in text


async def test_build_regime_peak_digest_handles_a_disarmed_threshold_with_a_real_median(monkeypatch):
    """24/08 real incident: base_momentum_shadow's REGIME_MIN_MEDIAN_PEAK_PCT
    can be None (disarmed) while its own sensor still has a real median --
    f"{None:.0f}" crashed the whole heartbeat tick every cycle on deploy.
    Locks in the fix, never a hand-typed threshold value."""
    async def _fake_state(median, threshold, is_open, samples):
        return {"median_peak_pct": median, "threshold_pct": threshold, "open": is_open, "samples": samples}

    import aria_core.solana_late_bonding_shadow as solana_mod
    import aria_core.robinhood_pump_shadow as rh_mod
    import aria_core.base_momentum_shadow as base_mod

    monkeypatch.setattr(solana_mod, "regime_state", lambda: _fake_state(14.2, 25.0, False, 30))
    monkeypatch.setattr(rh_mod, "regime_state", lambda: _fake_state(30.0, 25.0, True, 54))
    monkeypatch.setattr(base_mod, "regime_state", lambda: _fake_state(26.5, None, True, 3266))

    text = await digest.build_regime_peak_digest()  # must not raise

    assert "Base" in text and "26.5%" in text and "désarmé" in text


async def test_build_regime_peak_digest_shows_a_trend_after_multiple_cycles(monkeypatch):
    values = iter([10.0, 14.2, 17.6, 25.4])

    async def _fake_solana_state():
        v = next(values)
        return {"median_peak_pct": v, "threshold_pct": 25.0, "open": v >= 25.0, "samples": 30}

    async def _fake_open_state():
        return {"median_peak_pct": 40.0, "threshold_pct": 25.0, "open": True, "samples": 30}

    import aria_core.solana_late_bonding_shadow as solana_mod
    import aria_core.robinhood_pump_shadow as rh_mod
    import aria_core.base_momentum_shadow as base_mod

    monkeypatch.setattr(solana_mod, "regime_state", _fake_solana_state)
    monkeypatch.setattr(rh_mod, "regime_state", _fake_open_state)
    monkeypatch.setattr(base_mod, "regime_state", _fake_open_state)

    for _ in range(4):
        text = await digest.build_regime_peak_digest()

    assert "14.2->17.6->25.4" in text
