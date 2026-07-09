"""Wallet Sepolia d'ARIA — signature réelle, mais verrouillée au testnet (hors-ligne, injecté)."""
from __future__ import annotations

import pytest

from aria_core.onchain import sepolia_wallet as sw

GOOD_ROOT = "0x" + "ab" * 32
LEDGER_ADDRESS = "0x000000000000000000000000000000000000dEaD"


class _FakeAccount:
    address = "0xFakeAddress0000000000000000000000000001"

    @classmethod
    def from_key(cls, key):
        assert key == "0xsecret"
        return cls()

    def sign_transaction(self, tx):
        class _Signed:
            raw_transaction = b"\x01\x02\x03"
        return _Signed()


class _FakeFunctionCall:
    def __init__(self, call_repr):
        self.call_repr = call_repr

    def build_transaction(self, params):
        return {"call": self.call_repr, **params}


class _FakeContract:
    def __init__(self, address):
        self.address = address

    class functions:  # noqa: N801 — miroir de l'API web3.py (Contract.functions)
        @staticmethod
        def anchor(root_bytes):
            return _FakeFunctionCall(("anchor", root_bytes))

        @staticmethod
        def deposit():
            return _FakeFunctionCall(("deposit",))

        @staticmethod
        def approve(spender, amount):
            return _FakeFunctionCall(("approve", spender, amount))

        @staticmethod
        def exactInputSingle(params):  # noqa: N802 — nom ABI Uniswap V3
            return _FakeFunctionCall(("exactInputSingle", params))


class _FakeEth:
    def __init__(self):
        self.sent: list[bytes] = []
        self._hash_counter = 0

    def get_balance(self, addr):
        return 10**18  # 1 ETH en wei

    def get_transaction_count(self, addr):
        return 7

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        self._hash_counter += 1
        idx = self._hash_counter

        class _Hash:
            def hex(self):
                return f"0xdeadbeef{idx}"
        return _Hash()

    def contract(self, address, abi):
        return _FakeContract(address)


class _FakeW3:
    def __init__(self):
        self.eth = _FakeEth()

    def to_checksum_address(self, addr):
        return addr

    def from_wei(self, wei, unit):
        assert unit == "ether"
        return wei / 10**18


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("ARIA_SEPOLIA_WALLET_ENABLED", "1")
    monkeypatch.setenv("ARIA_SEPOLIA_PRIVATE_KEY", "0xsecret")
    yield


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_WALLET_ENABLED", raising=False)
    assert sw.get_address(account_cls=_FakeAccount) is None


def test_get_address_when_enabled():
    assert sw.get_address(account_cls=_FakeAccount) == _FakeAccount.address


def test_no_key_configured(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_PRIVATE_KEY", raising=False)
    assert sw.get_address(account_cls=_FakeAccount) is None


def test_get_balance_eth():
    balance = sw.get_balance_eth(w3=_FakeW3(), account_cls=_FakeAccount)
    assert balance == 1.0


def test_send_anchor_transaction_signs_and_sends():
    w3 = _FakeW3()
    tx_hash = sw.send_anchor_transaction(
        contract=LEDGER_ADDRESS, root=GOOD_ROOT, chain_id=sw.SEPOLIA_CHAIN_ID,
        w3=w3, account_cls=_FakeAccount,
    )
    assert tx_hash == "0xdeadbeef1"
    assert len(w3.eth.sent) == 1


def test_refuses_non_sepolia_chain_id():
    with pytest.raises(RuntimeError, match="Sepolia"):
        sw.send_anchor_transaction(
            contract=LEDGER_ADDRESS, root=GOOD_ROOT, chain_id=8453,  # mainnet — refusé
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


def test_refuses_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_WALLET_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="désactivé"):
        sw.send_anchor_transaction(
            contract=LEDGER_ADDRESS, root=GOOD_ROOT, chain_id=sw.SEPOLIA_CHAIN_ID,
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


def test_refuses_when_no_key(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ARIA_SEPOLIA_PRIVATE_KEY"):
        sw.send_anchor_transaction(
            contract=LEDGER_ADDRESS, root=GOOD_ROOT, chain_id=sw.SEPOLIA_CHAIN_ID,
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


ROUTER_ADDRESS = "0x000000000000000000000000000000000000BEEF"
TOKEN_OUT_ADDRESS = "0x000000000000000000000000000000000000CAFE"


def _swap_env(monkeypatch, *, enabled=True):
    if enabled:
        monkeypatch.setenv("ARIA_SEPOLIA_SWAP_ENABLED", "1")
    monkeypatch.setenv("ARIA_SEPOLIA_SWAP_ROUTER", ROUTER_ADDRESS)
    monkeypatch.setenv("ARIA_SEPOLIA_SWAP_TOKEN_OUT", TOKEN_OUT_ADDRESS)


def test_swap_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_SWAP_ENABLED", raising=False)
    assert sw.sepolia_swap_enabled() is False


def test_swap_requires_wallet_enabled_too(monkeypatch):
    monkeypatch.delenv("ARIA_SEPOLIA_WALLET_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_SEPOLIA_SWAP_ENABLED", "1")
    assert sw.sepolia_swap_enabled() is False


def test_send_test_swap_refuses_when_disabled(monkeypatch):
    _swap_env(monkeypatch, enabled=False)
    with pytest.raises(RuntimeError, match="désactivé"):
        sw.send_test_swap_transaction(
            amount_in_wei=10**15, chain_id=sw.SEPOLIA_CHAIN_ID,
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


def test_send_test_swap_refuses_non_sepolia(monkeypatch):
    _swap_env(monkeypatch)
    with pytest.raises(RuntimeError, match="Sepolia"):
        sw.send_test_swap_transaction(
            amount_in_wei=10**15, chain_id=8453,
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


def test_send_test_swap_refuses_amount_over_cap(monkeypatch):
    _swap_env(monkeypatch)
    with pytest.raises(RuntimeError, match="hors bornes"):
        sw.send_test_swap_transaction(
            amount_in_wei=sw.MAX_TEST_SWAP_WEI + 1, chain_id=sw.SEPOLIA_CHAIN_ID,
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


def test_send_test_swap_refuses_zero_amount(monkeypatch):
    _swap_env(monkeypatch)
    with pytest.raises(RuntimeError, match="hors bornes"):
        sw.send_test_swap_transaction(
            amount_in_wei=0, chain_id=sw.SEPOLIA_CHAIN_ID,
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


def test_send_test_swap_refuses_missing_router_config(monkeypatch):
    monkeypatch.setenv("ARIA_SEPOLIA_SWAP_ENABLED", "1")
    monkeypatch.delenv("ARIA_SEPOLIA_SWAP_ROUTER", raising=False)
    monkeypatch.delenv("ARIA_SEPOLIA_SWAP_TOKEN_OUT", raising=False)
    with pytest.raises(RuntimeError, match="non configurés"):
        sw.send_test_swap_transaction(
            amount_in_wei=10**15, chain_id=sw.SEPOLIA_CHAIN_ID,
            w3=_FakeW3(), account_cls=_FakeAccount,
        )


def test_send_test_swap_happy_path_signs_three_transactions(monkeypatch):
    _swap_env(monkeypatch)
    w3 = _FakeW3()
    result = sw.send_test_swap_transaction(
        amount_in_wei=10**15, chain_id=sw.SEPOLIA_CHAIN_ID,
        w3=w3, account_cls=_FakeAccount,
    )
    assert result == {
        "deposit_tx": "0xdeadbeef1",
        "approve_tx": "0xdeadbeef2",
        "swap_tx": "0xdeadbeef3",
    }
    assert len(w3.eth.sent) == 3


def test_send_test_swap_uses_op_stack_weth_predeploy_by_default(monkeypatch):
    _swap_env(monkeypatch)
    monkeypatch.delenv("ARIA_SEPOLIA_SWAP_TOKEN_IN", raising=False)
    assert sw.swap_token_in() == "0x4200000000000000000000000000000000000006"


class _FakeEthWithCode:
    def __init__(self, code: bytes):
        self._code = code

    def get_code(self, addr):
        return self._code


class _FakeW3WithCode:
    def __init__(self, code: bytes):
        self.eth = _FakeEthWithCode(code)

    def to_checksum_address(self, addr):
        return addr


def test_get_code_reports_deployed_contract():
    w3 = _FakeW3WithCode(b"\x60\x80\x60\x40")
    result = sw.get_code("0x4200000000000000000000000000000000000006", w3=w3)
    assert result["has_code"] is True
    assert result["code_length_bytes"] == 4


def test_get_code_reports_empty_address():
    w3 = _FakeW3WithCode(b"")
    result = sw.get_code("0x0000000000000000000000000000000000dead", w3=w3)
    assert result["has_code"] is False
    assert result["code_length_bytes"] == 0


def test_get_code_returns_none_on_rpc_failure():
    class _BrokenEth:
        def get_code(self, addr):
            raise ConnectionError("RPC unreachable")

    class _BrokenW3:
        eth = _BrokenEth()

        def to_checksum_address(self, addr):
            return addr

    assert sw.get_code("0xabc", w3=_BrokenW3()) is None
