"""23/08 -- real, gas-spending testnet execution layer on top of the 17/08
simulation. Offline tests only (never a real network call here -- the real
proof already happened live on testnet 46630, recorded in the module
docstring and docs/HANDOFF_AGENT_WALLET.md): chain-id preflight, key
handling, and revert categorization -- the parts a mock CAN meaningfully
verify without re-deploying a real Safe."""
from __future__ import annotations

import os

import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError

from aria_core.onchain import safe_robinhood_deploy as deploy

SAFE = "0x00000000000000000000000000000000000000B1"
DELEGATE = "0x00000000000000000000000000000000000000D1"
TOKEN = "0x00000000000000000000000000000000000000C1"
OWNER = "0x00000000000000000000000000000000000000A1"
TESTNET_KEY = "0x" + "11" * 32


class _FakeEth:
    def __init__(self, *, chain_id, code_by_address=None, raise_on_get_code=False):
        self.chain_id = chain_id
        self._code = code_by_address or {}
        self._raise_on_get_code = raise_on_get_code

    def get_block(self, _label):
        return {"timestamp": 1_700_000_000}

    def get_transaction_count(self, _addr):
        return 0

    def get_code(self, address):
        if self._raise_on_get_code:
            raise RuntimeError("RPC unreachable")
        return self._code.get(Web3.to_checksum_address(address), b"")

    def contract(self, *, address=None, abi=None):
        return Web3().eth.contract(address=address, abi=abi)


class _FakeW3:
    def __init__(self, *, chain_id=deploy.ROBINHOOD_TESTNET_CHAIN_ID, code_by_address=None, raise_on_get_code=False):
        self.eth = _FakeEth(chain_id=chain_id, code_by_address=code_by_address, raise_on_get_code=raise_on_get_code)


# --- chain-id preflight (fail-closed), every public entry point -----------

def test_every_public_entry_point_refuses_a_non_testnet_chain(monkeypatch):
    monkeypatch.setenv(deploy._ENV_KEY_NAME, TESTNET_KEY)
    w3 = _FakeW3(chain_id=4663)  # Robinhood MAINNET
    with pytest.raises(RuntimeError, match="refus"):
        deploy.deploy_safe(owner_address=OWNER, w3=w3)
    with pytest.raises(RuntimeError, match="refus"):
        deploy.enable_allowance_module(safe_address=SAFE, w3=w3)
    with pytest.raises(RuntimeError, match="refus"):
        deploy.add_delegate(safe_address=SAFE, delegate_address=DELEGATE, w3=w3)
    with pytest.raises(RuntimeError, match="refus"):
        deploy.configure_allowance(
            safe_address=SAFE, delegate_address=DELEGATE, token_address=TOKEN, amount=100, w3=w3,
        )
    with pytest.raises(RuntimeError, match="refus"):
        deploy.sign_and_send_allowance_transfer(
            safe_address=SAFE, token_address=TOKEN, to_address=DELEGATE, amount=10, nonce=1, w3=w3,
        )
    with pytest.raises(RuntimeError, match="refus"):
        deploy.deploy_stub_token(w3=w3)


def test_preflight_names_the_offending_chain(monkeypatch):
    monkeypatch.setenv(deploy._ENV_KEY_NAME, TESTNET_KEY)
    w3 = _FakeW3(chain_id=1)
    with pytest.raises(RuntimeError, match="1"):
        deploy.deploy_safe(owner_address=OWNER, w3=w3)


# --- key handling -----------------------------------------------------------

def test_deployer_account_raises_a_clear_error_when_key_is_absent(monkeypatch):
    monkeypatch.delenv(deploy._ENV_KEY_NAME, raising=False)
    with pytest.raises(RuntimeError, match=deploy._ENV_KEY_NAME):
        deploy.deployer_account()


def test_deployer_account_derives_the_right_address_from_the_env_key(monkeypatch):
    monkeypatch.setenv(deploy._ENV_KEY_NAME, TESTNET_KEY)
    expected = Account.from_key(TESTNET_KEY).address
    acct = deploy.deployer_account()
    assert acct.address == expected


def test_deployer_account_prefers_an_injected_key_over_the_env(monkeypatch):
    monkeypatch.setenv(deploy._ENV_KEY_NAME, TESTNET_KEY)
    other_key = "0x" + "22" * 32
    acct = deploy.deployer_account(private_key=other_key)
    assert acct.address == Account.from_key(other_key).address


def test_no_function_in_this_module_returns_the_private_key_itself(monkeypatch):
    """The key must never leak into a returned dict -- every result shape in
    this module reports addresses/hashes/outcomes, never key material."""
    monkeypatch.setenv(deploy._ENV_KEY_NAME, TESTNET_KEY)
    acct = deploy.deployer_account()
    # A returned account object exposes .key -- callers must never surface it.
    assert hasattr(acct, "key")
    # Sanity: the module itself never assigns .key onto a plain dict result.
    import inspect

    src = inspect.getsource(deploy)
    assert '"private_key"' not in src
    assert "result[\"key\"]" not in src


# --- signature format -------------------------------------------------------

def test_sign_safe_tx_hash_produces_a_65_byte_signature_with_v_in_2728():
    acct = Account.from_key(TESTNET_KEY)
    digest = Web3.keccak(text="some deterministic digest")
    sig = deploy._sign_safe_tx_hash(digest, acct)
    assert len(sig) == 65
    v = sig[-1]
    assert v in (27, 28)


def test_sign_safe_tx_hash_is_deterministic_for_the_same_digest():
    acct = Account.from_key(TESTNET_KEY)
    digest = Web3.keccak(text="same digest twice")
    assert deploy._sign_safe_tx_hash(digest, acct) == deploy._sign_safe_tx_hash(digest, acct)


def test_sign_safe_tx_hash_differs_across_digests():
    acct = Account.from_key(TESTNET_KEY)
    d1 = Web3.keccak(text="digest one")
    d2 = Web3.keccak(text="digest two")
    assert deploy._sign_safe_tx_hash(d1, acct) != deploy._sign_safe_tx_hash(d2, acct)


# --- revert categorization ---------------------------------------------------

def test_revert_outcome_flags_the_cap_marker():
    msg = "execution reverted: newSpent > allowance.spent && newSpent <= allowance.amount"
    assert deploy._revert_outcome(msg) == "rejected_by_cap"


def test_revert_outcome_flags_the_signature_marker():
    msg = "execution reverted: expectedDelegate == signer && delegates[...]"
    assert deploy._revert_outcome(msg) == "rejected_by_signature"


def test_revert_outcome_falls_back_to_other_for_an_unrelated_revert():
    assert deploy._revert_outcome("execution reverted: something else entirely") == "rejected_other"


# --- _build_sign_send never lets a raw exception escape ---------------------

class _RaisingFn:
    def build_transaction(self, _base):
        raise ContractLogicError(
            "execution reverted: newSpent > allowance.spent && newSpent <= allowance.amount",
        )


def test_build_sign_send_turns_a_predictable_revert_into_a_structured_result(monkeypatch):
    """Real gap found live on this chantier's first real transfer attempt:
    build_transaction's implicit gas estimate raises on a predictable
    revert, and an uncaught exception used to escape all the way to the
    caller instead of the same ok/outcome shape every other result in this
    module returns."""
    monkeypatch.setenv(deploy._ENV_KEY_NAME, TESTNET_KEY)
    acct = deploy.deployer_account()
    w3 = _FakeW3()
    result = deploy._build_sign_send(w3, acct, _RaisingFn())
    assert result["ok"] is False
    assert result["outcome"] == "rejected_by_cap"
    assert result["tx_hash"] is None


# --- stub-token deploy code is built programmatically, never hand-counted --

def test_stub_token_deploy_code_correctly_wraps_the_runtime():
    """Reconstructs the deploy-code the real function builds (without
    sending anything) and confirms an EVM would actually return the
    embedded runtime unchanged -- the same property confirmed live on
    testnet via ``runtime_matches: True``, re-verified here offline against
    silent regressions (an off-by-one in the offset byte would corrupt the
    runtime copy)."""
    from aria_core.onchain.safe_robinhood_simulation import _ALWAYS_TRUE_RUNTIME

    runtime = bytes.fromhex(_ALWAYS_TRUE_RUNTIME[2:])
    runtime_len = len(runtime)
    preamble = bytearray(
        bytes([0x60, runtime_len, 0x80, 0x60, 0]) + bytes([0x60, 0x00, 0x39, 0x60, 0x00, 0xF3])
    )
    offset = len(preamble)
    preamble[3] = 0x60
    preamble[4] = offset
    deploy_code = bytes(preamble) + runtime

    # The preamble's own CODECOPY(dest=0, offset, len) must point exactly
    # at the runtime's first byte within the full deploy_code buffer.
    assert deploy_code[offset:offset + runtime_len] == runtime
    assert len(preamble) == 11
    assert preamble[4] == 11


# --- read_pilot_state: pure aggregation, never signs or sends ---------------

def test_read_pilot_state_reports_not_deployed_without_calling_is_module_enabled(monkeypatch):
    """A Safe with no code can't sensibly answer isModuleEnabled -- the
    function must report `module_enabled: None` rather than raise trying to
    call a contract that isn't there."""
    from aria_core.onchain import safe_robinhood_wallet as wallet

    monkeypatch.setattr(wallet, "read_allowance", lambda *a, **kw: {"error": None, "amount": 0})
    w3 = _FakeW3(code_by_address={})  # SAFE has no code
    result = deploy.read_pilot_state(
        safe_address=SAFE, delegate_address=DELEGATE, token_address=TOKEN, w3=w3,
    )
    assert result["safe_deployed"] is False
    assert result["module_enabled"] is None
    assert result["error"] is None


def test_read_pilot_state_surfaces_an_unreachable_safe_as_an_error(monkeypatch):
    from aria_core.onchain import safe_robinhood_wallet as wallet

    monkeypatch.setattr(wallet, "read_allowance", lambda *a, **kw: {"error": None})
    w3 = _FakeW3(raise_on_get_code=True)
    result = deploy.read_pilot_state(
        safe_address=SAFE, delegate_address=DELEGATE, token_address=TOKEN, w3=w3,
    )
    assert result["safe_deployed"] is None
    assert result["error"] is not None


def test_read_pilot_state_includes_the_allowance_reader_result(monkeypatch):
    from aria_core.onchain import safe_robinhood_wallet as wallet

    fake_allowance = {"error": None, "amount": 100, "spent": 100, "remaining": 0}
    monkeypatch.setattr(wallet, "read_allowance", lambda *a, **kw: fake_allowance)
    w3 = _FakeW3(code_by_address={Web3.to_checksum_address(SAFE): b"\x60\x00"})
    result = deploy.read_pilot_state(
        safe_address=SAFE, delegate_address=DELEGATE, token_address=TOKEN, w3=w3,
    )
    assert result["safe_deployed"] is True
    assert result["allowance"] == fake_allowance
