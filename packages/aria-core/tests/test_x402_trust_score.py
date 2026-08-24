"""In-house x402 trust score (07/24) -- no real network call, both cascade
steps (wallet_transfers_fast, Blockscout) monkeypatched at the function
level."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.services import x402_trust_score as xts
from aria_core.services.blockscout import TokenTransfer, TokenTransfersResult

PAY_TO = "0xPayToAddress000000000000000000000000001"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
OTHER_TOKEN = "0xOtherToken00000000000000000000000000002"
PAYER_A = "0xPayerA00000000000000000000000000000001"
PAYER_B = "0xPayerB00000000000000000000000000000002"

# Anchored to real wall-clock time (24/08 fix) -- a hardcoded date silently
# broke every test in this file once real time crossed the default 30-day
# lookback window past the original fixed date (24/07 -> 24/08 = 31 days),
# with no test failure until that exact crossing. Same failure class as any
# "NOW = <fixed date>" constant compared against `datetime.now()` in
# production code -- relative deltas (`stale = NOW - timedelta(...)`) stay
# correct either way, only the anchor itself needs to track real time.
NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _transfer(*, token=USDC, to=PAY_TO, frm=PAYER_A, amount=0.02, when=NOW) -> TokenTransfer:
    return TokenTransfer(
        tx_hash="0xabc", from_address=frm, to_address=to, token_address=token,
        token_symbol="USDC", token_name="USD Coin", amount=amount, timestamp=_iso(when),
    )


@pytest.mark.asyncio
async def test_missing_address_unavailable_without_any_call(monkeypatch):
    def _fail(*a, **kw):
        raise AssertionError("must never be called for a missing address")

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fail)
    result = await xts.compute_x402_trust_score("")
    assert result.available is False


@pytest.mark.asyncio
async def test_happy_path_uses_fast_provider_first(monkeypatch):
    async def _fake_fast(address, chain, **kw):
        return TokenTransfersResult(
            available=True,
            transfers=[
                _transfer(frm=PAYER_A, amount=0.02),
                _transfer(frm=PAYER_A, amount=0.02),  # même payeur, 2e appel
                _transfer(frm=PAYER_B, amount=0.05),
            ],
        )

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fake_fast)

    class _ExplodingClient:
        async def get_token_transfers(self, *a, **kw):
            raise AssertionError("must never fall back to Blockscout when the fast provider succeeds")

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _ExplodingClient()
    )

    result = await xts.compute_x402_trust_score(PAY_TO)

    assert result.available is True
    assert result.transfer_count == 3
    assert result.unique_payers == 2
    assert round(result.volume_usd, 4) == 0.09


@pytest.mark.asyncio
async def test_falls_back_to_blockscout_when_fast_provider_unavailable(monkeypatch):
    async def _fake_fast(address, chain, **kw):
        return TokenTransfersResult(available=False, error="gate off")

    class _FakeBlockscoutClient:
        async def get_token_transfers(self, address, **kw):
            return TokenTransfersResult(available=True, transfers=[_transfer(frm=PAYER_A, amount=0.03)])

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fake_fast)
    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscoutClient()
    )

    result = await xts.compute_x402_trust_score(PAY_TO)

    assert result.available is True
    assert result.transfer_count == 1
    assert round(result.volume_usd, 4) == 0.03


@pytest.mark.asyncio
async def test_ignores_non_usdc_transfers(monkeypatch):
    async def _fake_fast(address, chain, **kw):
        return TokenTransfersResult(
            available=True,
            transfers=[_transfer(token=OTHER_TOKEN, amount=100.0), _transfer(token=USDC, amount=0.01)],
        )

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fake_fast)

    result = await xts.compute_x402_trust_score(PAY_TO)

    assert result.transfer_count == 1
    assert round(result.volume_usd, 4) == 0.01


@pytest.mark.asyncio
async def test_ignores_transfers_to_a_different_address(monkeypatch):
    async def _fake_fast(address, chain, **kw):
        return TokenTransfersResult(
            available=True,
            transfers=[_transfer(to="0xSomeoneElse000000000000000000000000003", amount=5.0)],
        )

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fake_fast)

    result = await xts.compute_x402_trust_score(PAY_TO)

    assert result.transfer_count == 0
    assert result.volume_usd == 0.0


@pytest.mark.asyncio
async def test_excludes_transfers_outside_the_lookback_window(monkeypatch):
    stale = NOW - timedelta(days=45)

    async def _fake_fast(address, chain, **kw):
        return TokenTransfersResult(
            available=True,
            transfers=[_transfer(amount=0.02, when=NOW), _transfer(amount=999.0, when=stale)],
        )

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fake_fast)

    result = await xts.compute_x402_trust_score(PAY_TO, lookback_days=30)

    assert result.transfer_count == 1
    assert round(result.volume_usd, 4) == 0.02


@pytest.mark.asyncio
async def test_both_sources_unavailable_degrades_honestly(monkeypatch):
    async def _fake_fast(address, chain, **kw):
        return TokenTransfersResult(available=False, error="gate off")

    class _FakeBlockscoutClient:
        async def get_token_transfers(self, address, **kw):
            return TokenTransfersResult(available=False, error="Blockscout down")

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fake_fast)
    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscoutClient()
    )

    result = await xts.compute_x402_trust_score(PAY_TO)

    assert result.available is False
    assert result.error == "Blockscout down"


@pytest.mark.asyncio
async def test_non_base_chain_skips_fast_provider_and_goes_straight_to_blockscout(monkeypatch):
    def _fail(*a, **kw):
        raise AssertionError("must never be called for a non-base chain")

    class _FakeBlockscoutClient:
        async def get_token_transfers(self, address, **kw):
            return TokenTransfersResult(available=True, transfers=[_transfer(amount=0.02)])

    monkeypatch.setattr("aria_core.services.wallet_transfers_fast.get_fast_token_transfers", _fail)
    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscoutClient()
    )

    result = await xts.compute_x402_trust_score(PAY_TO, chain="ethereum")

    assert result.available is True
