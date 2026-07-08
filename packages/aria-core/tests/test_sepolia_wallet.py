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
    def __init__(self, root_bytes):
        self.root_bytes = root_bytes

    def build_transaction(self, params):
        return {"root": self.root_bytes, **params}


class _FakeContract:
    def __init__(self, address):
        self.address = address

    class functions:  # noqa: N801 — miroir de l'API web3.py (Contract.functions)
        @staticmethod
        def anchor(root_bytes):
            return _FakeFunctionCall(root_bytes)


class _FakeEth:
    def __init__(self):
        self.sent: list[bytes] = []

    def get_balance(self, addr):
        return 10**18  # 1 ETH en wei

    def get_transaction_count(self, addr):
        return 7

    def send_raw_transaction(self, raw):
        self.sent.append(raw)

        class _Hash:
            def hex(self):
                return "0xdeadbeef"
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
    assert tx_hash == "0xdeadbeef"
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
