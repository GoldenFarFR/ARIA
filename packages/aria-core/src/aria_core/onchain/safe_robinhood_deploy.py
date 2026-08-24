"""Real, gas-spending testnet execution -- step 2+ of the homemade agent-
wallet plan, FIRST module in this chantier that ever sends a transaction.

Everything ``safe_robinhood_simulation.py`` proved was proven via ``eth_call``
state overrides: real proof against the real deployed contracts, but nothing
written, no gas spent, no key held. This module is the deliberate next step
(23/08, operator-provided testnet key + testnet gas): the SAME already-proven
structures (setup initializer, storage layout, EIP-712 digests, ABI) are now
used to build, sign, and broadcast real transactions on the TESTNET ONLY.

CHAIN-ID PREFLIGHT, same doctrine as the simulation module: every public
entry point re-verifies the connected chain before doing anything real, and
fails closed on doubt. This is the single most load-bearing guardrail in this
file -- a misconfigured RPC pointed at mainnet (4663) must never silently
sign and broadcast a real transaction there.

KEY HANDLING: the private key is read ONCE from ``ARIA_ROBINHOOD_DEPLOYER_
PRIVATE_KEY`` (testnet-only value, no real funds ever held), passed as a
plain string into ``eth_account.Account.from_key`` and never logged, never
returned by any function here, never written to the DB. For this FIRST
pilot cycle the SAME key acts as both the Safe owner and the allowance
delegate (single-key rehearsal, explicitly simpler than the target
architecture) -- documented as a known simplification, not the intended
production shape: a future real pilot needs the owner key and the agent
(delegate) key to be genuinely separate, so a compromised agent key can
never touch the owner's override authority.

STILL OUT OF SCOPE, same three points as ever: (1) the AllowanceModule
v0.1.1 audit gap remains an operator decision, (2) this module is imported
NOWHERE in production (no heartbeat cycle, no gate), (3) no mainnet code
path exists here at all.
"""
from __future__ import annotations

import os

from eth_abi import encode as abi_encode
from web3 import Web3

from aria_core.onchain.safe_robinhood_simulation import (
    _ALWAYS_TRUE_RUNTIME,
    SAFE_FALLBACK_HANDLER_V141,
    SAFE_L2_SINGLETON_V141,
    SAFE_PROXY_FACTORY_V141,
    ZERO_ADDRESS,
    _PROXY_FACTORY_ABI,
    _SAFE_SETUP_ABI,
    _build_setup_initializer,
    compute_transfer_digest,
)
from aria_core.onchain.safe_robinhood_wallet import (
    ALLOWANCE_MODULE_ADDRESS,
    ROBINHOOD_TESTNET_CHAIN_ID,
    _rpc_url,
    require_expected_chain,
)

_ENV_KEY_NAME = "ARIA_ROBINHOOD_DEPLOYER_PRIVATE_KEY"

# Same revert-message markers as the simulation module -- matching on WHICH
# require() fired (not just "did it revert") is what distinguishes a cap
# rejection from a signature rejection from an unrelated failure.
_CAP_REVERT_MARKER = "newSpent"
_SIGNATURE_REVERT_MARKER = "expectedDelegate"

_SAFE_EXEC_TX_ABI = [{
    "name": "execTransaction", "type": "function", "stateMutability": "nonpayable",
    "outputs": [{"type": "bool"}],
    "inputs": [
        {"type": "address", "name": "to"}, {"type": "uint256", "name": "value"},
        {"type": "bytes", "name": "data"}, {"type": "uint8", "name": "operation"},
        {"type": "uint256", "name": "safeTxGas"}, {"type": "uint256", "name": "baseGas"},
        {"type": "uint256", "name": "gasPrice"}, {"type": "address", "name": "gasToken"},
        {"type": "address", "name": "refundReceiver"}, {"type": "bytes", "name": "signatures"},
    ],
}]

_SAFE_TX_HASH_ABI = [{
    "name": "getTransactionHash", "type": "function", "stateMutability": "view",
    "outputs": [{"type": "bytes32"}],
    "inputs": [
        {"type": "address", "name": "to"}, {"type": "uint256", "name": "value"},
        {"type": "bytes", "name": "data"}, {"type": "uint8", "name": "operation"},
        {"type": "uint256", "name": "safeTxGas"}, {"type": "uint256", "name": "baseGas"},
        {"type": "uint256", "name": "gasPrice"}, {"type": "address", "name": "gasToken"},
        {"type": "address", "name": "refundReceiver"}, {"type": "uint256", "name": "_nonce"},
    ],
}]

_SAFE_NONCE_ABI = [{
    "name": "nonce", "type": "function", "stateMutability": "view",
    "inputs": [], "outputs": [{"type": "uint256"}],
}]

_SAFE_MODULE_ENABLE_ABI = [{
    "name": "enableModule", "type": "function", "stateMutability": "nonpayable",
    "inputs": [{"type": "address", "name": "module"}], "outputs": [],
}]

_SAFE_IS_MODULE_ENABLED_ABI = [{
    "name": "isModuleEnabled", "type": "function", "stateMutability": "view",
    "inputs": [{"type": "address", "name": "module"}], "outputs": [{"type": "bool"}],
}]

_ALLOWANCE_ADD_DELEGATE_ABI = [{
    "name": "addDelegate", "type": "function", "stateMutability": "nonpayable",
    "outputs": [], "inputs": [{"type": "address", "name": "delegate"}],
}]

_ALLOWANCE_SET_ABI = [{
    "name": "setAllowance", "type": "function", "stateMutability": "nonpayable",
    "outputs": [],
    "inputs": [
        {"type": "address", "name": "delegate"}, {"type": "address", "name": "token"},
        {"type": "uint96", "name": "allowanceAmount"}, {"type": "uint16", "name": "resetTimeMin"},
        {"type": "uint32", "name": "resetBaseMin"},
    ],
}]

_ALLOWANCE_EXECUTE_ABI = [{
    "name": "executeAllowanceTransfer", "type": "function", "stateMutability": "nonpayable",
    "outputs": [],
    "inputs": [
        {"type": "address", "name": "safe"}, {"type": "address", "name": "token"},
        {"type": "address", "name": "to"}, {"type": "uint96", "name": "amount"},
        {"type": "address", "name": "paymentToken"}, {"type": "uint96", "name": "payment"},
        {"type": "address", "name": "delegate"}, {"type": "bytes", "name": "signature"},
    ],
}]

# Standard sentinel used to walk the modules linked list -- same constant as
# the simulation module's own storage-layout proof.
_SENTINEL_ADDRESS = "0x0000000000000000000000000000000000000001"


def _w3(w3=None):
    return w3 if w3 is not None else Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 20}))


def _require_testnet(w3) -> None:
    """Thin alias over the dome-shared ``require_expected_chain`` (24/08 —
    was a third near-identical copy of this same preflight, deduplicated).
    Kept as a distinct name here (rather than inlining the shared call at
    all 6 sites) so this module's own deploy-only entry points stay
    testnet-only by construction until a deliberate future change opts a
    specific one into ``allowed_chain_ids={ROBINHOOD_MAINNET_CHAIN_ID}``."""
    require_expected_chain(w3)


def deployer_account(*, private_key: str | None = None):
    """Builds the ``eth_account`` object from the testnet-only key. Reads
    ``ARIA_ROBINHOOD_DEPLOYER_PRIVATE_KEY`` when no key is injected -- never
    logs or returns the key material itself, only the derived account."""
    from eth_account import Account

    key = (private_key if private_key is not None else os.environ.get(_ENV_KEY_NAME, "")).strip()
    if not key:
        raise RuntimeError(f"{_ENV_KEY_NAME} absente ou vide -- aucune cle testnet configuree")
    return Account.from_key(key)


def _send_and_wait(w3, signed_tx, *, timeout: int = 90) -> dict:
    """Broadcasts a raw signed transaction and waits for its receipt.
    Returns a plain dict rather than the raw receipt object so every caller
    here reports the same shape (``ok``/``tx_hash``/``gas_used``/``error``)."""
    try:
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
        return {
            "ok": receipt.status == 1,
            "tx_hash": tx_hash.hex(),
            "gas_used": receipt.gasUsed,
            "block_number": receipt.blockNumber,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 -- network/revert, never fabricate success
        return {"ok": False, "tx_hash": None, "gas_used": None, "block_number": None, "error": str(exc)}


def _revert_outcome(message: str) -> str:
    """Categorizes a revert message the same way the simulation module
    does -- WHICH require() fired, not just whether one did."""
    if _CAP_REVERT_MARKER in message:
        return "rejected_by_cap"
    if _SIGNATURE_REVERT_MARKER in message:
        return "rejected_by_signature"
    return "rejected_other"


def _build_sign_send(w3, account, contract_fn, *, extra_fields: dict | None = None) -> dict:
    """Shared build -> sign -> send -> wait pipeline for every real contract
    call in this module. ``contract_fn.build_transaction(...)`` triggers an
    implicit ``eth_estimate_gas``, which is exactly where a predictable
    revert (wrong cap, wrong signature) surfaces as a raised
    ``ContractLogicError`` -- caught HERE and turned into the same
    structured, categorized result every other outcome in this module
    already returns, rather than letting a raw traceback escape to the
    caller (a real gap found live on this chantier's first real transfer
    attempt: the exception was previously uncaught at this exact point)."""
    from web3.exceptions import ContractLogicError

    base = {
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
    }
    base.update(extra_fields or {})
    try:
        tx = contract_fn.build_transaction(base)
    except ContractLogicError as exc:
        message = str(exc)
        return {
            "ok": False, "tx_hash": None, "error": message,
            "outcome": _revert_outcome(message),
        }
    signed = account.sign_transaction(tx)
    result = _send_and_wait(w3, signed)
    result.setdefault("outcome", "sent" if result.get("ok") else "failed")
    return result


def deploy_safe(*, owner_address: str, threshold: int = 1, salt_nonce: int | None = None,
                 private_key: str | None = None, w3=None) -> dict:
    """REAL, gas-spending: sends ``createProxyWithNonce`` on the real Safe
    factory. Reuses the exact same setup-initializer builder already proven
    by ``simulate_safe_creation`` (``_build_setup_initializer``), so the
    real deployment is byte-for-byte the same call the simulation predicted
    an address for -- never a re-derived, potentially-diverging encoding.

    ``salt_nonce`` defaults to a value derived from the current block
    timestamp when omitted, so re-running this function twice does not
    collide with an already-deployed Safe at the same predicted address."""
    w3 = _w3(w3)
    _require_testnet(w3)
    account = deployer_account(private_key=private_key)

    if salt_nonce is None:
        salt_nonce = int(w3.eth.get_block("latest")["timestamp"])

    initializer = _build_setup_initializer(w3, [owner_address], threshold)
    factory = w3.eth.contract(address=Web3.to_checksum_address(SAFE_PROXY_FACTORY_V141), abi=_PROXY_FACTORY_ABI)

    predicted = factory.functions.createProxyWithNonce(
        Web3.to_checksum_address(SAFE_L2_SINGLETON_V141), initializer, salt_nonce,
    ).call({"from": Web3.to_checksum_address(owner_address)})
    predicted = Web3.to_checksum_address(predicted)
    if len(w3.eth.get_code(predicted)) > 0:
        return {
            "ok": False, "safe_address": predicted, "already_deployed": True,
            "salt_nonce": salt_nonce, "error": "un Safe existe deja a l'adresse predite avec ce salt_nonce",
        }

    fn = factory.functions.createProxyWithNonce(
        Web3.to_checksum_address(SAFE_L2_SINGLETON_V141), initializer, salt_nonce,
    )
    result = _build_sign_send(w3, account, fn)
    result["safe_address"] = predicted
    result["already_deployed"] = False
    result["salt_nonce"] = salt_nonce
    result["owner"] = Web3.to_checksum_address(owner_address)
    result["threshold"] = threshold
    if result["ok"]:
        result["deployed_confirmed"] = len(w3.eth.get_code(predicted)) > 0
    return result


def _sign_safe_tx_hash(digest: bytes, account) -> bytes:
    """Raw ECDSA signature over the Safe's own tx digest, v in {27,28} --
    the format ``execTransaction``'s ``checkSignatures`` recovers directly
    against the digest with NO extra prefix (distinct from the ``eth_sign``
    v>30 path or the EIP-1271 contract-signature v in {0,1} path, neither of
    which apply to a plain EOA owner signing a hash it already trusts)."""
    signed = account.unsafe_sign_hash(digest)
    v = signed.v if signed.v >= 27 else signed.v + 27
    return signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + v.to_bytes(1, "big")


def _exec_safe_transaction(*, safe_address: str, to: str, data: bytes, private_key: str | None, w3) -> dict:
    """Shared plumbing for every ``execTransaction`` call in this module:
    reads the Safe's real on-chain nonce, asks the Safe's own
    ``getTransactionHash`` for the digest (never a locally-guessed one --
    the same "trust the contract's own oracle" discipline already used for
    the AllowanceModule's ``generateTransferHash``), signs it with the owner
    key, and broadcasts. Value/operation/gas params are all zero/CALL --
    this module never does a delegatecall or sends value."""
    account = deployer_account(private_key=private_key)
    safe = w3.eth.contract(address=Web3.to_checksum_address(safe_address), abi=_SAFE_TX_HASH_ABI + _SAFE_NONCE_ABI + _SAFE_EXEC_TX_ABI)
    tx_nonce = safe.functions.nonce().call()
    args = (Web3.to_checksum_address(to), 0, data, 0, 0, 0, 0, ZERO_ADDRESS, ZERO_ADDRESS)
    digest = safe.functions.getTransactionHash(*args, tx_nonce).call()
    signature = _sign_safe_tx_hash(digest, account)

    fn = safe.functions.execTransaction(*args, signature)
    result = _build_sign_send(w3, account, fn)
    result["safe_tx_nonce"] = tx_nonce
    result["safe_tx_hash"] = "0x" + bytes(digest).hex()
    return result


def enable_allowance_module(*, safe_address: str, private_key: str | None = None, w3=None) -> dict:
    """REAL: the Safe calls its own ``enableModule(AllowanceModule)`` via a
    self-executed ``execTransaction`` (owner-signed, threshold=1 for this
    single-owner pilot Safe). Confirms via ``isModuleEnabled`` after the
    receipt lands, rather than trusting the ``ok`` flag alone -- a
    successful-looking receipt on a Safe transaction can still mean the
    INNER call reverted while the outer ``execTransaction`` itself
    succeeded (Safe swallows a failed inner call by design unless
    ``safeTxGas`` forces a revert)."""
    w3 = _w3(w3)
    _require_testnet(w3)
    module_iface = w3.eth.contract(abi=_SAFE_MODULE_ENABLE_ABI)
    data = module_iface.encode_abi("enableModule", args=[Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS)])

    result = _exec_safe_transaction(
        safe_address=safe_address, to=safe_address, data=data, private_key=private_key, w3=w3,
    )
    if result["ok"]:
        checker = w3.eth.contract(address=Web3.to_checksum_address(safe_address), abi=_SAFE_IS_MODULE_ENABLED_ABI)
        result["module_enabled_confirmed"] = checker.functions.isModuleEnabled(
            Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS)
        ).call()
    return result


def add_delegate(*, safe_address: str, delegate_address: str,
                  private_key: str | None = None, w3=None) -> dict:
    """REAL: the Safe calls ``AllowanceModule.addDelegate(delegate)`` via
    ``execTransaction`` -- a real prerequisite discovered by reading the
    contract's VERIFIED source (Blockscout), not assumed: ``setAllowance``
    itself REQUIRES ``delegates[safe][uint48(delegate)].delegate ==
    delegate`` already true, so a delegate must be registered here FIRST,
    contrary to what the simulation's optional ``register_delegate`` flag
    might suggest at a glance (that flag toggles an INJECTED override, not
    evidence that ``setAllowance`` self-registers)."""
    w3 = _w3(w3)
    _require_testnet(w3)
    module_iface = w3.eth.contract(abi=_ALLOWANCE_ADD_DELEGATE_ABI)
    data = module_iface.encode_abi("addDelegate", args=[Web3.to_checksum_address(delegate_address)])
    result = _exec_safe_transaction(
        safe_address=safe_address, to=ALLOWANCE_MODULE_ADDRESS, data=data,
        private_key=private_key, w3=w3,
    )
    result["delegate"] = Web3.to_checksum_address(delegate_address)
    return result


def configure_allowance(*, safe_address: str, delegate_address: str, token_address: str,
                         amount: int, reset_time_min: int = 0, reset_base_min: int = 0,
                         private_key: str | None = None, w3=None) -> dict:
    """REAL: the Safe calls ``AllowanceModule.setAllowance(...)`` via
    ``execTransaction`` (``setAllowance`` requires ``msg.sender == safe`` on
    the real contract, so it can only ever be called this way, never
    directly by an EOA -- confirmed by the contract's own source, same
    verification discipline as every other assumption in this chantier).
    ``reset_time_min=0`` (one-shot) is the operator's 17/08 decision for
    this pilot, kept as the default here too. Requires the delegate to
    already be registered -- call ``add_delegate`` first."""
    w3 = _w3(w3)
    _require_testnet(w3)
    module_iface = w3.eth.contract(abi=_ALLOWANCE_SET_ABI)
    data = module_iface.encode_abi("setAllowance", args=[
        Web3.to_checksum_address(delegate_address), Web3.to_checksum_address(token_address),
        amount, reset_time_min, reset_base_min,
    ])
    result = _exec_safe_transaction(
        safe_address=safe_address, to=ALLOWANCE_MODULE_ADDRESS, data=data,
        private_key=private_key, w3=w3,
    )
    result["delegate"] = Web3.to_checksum_address(delegate_address)
    result["token"] = Web3.to_checksum_address(token_address)
    result["amount"] = amount
    return result


def sign_and_send_allowance_transfer(*, safe_address: str, token_address: str, to_address: str,
                                      amount: int, nonce: int | None = None, private_key: str | None = None,
                                      w3=None) -> dict:
    """REAL: signs the EIP-712 AllowanceTransfer digest (same
    ``compute_transfer_digest`` already proven byte-for-byte identical to
    the contract's own ``generateTransferHash`` in the simulation module --
    never a re-derived encoding) with the delegate key, then broadcasts
    ``executeAllowanceTransfer``. For this single-key pilot the delegate IS
    the deployer/owner key (see module docstring) -- a future real pilot
    needs these to be genuinely separate keys.

    ``nonce`` defaults to reading the REAL on-chain nonce via
    ``read_allowance`` -- the contract's own source shows ``setAllowance``
    initializes a new token's nonce to 1 (never 0, "nonce should never be 0
    once allowance has been activated"), so hardcoding 0 here would sign a
    digest the contract never expects and fail on signature verification,
    not on the cap -- a real bug caught live on the first real transfer
    attempt of this chantier."""
    from aria_core.onchain.safe_robinhood_wallet import read_allowance

    w3 = _w3(w3)
    _require_testnet(w3)
    account = deployer_account(private_key=private_key)
    if nonce is None:
        current = read_allowance(safe_address, account.address, token_address, w3=w3)
        if current.get("error"):
            return {"ok": False, "tx_hash": None, "error": f"lecture nonce impossible: {current['error']}"}
        nonce = current["nonce"]
    digest = compute_transfer_digest(
        safe=safe_address, token=token_address, to=to_address, amount=amount, nonce=nonce,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID, module_address=ALLOWANCE_MODULE_ADDRESS,
    )
    signature = _sign_safe_tx_hash(digest, account)

    module = w3.eth.contract(address=Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS), abi=_ALLOWANCE_EXECUTE_ABI)
    fn = module.functions.executeAllowanceTransfer(
        Web3.to_checksum_address(safe_address), Web3.to_checksum_address(token_address),
        Web3.to_checksum_address(to_address), amount, ZERO_ADDRESS, 0,
        account.address, signature,
    )
    result = _build_sign_send(w3, account, fn)
    result["amount"] = amount
    result["requested_nonce"] = nonce
    return result


def deploy_stub_token(*, private_key: str | None = None, w3=None) -> dict:
    """REAL: deploys the SAME ``_ALWAYS_TRUE_RUNTIME`` bytecode already used
    (as an ``eth_call`` override, never deployed) by the simulation module's
    ``simulate_full_cycle`` -- a runtime that returns a single non-zero word
    for any call, i.e. an ERC20 whose ``transfer`` always reports success.
    Not a real token, no real balances: this exists ONLY so the module's
    real ``executeAllowanceTransfer`` -> ``token.transfer(...)`` call
    resolves against something with code, deliberately mirroring the
    simulation's own scaffold rather than testing an unrelated third-party
    ERC20 implementation. The deploy-code wrapper (PUSH/DUP/CODECOPY/RETURN)
    is built programmatically from the runtime's real length, never
    hand-counted, to avoid a silent off-by-one."""
    w3 = _w3(w3)
    _require_testnet(w3)
    account = deployer_account(private_key=private_key)

    runtime = bytes.fromhex(_ALWAYS_TRUE_RUNTIME[2:])
    runtime_len = len(runtime)
    # PUSH1 len, DUP1, PUSH1 offset, PUSH1 0x00, CODECOPY, PUSH1 0x00, RETURN
    preamble = (
        bytes([0x60, runtime_len, 0x80, 0x60, 0])  # offset placeholder patched below
        + bytes([0x60, 0x00, 0x39, 0x60, 0x00, 0xF3])
    )
    offset = len(preamble)
    preamble = bytearray(preamble)
    preamble[3] = 0x60
    preamble[4] = offset
    deploy_code = bytes(preamble) + runtime

    tx = {
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "data": deploy_code,
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
        "value": 0,
    }
    signed = account.sign_transaction(tx)
    try:
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "token_address": None, "error": str(exc)}

    if receipt.status != 1 or not receipt.contractAddress:
        return {"ok": False, "token_address": None, "tx_hash": tx_hash.hex(), "error": "deploiement echoue"}

    token_address = Web3.to_checksum_address(receipt.contractAddress)
    deployed_code = w3.eth.get_code(token_address)
    return {
        "ok": True, "token_address": token_address, "tx_hash": tx_hash.hex(),
        "gas_used": receipt.gasUsed,
        "runtime_matches": deployed_code == runtime,
        "error": None,
    }


def read_pilot_state(*, safe_address: str, delegate_address: str, token_address: str, w3=None) -> dict:
    """Read-only: consolidates everything a future session would otherwise
    have to reconstruct by hand across three separate modules (this file's
    own deployment, ``safe_robinhood_wallet``'s allowance reader, a raw
    ``isModuleEnabled`` call) into one call. Exists so this pilot's real
    state (which Safe, is the module actually enabled, what does the
    allowance look like right now) never has to be rediscovered from
    scratch -- the exact standing gap already flagged for
    ``shadow_persistent.py`` in ``docs/registre-automatisations.md``, worth
    avoiding here from day one rather than retrofitting later.

    Never signs, never sends a transaction -- pure aggregation of existing
    read-only calls, safe to run at any time including on mainnet (the
    underlying reads have no chain restriction of their own, though this
    whole module's OTHER functions do)."""
    from aria_core.onchain.safe_robinhood_wallet import read_allowance

    w3 = _w3(w3)
    safe = Web3.to_checksum_address(safe_address)
    module_checker = w3.eth.contract(address=safe, abi=_SAFE_IS_MODULE_ENABLED_ABI)

    try:
        deployed = len(w3.eth.get_code(safe)) > 0
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Safe unreachable ({exc})", "safe_deployed": None}

    module_enabled = None
    if deployed:
        try:
            module_enabled = module_checker.functions.isModuleEnabled(
                Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS)
            ).call()
        except Exception:  # noqa: BLE001 -- best-effort, never blocks the rest of the summary
            module_enabled = None

    allowance = read_allowance(safe, delegate_address, token_address, w3=w3)

    return {
        "error": None,
        "safe_address": safe,
        "safe_deployed": deployed,
        "module_enabled": module_enabled,
        "delegate": Web3.to_checksum_address(delegate_address),
        "token": Web3.to_checksum_address(token_address),
        "allowance": allowance,
    }
