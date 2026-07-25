"""Pairing the flat swap journal into positions with a realized P&L
(agent_wallet_positions.py, 07/25, Smart Account migration #41). Covers: USDC
detection, the pure FIFO pairing (buy+sell -> closed, buy-only -> open, sell
without buy -> unmatched and NEVER a fabricated P&L, failed/blocked/transfer
rows ignored, anomalies surfaced, multi-buy FIFO, chronological ordering), the
DB read path (wallet_product isolation, ordering, limit), and the two wired
entry points (swing feed passed EXPLICITLY, paper defaults never changed)."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_log, agent_wallet_positions as awp
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

TOKEN_A = "0x" + "a" * 40
TOKEN_B = "0x" + "b" * 40


# ── isolated journal DB (agent_wallet_log.DB_PATH is the single source of truth) ──


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "positions_test.db"))
    yield


def _row(**overrides) -> dict:
    base = {
        "id": 1, "wallet_product": awp.SWING_WALLET_PRODUCT, "chain": "base",
        "action_type": "swap", "token_in": USDC_BASE_ADDRESS, "token_out": TOKEN_A,
        "amount_in": 10.0, "amount_out": 1000.0, "slippage_bps": 1000, "tx_hash": "0x",
        "status": "ok", "reason": "", "created_at": "2026-07-25T00:00:00+00:00",
        "to_address": "",
    }
    base.update(overrides)
    return base


def _buy(id_, token, cost_usd, *, qty=1000.0, at=None) -> dict:
    return _row(
        id=id_, token_in=USDC_BASE_ADDRESS, token_out=token,
        amount_in=cost_usd, amount_out=qty,
        created_at=at or f"2026-07-25T00:00:{id_:02d}+00:00", tx_hash=f"0xbuy{id_}",
    )


def _sell(id_, token, proceeds_usd, *, qty=1000.0, at=None) -> dict:
    return _row(
        id=id_, token_in=token, token_out=USDC_BASE_ADDRESS,
        amount_in=qty, amount_out=proceeds_usd,
        created_at=at or f"2026-07-25T00:00:{id_:02d}+00:00", tx_hash=f"0xsell{id_}",
    )


# ── _is_usdc ─────────────────────────────────────────────────────────────────


def test_is_usdc_matches_canonical_address_case_insensitively():
    assert awp._is_usdc(USDC_BASE_ADDRESS) is True
    assert awp._is_usdc(USDC_BASE_ADDRESS.upper()) is True
    assert awp._is_usdc("  " + USDC_BASE_ADDRESS + "  ") is True


def test_is_usdc_matches_bare_symbol():
    assert awp._is_usdc("USDC") is True
    assert awp._is_usdc("usdc") is True


def test_is_usdc_false_on_other_tokens_and_empty():
    assert awp._is_usdc(TOKEN_A) is False
    assert awp._is_usdc("") is False
    assert awp._is_usdc(None) is False


# ── pair_swaps: pure pairing ─────────────────────────────────────────────────


def test_empty_rows_produces_empty_result():
    result = awp.pair_swaps([])
    assert result.closed == []
    assert result.open == []
    assert result.unmatched_sells == []
    assert result.anomalies == []


def test_single_buy_is_open_never_closed():
    result = awp.pair_swaps([_buy(1, TOKEN_A, 10.0)])
    assert len(result.open) == 1
    assert result.closed == []
    pos = result.open[0]
    assert pos["status"] == "open"
    assert pos["contract"] == TOKEN_A
    assert pos["cost_usd"] == 10.0
    assert pos["pnl_usd"] is None  # an open position never has a P&L
    assert pos["entry_price"] == pytest.approx(0.01)
    assert pos["id"] == -1  # negated buy-row id


def test_buy_then_sell_is_one_closed_position_with_real_pnl():
    result = awp.pair_swaps([_buy(1, TOKEN_A, 10.0), _sell(2, TOKEN_A, 12.0)])
    assert len(result.closed) == 1
    assert result.open == []
    pos = result.closed[0]
    assert pos["status"] == "closed"
    assert pos["contract"] == TOKEN_A
    assert pos["cost_usd"] == 10.0
    assert pos["proceeds_usd"] == 12.0
    assert pos["pnl_usd"] == pytest.approx(2.0)
    assert pos["pnl_pct"] == pytest.approx(20.0)
    assert pos["entry_price"] == pytest.approx(0.01)
    assert pos["exit_price"] == pytest.approx(0.012)
    assert pos["id"] == -2  # negated SELL-row id (the closing leg)
    assert pos["buy_row_id"] == 1
    assert pos["sell_row_id"] == 2


def test_losing_position_has_negative_pnl():
    result = awp.pair_swaps([_buy(1, TOKEN_A, 10.0), _sell(2, TOKEN_A, 7.0)])
    pos = result.closed[0]
    assert pos["pnl_usd"] == pytest.approx(-3.0)
    assert pos["pnl_pct"] == pytest.approx(-30.0)


def test_pnl_is_exact_even_when_quantity_unknown():
    """P&L uses only the USD legs, so a zero/unknown token quantity still yields
    a real P&L (only the per-token prices degrade to None)."""
    result = awp.pair_swaps([
        _buy(1, TOKEN_A, 10.0, qty=0.0),
        _sell(2, TOKEN_A, 15.0, qty=0.0),
    ])
    pos = result.closed[0]
    assert pos["pnl_usd"] == pytest.approx(5.0)
    assert pos["entry_price"] is None
    assert pos["exit_price"] is None
    assert pos["pnl_pct"] == pytest.approx(50.0)


def test_sell_without_a_matching_buy_is_never_a_pnl():
    """The explicitly-required edge case: a sell with no open buy must NOT
    become a position (a phantom entry would fabricate a huge profit). It is
    surfaced separately and carries no pnl_usd key at all."""
    result = awp.pair_swaps([_sell(1, TOKEN_A, 50.0)])
    assert result.closed == []
    assert result.open == []
    assert len(result.unmatched_sells) == 1
    unmatched = result.unmatched_sells[0]
    assert unmatched["token"] == TOKEN_A
    assert unmatched["proceeds_usd"] == 50.0
    assert "pnl_usd" not in unmatched


def test_failed_and_blocked_rows_are_never_legs():
    rows = [
        _buy(1, TOKEN_A, 10.0, ),
        _row(id=2, token_in=TOKEN_A, token_out=USDC_BASE_ADDRESS, status="failed",
             amount_in=1000.0, amount_out=8.0, created_at="2026-07-25T00:00:02+00:00"),
        _row(id=3, token_in=TOKEN_A, token_out=USDC_BASE_ADDRESS, status="blocked",
             amount_in=1000.0, amount_out=8.0, created_at="2026-07-25T00:00:03+00:00"),
    ]
    result = awp.pair_swaps(rows)
    # the buy stays open -- neither the failed nor the blocked sell closes it
    assert len(result.open) == 1
    assert result.closed == []
    assert result.unmatched_sells == []


def test_transfer_rows_are_ignored_entirely():
    """A named-exception-#4 transfer (action_type='transfer') is not a trading
    leg and must not even reach anomaly classification."""
    rows = [
        _buy(1, TOKEN_A, 10.0),
        _row(id=2, action_type="transfer", token_in=USDC_BASE_ADDRESS, token_out="",
             to_address="0x" + "f" * 40, amount_in=5.0,
             created_at="2026-07-25T00:00:02+00:00"),
    ]
    result = awp.pair_swaps(rows)
    assert len(result.open) == 1
    assert result.anomalies == []


def test_usdc_to_usdc_and_token_to_token_are_anomalies():
    rows = [
        _row(id=1, token_in=USDC_BASE_ADDRESS, token_out="USDC"),  # USDC->USDC
        _row(id=2, token_in=TOKEN_A, token_out=TOKEN_B,
             created_at="2026-07-25T00:00:02+00:00"),  # X->Y
    ]
    result = awp.pair_swaps(rows)
    assert result.closed == []
    assert result.open == []
    assert len(result.anomalies) == 2


def test_two_full_roundtrips_produce_two_closed_positions():
    rows = [
        _buy(1, TOKEN_A, 10.0),
        _sell(2, TOKEN_A, 12.0),
        _buy(3, TOKEN_B, 20.0),
        _sell(4, TOKEN_B, 15.0),
    ]
    result = awp.pair_swaps(rows)
    assert len(result.closed) == 2
    by_contract = {p["contract"]: p for p in result.closed}
    assert by_contract[TOKEN_A]["pnl_usd"] == pytest.approx(2.0)
    assert by_contract[TOKEN_B]["pnl_usd"] == pytest.approx(-5.0)


def test_multi_buy_same_token_then_one_sell_closes_oldest_lot_fifo():
    """Two buys of the same token before any sell (accumulation the dormant
    swing path never produces): the sell closes the OLDEST lot, the newer lot
    stays genuinely open -- never an invented double close."""
    rows = [
        _buy(1, TOKEN_A, 10.0, at="2026-07-25T00:00:01+00:00"),
        _buy(2, TOKEN_A, 20.0, at="2026-07-25T00:00:02+00:00"),
        _sell(3, TOKEN_A, 18.0, at="2026-07-25T00:00:03+00:00"),
    ]
    result = awp.pair_swaps(rows)
    assert len(result.closed) == 1
    assert len(result.open) == 1
    # oldest buy (cost 10) is the one closed
    assert result.closed[0]["cost_usd"] == 10.0
    assert result.closed[0]["buy_row_id"] == 1
    # the newer lot (cost 20) remains open
    assert result.open[0]["cost_usd"] == 20.0
    assert result.open[0]["buy_row_id"] == 2


def test_pairing_orders_chronologically_regardless_of_input_order():
    """Rows shuffled: the pairing must still match the OLD buy to the sell,
    driven by created_at then id, not by list order."""
    rows = [
        _sell(2, TOKEN_A, 12.0, at="2026-07-25T00:00:02+00:00"),
        _buy(1, TOKEN_A, 10.0, at="2026-07-25T00:00:01+00:00"),
    ]
    result = awp.pair_swaps(rows)
    assert len(result.closed) == 1
    assert result.closed[0]["buy_row_id"] == 1
    assert result.closed[0]["sell_row_id"] == 2


def test_closed_position_carries_no_invented_context():
    """The journal has no thesis/discovery_channel/regime -- these must be None,
    never fabricated (the consumers render them 'inconnu'/'(absente)')."""
    result = awp.pair_swaps([_buy(1, TOKEN_A, 10.0), _sell(2, TOKEN_A, 12.0)])
    pos = result.closed[0]
    for key in ("thesis", "discovery_channel", "conviction_tier", "entry_regime",
                "close_reason", "close_notes", "strategy", "symbol"):
        assert pos[key] is None


# ── DB read path ─────────────────────────────────────────────────────────────


async def _record(token_in, token_out, amount_in, amount_out, *,
                  status="ok", product=awp.SWING_WALLET_PRODUCT, action="swap"):
    await agent_wallet_log.record_transaction(
        wallet_product=product, chain="base", action_type=action,
        token_in=token_in, token_out=token_out, amount_in=amount_in,
        amount_out=amount_out, status=status,
    )


async def _record_roundtrip(token, cost_usd, proceeds_usd, *, qty=1000.0,
                            product=awp.SWING_WALLET_PRODUCT):
    await _record(USDC_BASE_ADDRESS, token, cost_usd, qty, product=product)
    await _record(token, USDC_BASE_ADDRESS, qty, proceeds_usd, product=product)


@pytest.mark.asyncio
async def test_load_positions_reads_the_journal_db():
    await _record_roundtrip(TOKEN_A, 10.0, 12.0)
    await _record(USDC_BASE_ADDRESS, TOKEN_B, 20.0, 500.0)  # open buy only
    result = await awp.load_positions()
    assert len(result.closed) == 1
    assert len(result.open) == 1
    assert result.closed[0]["contract"] == TOKEN_A


@pytest.mark.asyncio
async def test_closed_positions_fetch_most_recent_first_and_limit():
    await _record_roundtrip(TOKEN_A, 10.0, 5.0)   # older, loss
    await _record_roundtrip(TOKEN_B, 10.0, 30.0)  # newer, win
    closed = await awp.closed_positions_fetch()
    assert [p["contract"] for p in closed] == [TOKEN_B, TOKEN_A]  # newest first
    limited = await awp.closed_positions_fetch(limit=1)
    assert len(limited) == 1
    assert limited[0]["contract"] == TOKEN_B


@pytest.mark.asyncio
async def test_recent_realized_pnls_most_recent_first_only_closed():
    await _record_roundtrip(TOKEN_A, 10.0, 5.0)   # -5, older
    await _record_roundtrip(TOKEN_B, 10.0, 13.0)  # +3, newer
    await _record(USDC_BASE_ADDRESS, "0x" + "c" * 40, 50.0, 1000.0)  # open, excluded
    pnls = await awp.recent_realized_pnls()
    assert pnls == pytest.approx([3.0, -5.0])  # newest-first, open excluded


@pytest.mark.asyncio
async def test_wallet_product_isolation():
    await _record_roundtrip(TOKEN_A, 10.0, 12.0, product=awp.SWING_WALLET_PRODUCT)
    await _record_roundtrip(TOKEN_B, 10.0, 12.0, product="coinbase_agentic_wallet")
    swing_closed = await awp.closed_positions_fetch(awp.SWING_WALLET_PRODUCT)
    assert len(swing_closed) == 1
    assert swing_closed[0]["contract"] == TOKEN_A


@pytest.mark.asyncio
async def test_open_positions_fetch():
    await _record(USDC_BASE_ADDRESS, TOKEN_A, 10.0, 1000.0)  # open
    await _record_roundtrip(TOKEN_B, 10.0, 12.0)             # closed
    open_positions = await awp.open_positions_fetch()
    assert len(open_positions) == 1
    assert open_positions[0]["contract"] == TOKEN_A
    assert open_positions[0]["pnl_usd"] is None


@pytest.mark.asyncio
async def test_pairing_health_counts():
    await _record_roundtrip(TOKEN_A, 10.0, 12.0)             # 1 closed
    await _record(USDC_BASE_ADDRESS, TOKEN_B, 20.0, 500.0)   # 1 open
    await _record(TOKEN_A, USDC_BASE_ADDRESS, 500.0, 8.0)    # unmatched sell (A already closed)
    health = await awp.pairing_health()
    assert health["closed"] == 1
    assert health["open"] == 1
    assert health["unmatched_sells"] == 1
    assert health["anomalies"] == 0


@pytest.mark.asyncio
async def test_no_rows_gives_empty_feeds():
    assert await awp.closed_positions_fetch() == []
    assert await awp.open_positions_fetch() == []
    assert await awp.recent_realized_pnls() == []


# ── wired entry points (swing feed EXPLICIT, paper defaults untouched) ────────


@pytest.mark.asyncio
async def test_swing_closed_positions_fetch_is_zero_arg_and_bound_to_swing():
    await _record_roundtrip(TOKEN_A, 10.0, 12.0)
    await _record_roundtrip(TOKEN_B, 10.0, 12.0, product="coinbase_agentic_wallet")
    closed = await awp.swing_closed_positions_fetch()  # takes no args (positions_fetch seam)
    assert len(closed) == 1
    assert closed[0]["contract"] == TOKEN_A


@pytest.mark.asyncio
async def test_run_swing_loss_batch_review_passes_the_swing_feed_explicitly(monkeypatch):
    """Proves the wiring never changes trade_loss_batch_review's paper default:
    it always passes positions_fetch=swing_closed_positions_fetch explicitly."""
    from aria_core.skills import trade_loss_batch_review as tlbr

    captured = {}

    async def fake_cycle(*, llm=None, positions_fetch=None):
        captured["positions_fetch"] = positions_fetch
        captured["llm"] = llm
        return {"outcome": "captured"}

    monkeypatch.setattr(tlbr, "run_trade_loss_batch_review_cycle", fake_cycle)
    sentinel_llm = object()
    result = await awp.run_swing_loss_batch_review_cycle(llm=sentinel_llm)
    assert result == {"outcome": "captured"}
    assert captured["positions_fetch"] is awp.swing_closed_positions_fetch
    assert captured["llm"] is sentinel_llm


@pytest.mark.asyncio
async def test_run_swing_loss_batch_review_end_to_end_uses_negative_swing_ids(monkeypatch):
    """Full run against the real batch reviewer over 10 real swing losses --
    confirms the swing position ids are negative (disjoint from paper's
    positive AUTOINCREMENT ids in the shared trade_loss_batch tables)."""
    from aria_core.skills import trade_loss_batch_review as tlbr

    monkeypatch.setattr(tlbr, "DB_PATH", agent_wallet_log.DB_PATH)  # same isolated tmp db
    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: False)

    for i in range(10):  # 10 losing round-trips, distinct tokens
        await _record_roundtrip("0x" + f"{i:040x}", 10.0, 4.0)

    import json

    async def fake_llm(*args, **kwargs):
        return json.dumps({"pattern_found": False, "pattern_summary": "", "adjustment": ""})

    result = await awp.run_swing_loss_batch_review_cycle(llm=fake_llm)
    assert result["outcome"] == "ok"
    assert result["batches_reviewed"] == 1
    ids = result["results"][0]["position_ids"]
    assert len(ids) == 10
    assert all(pid < 0 for pid in ids)  # swing ids never collide with paper ids


@pytest.mark.asyncio
async def test_evaluate_swing_risk_from_log_wires_real_feed(monkeypatch):
    """recent_pnls comes from the journal; equity_usd stays injected; the
    post_mortem_fn wraps run_swing_post_mortem over the real recent buys."""
    from aria_core import agent_wallet_smart_swing as sws

    await _record_roundtrip(TOKEN_A, 10.0, 4.0)   # -6, older
    await _record_roundtrip(TOKEN_B, 10.0, 3.0)   # -7, newer

    captured = {}

    async def fake_evaluate(*, equity_usd, recent_pnls, post_mortem_fn=None, notify_fn=None):
        captured["equity_usd"] = equity_usd
        captured["recent_pnls"] = recent_pnls
        captured["post_mortem_fn"] = post_mortem_fn
        return "state"

    pm_seen = {}

    async def fake_post_mortem(recent_buys, *, llm=None):
        pm_seen["recent_buys"] = recent_buys
        pm_seen["llm"] = llm
        return "post-mortem text"

    monkeypatch.setattr(sws, "evaluate_swing_risk", fake_evaluate)
    monkeypatch.setattr(sws, "run_swing_post_mortem", fake_post_mortem)

    sentinel_llm = object()
    out = await awp.evaluate_swing_risk_from_log(
        equity_usd=250.0, post_mortem_llm=sentinel_llm,
    )
    assert out == "state"
    assert captured["equity_usd"] == 250.0  # injected, never invented here
    assert captured["recent_pnls"] == pytest.approx([-7.0, -6.0])  # real, newest-first

    # the wired post_mortem_fn feeds the real recent buys into run_swing_post_mortem
    text = await captured["post_mortem_fn"]()
    assert text == "post-mortem text"
    assert len(pm_seen["recent_buys"]) == 2
    assert pm_seen["llm"] is sentinel_llm


@pytest.mark.asyncio
async def test_swing_wallet_product_matches_smart_swing():
    """Guard against drift: the literal here must equal the real swing tag the
    execution path logs under (agent_wallet_smart_swing.WALLET_PRODUCT)."""
    from aria_core import agent_wallet_smart_swing as sws

    assert awp.SWING_WALLET_PRODUCT == sws.WALLET_PRODUCT
