"""Tests du gate de regime chaine (25/08) -- fonction pure de classification,
puis persistance/cycle avec la DB reelle (tmp_path) et DefiLlama mocke."""

from types import SimpleNamespace

import pytest

from aria_core import paths
from aria_core.services import defillama
from aria_core.skills import chain_liquidity_regime as regime


def _flat_series(days: int, value: float) -> list[tuple[int, float]]:
    return [(i * 86_400, value) for i in range(days)]


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "aria.db")
    monkeypatch.setattr(paths, "aria_db_path", lambda: db_path)
    monkeypatch.setattr(regime, "DB_PATH", db_path)
    return db_path


# --- classify_chain_regime (pure) ---

def test_below_burn_in_reads_insufficient_never_a_guessed_regime():
    tvl = _flat_series(regime.BURN_IN_DAYS - 1, 1_000_000.0)
    volume = _flat_series(regime.BURN_IN_DAYS - 1, 100_000.0)

    got = regime.classify_chain_regime("robinhood", tvl, volume)

    assert got.regime == regime.REGIME_INSUFFICIENT
    assert got.volume_ratio_to_ewma is None


def test_volume_at_baseline_reads_calm():
    days = regime.BURN_IN_DAYS + 10
    tvl = _flat_series(days, 1_000_000.0)
    volume = _flat_series(days, 100_000.0)  # every day identical -> ratio == 1.0

    got = regime.classify_chain_regime("base", tvl, volume)

    assert got.regime == regime.REGIME_CALM
    assert got.volume_ratio_to_ewma == pytest.approx(1.0, abs=1e-6)


def test_volume_spike_with_tvl_holding_reads_healthy_inflow():
    days = regime.BURN_IN_DAYS + 10
    tvl = _flat_series(days, 1_000_000.0)
    volume = _flat_series(days, 100_000.0)
    volume[-1] = (volume[-1][0], 100_000.0 * (regime.INFLOW_RATIO_THRESHOLD + 1.0))

    got = regime.classify_chain_regime("solana", tvl, volume)

    assert got.regime == regime.REGIME_INFLOW
    assert got.tvl_trend_pct == pytest.approx(0.0, abs=1e-6)


def test_volume_spike_with_tvl_collapsing_reads_toxic_spike():
    """The exact case the 3-7 day confirmation window exists to catch: a
    volume spike whose TVL does NOT hold -- pump, not real activity."""
    days = regime.BURN_IN_DAYS + 10
    tvl = _flat_series(days, 1_000_000.0)
    tvl[-1] = (tvl[-1][0], 1_000_000.0 * (1 + regime.TOXIC_TVL_DROP_PCT / 100.0) * 0.5)
    volume = _flat_series(days, 100_000.0)
    volume[-1] = (volume[-1][0], 100_000.0 * (regime.INFLOW_RATIO_THRESHOLD + 1.0))

    got = regime.classify_chain_regime("base", tvl, volume)

    assert got.regime == regime.REGIME_TOXIC_SPIKE
    assert got.tvl_trend_pct is not None and got.tvl_trend_pct <= regime.TOXIC_TVL_DROP_PCT


# --- persistence ("no expiration", same pattern as market_sentiment) ---

@pytest.mark.asyncio
async def test_upsert_always_overwrites_the_previous_reading():
    first = regime.ChainRegimeReading(
        chain="base", regime=regime.REGIME_CALM, detail="d1",
        volume_ratio_to_ewma=1.0, tvl_trend_pct=0.0, history_days=90,
    )
    second = regime.ChainRegimeReading(
        chain="base", regime=regime.REGIME_INFLOW, detail="d2",
        volume_ratio_to_ewma=2.0, tvl_trend_pct=5.0, history_days=91,
    )

    await regime.upsert_reading(first)
    await regime.upsert_reading(second)
    got = await regime.latest_regime("base")

    assert got["regime"] == regime.REGIME_INFLOW
    assert got["volume_ratio_to_ewma"] == 2.0


@pytest.mark.asyncio
async def test_latest_regime_is_none_before_any_cycle_ran():
    got = await regime.latest_regime("base")

    assert got is None


# --- run_chain_regime_cycle (network mocked) ---

@pytest.mark.asyncio
async def test_cycle_degrades_to_insufficient_on_network_failure(monkeypatch):
    async def _fail_tvl(_chain):
        return defillama.ChainTvlSeries(chain="base", available=False, error="down")

    async def _ok_volume(_chain):
        return defillama.ChainDexVolumeSeries(chain="base", available=True, points=[(1, 1.0)])

    monkeypatch.setattr(defillama, "get_chain_tvl_history", _fail_tvl)
    monkeypatch.setattr(defillama, "get_chain_dex_volume", _ok_volume)

    got = await regime.run_chain_regime_cycle("base")

    assert got.regime == regime.REGIME_INSUFFICIENT
    persisted = await regime.latest_regime("base")
    assert persisted["regime"] == regime.REGIME_INSUFFICIENT


@pytest.mark.asyncio
async def test_cycle_uses_the_capitalized_slug_for_tvl_and_lowercase_for_volume(monkeypatch):
    """25/08 finding wired end to end: historicalChainTvl wants "Robinhood",
    overview/dexs wants lowercase -- get_chain_dex_volume already lowercases
    internally, this only checks the TVL call site."""
    seen_tvl_chain = []

    async def _capture_tvl(chain):
        seen_tvl_chain.append(chain)
        days = regime.BURN_IN_DAYS + 1
        return defillama.ChainTvlSeries(
            chain=chain, available=True, points=_flat_series(days, 1.0),
        )

    async def _ok_volume(_chain):
        days = regime.BURN_IN_DAYS + 1
        return defillama.ChainDexVolumeSeries(
            chain="robinhood", available=True, points=_flat_series(days, 1.0),
        )

    monkeypatch.setattr(defillama, "get_chain_tvl_history", _capture_tvl)
    monkeypatch.setattr(defillama, "get_chain_dex_volume", _ok_volume)

    await regime.run_chain_regime_cycle("robinhood")

    assert seen_tvl_chain == ["Robinhood"]
