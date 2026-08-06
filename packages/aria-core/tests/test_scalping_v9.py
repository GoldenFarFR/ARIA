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


@pytest.fixture(autouse=True)
def _reset_candles_cache():
    """06/08 -- same trap as ``test_momentum_entry.py``'s own
    ``_reset_candles_cache`` fixture: ``momentum_entry._candles_cache`` is a
    module-level dict keyed by (chain, pool, mode, skip_daily), shared across
    every ``_fetch_candles`` caller -- and every test in this file reuses the
    SAME pool address ("0xpool") and mode ("scalping_5m"), a guaranteed
    collision. Found while writing the provenance-traceability tests below:
    without this reset, a cache hit from an EARLIER test silently skips
    ``_fetch_candles_impl`` (and the provenance tag it sets) entirely on a
    LATER test, serving stale candles from a completely different scenario."""
    from aria_core import momentum_entry

    momentum_entry._candles_cache.clear()
    yield
    momentum_entry._candles_cache.clear()


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


class _FakeMobulaResult:
    def __init__(self, candles):
        self.candles = candles
        self.available = bool(candles)


def _patch_geckoterminal_dead_mobula_alive(monkeypatch, *, mobula_candles=None):
    """Shared wiring for "GeckoTerminal down, Mobula answers" scalping-fallback
    scenarios -- GeckoTerminal returns available=False, Mobula returns real
    15m candles. 61 candles by default: run_v9_cycle trims the still-forming
    last one, landing exactly on ``_signal_series()``'s default n=60 (same
    padding convention as ``_patch_cycle_io`` above -- a bare 60 here caused a
    real, order-dependent ``IndexError`` found while writing the traceability
    tests below: candles[59] out of range once trimmed to 59)."""
    from aria_core import momentum_entry
    from aria_core.services import geckoterminal, mobula

    async def dead_gecko(pool, *, network="base", mode="standard", **kw):
        return _FakeOhlcv([])

    monkeypatch.setattr(geckoterminal.geckoterminal_client, "get_ohlcv", dead_gecko)
    monkeypatch.setattr(momentum_entry, "_provider_in_cooldown", lambda provider: False)
    monkeypatch.setattr(momentum_entry, "_record_provider_outcome", lambda *a, **kw: None)
    monkeypatch.setattr(mobula, "mobula_configured", lambda: True)

    candles = mobula_candles if mobula_candles is not None else _flat_candles(61)

    async def fake_mobula_get_ohlcv(contract, *, blockchain="base", period="15m"):
        return _FakeMobulaResult(candles)

    monkeypatch.setattr(mobula, "get_ohlcv", fake_mobula_get_ohlcv)


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


@pytest.mark.asyncio
async def test_buy_via_mobula_fallback_when_geckoterminal_unavailable(tmp_db, monkeypatch):
    """06/08 operator-confirmed fix: v9 used to call GeckoTerminal directly
    with no fallback (real missed entry, VELVET) -- now goes through
    momentum_entry._fetch_candles's cascade like v8. GeckoTerminal down
    (available=False) must no longer mean "blind for this cycle" as long as
    Mobula has real candles."""
    from aria_core import momentum_entry
    from aria_core.skills import entry_signals

    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")

    async def fake_pair_lookup(contract, *, chain="base"):
        return _FakePair(price=2.0)

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    _patch_geckoterminal_dead_mobula_alive(monkeypatch)

    rsi, mfi = _signal_series()
    monkeypatch.setattr(entry_signals, "rsi_series", lambda closes, period=14: rsi)
    monkeypatch.setattr(indicators, "mfi_series", lambda c, *, period=10: mfi)

    async def fake_honeypot(contract, chain, *, liquidity_usd=None, volume_24h_usd=None):
        return True, "", ""

    monkeypatch.setattr(momentum_entry, "_check_honeypot", fake_honeypot)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert len(actions["opened"]) == 1
    assert not any(h["reason"] == "ohlcv_unavailable" for h in actions["holds"])


# ── full traceability (06/08 operator request) ───────────────────────────────

async def _cycle_log_rows(tmp_path):
    import aiosqlite

    async with aiosqlite.connect(pt.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM v9_cycle_log ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_buy_thesis_includes_data_provenance_annotation(tmp_db, monkeypatch):
    """The BUY thesis must name the real provider/timeframe that served the
    candles -- degraded (fallback, wrong granularity) flagged explicitly,
    never silently presented as if it were the configured 5min GeckoTerminal
    read."""
    from aria_core import momentum_entry
    from aria_core.skills import entry_signals

    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")

    async def fake_pair_lookup(contract, *, chain="base"):
        return _FakePair(price=2.0)

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    _patch_geckoterminal_dead_mobula_alive(monkeypatch)

    rsi, mfi = _signal_series()
    monkeypatch.setattr(entry_signals, "rsi_series", lambda closes, period=14: rsi)
    monkeypatch.setattr(indicators, "mfi_series", lambda c, *, period=10: mfi)

    async def fake_honeypot(contract, chain, *, liquidity_usd=None, volume_24h_usd=None):
        return True, "", ""

    monkeypatch.setattr(momentum_entry, "_check_honeypot", fake_honeypot)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert len(actions["opened"]) == 1
    pos = (await pt.get_open_positions(wallet=pt.V9_WALLET))[0]
    # SPX configured at 5min, Mobula only serves 15m -- must read as degraded.
    assert "[Données : mobula, 15m -- DÉGRADÉ]" in pos["thesis"]


@pytest.mark.asyncio
async def test_buy_thesis_provenance_not_flagged_degraded_on_exact_timeframe(tmp_db, monkeypatch):
    """GeckoTerminal always serves the exact requested granularity -- no
    DÉGRADÉ marker when it's the one that answered."""
    rsi, mfi = _signal_series()
    _patch_cycle_io(monkeypatch, spot=2.0, rsi=rsi, mfi=mfi)
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    await v9.run_v9_cycle()

    pos = (await pt.get_open_positions(wallet=pt.V9_WALLET))[0]
    assert "[Données : geckoterminal, scalping_5m]" in pos["thesis"]
    assert "DÉGRADÉ" not in pos["thesis"]


@pytest.mark.asyncio
async def test_cycle_log_records_every_hold_including_no_signal(tmp_db, monkeypatch):
    """A HOLD cycle where nothing is bought must still leave a row -- the
    whole point of full traceability ('je veut tout savoir si un jour on
    doit comprendre se qui a fonctionner ou non')."""
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    _patch_cycle_io(monkeypatch, rsi=[50.0] * 60, mfi=[50.0] * 60)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert actions["opened"] == []
    rows = await _cycle_log_rows(tmp_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "SPX"
    assert row["action"] == "hold"
    assert row["reason"] == "no_signal"
    assert row["provider"] == "geckoterminal"
    assert row["timeframe_served"] == "scalping_5m"
    assert row["degraded"] == 0
    assert row["rsi_last"] == pytest.approx(50.0)
    assert row["mfi_last"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_cycle_log_records_ohlcv_unavailable_hold_with_no_provider(tmp_db, monkeypatch):
    """Everything down (no fallback answered): provenance is None -- the
    row must record that honestly, never fabricate a provider. DexPaprika/
    Codex explicitly mocked unavailable too -- otherwise this test would
    make a REAL outbound network call (dexpaprika.get_ohlcv has no
    "configured" gate, unlike codex/mobula) instead of exercising the
    intended "nothing answered" path deterministically."""
    from aria_core import momentum_entry
    from aria_core.services import codex, dexpaprika, geckoterminal, mobula

    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")

    async def fake_pair_lookup(contract, *, chain="base"):
        return _FakePair(price=2.0)

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)

    async def dead_gecko(pool, *, network="base", mode="standard", **kw):
        return _FakeOhlcv([])

    async def dead_dexpaprika(pool, *, network="base", mode="standard", **kw):
        return _FakeOhlcv([])

    monkeypatch.setattr(geckoterminal.geckoterminal_client, "get_ohlcv", dead_gecko)
    monkeypatch.setattr(dexpaprika, "get_ohlcv", dead_dexpaprika)
    monkeypatch.setattr(codex, "codex_configured", lambda: False)
    monkeypatch.setattr(momentum_entry, "_provider_in_cooldown", lambda provider: False)
    monkeypatch.setattr(momentum_entry, "_record_provider_outcome", lambda *a, **kw: None)
    monkeypatch.setattr(mobula, "mobula_configured", lambda: False)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert actions["opened"] == []
    rows = await _cycle_log_rows(tmp_db)
    assert len(rows) == 1
    assert rows[0]["reason"] == "ohlcv_unavailable"
    assert rows[0]["provider"] is None
    assert rows[0]["degraded"] == 0


@pytest.mark.asyncio
async def test_cycle_log_records_the_buy_row(tmp_db, monkeypatch):
    rsi, mfi = _signal_series()
    _patch_cycle_io(monkeypatch, spot=2.0, rsi=rsi, mfi=mfi)
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    await v9.run_v9_cycle()

    rows = await _cycle_log_rows(tmp_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "buy"
    assert row["reason"] == "synchronized_transition"
    assert row["provider"] == "geckoterminal"
    assert row["rsi_last"] == pytest.approx(15.0)
    assert row["mfi_last"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_get_last_candle_provenance_none_before_any_fetch(tmp_db, monkeypatch):
    """Sanity check on the ContextVar seam itself: a fresh task with no
    ``_fetch_candles`` call yet must read None, never stale state leaked
    from an unrelated earlier call."""
    from aria_core import momentum_entry

    async def _isolated_read():
        return momentum_entry.get_last_candle_provenance()

    assert await asyncio.create_task(_isolated_read()) is None


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


@pytest.mark.asyncio
async def test_visible_reporting_hides_retired_pockets(tmp_db):
    """06/08 operator order: retired wallets never surface on operator-facing
    lists -- all_reporting_wallets (risk math) keeps them, the visible list
    drops them."""
    import aiosqlite

    await pt._ensure_tables()
    async with aiosqlite.connect(pt.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO paper_state (wallet, starting_capital, created_at) "
            "VALUES ('scalping_v1', 1000000.0, '2026-07-30T00:00:00+00:00')",
        )
        await db.commit()

    assert "scalping_v1" in await pt.all_reporting_wallets()
    visible = await pt.visible_reporting_wallets()
    assert "scalping_v1" not in visible
    for retired in pt._RETIRED_SCALPING_WALLETS:
        assert retired not in visible


# tf setting tests
@pytest.mark.asyncio
async def test_set_timeframe_discrete_values_only(tmp_db):
    entry, error = await v9.set_watchlist_settings(SPX, timeframe_min=15)
    assert error == "" and entry["timeframe_min"] == 15
    entry, error = await v9.set_watchlist_settings(SPX, timeframe_min=10)
    assert entry is None and "non supportée" in error
    # unchanged after the refusal
    assert (await v9.get_watchlist())[0]["timeframe_min"] == 15


@pytest.mark.asyncio
async def test_cycle_requests_the_token_timeframe(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await v9.set_watchlist_settings(SPX, timeframe_min=30)
    modes: list[str] = []
    from aria_core.services import geckoterminal

    async def fake_get_ohlcv(pool, *, network="base", mode="standard", **kw):
        modes.append(mode)
        return _FakeOhlcv([])

    async def fake_pair_lookup(contract, *, chain="base"):
        return _FakePair()

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    monkeypatch.setattr(geckoterminal.geckoterminal_client, "get_ohlcv", fake_get_ohlcv)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    await v9.run_v9_cycle()

    assert modes == ["scalping_30m"]


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
    """The operator will add ~4 more contracts -- every seed entry must carry
    the 3 keys the engine reads, nothing engine-side to change."""
    for token in v9.V9_WATCHLIST:
        assert set(token) == {"contract", "chain", "symbol"}


# ── dynamic watchlist (DB, /v9add -- operator self-service, 06/08) ───────────

@pytest.mark.asyncio
async def test_watchlist_seeds_spx_once(tmp_db):
    tokens = await v9.get_watchlist()
    assert [t["symbol"] for t in tokens] == ["SPX"]
    assert tokens[0]["contract"] == SPX.lower()
    # a second read never duplicates the seed
    assert len(await v9.get_watchlist()) == 1


@pytest.mark.asyncio
async def test_add_resolves_most_liquid_chain(tmp_db, monkeypatch):
    calls: list[str] = []

    async def fake_pair_lookup(contract, *, chain="base"):
        calls.append(chain)
        pair = _FakePair(price=1.0, liquidity=100_000.0 if chain == "base" else 900_000.0)
        pair.base_symbol = "TOK"
        return pair

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)

    entry, error = await v9.add_watchlist_token("0x" + "b" * 40)

    assert error == ""
    assert calls == ["base", "ethereum"]  # both probed, most liquid wins
    assert entry["chain"] == "ethereum"
    symbols = [t["symbol"] for t in await v9.get_watchlist()]
    assert "TOK" in symbols and "SPX" in symbols


@pytest.mark.asyncio
async def test_add_no_pool_refuses_with_reason(tmp_db, monkeypatch):
    async def fake_pair_lookup(contract, *, chain="base"):
        return None

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)

    entry, error = await v9.add_watchlist_token("0x" + "c" * 40)

    assert entry is None
    assert "aucun pool liquide" in error
    assert len(await v9.get_watchlist()) == 1  # SPX only, nothing half-added


@pytest.mark.asyncio
async def test_remove_deactivates_and_readd_reactivates(tmp_db, monkeypatch):
    assert await v9.remove_watchlist_token(SPX) is True
    assert await v9.get_watchlist() == []
    # removing again: nothing active anymore
    assert await v9.remove_watchlist_token(SPX) is False

    async def fake_pair_lookup(contract, *, chain="base"):
        pair = _FakePair()
        return pair if chain == "base" else None

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    entry, error = await v9.add_watchlist_token(SPX)
    assert error == ""
    assert [t["contract"] for t in await v9.get_watchlist()] == [SPX.lower()]


@pytest.mark.asyncio
async def test_removed_seed_never_resurrected_by_ensure(tmp_db):
    """INSERT OR IGNORE seed: a /v9remove of SPX must survive the next
    table-ensure pass (the exact trap the seed doctrine comment guards)."""
    await v9.remove_watchlist_token(SPX)
    await v9._ensure_watchlist_table()
    assert await v9.get_watchlist() == []


# ── per-token settings (/v9set -- real-time tuning, 06/08) ───────────────────

@pytest.mark.asyncio
async def test_settings_defaults_resolved_on_seed(tmp_db):
    token = (await v9.get_watchlist())[0]
    assert token["rsi_period"] == 18 and token["rsi_lower"] == 21.0
    assert token["mfi_period"] == 10 and token["mfi_lower"] == 20.0
    assert token["trail_pct"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_set_settings_partial_update(tmp_db):
    entry, error = await v9.set_watchlist_settings(SPX, rsi_lower=25.0, trail_pct=0.04)
    assert error == ""
    assert entry["rsi_lower"] == 25.0
    assert entry["trail_pct"] == pytest.approx(0.04)
    # untouched keys keep their defaults
    assert entry["rsi_period"] == 18 and entry["mfi_lower"] == 20.0


@pytest.mark.asyncio
async def test_set_settings_bounds_and_unknown_key_refused(tmp_db):
    entry, error = await v9.set_watchlist_settings(SPX, rsi_lower=150.0)
    assert entry is None and "hors bornes" in error
    entry, error = await v9.set_watchlist_settings(SPX, buy_pct=0.5)
    assert entry is None and "inconnu" in error
    entry, error = await v9.set_watchlist_settings("0x" + "d" * 40, rsi_lower=25.0)
    assert entry is None and "absent" in error
    # nothing half-persisted
    token = (await v9.get_watchlist())[0]
    assert token["rsi_lower"] == 21.0


@pytest.mark.asyncio
async def test_cycle_honors_per_token_thresholds(tmp_db, monkeypatch):
    """RSI at 24 with a per-token rsi_lower of 25: buys -- the same series
    under the default 21 threshold would never signal."""
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await v9.set_watchlist_settings(SPX, rsi_lower=25.0)
    rsi = [50.0] * 60
    mfi = [50.0] * 60
    rsi[-1] = 24.0  # below 25, above the default 21
    mfi[-1] = 10.0
    _patch_cycle_io(monkeypatch, rsi=rsi, mfi=mfi)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert len(actions["opened"]) == 1
    assert "< 25" in (actions["opened"][0].get("thesis") or "")


@pytest.mark.asyncio
async def test_cycle_honors_per_token_trail(tmp_db, monkeypatch):
    """trail=3%: a -4% dip from the peak closes (would survive at the
    default -5%)."""
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await v9.set_watchlist_settings(SPX, trail_pct=0.03)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)
    pos = await pt.open_position(
        SPX, "SPX", 1.013, wallet=pt.V9_WALLET, alloc_usd=30_000,
        mode="standard", allow_multiple=True,
    )
    await pt._update_high_water(pos["id"], 1.0)

    _patch_cycle_io(monkeypatch, spot=0.96, rsi=[50.0] * 60, mfi=[50.0] * 60)
    actions = await v9.run_v9_cycle()

    assert len(actions["closed"]) == 1
    assert actions["closed"][0]["close_reason"] == "trailing -3% (v9)"


def test_v9set_arg_parsing():
    from aria_core.gateway.telegram_bot import _parse_v9set_args

    settings, error = _parse_v9set_args(["rsi=16/25", "mfi=10/18", "trail=4"])
    assert error == ""
    assert settings == {
        "rsi_period": 16, "rsi_lower": 25.0,
        "mfi_period": 10, "mfi_lower": 18.0,
        "trail_pct": pytest.approx(0.04),
    }
    # threshold-only form
    settings, error = _parse_v9set_args(["rsi=25"])
    assert error == "" and settings == {"rsi_lower": 25.0}
    # trail accepts a % suffix
    settings, error = _parse_v9set_args(["trail=5%"])
    assert error == "" and settings == {"trail_pct": pytest.approx(0.05)}
    for bad in (["foo=1"], ["rsi"], ["rsi=abc"]):
        settings, error = _parse_v9set_args(bad)
        assert settings == {} and error


@pytest.mark.asyncio
async def test_cycle_reads_db_watchlist_not_the_seed_tuple(tmp_db, monkeypatch):
    """The 5-min cycle iterates the DB list -- a /v9remove takes effect on
    the very next cycle without any code change."""
    monkeypatch.setenv("ARIA_SCALPING_V9_ENABLED", "true")
    await v9.remove_watchlist_token(SPX)
    rsi, mfi = _signal_series()
    _patch_cycle_io(monkeypatch, rsi=rsi, mfi=mfi)
    await pt.reset_portfolio(1_000_000.0, wallet=pt.V9_WALLET)

    actions = await v9.run_v9_cycle()

    assert actions["checked"] == 0
    assert actions["opened"] == []
