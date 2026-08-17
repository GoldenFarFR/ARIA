"""17/08, step 2 of the homemade agent wallet -- simulation-only proof of the
Safe + AllowanceModule cycle. Mirrors test_safe_robinhood_wallet.py's fake-w3
injection pattern (never a real network call in tests); the live proofs
against the real testnet contracts are recorded in the module docstring and
the HANDOFF, not re-run here."""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest
from web3 import Web3

from aria_core.onchain import safe_robinhood_simulation as sim

SAFE = "0x00000000000000000000000000000000000000B1"
DELEGATE = "0x00000000000000000000000000000000000000D1"
TOKEN = "0x00000000000000000000000000000000000000C1"
OWNER = "0x00000000000000000000000000000000000000A1"


class _FakeEth:
    def __init__(self, *, chain_id, code_by_address=None, call_result=None):
        self.chain_id = chain_id
        self._code = code_by_address or {}
        self._call_result = call_result

    def get_code(self, address):
        return self._code.get(Web3.to_checksum_address(address), b"")

    def contract(self, *, address=None, abi=None):
        return Web3().eth.contract(address=address, abi=abi)


class _FakeProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def make_request(self, method, params):
        self.calls.append((method, params))
        return self._responses.pop(0)


class _FakeW3:
    def __init__(self, *, chain_id=sim.ROBINHOOD_TESTNET_CHAIN_ID, code_by_address=None, responses=None):
        self.eth = _FakeEth(chain_id=chain_id, code_by_address=code_by_address)
        self.provider = _FakeProvider(responses or [])


def _cap_revert(message="execution reverted: newSpent > allowance.spent && newSpent <= allowance.amount"):
    return {"error": {"message": message}}


def _signature_revert():
    return {"error": {"message": "execution reverted: expectedDelegate == signer && delegates[...]"}}


# --- chain-id preflight (fail-closed) ------------------------------------

def test_every_public_entry_point_refuses_a_non_testnet_chain():
    """The preflight is the guardrail that keeps this module from ever
    simulating against real mainnet balances -- it must hold on EVERY entry
    point, not just the first one written."""
    w3 = _FakeW3(chain_id=4663)  # Robinhood MAINNET
    with pytest.raises(RuntimeError, match="refus"):
        sim.verify_safe_stack_deployed(w3=w3)
    with pytest.raises(RuntimeError, match="refus"):
        sim.simulate_safe_creation([OWNER], w3=w3)
    with pytest.raises(RuntimeError, match="refus"):
        sim.simulate_allowance_cap(
            cap=100, requested_amounts=[50], safe=SAFE, delegate=DELEGATE, token=TOKEN, w3=w3,
        )


def test_preflight_names_the_offending_chain():
    w3 = _FakeW3(chain_id=1)
    with pytest.raises(RuntimeError, match="1"):
        sim.verify_safe_stack_deployed(w3=w3)


# --- deployment verification ---------------------------------------------

def test_verify_safe_stack_reports_each_contract_separately():
    w3 = _FakeW3(code_by_address={
        Web3.to_checksum_address(sim.SAFE_PROXY_FACTORY_V141): b"\x60" * 3054,
        Web3.to_checksum_address(sim.SAFE_L2_SINGLETON_V141): b"\x60" * 24421,
        Web3.to_checksum_address(sim.SAFE_FALLBACK_HANDLER_V141): b"\x60" * 5637,
        Web3.to_checksum_address(sim.ALLOWANCE_MODULE_ADDRESS): b"\x60" * 14908,
    })
    result = sim.verify_safe_stack_deployed(w3=w3)
    assert result["all_deployed"] is True
    assert result["sizes"]["proxy_factory"] == 3054


def test_verify_safe_stack_flags_which_piece_is_missing():
    """A missing FACTORY is the one that makes Safe creation impossible --
    the verdict must not collapse to a bare boolean that hides which."""
    w3 = _FakeW3(code_by_address={
        Web3.to_checksum_address(sim.SAFE_L2_SINGLETON_V141): b"\x60" * 100,
        Web3.to_checksum_address(sim.ALLOWANCE_MODULE_ADDRESS): b"\x60" * 100,
    })
    result = sim.verify_safe_stack_deployed(w3=w3)
    assert result["all_deployed"] is False
    assert result["sizes"]["proxy_factory"] == 0
    assert result["sizes"]["singleton_l2"] > 0


# --- allowance packing (field order proven in step 1) --------------------

def test_pack_allowance_places_each_field_at_its_proven_offset():
    packed = sim.pack_allowance(100, spent=7, reset_time_min=3, last_reset_min=9, nonce=2)
    assert (packed >> 0) & ((1 << 96) - 1) == 100
    assert (packed >> 96) & ((1 << 96) - 1) == 7
    assert (packed >> 192) & 0xFFFF == 3
    assert (packed >> 208) & 0xFFFFFFFF == 9
    assert (packed >> 240) & 0xFFFF == 2


def test_pack_allowance_rejects_values_past_uint96():
    """Silently wrapping here would inject an allowance the caller never
    intended -- on a guardrail, that is worse than an exception."""
    with pytest.raises(ValueError, match="uint96"):
        sim.pack_allowance(1 << 96)
    with pytest.raises(ValueError, match="uint96"):
        sim.pack_allowance(10, spent=1 << 96)


def test_one_shot_is_the_default_regime():
    """Operator decision 17/08: the pilot runs one-shot (cap never refills by
    itself). A future edit flipping this default must break a test."""
    packed = sim.pack_allowance(100)
    assert (packed >> 192) & 0xFFFF == 0


def test_allowance_storage_slot_is_deterministic_and_key_sensitive():
    a = sim._allowance_storage_slot(SAFE, DELEGATE, TOKEN)
    assert a == sim._allowance_storage_slot(SAFE, DELEGATE, TOKEN)
    assert a != sim._allowance_storage_slot(SAFE, TOKEN, DELEGATE)  # order matters
    assert re.fullmatch(r"0x[0-9a-f]{64}", a)


# --- THE proof: the cap discriminates ------------------------------------

def test_cap_rejects_over_limit_and_passes_under_limit():
    """The whole point of step 2. A contract that always reverted would look
    identical to a working guardrail if only the over-limit case were
    checked, so both halves are asserted here."""
    w3 = _FakeW3(responses=[_signature_revert(), _signature_revert(), _cap_revert()])
    result = sim.simulate_allowance_cap(
        cap=100, requested_amounts=[50, 100, 150],
        safe=SAFE, delegate=DELEGATE, token=TOKEN, w3=w3,
    )
    outcomes = [r["outcome"] for r in result["results"]]
    assert outcomes == ["passed_cap", "passed_cap", "rejected_by_cap"]
    assert result["cap_enforced_consistently"] is True
    assert result["one_shot"] is True


def test_cap_boundary_is_inclusive():
    """Spending exactly the cap must be allowed -- an off-by-one here would
    quietly shrink the operator's real envelope."""
    w3 = _FakeW3(responses=[_signature_revert()])
    result = sim.simulate_allowance_cap(
        cap=100, requested_amounts=[100], safe=SAFE, delegate=DELEGATE, token=TOKEN, w3=w3,
    )
    assert result["results"][0]["over_cap"] is False
    assert result["cap_enforced_consistently"] is True


def test_already_spent_shrinks_the_remaining_envelope():
    """One-shot regime: what was already spent is gone until a human re-arms
    it, so 60 must be refused once 50 of a 100 cap is consumed."""
    w3 = _FakeW3(responses=[_cap_revert()])
    result = sim.simulate_allowance_cap(
        cap=100, requested_amounts=[60], already_spent=50,
        safe=SAFE, delegate=DELEGATE, token=TOKEN, w3=w3,
    )
    assert result["results"][0]["over_cap"] is True
    assert result["results"][0]["outcome"] == "rejected_by_cap"
    assert result["cap_enforced_consistently"] is True


def test_inconsistent_enforcement_is_reported_not_smoothed_over():
    """If the real contract ever let an over-cap transfer through, this must
    surface loudly rather than be averaged away into a pass."""
    w3 = _FakeW3(responses=[_signature_revert()])
    result = sim.simulate_allowance_cap(
        cap=100, requested_amounts=[150], safe=SAFE, delegate=DELEGATE, token=TOKEN, w3=w3,
    )
    assert result["results"][0]["over_cap"] is True
    assert result["results"][0]["outcome"] == "passed_cap"
    assert result["cap_enforced_consistently"] is False


def test_simulation_uses_eth_call_only():
    w3 = _FakeW3(responses=[_cap_revert()])
    sim.simulate_allowance_cap(
        cap=10, requested_amounts=[50], safe=SAFE, delegate=DELEGATE, token=TOKEN, w3=w3,
    )
    assert [m for m, _ in w3.provider.calls] == ["eth_call"]


def test_cap_must_be_positive():
    w3 = _FakeW3()
    with pytest.raises(ValueError, match="cap"):
        sim.simulate_allowance_cap(
            cap=0, requested_amounts=[1], safe=SAFE, delegate=DELEGATE, token=TOKEN, w3=w3,
        )


# --- Safe creation simulation --------------------------------------------

def test_safe_creation_rejects_an_incoherent_threshold():
    w3 = _FakeW3()
    with pytest.raises(ValueError, match="threshold"):
        sim.simulate_safe_creation([OWNER], threshold=2, w3=w3)


def test_safe_creation_requires_at_least_one_owner():
    w3 = _FakeW3()
    with pytest.raises(ValueError, match="owner"):
        sim.simulate_safe_creation([], w3=w3)


# --- structural guardrail ------------------------------------------------

def _identifiers_used(module) -> set[str]:
    """Every attribute/name/string-literal actually referenced in the module's
    AST. Deliberately AST-based rather than a raw text scan: the docstring
    legitimately NAMES the forbidden calls to explain why they are absent, and
    a text scan flags its own documentation (found live while writing this
    test). The AST sees real code only."""
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.add(node.value)  # catches a make_request("eth_sendTransaction", ...)
    return used


def test_simulation_module_never_sends_a_transaction():
    """Structural guardrail, the counterpart of
    ``test_allowance_module_abi_is_read_only`` on the read-only module: this
    one carries WRITE-function ABIs, so what keeps it harmless is that it can
    only ever reach them through ``eth_call``. Adding any real send path here
    must break CI immediately."""
    used = _identifiers_used(sim)
    for forbidden in (
        "send_raw_transaction", "send_transaction", "build_transaction",
        "sign_transaction", "eth_sendTransaction", "eth_sendRawTransaction",
        "transact",
    ):
        assert forbidden not in used, f"chemin d'ecriture interdit trouve: {forbidden}"


def test_simulation_module_reads_no_private_key_from_the_environment():
    """No key material, no env lookup: the module cannot be turned into a
    signer by configuration alone."""
    used = _identifiers_used(sim)
    for forbidden in ("environ", "getenv", "private_key", "from_key", "account"):
        assert forbidden not in used, f"acces cle/env interdit trouve: {forbidden}"


def test_only_eth_call_is_ever_issued_as_a_raw_rpc_method():
    """The module talks to the RPC through make_request in one place; assert
    the only literal RPC method name in the whole module is the read-only
    one."""
    used = _identifiers_used(sim)
    rpc_methods = {s for s in used if s.startswith("eth_")}
    assert rpc_methods == {"eth_call"}, f"methodes RPC inattendues: {rpc_methods}"
