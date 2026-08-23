"""Robinhood Chain swap router -- offline tests only, same fake-w3 injection
doctrine as ``test_sepolia_wallet.py``/``test_safe_robinhood_signer.py``.
This module is NEVER wired to production (see its own module docstring) --
these tests exist to prove the mechanism is correct and bounded, ready for
the day a governance decision and a verified testnet DEX address exist."""
from __future__ import annotations

import pytest

from aria_core.onchain import robinhood_swap_router as rsr
from aria_core.onchain import safe_robinhood_wallet as srw

ROUTER = "0x" + "aa" * 20
TOKEN_IN = "0x" + "bb" * 20
TOKEN_OUT = "0x" + "cc" * 20
RECIPIENT = "0x" + "dd" * 20


def _config(**overrides):
    params = dict(
        router_address=ROUTER, token_in=TOKEN_IN, token_out=TOKEN_OUT,
        fee_tier=3000, slippage_bps=500,
    )
    params.update(overrides)
    return rsr.RobinhoodSwapConfig(**params)


# ── RobinhoodSwapConfig ──────────────────────────────────────────────────

def test_config_happy_path():
    cfg = _config()
    assert cfg.chain_id == srw.ROBINHOOD_TESTNET_CHAIN_ID


def test_config_rejects_mainnet_chain_id():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="testnet"):
        _config(chain_id=4663)


def test_config_rejects_missing_router_address():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="explicitement"):
        _config(router_address="")


def test_config_rejects_missing_token_in():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="explicitement"):
        _config(token_in="")


def test_config_rejects_missing_token_out():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="explicitement"):
        _config(token_out="")


def test_config_rejects_zero_slippage():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="slippage"):
        _config(slippage_bps=0)


def test_config_rejects_negative_slippage():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="slippage"):
        _config(slippage_bps=-1)


def test_config_rejects_slippage_over_ten_percent():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="slippage"):
        _config(slippage_bps=rsr.MAX_SLIPPAGE_BPS + 1)


def test_config_accepts_slippage_exactly_at_ten_percent_ceiling():
    cfg = _config(slippage_bps=rsr.MAX_SLIPPAGE_BPS)
    assert cfg.slippage_bps == 1_000


def test_config_rejects_zero_fee_tier():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="fee_tier"):
        _config(fee_tier=0)


# ── compute_min_amount_out ───────────────────────────────────────────────

def test_compute_min_amount_out_applies_slippage():
    # 5% slippage (500 bps) on 1_000_000 -> 950_000
    assert rsr.compute_min_amount_out(1_000_000, 500) == 950_000


def test_compute_min_amount_out_floors_never_rounds_up():
    # 1 bps of 999 rounds down, never favors the trader over the floor
    assert rsr.compute_min_amount_out(999, 1) == 998


def test_compute_min_amount_out_rejects_slippage_over_ceiling():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="hors bornes"):
        rsr.compute_min_amount_out(1_000, rsr.MAX_SLIPPAGE_BPS + 1)


def test_compute_min_amount_out_rejects_zero_slippage():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="hors bornes"):
        rsr.compute_min_amount_out(1_000, 0)


def test_compute_min_amount_out_rejects_negative_quote():
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="negatif"):
        rsr.compute_min_amount_out(-1, 500)


# ── build_swap_transaction (pure construction, no signing) ──────────────

class _FakeFunctionCall:
    def __init__(self, call_repr):
        self.call_repr = call_repr

    def build_transaction(self, params):
        return {"call": self.call_repr, **params}


class _FakeContract:
    class functions:  # noqa: N801 -- mirrors web3.py's Contract.functions
        @staticmethod
        def exactInputSingle(params):  # noqa: N802 -- Uniswap V3 ABI name
            return _FakeFunctionCall(("exactInputSingle", params))

        @staticmethod
        def deposit():
            return _FakeFunctionCall(("deposit",))

        @staticmethod
        def approve(spender, amount):
            return _FakeFunctionCall(("approve", spender, amount))


class _FakeEth:
    def __init__(self, *, chain_id, tx_count=0, send_should_fail=False, receipt_status=1):
        self.chain_id = chain_id
        self._tx_count = tx_count
        self._send_should_fail = send_should_fail
        self._receipt_status = receipt_status
        self.sent: list = []

    def contract(self, address=None, abi=None):
        return _FakeContract()

    def get_transaction_count(self, address):
        return self._tx_count

    def send_raw_transaction(self, raw):
        if self._send_should_fail:
            raise RuntimeError("RPC send failed (simulated)")
        self.sent.append(raw)

        class _Hash:
            def __init__(self, n):
                self._n = n

            def hex(self):
                return f"0xdeadbeef{self._n}"
        return _Hash(len(self.sent))

    def wait_for_transaction_receipt(self, tx_hash, timeout=60):
        class _Receipt:
            status = self._receipt_status
        return _Receipt()


class _FakeW3:
    def __init__(self, **kw):
        self.eth = _FakeEth(**kw)

    def to_checksum_address(self, addr):
        return addr


class _FakeAccount:
    address = RECIPIENT

    def sign_transaction(self, tx):
        class _Signed:
            raw_transaction = b"\x01\x02\x03"
        return _Signed()


def test_build_swap_transaction_applies_slippage_floor():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    tx = rsr.build_swap_transaction(
        config=_config(slippage_bps=500), amount_in=10**15, quoted_amount_out=1_000_000,
        recipient=RECIPIENT, nonce=3, w3=w3,
    )
    _, params = tx["call"]
    token_in, token_out, fee, recipient, amount_in, min_out, sqrt_limit = params
    assert token_in == TOKEN_IN
    assert token_out == TOKEN_OUT
    assert fee == 3000
    assert recipient == RECIPIENT
    assert amount_in == 10**15
    assert min_out == 950_000  # 5% slippage floor
    assert sqrt_limit == 0
    assert tx["nonce"] == 3
    assert tx["chainId"] == srw.ROBINHOOD_TESTNET_CHAIN_ID


def test_build_swap_transaction_rejects_amount_over_cap():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="hors bornes"):
        rsr.build_swap_transaction(
            config=_config(), amount_in=rsr.MAX_TEST_SWAP_WEI + 1, quoted_amount_out=1,
            recipient=RECIPIENT, nonce=0, w3=w3,
        )


def test_build_swap_transaction_rejects_zero_amount():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    with pytest.raises(rsr.RobinhoodSwapConfigError, match="hors bornes"):
        rsr.build_swap_transaction(
            config=_config(), amount_in=0, quoted_amount_out=1,
            recipient=RECIPIENT, nonce=0, w3=w3,
        )


# ── execute_bounded_swap (full sequence, offline) ────────────────────────

@pytest.mark.asyncio
async def test_execute_bounded_swap_refuses_wrong_chain():
    w3 = _FakeW3(chain_id=4663)  # mainnet -- must be refused
    with pytest.raises(RuntimeError, match="testnet"):
        await rsr.execute_bounded_swap(
            config=_config(), amount=10**15, quoted_amount_out=1_000,
            account=_FakeAccount(), w3=w3,
        )


@pytest.mark.asyncio
async def test_execute_bounded_swap_rejects_amount_over_cap():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await rsr.execute_bounded_swap(
        config=_config(), amount=rsr.MAX_TEST_SWAP_WEI + 1, quoted_amount_out=1_000,
        account=_FakeAccount(), w3=w3,
    )
    assert result["error"] is not None
    assert result["tx_hash"] is None
    assert w3.eth.sent == []


@pytest.mark.asyncio
async def test_execute_bounded_swap_rejects_zero_amount():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await rsr.execute_bounded_swap(
        config=_config(), amount=0, quoted_amount_out=1_000,
        account=_FakeAccount(), w3=w3,
    )
    assert result["error"] is not None
    assert result["tx_hash"] is None


@pytest.mark.asyncio
async def test_execute_bounded_swap_happy_path_without_wrap_signs_two_transactions():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await rsr.execute_bounded_swap(
        config=_config(), amount=10**15, quoted_amount_out=1_000_000,
        account=_FakeAccount(), w3=w3, wrap_native=False,
    )
    assert result["error"] is None
    assert result["deposit_tx"] is None
    assert result["approve_tx"] == "0xdeadbeef1"
    assert result["swap_tx"] == "0xdeadbeef2"
    assert result["tx_hash"] == "0xdeadbeef2"
    assert result["status"] == "ok"
    assert len(w3.eth.sent) == 2


@pytest.mark.asyncio
async def test_execute_bounded_swap_happy_path_with_wrap_signs_three_transactions():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await rsr.execute_bounded_swap(
        config=_config(), amount=10**15, quoted_amount_out=1_000_000,
        account=_FakeAccount(), w3=w3, wrap_native=True,
    )
    assert result["error"] is None
    assert result["deposit_tx"] == "0xdeadbeef1"
    assert result["approve_tx"] == "0xdeadbeef2"
    assert result["swap_tx"] == "0xdeadbeef3"
    assert len(w3.eth.sent) == 3


@pytest.mark.asyncio
async def test_execute_bounded_swap_reports_send_failure():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID, send_should_fail=True)
    result = await rsr.execute_bounded_swap(
        config=_config(), amount=10**15, quoted_amount_out=1_000_000,
        account=_FakeAccount(), w3=w3,
    )
    assert result["error"] is not None
    assert result["tx_hash"] is None


@pytest.mark.asyncio
async def test_execute_bounded_swap_reports_reverted_receipt():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID, receipt_status=0)
    result = await rsr.execute_bounded_swap(
        config=_config(), amount=10**15, quoted_amount_out=1_000_000,
        account=_FakeAccount(), w3=w3,
    )
    assert result["error"] is None  # the SEND itself succeeded
    assert result["status"] == "reverted"  # but the chain rejected it
