"""17/08, first milestone of the homemade agent wallet's Robinhood Chain leg
-- read-only contract-deployment verification only, no signing yet. Mirrors
test_sepolia_wallet.py's fake-w3 injection pattern (never a real network
call in tests)."""
from __future__ import annotations

from aria_core.onchain import safe_robinhood_wallet as srw


class _FakeEth:
    def __init__(self, *, chain_id, code_by_address):
        self.chain_id = chain_id
        self._code_by_address = code_by_address

    def get_code(self, address):
        return self._code_by_address[address]


class _FakeW3:
    def __init__(self, *, chain_id, code_by_address):
        self.eth = _FakeEth(chain_id=chain_id, code_by_address=code_by_address)


def _deployed_w3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID):
    return _FakeW3(
        chain_id=chain_id,
        code_by_address={
            srw.SAFE_SINGLETON_V141_ADDRESS: b"\x60\x80" * 100,
            srw.ALLOWANCE_MODULE_ADDRESS: b"\x60\x80" * 50,
        },
    )


def test_verify_contracts_deployed_happy_path():
    result = srw.verify_contracts_deployed(w3=_deployed_w3())
    assert result["error"] is None
    assert result["chain_id_ok"] is True
    assert result["safe_singleton"]["deployed"] is True
    assert result["allowance_module"]["deployed"] is True


def test_verify_contracts_deployed_flags_wrong_chain():
    result = srw.verify_contracts_deployed(w3=_deployed_w3(chain_id=1))
    assert result["chain_id_ok"] is False
    assert result["chain_id"] == 1


def test_verify_contracts_deployed_flags_empty_bytecode():
    w3 = _FakeW3(
        chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID,
        code_by_address={srw.SAFE_SINGLETON_V141_ADDRESS: b"", srw.ALLOWANCE_MODULE_ADDRESS: b""},
    )
    result = srw.verify_contracts_deployed(w3=w3)
    assert result["safe_singleton"]["deployed"] is False
    assert result["allowance_module"]["deployed"] is False


def test_verify_contracts_deployed_never_raises_on_rpc_failure():
    class _BrokenEth:
        @property
        def chain_id(self):
            raise ConnectionError("RPC unreachable")

    class _BrokenW3:
        eth = _BrokenEth()

    result = srw.verify_contracts_deployed(w3=_BrokenW3())
    assert result["error"] is not None
    assert result["chain_id_ok"] is None
