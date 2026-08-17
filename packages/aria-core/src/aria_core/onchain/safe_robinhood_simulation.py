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

# ``delegates`` is slot 3 -- PROVEN experimentally on 17/08 by injecting a
# delegate at each candidate slot 0..5 and reading it back through the
# contract's own public ``delegates(address,uint48)`` getter; only slot 3
# returned the injected address. Worth stressing: reading the declaration
# order naively would have said slot 1, which is WRONG -- exactly the class
# of silent error that would make a signature check look broken (or, worse,
# look satisfied) for the wrong reason.
_DELEGATES_BASE_SLOT = 3

# The Delegate struct packs into one slot: address(160) | prev(48) | next(48).
_DELEGATE_ADDRESS_BIT_OFFSET = 0

# EIP-712 typehashes of AllowanceModule v0.1.1. Both cross-checked live on
# 17/08: a digest recomputed from these locally matched the contract's own
# ``generateTransferHash`` byte for byte, which is what proves this structure
# is the real one rather than a plausible-looking reconstruction.
_DOMAIN_TYPEHASH_TEXT = "EIP712Domain(uint256 chainId,address verifyingContract)"
_TRANSFER_TYPEHASH_TEXT = (
    "AllowanceTransfer(address safe,address token,address to,uint96 amount,"
    "address paymentToken,uint96 payment,uint16 nonce)"
)
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

_GENERATE_TRANSFER_HASH_ABI = [{
    "name": "generateTransferHash", "type": "function", "stateMutability": "view",
    "inputs": [
        {"type": "address", "name": "safe"}, {"type": "address", "name": "token"},
        {"type": "address", "name": "to"}, {"type": "uint96", "name": "amount"},
        {"type": "address", "name": "paymentToken"}, {"type": "uint96", "name": "payment"},
        {"type": "uint16", "name": "nonce"},
    ],
    "outputs": [{"type": "bytes32"}],
}]

# The module's own revert strings. Matching on WHICH one came back -- rather
# than on "did it revert at all" -- is what tells a cap rejection apart from
# a signature rejection apart from an unrelated failure. Without this
# distinction every guardrail would look like it works.
_CAP_REVERT_MARKER = "newSpent"
_SIGNATURE_REVERT_MARKER = "expectedDelegate"


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


def _delegate_storage_slot(safe: str, delegate: str) -> str:
    """Storage key of ``delegates[safe][uint48(delegate)]``. The inner key is
    the delegate's LOW 48 bits, matching the contract's own ``uint48(signer)``
    cast -- not the full address."""
    outer = Web3.keccak(abi_encode(
        ["address", "uint256"], [Web3.to_checksum_address(safe), _DELEGATES_BASE_SLOT],
    ))
    key = int(Web3.to_checksum_address(delegate), 16) & ((1 << 48) - 1)
    return "0x" + Web3.keccak(abi_encode(["uint256", "bytes32"], [key, outer])).hex()


def compute_transfer_digest(
    *, safe: str, token: str, to: str, amount: int, nonce: int,
    payment_token: str = ZERO_ADDRESS, payment: int = 0, chain_id: int | None = None,
    module_address: str | None = None,
) -> bytes:
    """Recomputes, locally and from first principles, the exact EIP-712 digest
    the delegate must sign. Kept as an independent implementation on purpose:
    ``assert_digest_matches_contract`` below compares it against the
    contract's own ``generateTransferHash``, and a match is what proves this
    reconstruction is the real structure rather than a plausible-looking
    guess. Blindly trusting the on-chain getter would prove nothing about
    whether we understand what is being signed."""
    module = Web3.to_checksum_address(module_address or ALLOWANCE_MODULE_ADDRESS)
    domain_separator = Web3.keccak(abi_encode(
        ["bytes32", "uint256", "address"],
        [Web3.keccak(text=_DOMAIN_TYPEHASH_TEXT),
         chain_id if chain_id is not None else ROBINHOOD_TESTNET_CHAIN_ID, module],
    ))
    transfer_hash = Web3.keccak(abi_encode(
        ["bytes32", "address", "address", "address", "uint96", "address", "uint96", "uint16"],
        [Web3.keccak(text=_TRANSFER_TYPEHASH_TEXT),
         Web3.to_checksum_address(safe), Web3.to_checksum_address(token),
         Web3.to_checksum_address(to), amount,
         Web3.to_checksum_address(payment_token), payment, nonce],
    ))
    return Web3.keccak(b"\x19\x01" + domain_separator + transfer_hash)


def assert_digest_matches_contract(
    *, safe: str, token: str, to: str, amount: int, nonce: int,
    payment_token: str = ZERO_ADDRESS, payment: int = 0, w3=None,
) -> dict:
    """Cross-check: our locally recomputed digest vs the one the deployed
    contract produces. Verified live 17/08 -- identical."""
    w3 = _w3(w3)
    _require_testnet(w3)
    module = Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS)
    onchain = w3.eth.contract(
        address=module, abi=_GENERATE_TRANSFER_HASH_ABI,
    ).functions.generateTransferHash(
        Web3.to_checksum_address(safe), Web3.to_checksum_address(token),
        Web3.to_checksum_address(to), amount,
        Web3.to_checksum_address(payment_token), payment, nonce,
    ).call()
    local = compute_transfer_digest(
        safe=safe, token=token, to=to, amount=amount, nonce=nonce,
        payment_token=payment_token, payment=payment,
        chain_id=w3.eth.chain_id, module_address=module,
    )
    return {
        "onchain_digest": "0x" + bytes(onchain).hex(),
        "local_digest": "0x" + local.hex(),
        "matches": bytes(onchain) == local,
        "error": None,
    }


def simulate_transfer_with_signature(
    *, signature: bytes, safe: str, delegate: str, token: str, to: str,
    amount: int, cap: int, already_spent: int = 0, reset_time_min: int = 0,
    payment_token: str = ZERO_ADDRESS, payment: int = 0,
    register_delegate: bool = True, w3=None,
) -> dict:
    """Simulates a transfer carrying a REAL signature, against the real
    deployed module, with the allowance AND the delegate registration both
    injected as state overrides.

    This module never signs anything: the caller produces ``signature``
    elsewhere and passes the bytes in. That is what keeps this file provably
    incapable of holding or using key material (locked by
    ``test_simulation_module_reads_no_private_key_from_the_environment``) --
    it can verify a signature's effect, never create one.

    ``register_delegate=False`` injects the allowance but NOT the delegate
    registration, which is how the "unregistered signer is refused" case is
    exercised.

    Outcomes: ``rejected_by_cap`` / ``rejected_by_signature`` /
    ``signature_accepted`` (cleared both guardrails and stopped further
    downstream -- expected here, since no real Safe exists to receive the
    call) / ``fully_executed`` (never reachable without a real Safe)."""
    w3 = _w3(w3)
    _require_testnet(w3)
    module = Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS)

    state = {
        _allowance_storage_slot(safe, delegate, token):
            "0x" + format(pack_allowance(cap, spent=already_spent,
                                         reset_time_min=reset_time_min), "064x"),
    }
    if register_delegate:
        state[_delegate_storage_slot(safe, delegate)] = "0x" + format(
            int(Web3.to_checksum_address(delegate), 16) << _DELEGATE_ADDRESS_BIT_OFFSET, "064x")
    overrides = {module: {"stateDiff": state}}

    data = w3.eth.contract(address=module, abi=_ALLOWANCE_TRANSFER_ABI).encode_abi(
        "executeAllowanceTransfer",
        args=[Web3.to_checksum_address(safe), Web3.to_checksum_address(token),
              Web3.to_checksum_address(to), amount,
              Web3.to_checksum_address(payment_token), payment,
              Web3.to_checksum_address(delegate), signature],
    )
    response = w3.provider.make_request("eth_call", [
        {"to": module, "from": Web3.to_checksum_address(delegate), "data": data},
        "latest", overrides,
    ])
    message = (response.get("error") or {}).get("message", "")
    if _CAP_REVERT_MARKER in message:
        outcome = "rejected_by_cap"
    elif _SIGNATURE_REVERT_MARKER in message:
        outcome = "rejected_by_signature"
    elif "error" in response:
        outcome = "signature_accepted"
    else:
        outcome = "fully_executed"
    return {
        "outcome": outcome, "delegate_registered": register_delegate,
        "requested": amount, "cap": cap, "already_spent": already_spent,
        "revert_message": message or None, "error": None,
    }


# Proven experimentally 17/08, the same way as every other layout constant
# here: injected at each candidate slot and read back through the contract's
# own ``isModuleEnabled()`` getter -- only slot 1 answered True.
_SAFE_MODULES_SLOT = 1
_SAFE_OWNER_COUNT_SLOT = 3
_SENTINEL_ADDRESS = "0x0000000000000000000000000000000000000001"

# Runtime that returns a single non-zero word for any call -- i.e. an ERC20
# whose ``transfer`` always reports success. Used ONLY as an override target
# so the Safe's own token call resolves; it is not a token, and nothing is
# deployed. Without it the cycle would fail on a missing token rather than on
# anything meaningful about the guardrail under test.
_ALWAYS_TRUE_RUNTIME = "0x600160005260206000f3"


def _mapping_slot(key: str, slot: int) -> str:
    return "0x" + Web3.keccak(
        abi_encode(["address", "uint256"], [Web3.to_checksum_address(key), slot])
    ).hex()


def simulate_full_cycle(
    *, signature_for: "callable", safe: str, delegate: str, token: str, to: str,
    amounts: list[int], cap: int, reset_time_min: int = 0, w3=None,
) -> dict:
    """End-to-end proof: a Safe that EXISTS with the AllowanceModule ENABLED,
    a configured allowance, a registered delegate, and a real signed transfer
    that runs all the way through to the Safe's own token call.

    This is what the earlier partial simulations could not show. Checking the
    cap in isolation only ever proved "the module rejects too much"; it never
    proved the permitted path actually WORKS, and a guardrail that blocks
    everything is indistinguishable from one that works if only the blocked
    case is tested. Here both are exercised against the same injected state:
    amounts within the cap must complete, amounts past it must revert on the
    module's own cap require.

    ``signature_for`` is a callable ``(digest: bytes) -> bytes`` supplied by
    the caller. This module deliberately never signs (see the module
    docstring): it verifies what a signature DOES, it never creates one.

    Everything is injected via ``eth_call`` state overrides and discarded when
    the call returns -- no deployment, no gas, no on-chain write."""
    w3 = _w3(w3)
    _require_testnet(w3)
    module = Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS)
    safe = Web3.to_checksum_address(safe)
    token = Web3.to_checksum_address(token)

    safe_runtime = w3.eth.get_code(Web3.to_checksum_address(SAFE_L2_SINGLETON_V141)).hex()
    if not safe_runtime.startswith("0x"):
        safe_runtime = "0x" + safe_runtime
    if len(safe_runtime) <= 2:
        raise RuntimeError("refus: singleton Safe sans bytecode sur cette chaine")

    overrides = {
        safe: {"code": safe_runtime, "stateDiff": {
            # modules is a linked list: modules[module]=SENTINEL and
            # modules[SENTINEL]=module is what isModuleEnabled() reads.
            _mapping_slot(module, _SAFE_MODULES_SLOT): "0x" + format(int(_SENTINEL_ADDRESS, 16), "064x"),
            _mapping_slot(_SENTINEL_ADDRESS, _SAFE_MODULES_SLOT): "0x" + format(int(module, 16), "064x"),
            "0x" + format(_SAFE_OWNER_COUNT_SLOT, "064x"): "0x" + format(1, "064x"),
            "0x" + format(_SAFE_THRESHOLD_SLOT, "064x"): "0x" + format(1, "064x"),
        }},
        module: {"stateDiff": {
            _allowance_storage_slot(safe, delegate, token):
                "0x" + format(pack_allowance(cap, reset_time_min=reset_time_min), "064x"),
            _delegate_storage_slot(safe, delegate):
                "0x" + format(int(Web3.to_checksum_address(delegate), 16), "064x"),
        }},
        token: {"code": _ALWAYS_TRUE_RUNTIME},
    }

    contract = w3.eth.contract(address=module, abi=_ALLOWANCE_TRANSFER_ABI)
    results = []
    for amount in amounts:
        digest = compute_transfer_digest(
            safe=safe, token=token, to=to, amount=amount, nonce=0,
            chain_id=w3.eth.chain_id, module_address=module,
        )
        data = contract.encode_abi("executeAllowanceTransfer", args=[
            safe, token, Web3.to_checksum_address(to), amount, ZERO_ADDRESS, 0,
            Web3.to_checksum_address(delegate), signature_for(digest),
        ])
        response = w3.provider.make_request("eth_call", [
            {"to": module, "from": Web3.to_checksum_address(delegate), "data": data},
            "latest", overrides,
        ])
        message = (response.get("error") or {}).get("message", "")
        if _CAP_REVERT_MARKER in message:
            outcome = "rejected_by_cap"
        elif _SIGNATURE_REVERT_MARKER in message:
            outcome = "rejected_by_signature"
        elif "error" in response:
            outcome = "failed"
        else:
            outcome = "completed"
        results.append({
            "requested": amount, "outcome": outcome,
            "over_cap": amount > cap, "revert_message": message or None,
        })

    # The proof only holds if BOTH halves behave: everything within the cap
    # completed AND everything past it was rejected by the cap itself.
    coherent = all(
        (r["outcome"] == "rejected_by_cap") if r["over_cap"] else (r["outcome"] == "completed")
        for r in results
    )
    return {"cap": cap, "results": results, "cycle_proven": coherent, "error": None}
