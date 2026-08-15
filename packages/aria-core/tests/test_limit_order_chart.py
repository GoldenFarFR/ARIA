"""limit_order_chart.py -- chart screenshot sent to Telegram when a limit
order is placed, piloted on scalping_v6/v7 then extended to every pocket the
same day (04/08). No real network call, no real Telegram send:
momentum_entry.fetch_candles/telegram_bot.send_photo/chart_render are
monkeypatched."""
from __future__ import annotations

import pytest

from aria_core import limit_order_chart
from aria_core import momentum_entry
from aria_core.gateway import telegram_bot
from aria_core.skills import chart_render
from aria_core.skills.ta_levels import Candle


def _candles() -> list[Candle]:
    return [Candle(ts=i, open=100.0, high=103.0, low=97.0, close=100.0 + i, volume=50.0) for i in range(20)]


@pytest.mark.asyncio
async def test_non_scalping_wallet_uses_standard_mode_and_default_horizon(monkeypatch, tmp_path):
    """swing/vc (any non-scalping pocket) still gets a chart -- the
    04/08 same-day extension past the scalping_v6/v7 pilot -- but with the
    real timeframe (mode="standard") and chart_render's own multi-week
    horizon label (None override), never the scalping-tuned hour-scale
    settings."""
    seen_mode = {}
    seen_horizon = {}

    async def _fake_fetch_candles(pool_address, chain, *, contract="", mode="standard"):
        seen_mode["mode"] = mode
        return _candles()

    def _fake_render(candles, *, entry=None, invalidation=None, target=None, horizon_label=None):
        seen_horizon["horizon_label"] = horizon_label
        return "data:image/png;base64,fake"

    async def _fake_send_photo(path, *, caption="", chat_id=None):
        return True

    monkeypatch.setattr(momentum_entry, "fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(chart_render, "render_scenario_png", _fake_render)
    monkeypatch.setattr(chart_render, "save_png_data_uri", lambda data_uri, path: open(path, "wb").close())
    monkeypatch.setattr(telegram_bot, "send_photo", _fake_send_photo)

    await limit_order_chart.maybe_send_order_chart(
        {"wallet": "swing", "contract": "0xabc", "chain": "base", "id": 1, "symbol": "TOK"},
        {"pool_address": "0xpool", "price_at_order_placed": 1.5, "invalidation": 1.2, "target": 1.9},
    )
    assert seen_mode["mode"] == "standard"
    assert seen_horizon["horizon_label"] is None


@pytest.mark.asyncio
async def test_noop_when_pool_address_missing(monkeypatch):
    called = []
    monkeypatch.setattr(momentum_entry, "fetch_candles", lambda *a, **kw: called.append("fetch") or _candles())
    monkeypatch.setattr(telegram_bot, "send_photo", lambda *a, **kw: called.append("send"))

    await limit_order_chart.maybe_send_order_chart(
        {"wallet": "scalping_v6", "contract": "0xabc", "chain": "base", "id": 1},
        {},
    )
    assert called == []


@pytest.mark.asyncio
async def test_noop_when_no_candles_returned(monkeypatch):
    async def _empty_candles(*a, **kw):
        return []

    sent = []
    monkeypatch.setattr(momentum_entry, "fetch_candles", _empty_candles)
    monkeypatch.setattr(telegram_bot, "send_photo", lambda *a, **kw: sent.append((a, kw)))

    await limit_order_chart.maybe_send_order_chart(
        {"wallet": "scalping_v7", "contract": "0xabc", "chain": "base", "id": 1},
        {"pool_address": "0xpool"},
    )
    assert sent == []


@pytest.mark.asyncio
async def test_scalping_wallet_with_pool_address_sends_photo_and_cleans_up(monkeypatch, tmp_path):
    async def _fake_fetch_candles(pool_address, chain, *, contract="", mode="standard"):
        assert pool_address == "0xpool"
        assert chain == "base"
        assert mode == "scalping"
        return _candles()

    sent = []

    async def _fake_send_photo(path, *, caption="", chat_id=None):
        import os
        assert os.path.exists(path)
        sent.append((path, caption))
        return True

    monkeypatch.setattr(momentum_entry, "fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(telegram_bot, "send_photo", _fake_send_photo)

    order = {"wallet": "scalping_v6", "contract": "0xabc", "chain": "base", "id": 42, "symbol": "TOK"}
    sig = {"pool_address": "0xpool", "price_at_order_placed": 1.5, "invalidation": 1.2, "target": 1.9}
    await limit_order_chart.maybe_send_order_chart(order, sig)

    assert len(sent) == 1
    path, caption = sent[0]
    assert "TOK" in caption
    assert "scalping_v6" in caption
    # Best-effort cleanup: the temp file must not survive the call.
    import os
    assert not os.path.exists(path)


@pytest.mark.asyncio
async def test_never_raises_when_fetch_candles_errors(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(momentum_entry, "fetch_candles", _boom)

    # Must not raise -- best-effort, purely visual, never blocks the real
    # trading cycle that placed the order.
    await limit_order_chart.maybe_send_order_chart(
        {"wallet": "scalping_v6", "contract": "0xabc", "chain": "base", "id": 1},
        {"pool_address": "0xpool"},
    )


@pytest.mark.asyncio
async def test_never_raises_when_telegram_send_errors(monkeypatch):
    async def _fake_fetch_candles(*a, **kw):
        return _candles()

    async def _boom(*a, **kw):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(momentum_entry, "fetch_candles", _fake_fetch_candles)
    monkeypatch.setattr(telegram_bot, "send_photo", _boom)

    await limit_order_chart.maybe_send_order_chart(
        {"wallet": "scalping_v6", "contract": "0xabc", "chain": "base", "id": 1},
        {"pool_address": "0xpool"},
    )
