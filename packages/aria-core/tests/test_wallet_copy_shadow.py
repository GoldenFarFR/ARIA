"""Wallet-copy shadow -- fictional per-wallet ledger, never real capital,
never a live trigger (cf. module docstring). Same isolated-DB test pattern
as test_narrative_signal_shadow.py."""
from __future__ import annotations

import pytest

from aria_core import wallet_copy_shadow as wcs
from aria_core.services.blockscout import TokenTransfer, TokenTransfersResult

WALLET = list(wcs.TRACKED_WALLETS.keys())[0]
META = wcs.TRACKED_WALLETS[WALLET]
TOKEN = "0x" + "c" * 40
WETH = "0x4200000000000000000000000000000000000006"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "wallet_copy_shadow.db")
    monkeypatch.setattr(wcs, "DB_PATH", db_path)
    monkeypatch.setattr(wcs, "_table_ready", False)
    yield


def _transfer(*, tx_hash, to_address="", from_address="", token_address=TOKEN, symbol="TP"):
    return TokenTransfer(
        tx_hash=tx_hash, from_address=from_address, to_address=to_address,
        token_address=token_address, token_symbol=symbol, token_name="TestProto",
        amount=1_000.0, timestamp="2026-08-08T12:00:00Z",
    )


def _mock_transfers(monkeypatch, transfers, *, available=True, error=None):
    async def _fake(address, limit=50, *, max_pages=1, token_type=None):
        return TokenTransfersResult(transfers=transfers, available=available, error=error)

    from aria_core.services.blockscout import blockscout_client

    monkeypatch.setattr(blockscout_client, "get_token_transfers", _fake)


def _mock_price(monkeypatch, price):
    async def _fake(contract):
        return price

    monkeypatch.setattr(wcs, "_current_price_usd", _fake)


@pytest.mark.asyncio
async def test_detected_buy_opens_a_fictional_position(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 1
    assert result.closed == 0
    stats = (await wcs.summary())[WALLET]
    assert stats["open_positions"] == 1
    assert stats["closed_positions"] == 0


@pytest.mark.asyncio
async def test_matching_sell_closes_the_position_and_computes_realized_pnl(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    await wcs.scan_wallet(WALLET, META)

    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xsell1", from_address=WALLET)])
    _mock_price(monkeypatch, 2.0)  # doubled -> +100% on the fictional POSITION_SIZE_USD stake
    result = await wcs.scan_wallet(WALLET, META)
    assert result.closed == 1

    stats = (await wcs.summary())[WALLET]
    assert stats["closed_positions"] == 1
    assert stats["open_positions"] == 0
    assert stats["realized_pnl_usd"] == pytest.approx(wcs.POSITION_SIZE_USD, rel=1e-6)


@pytest.mark.asyncio
async def test_quote_token_transfer_is_never_copied(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xweth1", to_address=WALLET, token_address=WETH, symbol="WETH")])
    _mock_price(monkeypatch, 1.0)
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 0
    assert (await wcs.summary())[WALLET]["open_positions"] == 0


@pytest.mark.asyncio
async def test_second_buy_while_already_open_is_ignored(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    await wcs.scan_wallet(WALLET, META)

    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy2", to_address=WALLET)])
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 0
    assert (await wcs.summary())[WALLET]["open_positions"] == 1


@pytest.mark.asyncio
async def test_cursor_prevents_reprocessing_the_same_transfer(monkeypatch):
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    _mock_price(monkeypatch, 1.0)
    first = await wcs.scan_wallet(WALLET, META)
    assert first.opened == 1

    # Same transfer still returned by Blockscout (unchanged window) -- must
    # not open a second fictional position for the already-open contract,
    # and cursor logic must not reprocess a transfer already handled.
    second = await wcs.scan_wallet(WALLET, META)
    assert second.opened == 0
    assert (await wcs.summary())[WALLET]["open_positions"] == 1


@pytest.mark.asyncio
async def test_blockscout_failure_is_best_effort_never_raises(monkeypatch):
    _mock_transfers(monkeypatch, [], available=False, error="blockscout unavailable")
    result = await wcs.scan_wallet(WALLET, META)
    assert result.opened == 0
    assert result.closed == 0
    assert result.error == "blockscout unavailable"


@pytest.mark.asyncio
async def test_scan_failure_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(wcs, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(wcs, "_table_ready", False)
    _mock_transfers(monkeypatch, [_transfer(tx_hash="0xbuy1", to_address=WALLET)])
    result = await wcs.scan_wallet(WALLET, META)
    assert result.error is not None


@pytest.mark.asyncio
async def test_summary_reports_all_tracked_wallets_even_with_no_activity():
    result = await wcs.summary()
    assert set(result.keys()) == set(wcs.TRACKED_WALLETS.keys())
    for wallet, stats in result.items():
        assert stats["closed_positions"] == 0
        assert stats["open_positions"] == 0
        assert stats["label"] == wcs.TRACKED_WALLETS[wallet]["label"]


@pytest.mark.asyncio
async def test_run_scan_cycle_covers_all_eight_wallets(monkeypatch):
    async def _empty(address, limit=50, *, max_pages=1, token_type=None):
        return TokenTransfersResult(transfers=[], available=True, error=None)

    from aria_core.services.blockscout import blockscout_client

    monkeypatch.setattr(blockscout_client, "get_token_transfers", _empty)
    results = await wcs.run_scan_cycle()
    assert len(results) == len(wcs.TRACKED_WALLETS) == 8


def test_position_size_is_a_fixed_fictional_stake_not_real_capital():
    assert wcs.POSITION_SIZE_USD == 1_000.0
