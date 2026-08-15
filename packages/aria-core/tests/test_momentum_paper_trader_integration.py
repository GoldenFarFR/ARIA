"""Real boundary momentum_entry.evaluate_momentum_entry -> paper_trader.
open_position (#105, end-to-end pipeline test axis). Each side of this
boundary already has deep unit coverage (test_momentum_entry.py /
test_paper_trader.py), but nothing ever exercised the REAL dict
``evaluate_momentum_entry`` produces against the REAL ``open_position`` --
exactly the shape of the historical bug in ``_resolve_dev_behavior``/
``holders=`` (invisible for as long as each side was tested with its own
independent mocks). Only the external network boundaries are mocked
(DexScreener/GeckoTerminal/GoPlus/Blockscout/LLM, via test_momentum_entry.
_patch_pipeline) -- the real ``evaluate_momentum_entry``, the real
``open_position``, and the real ``sig.get(...)`` mapping (copied verbatim
from ``paper_trader.run_paper_cycle``'s own call site) all run for real."""
from __future__ import annotations

import asyncio
import json

import pytest

from aria_core import momentum_entry as me
from aria_core import momentum_funnel_log
from aria_core import momentum_websocket as mw
from aria_core import outgoing_pause
from aria_core import paper_trader as pt
from aria_core.skills import market_sentiment

from test_momentum_entry import (  # noqa: E402 -- reuse the proven pipeline mocks, never duplicate them
    CONTRACT,
    EntrySignal,
    _isolated_blacklist_db,
    _isolated_holder_concentration_cache_db,
    _isolated_holder_concentration_outage_bypass_db,
    _isolated_rejection_cache_db,
    _pair,
    _patch_pipeline,
)


@pytest.fixture()
def tmp_paper_db(tmp_path, monkeypatch):
    """Same isolation as test_paper_trader.py's own ``tmp_db`` fixture --
    duplicated here (not imported) because that one also drags in
    limit_orders' DB_PATH, irrelevant to a single open_position() call."""
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    monkeypatch.setattr(momentum_funnel_log, "DB_PATH", str(tmp_path / "momentum_funnel.db"))
    monkeypatch.setattr(market_sentiment, "DB_PATH", str(tmp_path / "market_sentiment.db"))


@pytest.mark.asyncio
async def test_real_buy_signal_opens_a_position_with_correctly_mapped_fields(
    monkeypatch,
    tmp_paper_db,
    _isolated_blacklist_db,
    _isolated_rejection_cache_db,
    _isolated_holder_concentration_cache_db,
    _isolated_holder_concentration_outage_bypass_db,
):
    """The REAL evaluate_momentum_entry produces a real BUY sig (network
    boundaries mocked only), consumed by the REAL open_position() using
    EXACTLY the sig.get(...) mapping paper_trader.run_paper_cycle uses in
    production (copied from its real call site, never hand-rebuilt here) --
    if evaluate_momentum_entry ever renames/drops a key consumed downstream,
    THIS test breaks. Neither isolated suite would ever catch it (each one
    mocks the other side of the boundary)."""
    strong = EntrySignal(present=True, entry=1.5, invalidation=1.0, target=2.5, rr=2.0)
    _patch_pipeline(monkeypatch, pairs=[_pair(liquidity_usd=200_000.0)], signal=strong, align=(3, []))

    sig = await me.evaluate_momentum_entry(CONTRACT, "base")
    assert sig["action"] == "BUY"

    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(
        CONTRACT,
        sig.get("symbol", ""),
        sig["price"],
        wallet="swing",
        target_price=sig.get("target"),
        invalidation_price=sig.get("invalidation"),
        alloc_usd=50_000.0,
        category=sig.get("category", ""),
        entry_security_json=sig.get("entry_security_json", ""),
        chain=sig.get("chain") or "base",
        thesis=sig.get("these") or "; ".join(sig.get("reasons") or []) or None,
        pool_liquidity_usd=sig.get("liquidity_usd"),
        entry_market_cap_usd=sig.get("market_cap_usd"),
        entry_atr_pct=sig.get("entry_atr_pct"),
        strategy=sig.get("strategy") or "momentum",
        entry_regime=sig.get("regime"),
        entry_dev_sold_pct=sig.get("dev_sold_pct"),
        rr=sig.get("rr"),
        align_score=sig.get("align_score"),
        align_ema=sig.get("align_ema"),
        align_macd=sig.get("align_macd"),
        align_pattern=sig.get("align_pattern"),
        mode=sig.get("mode", "standard"),
        gp_low=sig.get("gp_low"),
        gp_high=sig.get("gp_high"),
    )

    assert pos is not None
    # 17/07 historical bug: thesis silently stayed None on every momentum
    # trade (sig.get("these") alone only covered the old VC analyzer) --
    # locked here against a future regression on the same key.
    assert pos["thesis"], "empty thesis -- sig.get('reasons') probably changed shape"
    assert pos["rr"] == pytest.approx(2.0)
    assert pos["align_score"] == 3
    assert pos["chain"] == "base"
    assert pos["symbol"] == sig["symbol"]
    assert pos["entry_security_json"] == sig["entry_security_json"]


@pytest.mark.asyncio
async def test_real_hold_signal_never_reaches_open_position(
    monkeypatch,
    tmp_paper_db,
    _isolated_blacklist_db,
    _isolated_rejection_cache_db,
    _isolated_holder_concentration_cache_db,
    _isolated_holder_concentration_outage_bypass_db,
):
    """Mirror check: a real HOLD (insufficient liquidity, a hard gate that
    never depends on the LLM/network mocks) must produce a dict with no
    usable "price"/"symbol" for a caller that naively skipped the
    action == "BUY" guard -- confirms the boundary fails safe, not just that
    the happy path works."""
    _patch_pipeline(monkeypatch, pairs=[_pair(liquidity_usd=15_000.0)])

    sig = await me.evaluate_momentum_entry(CONTRACT, "base")
    assert sig["action"] == "HOLD"
    assert sig["hold_reason"] == "insufficient_liquidity"
    # A HOLD dict never carries the BUY-only fields open_position needs --
    # any caller reaching open_position() without checking action=="BUY"
    # first would immediately fail on a missing/None price, never silently
    # open a position on bad data.
    assert sig.get("price") is None or "target" not in sig


def _listing_frame(items: list[dict]) -> str:
    return json.dumps({"limit": len(items), "data": items})


def _item(*, chain_id="base", token_address=CONTRACT, description="test") -> dict:
    return {"chainId": chain_id, "tokenAddress": token_address, "description": description, "links": []}


@pytest.mark.asyncio
async def test_real_websocket_frame_reaches_a_real_open_position(
    monkeypatch,
    tmp_path,
    tmp_paper_db,
    caplog,
    _isolated_blacklist_db,
    _isolated_rejection_cache_db,
    _isolated_holder_concentration_cache_db,
    _isolated_holder_concentration_outage_bypass_db,
):
    """Widest boundary in the pipeline: a raw JSON WebSocket frame (the exact
    shape DexScreener sends, per parse_listing's own docstring) all the way
    to a real open_position() call -- through _ingest_frame, _drain_once,
    the REAL run_paper_cycle, the REAL _default_momentum_analyzer, and the
    REAL evaluate_momentum_entry. Only external network calls are mocked
    (DexScreener/GeckoTerminal/GoPlus/Blockscout/LLM via _patch_pipeline,
    plus the liquidity prefilter and the two remaining real-network lookups
    run_paper_cycle's WS caller makes: meta-regime and trading_mode). Every
    existing test_momentum_websocket.py test mocks run_paper_cycle itself at
    this exact point -- so a WS-side bug in candidate shape (chain/contract
    keys, chain_by_contract mapping) that only trips inside the REAL
    evaluate_momentum_entry would be invisible to that file, same failure
    shape as the other two boundary tests above."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda **kw: False)
    # Same bypass as test_paper_trader.py's own autouse fixture: run_paper_cycle
    # re-checks R/R at a fresh price right before open_position -- irrelevant
    # to what this boundary test verifies (candidate shape survives the WS
    # drain into a real decision), covered by its own dedicated tests.
    monkeypatch.setattr(pt, "_execution_rr_still_valid", lambda *_a, **_kw: True)

    from aria_core import limit_orders

    monkeypatch.setattr(limit_orders, "DB_PATH", str(tmp_path / "limit_orders.db"))

    async def _passthrough_prefilter(candidates):
        for c in candidates:
            c.setdefault("price_usd", 1.5)
        return candidates

    monkeypatch.setattr(mw, "_batch_liquidity_prefilter", _passthrough_prefilter)

    async def _neutral_regime():
        return None

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", _neutral_regime)

    strong = EntrySignal(present=True, entry=1.5, invalidation=1.0, target=2.5, rr=2.0)
    _patch_pipeline(monkeypatch, pairs=[_pair(liquidity_usd=200_000.0)], signal=strong, align=(3, []))

    await pt.reset_portfolio(1_000_000.0)

    listener = mw.MomentumWebsocketListener()
    await listener._ingest_frame(_listing_frame([_item()]))
    assert (CONTRACT, "base") in listener._pending

    caplog.set_level("INFO")
    await listener._drain_once()

    # 11/08 -- this exact assertion is what surfaced the real risk_guard
    # UnboundLocalError bug (see test_momentum_entry.py's two new
    # regression tests): this WS path is the first caller to reach
    # dex_composite_score without conviction research having run first.
    assert not any("dex composite score failed" in r.message for r in caplog.records)

    positions = await pt.get_open_positions(wallet="swing")
    assert len(positions) == 1
    pos = positions[0]
    assert pos["contract"] == CONTRACT
    assert pos["thesis"], "empty thesis -- same historical bug class as the direct boundary test above"
    assert pos["rr"] == pytest.approx(2.0)
