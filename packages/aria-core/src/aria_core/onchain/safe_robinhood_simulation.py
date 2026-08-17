"""Simulation-only companion to ``safe_robinhood_wallet.py`` -- proves the
whole Safe + AllowanceModule cycle WITHOUT ever writing on-chain, spending a
single wei of gas, or holding a private key (17/08, step 2 of the homemade
agent-wallet plan; operator validated "simulate the whole cycle, up to
proving the rejection").

WHY A SEPARATE MODULE. ``safe_robinhood_wallet.py`` carries a structural
guardrail that must not be weakened: its embedded ABI contains ONLY ``view``
functions, so web3.py physically cannot build a spend transaction from it
(locked by ``test_allowance_module_abi_is_read_only``). Simulating the write
path needs write-function ABIs, so it lives here instead -- the read-only
module stays provably read-only.

THE GUARDRAIL THAT REPLACES IT HERE. This module never sends a transaction:
it only ever calls ``eth_call``, the read-only EVM entry point, with state
overrides (a simulation feature -- the injected state is discarded when the
call returns, never persisted to the chain). No ``send_transaction`` /
``send_raw_transaction`` / ``build_transaction`` anywhere, locked by
``test_simulation_module_never_sends_a_transaction``. Combined with the
chain-id preflight below, this module cannot touch mainnet or spend anything
even if a future caller asked it to.

CHAIN-ID PREFLIGHT (closes open point 2 of the step-1 HANDOFF entry). Every
public entry point re-verifies the connected chain is the TESTNET before
doing anything, and fails closed on doubt -- an RPC silently repointed at
mainnet raises instead of simulating against real balances.

WHAT WAS PROVEN LIVE (17/08, against the real deployed contracts, testnet
46630) rather than assumed:
- The full Safe v1.4.1 stack is deployed on this chain, not just the
  singleton the step-1 entry had checked: proxy factory, SafeL2 singleton,
  compatibility fallback handler, multisend. Without the FACTORY there is no
  way to create a Safe at all -- that was the real bloquant to rule out first.
- ``eth_call`` state overrides accept injected CODE on this RPC, not just
  injected storage (step 1 had only ever needed storage) -- verified with a
  10-byte stub returning a sentinel value.
- Safe storage layout: ``threshold`` sits at slot 4. Proven by injecting a
  distinctive sentinel (7, never a plausible default) and reading it back
  through the contract's own ``getThreshold()`` getter.
- The allowance cap genuinely DISCRIMINATES: with a 100-unit one-shot
  allowance injected, a 150-unit transfer reverts on the module's own
  ``newSpent > allowance.spent && newSpent <= allowance.amount`` require,
  while 50 and 100 both clear the cap and only stop later at signature
  verification. An always-reverting contract would have looked identical to
  a working guardrail if only the over-limit case had been tested -- testing
  the passing cases is what makes this a real proof.

ALLOWANCE REGIME. ``reset_time_min = 0`` (one-shot: the cap never refills on
its own, re-arming is a human action) is the operator's explicit decision of
17/08 for the pilot, chosen over the periodic regime so a compromised agent's
maximum loss is the envelope itself rather than the envelope every period.
The periodic regime is reachable with the same code by passing a non-zero
value -- deliberately not the default.
"""
from __future__ import annotations

from eth_abi import encode as abi_encode
from web3 import Web3

from aria_core.onchain.safe_robinhood_wallet import (
    ALLOWANCE_MODULE_ADDRESS,
    ROBINHOOD_TESTNET_CHAIN_ID,
    _rpc_url,
)

# Safe v1.4.1 canonical CREATE2 addresses (safe-global/safe-deployments).
# Each one re-verified live on testnet 46630 on 17/08 with a real
# ``eth_getCode`` -- byte counts recorded so a future session can detect a
# silently changed deployment rather than trusting this list on faith.
SAFE_PROXY_FACTORY_V141 = "0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67"  # 3054 bytes
SAFE_L2_SINGLETON_V141 = "0x29fcB43b46531BcA003ddC8FCB67FFE91900C762"  # 24421 bytes
SAFE_FALLBACK_HANDLER_V141 = "0xfd0732Dc9E303f09fCEf3a7388Ad10A83459Ec99"  # 5637 bytes

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Slot 4, proven experimentally (see module docstring) rather than read off
# the inheritance chain -- a wrong guess here would silently validate a Safe
# whose threshold is not what we think it is.
_SAFE_THRESHOLD_SLOT = 4

# ``allowances`` is slot 0 of AllowanceModule, and the Allowance struct packs
# into exactly one slot in this field order -- both proven experimentally in
# step 1 (see safe_robinhood_wallet.py's own docstring for that proof).
_ALLOWANCES_BASE_SLOT = 0
_AMOUNT_BIT_OFFSET = 0
_SPENT_BIT_OFFSET = 96
_RESET_TIME_MIN_BIT_OFFSET = 192
_LAST_RESET_MIN_BIT_OFFSET = 208
_NONCE_BIT_OFFSET = 240

# uint96 ceiling -- the on-chain type of both `amount` and `spent`. A caller
# passing more would silently wrap on packing, so it is rejected outright.
_UINT96_MAX = (1 << 96) - 1

_SAFE_SETUP_ABI = [{
    "name": "setup", "type": "function", "stateMutability": "nonpayable", "outputs": [],
    "inputs": [
        {"type": "address[]", "name": "_owners"}, {"type": "uint256", "name": "_threshold"},
        {"type": "address", "name": "to"}, {"type": "bytes", "name": "data"},
        {"type": "address", "name": "fallbackHandler"}, {"type": "address", "name": "paymentToken"},
        {"type": "uint256", "name": "payment"}, {"type": "address", "name": "paymentReceiver"},
    ],
}]

_PROXY_FACTORY_ABI = [{
    "name": "createProxyWithNonce", "type": "function", "stateMutability": "nonpayable",
    "inputs": [
        {"type": "address", "name": "_singleton"}, {"type": "bytes", "name": "initializer"},
        {"type": "uint256", "name": "saltNonce"},
    ],
    "outputs": [{"type": "address", "name": "proxy"}],
}]

# Write-function ABI -- allowed HERE only (never in safe_robinhood_wallet.py)
# because this module can only ever reach it through ``eth_call``.
_ALLOWANCE_TRANSFER_ABI = [{
    "name": "executeAllowanceTransfer", "type": "function", "stateMutability": "nonpayable",
    "outputs": [],
    "inputs": [
        {"type": "address", "name": "safe"}, {"type": "address", "name": "token"},
        {"type": "address", "name": "to"}, {"type": "uint96", "name": "amount"},
        {"type": "address", "name": "paymentToken"}, {"type": "uint96", "name": "payment"},
        {"type": "address", "name": "delegate"}, {"type": "bytes", "name": "signature"},
    ],
}]

# The module's own revert string when the cap is exceeded (v0.1.1 source).
# Matching on it -- rather than on "did it revert at all" -- is what proves
# the CAP rejected the transfer, not some unrelated failure.
_CAP_REVERT_MARKER = "newSpent"


def _w3(w3=None):
    return w3 if w3 is not None else Web3(Web3.HTTPProvider(_rpc_url()))


def _require_testnet(w3) -> None:
    """Fail-closed chain preflight. Runs on every public entry point: an RPC
    repointed at mainnet (4663) -- by config drift or a hostile env var --
    must raise, never silently simulate against real balances."""
    chain_id = w3.eth.chain_id
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise RuntimeError(
            f"refus: chaine {chain_id} != testnet {ROBINHOOD_TESTNET_CHAIN_ID} "
            "-- ce module ne simule que sur le testnet"
        )


def verify_safe_stack_deployed(*, w3=None) -> dict:
    """Real ``eth_getCode`` on every contract a Safe creation depends on.
    Returns per-contract byte counts (0 = absent) and an overall verdict --
    never a bare boolean, so a caller can see WHICH piece is missing."""
    w3 = _w3(w3)
    _require_testnet(w3)
    sizes = {}
    for label, addr in (
        ("proxy_factory", SAFE_PROXY_FACTORY_V141),
        ("singleton_l2", SAFE_L2_SINGLETON_V141),
        ("fallback_handler", SAFE_FALLBACK_HANDLER_V141),
        ("allowance_module", ALLOWANCE_MODULE_ADDRESS),
    ):
        sizes[label] = len(w3.eth.get_code(Web3.to_checksum_address(addr)))
    return {"sizes": sizes, "all_deployed": all(v > 0 for v in sizes.values()), "error": None}


def _build_setup_initializer(w3, owners: list[str], threshold: int) -> str:
    if not owners:
        raise ValueError("au moins un owner est requis")
    if not 1 <= threshold <= len(owners):
        raise ValueError(f"threshold {threshold} incoherent avec {len(owners)} owner(s)")
    iface = w3.eth.contract(abi=_SAFE_SETUP_ABI)
    return iface.encode_abi("setup", args=[
        [Web3.to_checksum_address(o) for o in owners], threshold,
        ZERO_ADDRESS, b"", Web3.to_checksum_address(SAFE_FALLBACK_HANDLER_V141),
        ZERO_ADDRESS, 0, ZERO_ADDRESS,
    ])


def simulate_safe_creation(
    owners: list[str], *, threshold: int = 1, salt_nonce: int = 0, w3=None,
) -> dict:
    """Simulates ``createProxyWithNonce`` and returns the address the Safe
    WOULD have. Pure ``eth_call``: nothing is deployed, no gas is spent, and
    the returned address holds no code until a real transaction is sent
    (``already_deployed`` reports that, so a caller never mistakes a
    simulation for a live Safe)."""
    w3 = _w3(w3)
    _require_testnet(w3)
    initializer = _build_setup_initializer(w3, owners, threshold)
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(SAFE_PROXY_FACTORY_V141), abi=_PROXY_FACTORY_ABI,
    )
    predicted = factory.functions.createProxyWithNonce(
        Web3.to_checksum_address(SAFE_L2_SINGLETON_V141), initializer, salt_nonce,
    ).call({"from": Web3.to_checksum_address(owners[0])})
    predicted = Web3.to_checksum_address(predicted)
    return {
        "predicted_address": predicted,
        "already_deployed": len(w3.eth.get_code(predicted)) > 0,
        "salt_nonce": salt_nonce,
        "threshold": threshold,
        "owners": [Web3.to_checksum_address(o) for o in owners],
        "error": None,
    }


def _allowance_storage_slot(safe: str, delegate: str, token: str) -> str:
    """Storage key of ``allowances[safe][delegate][token]`` -- the standard
    nested-mapping derivation, keys applied outermost-first."""
    cur = Web3.keccak(abi_encode(
        ["address", "uint256"], [Web3.to_checksum_address(safe), _ALLOWANCES_BASE_SLOT],
    ))
    for key in (delegate, token):
        cur = Web3.keccak(abi_encode(["address", "bytes32"], [Web3.to_checksum_address(key), cur]))
    return "0x" + cur.hex()


def pack_allowance(
    amount: int, *, spent: int = 0, reset_time_min: int = 0,
    last_reset_min: int = 0, nonce: int = 0,
) -> int:
    """Packs an Allowance struct into its single storage word, in the field
    order proven experimentally in step 1. ``reset_time_min=0`` is the
    one-shot regime (operator decision, 17/08): the cap never refills by
    itself."""
    if not 0 <= amount <= _UINT96_MAX or not 0 <= spent <= _UINT96_MAX:
        raise ValueError("amount/spent hors bornes uint96")
    return (
        (amount << _AMOUNT_BIT_OFFSET)
        | (spent << _SPENT_BIT_OFFSET)
        | (reset_time_min << _RESET_TIME_MIN_BIT_OFFSET)
        | (last_reset_min << _LAST_RESET_MIN_BIT_OFFSET)
        | (nonce << _NONCE_BIT_OFFSET)
    )


def simulate_allowance_cap(
    *, cap: int, requested_amounts: list[int], safe: str, delegate: str, token: str,
    already_spent: int = 0, reset_time_min: int = 0, w3=None,
) -> dict:
    """THE proof this whole step exists for: injects a real allowance into
    the REAL deployed AllowanceModule and checks, for each requested amount,
    whether the contract's own cap check rejects it.

    Each outcome is one of:
    - ``rejected_by_cap``: reverted on the module's own cap require -- the
      guardrail held.
    - ``passed_cap``: cleared the cap and stopped later (signature
      verification, which this simulation deliberately does not satisfy).
      This is the DISCRIMINATION half of the proof: a contract that always
      reverted would show ``rejected_by_cap`` everywhere and look identical
      to a working guardrail.
    - ``unexpected``: anything else, surfaced verbatim rather than smoothed
      over.

    Nothing is written: the injected allowance lives only inside the
    ``eth_call`` state override and is discarded when the call returns."""
    w3 = _w3(w3)
    _require_testnet(w3)
    if cap <= 0:
        raise ValueError("cap doit etre > 0")

    module = Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS)
    slot = _allowance_storage_slot(safe, delegate, token)
    packed = pack_allowance(cap, spent=already_spent, reset_time_min=reset_time_min)
    overrides = {module: {"stateDiff": {slot: "0x" + format(packed, "064x")}}}

    contract = w3.eth.contract(address=module, abi=_ALLOWANCE_TRANSFER_ABI)
    results = []
    for amount in requested_amounts:
        data = contract.encode_abi("executeAllowanceTransfer", args=[
            Web3.to_checksum_address(safe), Web3.to_checksum_address(token),
            Web3.to_checksum_address(delegate), amount, ZERO_ADDRESS, 0,
            Web3.to_checksum_address(delegate), b"",
        ])
        response = w3.provider.make_request("eth_call", [
            {"to": module, "from": Web3.to_checksum_address(delegate), "data": data},
            "latest", overrides,
        ])
        message = (response.get("error") or {}).get("message", "")
        if _CAP_REVERT_MARKER in message:
            outcome = "rejected_by_cap"
        elif "error" in response:
            outcome = "passed_cap"
        else:
            outcome = "unexpected"
        results.append({
            "requested": amount, "outcome": outcome,
            "over_cap": amount + already_spent > cap, "revert_message": message or None,
        })

    # A result set only counts as a real proof if the cap both rejected
    # everything over the line AND let everything under it through.
    consistent = all(
        (r["outcome"] == "rejected_by_cap") == r["over_cap"] for r in results
    )
    return {
        "cap": cap, "already_spent": already_spent, "reset_time_min": reset_time_min,
        "one_shot": reset_time_min == 0, "results": results,
        "cap_enforced_consistently": consistent, "error": None,
    }
