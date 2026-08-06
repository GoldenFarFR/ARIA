"""scalping_v9 -- fixed-watchlist RSI+MFI synchronized-oversold engine
(06/08, full operator spec). Offline, no real network call: pair lookup,
OHLCV, honeypot and indicators are mocked to exercise the engine's OWN
signal/episode/sizing/trailing logic in isolation."""
from __future__ import annotations

import asyncio

import pytest

from aria_core import paper_trader as pt
from aria_core import scalping_v9 as v9
from aria_core.skills import indicators
from aria_core.skills.ta_levels import Candle

SPX = v9.V9_WATCHLIST[0]["contract"]


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_episode_memory():
    v9._last_buy_episode_ts.clear()
    yield
    v9._last_buy_episode_ts.clear()


def _flat_candles(n=60, price=1.0, volume=1000.0):
    return [
        Candle(ts=float(i), open=price, high=price * 1.01, low=price * 0.99,
               close=price, volume=volume)
        for i in range(n)
    ]


class _FakePair:
    def __init__(self, price=1.0, liquidity=500_000.0):
        self.pair_address = "0xpool"
        self.price_usd = price
        self.base_symbol = "SPX"
        self.liquidity_usd = liquidity
        self.market_cap_usd = 300_000_000.0


class _FakeOhlcv:
    def __init__(self, candles):
        self.candles = candles
        self.available = bool(candles)
        self.error = None


def _patch_cycle_io(
    monkeypatch, *, spot=1.0, candles=None, rsi=None, mfi=None, honeypot_clear=True,
):
    """Wires every external dependency of run_v9_cycle to offline fakes.
    ``rsi``/``mfi`` are the series AFTER the still-forming-candle trim --
    the fake series are padded by one so the trim lands exactly on them."""
    from aria_core import momentum_entry
    from aria_core.services import geckoterminal
    from aria_core.skills import entry_signals

    candles = candles if candles is not None else _flat_candles()

    async def fake_pair_lookup(contract, *, chain="base"):
        return _FakePair(price=spot)

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)

    async def fake_get_ohlcv(pool, *, network="base", mode="standard", **kw):
        assert mode == "scalping_5m"  # 5-min candles ONLY, operator spec
        # +1 candle: run_v9_cycle trims the still-forming last one
        return _FakeOhlcv(candles + candles[-1:])

    monkeypatch.setattr(
        geckoterminal.geckoterminal_client, "get_ohlcv", fake_get_ohlcv,
    )

    if rsi is not None:
        monkeypatch.setattr(
            entry_signals, "rsi_series", lambda closes, period=14: rsi,
        )
    if mfi is not None:
        monkeypatch.setattr(indicators, "mfi_series", lambda c, *, period=10: mfi)

    async def fake_honeypot(contract, chain, *, liquidity_usd=None, volume_24h_usd=None):
        if honeypot_clear:
            return True, "", ""
        return False, "honeypot confirmé", "honeypot_confirmed"

    monkeypatch.setattr(momentum_entry, "_check_honeypot", fake_honeypot)


def _signal_series(n=60):
    """RSI/MFI series with a fresh synchronized transition on the LAST
    closed candle: both above their limits everywhere, both below at [-1]."""
    rsi = [50.0] * n
    mfi = [50.0] * n
    rsi[-1] = 15.0
    mfi[-1] = 10.0
    return rsi, mfi


# ── MFI indicator ────────────────────────────────────────────────────────────

def test_mfi_pure_inflow_reads_100():
    up = [
        Candle(ts=i, open=1 + i * 0.01, high=1.05 + i * 0.01, low=0.95 + i * 0.01,
               close=1 + i * 0.01, volume=100.0)
        for i in range(15)
    ]
    assert indicators.mfi_series(up, period=10)[-1] == 100.0


def test_mfi_pure_outflow_reads_0():
    down = [
        Candle(ts=i, open=2 - i * 0.01, high=2.05 - i * 0.01, low=1.95 - i * 0.01,
               close=2 - i * 0.01, volume=100.0)
        for i in range(15)
    ]
    assert indicators.mfi_series(down, period=10)[-1] == 0.0


def test_mfi_warmup_is_none():
    assert indicators.mfi_series(_flat_candles(10), period=10) == [None] * 10


def test_mfi_mixed_flows_between_bounds():
    candles = []
    price = 1.0
    for i in range(30):
        price *= 1.02 if i % 2 == 0 else 0.99
        candles.append(Candle(ts=i, open=price, high=price * 1.01,
                              low=price * 0.99, close=price, volume=100.0 + i))
    val = indicators.mfi_series(candles, period=10)[-1]
    assert val is not None and 0.0 < val < 100.0


# ── entry transition (one buy per synchronized episode) ──────────────────────

def test_transition_requires_both_below_simultaneously():
    # RSI below alone -- never a signal ("1 seul des deux... il faut pas acheter")
    rsi = [50.0] * 10
    mfi = [50.0] * 10
    rsi[-1] = 15.0
    assert v9._find_entry_transition(rsi, mfi) is None


def test_transition_rejects_desynchronized_dips():
    # RSI dips at [-2], MFI only at [-1] -- "à deux ou 3 bougies près... on
    # n'achète pas": at no candle are BOTH below at the same time.
    rsi = [50.0] * 10
    mfi = [50.0] * 10
    rsi[-2] = 15.0
    mfi[-1] = 10.0
    assert v9._find_entry_transition(rsi, mfi) is None


def test_transition_fires_on_synchronized_candle():
    rsi, mfi = _signal_series(10)
    assert v9._find_entry_transition(rsi, mfi) == 9


def test_transition_still_fresh_one_candle_late():
    # Transition at [-2], still both-below at [-1]: a cycle that fired just
    # after the close must still catch it (episode live, "il faut être rapide").
    rsi = [50.0] * 10
    mfi = [50.0] * 10
    rsi[-2] = rsi[-1] = 15.0
    mfi[-2] = mfi[-1] = 10.0
    assert v9._find_entry_transition(rsi, mfi) == 8


def test_transition_none_when_episode_already_running():
    # Both below for the whole window -- no FRESH transition, the episode was
    # already bought (or predates the window): one buy per episode.
    rsi = [15.0] * 10
    mfi = [10.0] * 10
    assert v9._find_entry_transition(rsi, mfi) is None


def test_transition_none_during_warmup():
    rsi = [None] * 9 + [15.0]
    mfi = [None] * 9 + [10.0]
    assert v9._find_entry_transition(rsi, mfi) is None


# ── run_v9_cycle -- entry side ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gate_off_is_a_complete_noop(tmp_db, monkeypatch):
    monkeypatch.delenv("ARIA_SCALPING_V9_ENABLED", raising=False)
    called = False

    async def fake_pair_lookup(contract, *, chain="base"):
        nonlocal called
        called = True

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    actions = await v9.run_v9_cycle()
    assert actions == {"opened": [], "closed": [], "checked": 0, "holds": []}
    assert called is False


@pytest.mark.asyncio
async def test_buy_on_synchronized_signal_full_spec(tmp_db, monkeypatch):
    """The whole operator spec in one pass: fill at spot +1.3%, alloc 3% of
    remaining cash, high-water reseeded at SPOT, thesis traces both values."""
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    rsi, mfi = _signal_series()
    _patch_cycle_io(monkeypatch, spot=2.0, rsi=rsi, mfi=mfi)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert len(actions["opened"]) == 1
    pos = (await pt.get_open_positions(wallet=pt.V9_WALLET))[0]
    assert pos["entry_price"] == pytest.approx(2.0 * 1.013)
    assert pos["cost_usd"] == pytest.approx(30_000.0)  # 3% of 1M
    assert pos["high_water_price"] == pytest.approx(2.0)  # SPOT, never the fill
    assert pos["mode"] == "standard"  # fees modeled by v9 itself, never doubled
    assert "RSI(18)" in (pos["thesis"] or "") and "MFI(10)" in (pos["thesis"] or "")


@pytest.mark.asyncio
async def test_second_buy_sizes_on_remaining_cash(tmp_db, monkeypatch):
    """3% of REMAINING cash, and stacking on the same contract is legal
    (one position per episode)."""
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    rsi, mfi = _signal_series()
    _patch_cycle_io(monkeypatch, spot=1.0, rsi=rsi, mfi=mfi)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    await v9.run_v9_cycle()
    # new, later episode: distinct transition ts + guard disarmed
    v9._last_buy_episode_ts.clear()

    async def no_guard(contract):
        return False

    monkeypatch.setattr(v9, "_recent_position_guard", no_guard)
    await v9.run_v9_cycle()

    positions = await pt.get_open_positions(wallet=pt.V9_WALLET)
    assert len(positions) == 2  # stacked on the SAME contract
    # second alloc = 3% of (1M - 30k)
    assert positions[1]["cost_usd"] == pytest.approx(0.03 * (1_000_000.0 - 30_000.0))


@pytest.mark.asyncio
async def test_same_episode_never_bought_twice(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    rsi, mfi = _signal_series()
    _patch_cycle_io(monkeypatch, spot=1.0, rsi=rsi, mfi=mfi)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    first = await v9.run_v9_cycle()
    second = await v9.run_v9_cycle()

    assert len(first["opened"]) == 1
    assert second["opened"] == []
    assert any(h["reason"] == "episode_already_bought" for h in second["holds"])


@pytest.mark.asyncio
async def test_honeypot_gate_fail_closed(tmp_db, monkeypatch):
    """The one hard guardrail (CLAUDE.md absolute) is never waived even
    though every other floor (liquidity/volume) is deliberately absent."""
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    rsi, mfi = _signal_series()
    _patch_cycle_io(monkeypatch, rsi=rsi, mfi=mfi, honeypot_clear=False)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert actions["opened"] == []
    assert any(h["reason"].startswith("honeypot:") for h in actions["holds"])


@pytest.mark.asyncio
async def test_no_signal_no_buy(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    _patch_cycle_io(monkeypatch, rsi=[50.0] * 60, mfi=[50.0] * 60)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert actions["opened"] == []
    assert any(h["reason"] == "no_signal" for h in actions["holds"])


# ── run_v9_cycle -- exit side (flat -5% trailing on SPOT) ────────────────────

@pytest.mark.asyncio
async def test_trailing_advances_high_water_then_closes(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)
    pos = await pt.open_position(
        SPX, "SPX", 1.013, wallet=pt.V9_WALLET, alloc_usd=30_000,
        mode="standard", allow_multiple=True,
    )
    await pt._update_high_water(pos["id"], 1.0)

    # price rallies to 1.20 -- high water follows, nothing closes
    _patch_cycle_io(monkeypatch, spot=1.20, rsi=[50.0] * 60, mfi=[50.0] * 60)
    actions = await v9.run_v9_cycle()
    assert actions["closed"] == []
    p = (await pt.get_open_positions(wallet=pt.V9_WALLET))[0]
    assert p["high_water_price"] == pytest.approx(1.20)

    # drops to exactly -5% from the peak -- closes at spot -1.3%
    _patch_cycle_io(monkeypatch, spot=1.20 * 0.95, rsi=[50.0] * 60, mfi=[50.0] * 60)
    actions = await v9.run_v9_cycle()
    assert len(actions["closed"]) == 1
    closed = actions["closed"][0]
    assert closed["close_reason"] == "trailing -5% (v9)"
    assert closed["exit_price"] == pytest.approx(1.20 * 0.95 * (1 - 0.013))
    assert await pt.get_open_positions(wallet=pt.V9_WALLET) == []


@pytest.mark.asyncio
async def test_small_dip_never_closes(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)
    pos = await pt.open_position(
        SPX, "SPX", 1.013, wallet=pt.V9_WALLET, alloc_usd=30_000,
        mode="standard", allow_multiple=True,
    )
    await pt._update_high_water(pos["id"], 1.0)

    _patch_cycle_io(monkeypatch, spot=0.96, rsi=[50.0] * 60, mfi=[50.0] * 60)
    actions = await v9.run_v9_cycle()
    assert actions["closed"] == []
    assert len(await pt.get_open_positions(wallet=pt.V9_WALLET)) == 1


# ── integration seams ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generic_cycle_never_manages_v9_positions(tmp_db, monkeypatch):
    """The generic loop's ATR-trail/TP/stagnation machinery must never touch
    a v9 position -- v9's own cycle is the single manager (operator spec:
    -5% trailing is the ONLY exit)."""
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)
    await pt.open_position(
        SPX, "SPX", 1.0, wallet=pt.V9_WALLET, alloc_usd=30_000,
        mode="standard", allow_multiple=True,
    )

    price_calls: list[str] = []

    async def price_lookup(contract):
        price_calls.append(contract)
        return 0.01  # -99% -- would trigger EVERY generic exit if managed

    actions = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)

    assert actions["closed"] == []
    assert price_calls == []  # never even priced by the generic loop
    assert len(await pt.get_open_positions(wallet=pt.V9_WALLET)) == 1


def test_all_pocket_wallets_includes_v9_when_gate_on(monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    assert pt.V9_WALLET in pt.all_pocket_wallets()
    monkeypatch.delenv("ARIA_SCALPING_V9_ENABLED")
    assert pt.V9_WALLET not in pt.all_pocket_wallets()


def test_v9_is_a_scalping_pocket():
    assert pt.is_scalping_pocket(pt.V9_WALLET) is True
    assert pt.uses_fine_rsi_confirmation(pt.V9_WALLET) is False


@pytest.mark.asyncio
async def test_open_position_allow_multiple_seam(tmp_db):
    """Without allow_multiple, the single-position-per-contract refusal is
    byte-for-byte unchanged for every existing caller."""
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)
    first = await pt.open_position(
        SPX, "SPX", 1.0, wallet=pt.V9_WALLET, alloc_usd=10_000,
    )
    blocked = await pt.open_position(
        SPX, "SPX", 1.0, wallet=pt.V9_WALLET, alloc_usd=10_000,
    )
    allowed = await pt.open_position(
        SPX, "SPX", 1.0, wallet=pt.V9_WALLET, alloc_usd=10_000,
        allow_multiple=True,
    )
    assert first is not None
    assert blocked is None
    assert allowed is not None


def test_watchlist_extensible_shape():
    """The operator will add ~4 more contracts -- every entry must carry the
    3 keys the engine reads, nothing engine-side to change."""
    for token in v9.V9_WATCHLIST:
        assert set(token) == {"contract", "chain", "symbol"}
