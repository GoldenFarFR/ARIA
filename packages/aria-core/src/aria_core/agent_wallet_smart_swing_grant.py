"""One-time Spend Permission grant on the Tangem-owned Smart Account
``aria-smart-st`` -- the single hardware-gated setup action that authorizes the
delegated spender (``aria-spender-smart-st``) to pull USDC autonomously later
(safety layer #2 of ``agent_wallet_smart_swing.py``).

WHY THIS MODULE EXISTS (verdict from the 2026-07-24/25 CDP research, verified
against the really-installed cdp-sdk 1.47.1 -- never guessed):

The obvious high-level path -- ``cdp.evm.create_spend_permission()`` ->
sign -> ``send_user_operation`` -- is a DEAD END for a Tangem owner. That path
finalizes by signing the raw ``userOpHash`` with ``owner.unsafe_sign_hash`` (a
raw ECDSA over a 32-byte hash, no EIP-191 prefix, no EIP-712 struct -- verified
``actions/evm/send_user_operation.py`` + ``send_user_operation`` endpoint
docstring in ``openapi_client/api/evm_smart_accounts_api.py``). The Tangem
WalletConnect bridge deliberately REFUSES raw-hash signing: its allowlist is
``personal_sign`` / ``eth_sendTransaction`` / ``eth_signTypedData_v4`` only (see
``packages/tangem-wc-bridge/server.js`` ``ALLOWED_METHODS``) -- ``eth_sign``
(raw hash) is the classic blind-hash phishing vector a hardware wallet must
refuse. So there is no way to feed a raw ``userOpHash`` to the card.

The path that WORKS is ``SpendPermissionManager.approveWithSignature(perm,
signature)`` (contract ``0xf85210B21cC50302F477BA56686d2019dC9b67Ad``): the
grantor authorizes the permission with an EIP-712 signature validated by
ERC-1271 (and ERC-6492 while the account is still counterfactual/undeployed).
Because ``aria-smart-st`` is a Coinbase Smart Wallet, its Tangem owner signs an
EIP-712 typed message -- exactly what ``eth_signTypedData_v4`` does. Anyone may
submit the resulting transaction; we submit it from the CDP-managed spender.

The cdp-sdk contributes the cryptographic wrapping primitives (reused as-is:
``create_smart_account_signature_wrapper``; and the ERC-6492 factory/magic
constants, reused by import). It ships NO end-to-end external-owner grant
helper, so ``getHash`` / ``approveWithSignature`` / ``isApproved`` are exercised
via raw web3 against the manager ABI, and the submission via
``cdp.evm.send_transaction`` -- all specified below and mirrored from the SDK's
own ``EvmSmartAccount.sign_typed_data`` (``evm_smart_account.py``).

DOCTRINE (this is a real-capital safety envelope, never a convenience helper):
  - ONE-TIME, HUMAN-SUPERVISED setup only. Invoked ONCE, deliberately, by a
    session with the operator physically present and ready to tap their Tangem
    card. It is NOT part of trading orchestration and MUST NEVER be imported by
    any heartbeat / cron / autonomous path. Nothing in production imports it.
  - The granted permission's account/spender/token/allowance/period come ONLY
    from ``agent_wallet_smart_swing.build_spend_permission_input()`` (reused,
    never reconstructed, never a free parameter) -- so this module can never
    grant something different from the reviewed envelope.
  - Never assumes success: the ONLY success criterion is the on-chain
    ``isApproved(perm)`` view reading ``true`` after submission. A submitted-but-
    unconfirmed grant is reported as such, never as "granted".
  - ``dry_run=True`` by DEFAULT: an accidental/programmatic call performs NO tap
    and NO submission -- it only does read-only chain reads and returns the exact
    payload the operator would be asked to sign, for pre-flight inspection.
  - Never touches a private key or a secret. The Tangem NFC tap is the only
    thing that produces a signature; a signature is public data, not a secret.

TWO EMPIRICAL UNKNOWNS the first real run must confront (documented, never
papered over -- see ``docs/HANDOFF_COINBASE_CDP.md``):
  D1 (deployment / ERC-6492). ``aria-smart-st`` is almost certainly
     counterfactual (undeployed): a CDP smart account only deploys on its first
     UserOperation, and that first UserOp needs raw-hash owner signing the
     Tangem cannot do -- so the account may never deploy via the owner path.
     ``approveWithSignature`` will then need an ERC-6492-wrapped signature
     (deploy-then-validate), which this module builds when ``get_code`` is empty,
     mirroring the SDK. The 6492 factory calldata reproduces
     ``createAccount([owner], 0)``; it is only valid if ``aria-smart-st``'s
     counterfactual address was derived from exactly (factory, [Tangem owner],
     salt/index 0). If ``isApproved`` stays false after a 6492 submit, THIS
     derivation mismatch is the prime suspect. Cleaner alternative worth
     weighing operationally: permissionlessly deploy the account first via
     ``CoinbaseSmartWalletFactory.createAccount([owner], 0)`` (no owner signature
     needed), after which a plain wrapper (no 6492) suffices -- out of scope here.
  D2 (Tangem ``eth_signTypedData_v4`` acceptance). Only a trivial message
     round-trip has ever been proven. Signing this replay-safe typed data --
     whose ``verifyingContract`` is the smart account itself -- is structurally
     valid EIP-712 but has never gone through the card. The app may render it
     oddly or refuse an unfamiliar domain. This is the #1 thing to prove.

ORDERING CONSTRAINT (real, easy to trip): the default submitter is the spender
EOA. If the swap-only CDP Policy (``build_swap_only_policy``) is already ATTACHED
to the spender, it default-denies this call to the manager (neither a router
call nor a return-transfer) and the grant tx is rejected. Therefore run this
grant BEFORE attaching that Policy, or inject a ``submit_fn`` that submits from a
different funded account. The spender also needs a little ETH on Base for gas
(and more if the 6492 path deploys the account in the same tx)."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS
from aria_core.agent_wallet_smart_swing import (
    NETWORK,
    SMART_ST_ADDRESS,
    SPENDER_ADDRESS,
    SPEND_PERMISSION_MANAGER_ADDRESS,
    TANGEM_ST_OWNER,
    build_spend_permission_input,
)

logger = logging.getLogger(__name__)

# Distinct real-money log prefix (a log-grep tells the one-time grant apart from
# the recurring swing execution "[REAL MONEY] smart-swing (aria-smart-st)" and
# the adapter's own prefix).
_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] smart-swing GRANT (aria-smart-st)"

# The WalletConnect method the Tangem bridge uses to produce an EIP-712
# signature. The ONLY value-adjacent method the bridge allows that can sign a
# structured message (verified server.js ALLOWED_METHODS, gated behind
# TANGEM_BRIDGE_ALLOW_TX_SIGNING); never `eth_sign` (raw hash, refused).
_SIGN_METHOD = "eth_signTypedData_v4"

# Result statuses -- `granted` is True for exactly ONE of them ("granted").
STATUS_DRY_RUN = "dry_run"                        # no tap, no submit -- payload preview only
STATUS_GRANTED = "granted"                        # isApproved() confirmed true
STATUS_SUBMITTED_NOT_CONFIRMED = "submitted_not_confirmed"  # tx sent, isApproved still false
STATUS_REJECTED = "rejected"                      # operator declined / bridge refused the request
STATUS_ERROR = "error"                            # any failure before a confirmed grant


# ── Injected seams (default to the real implementations further down) ────────
# Same "inject the network/CDP touchpoints, keep the orchestration pure and
# unit-testable" doctrine as agent_wallet_smart_swing.py. Overridden ONLY in
# tests; never rebound in production.
GetHashFn = Callable[[tuple], Awaitable[str]]      # perm tuple -> 0x bytes32 permission hash
GetCodeFn = Callable[[str], Awaitable[bytes]]      # address -> on-chain bytecode (empty => undeployed)
IsApprovedFn = Callable[[tuple], Awaitable[bool]]  # perm tuple -> on-chain isApproved()
SubmitFn = Callable[..., Awaitable[str]]           # (to=, data=, network=) -> tx hash
PairingUriCallback = Callable[[str], None]         # surface the WC pairing URI to the operator


@dataclass(frozen=True)
class SpendPermissionGrantResult:
    """Structured outcome of a grant attempt. ``granted`` is True ONLY on an
    explicit on-chain ``isApproved()==true`` -- never inferred from a submitted
    transaction hash alone."""

    status: str
    granted: bool
    reason: str = ""
    # Echo of exactly what was authorized (all from build_spend_permission_input).
    account: str = SMART_ST_ADDRESS
    spender: str = SPENDER_ADDRESS
    token: str = USDC_BASE_ADDRESS
    allowance_atomic: int = 0
    period_seconds: int = 0
    salt: int = 0
    # The on-chain EIP-712 permission hash the owner signed (via getHash).
    permission_hash: str = ""
    account_deployed: bool | None = None
    used_erc6492: bool | None = None
    tx_hash: str = ""
    # Exactly what the operator was (or would be) asked to sign on their card --
    # public data, surfaced for the pre-flight walkthrough.
    replay_safe_typed_data: dict[str, Any] | None = None


# ── Pure helpers (no network, fully unit-testable) ───────────────────────────


def build_frozen_permission():
    """Resolve the reviewed ``SpendPermissionInput`` into a concrete
    ``SpendPermission`` ONCE and return it frozen.

    ``resolve_spend_permission`` invents a random ``salt`` and a concrete
    ``start`` (``datetime.now()``) each call (verified
    ``cdp/spend_permissions/utils.py``). The exact same struct -- salt, start,
    end included -- MUST be used for the hash, the signature, and the calldata,
    or ``isApproved`` stays false. So this resolves once; the caller threads the
    returned object everywhere and never re-resolves."""
    from cdp.spend_permissions.utils import resolve_spend_permission

    spi = build_spend_permission_input()  # account/spender/token/allowance/period, reviewed constants
    return resolve_spend_permission(spi, NETWORK)


def build_permission_tuple(perm) -> tuple:
    """``SpendPermission`` -> the ABI tuple in the manager's struct order
    (account, spender, token, allowance, period, start, end, salt, extraData).

    Byte-for-byte mirror of the SDK's own conversion
    (``actions/evm/spend_permissions/smart_account_use.py`` -- checksum
    addresses, int coercion, extraData hex->bytes) so the tuple we hash / sign /
    submit is identical to what the SDK would build for the same struct."""
    from web3 import Web3

    return (
        Web3.to_checksum_address(perm.account),
        Web3.to_checksum_address(perm.spender),
        Web3.to_checksum_address(perm.token),
        int(perm.allowance),
        int(perm.period),
        int(perm.start),
        int(perm.end),
        int(perm.salt),
        bytes.fromhex(perm.extra_data[2:])
        if perm.extra_data.startswith("0x")
        else bytes.fromhex(perm.extra_data),
    )


def build_replay_safe_typed_data(
    inner_hash_hex: str, chain_id: int, smart_account_address: str
) -> dict[str, Any]:
    """Build the EIP-712 typed data the Coinbase Smart Wallet owner must sign to
    authorize a hash: the ``CoinbaseSmartWalletMessage{ hash }`` struct under the
    Coinbase Smart Wallet domain.

    We take the getHash route (``inner_hash_hex`` = the on-chain
    ``SpendPermissionManager.getHash(perm)``, the authoritative SpendPermission
    EIP-712 digest) and wrap it directly here, rather than calling the SDK's
    ``create_replay_safe_typed_data`` -- that helper recomputes the inner hash
    from a full typed-data structure it is handed, so it cannot consume a
    pre-computed on-chain hash. The wrapper structure itself is copied verbatim
    from that helper (``actions/evm/sign_and_wrap_typed_data_for_smart_account.py``
    lines 231-251): identical domain, types, primaryType, and message shape --
    only the source of ``message.hash`` differs (on-chain getHash vs. locally
    recomputed), which is deliberately the safer source (no risk of
    mis-reconstructing the manager's own EIP-712 domain/type encoding).

    ``isValidSignature`` on the smart wallet validates a signature against
    exactly this replay-safe hash, so signing this typed data is what makes
    ``approveWithSignature`` accept the owner's authorization."""
    from web3 import Web3

    h = inner_hash_hex if inner_hash_hex.startswith("0x") else "0x" + inner_hash_hex
    return {
        "domain": {
            "name": "Coinbase Smart Wallet",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": Web3.to_checksum_address(smart_account_address),
        },
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "CoinbaseSmartWalletMessage": [{"name": "hash", "type": "bytes32"}],
        },
        "primaryType": "CoinbaseSmartWalletMessage",
        "message": {"hash": h},
    }


def normalize_signature_v(signature_hex: str) -> str:
    """Normalize the recovery id ``v`` of a 65-byte ECDSA signature to {27, 28}.

    ``create_smart_account_signature_wrapper`` splits r||s||v without normalizing
    (verified sign_and_wrap...py:276-281), while the Coinbase Smart Wallet's
    ECDSA recover expects ``v ∈ {27, 28}``. Some wallets (possibly the Tangem)
    return ``v ∈ {0, 1}`` -- this bumps those by 27. Values already in {27,28}
    are left untouched; anything else is left as-is with a warning (never
    silently mangled -- a wrong guess would produce a wrong signer)."""
    raw = signature_hex[2:] if signature_hex.startswith("0x") else signature_hex
    if len(raw) != 130:
        # Not a canonical 65-byte signature -- don't touch it, let the wrapper /
        # on-chain validation surface the real problem rather than corrupt it.
        logger.warning(
            "%s -- signature is %d hex chars, not 130 (65 bytes); leaving v untouched",
            _REAL_MONEY_LOG_PREFIX, len(raw),
        )
        return "0x" + raw
    v = int(raw[128:130], 16)
    if v in (0, 1):
        v += 27
    elif v not in (27, 28):
        logger.warning(
            "%s -- unexpected signature v=%d (expected 0/1/27/28); leaving as-is",
            _REAL_MONEY_LOG_PREFIX, v,
        )
        return "0x" + raw
    return "0x" + raw[:128] + format(v, "02x")


def wrap_owner_signature(
    owner_signature_hex: str, *, account_deployed: bool, owner_address: str
) -> tuple[str, bool]:
    """Wrap a raw owner EIP-712 signature into what
    ``approveWithSignature`` expects, returning ``(final_signature_hex,
    used_erc6492)``.

    Step 1 (always): ``create_smart_account_signature_wrapper`` (reused as-is)
    packs the signature into the Coinbase Smart Wallet ``SignatureWrapper``
    (ownerIndex=0 -- the Tangem is the sole owner).

    Step 2 (only when the account is NOT yet deployed): ERC-6492-wrap the
    SignatureWrapper so ``approveWithSignature`` can deploy-then-validate. This
    logic is mirrored exactly from the SDK's ``EvmSmartAccount.sign_typed_data``
    (evm_smart_account.py:604-623): ``createAccount([owner], 0)`` factory
    calldata against ``_COINBASE_SMART_WALLET_FACTORY``, appended with the
    ERC-6492 magic suffix. The factory/selector/suffix constants are IMPORTED
    from the SDK (not re-typed) so they can never silently drift from the values
    the SDK itself uses -- a wrong factory address would deploy a different
    address and the grant would fail."""
    from eth_abi import encode
    from web3 import Web3

    from cdp.actions.evm.sign_and_wrap_typed_data_for_smart_account import (
        create_smart_account_signature_wrapper,
    )

    wrapped = create_smart_account_signature_wrapper(
        signature_hex=owner_signature_hex, owner_index=0
    )
    if account_deployed:
        return wrapped, False

    # Undeployed -> ERC-6492 wrap (deploy-then-validate).
    from cdp.evm_smart_account import (
        _COINBASE_SMART_WALLET_FACTORY,
        _CREATE_ACCOUNT_SELECTOR,
        _ERC6492_MAGIC_SUFFIX,
    )

    owner_bytes = encode(["address"], [Web3.to_checksum_address(owner_address)])
    factory_calldata = _CREATE_ACCOUNT_SELECTOR + encode(
        ["bytes[]", "uint256"], [[owner_bytes], 0]
    )
    inner_sig_bytes = bytes.fromhex(wrapped.removeprefix("0x"))
    eip6492_bytes = (
        encode(
            ["address", "bytes", "bytes"],
            [_COINBASE_SMART_WALLET_FACTORY, factory_calldata, inner_sig_bytes],
        )
        + _ERC6492_MAGIC_SUFFIX
    )
    return "0x" + eip6492_bytes.hex(), True


def encode_approve_with_signature_calldata(permission_tuple: tuple, final_signature_hex: str) -> str:
    """ABI-encode ``approveWithSignature(SpendPermission, bytes)`` calldata.

    Mirrors the SDK's own ``contract.encode_abi("spend", ...)`` usage
    (``smart_account_use.py``): builds the manager contract from
    ``SPEND_PERMISSION_MANAGER_ABI`` and encodes the call. The signature bytes
    are the (possibly 6492-wrapped) SignatureWrapper from ``wrap_owner_signature``."""
    from web3 import Web3

    from cdp.spend_permissions import SPEND_PERMISSION_MANAGER_ABI

    w3 = Web3()
    contract = w3.eth.contract(
        address=w3.to_checksum_address(SPEND_PERMISSION_MANAGER_ADDRESS),
        abi=SPEND_PERMISSION_MANAGER_ABI,
    )
    sig_bytes = bytes.fromhex(final_signature_hex.removeprefix("0x"))
    return contract.encode_abi("approveWithSignature", args=[permission_tuple, sig_bytes])


# ── Real-default I/O seams (built lazily; each isolated for easy mocking) ─────


def _make_manager_contract(rpc_url: str):
    from web3 import Web3

    from cdp.spend_permissions import SPEND_PERMISSION_MANAGER_ABI

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    contract = w3.eth.contract(
        address=w3.to_checksum_address(SPEND_PERMISSION_MANAGER_ADDRESS),
        abi=SPEND_PERMISSION_MANAGER_ABI,
    )
    return w3, contract


def _default_get_hash_fn(rpc_url: str) -> GetHashFn:
    async def _get_hash(permission_tuple: tuple) -> str:
        _, contract = _make_manager_contract(rpc_url)
        raw = await asyncio.to_thread(contract.functions.getHash(permission_tuple).call)
        # web3 returns bytes/HexBytes for a bytes32 return.
        from web3 import Web3

        return Web3.to_hex(raw)

    return _get_hash


def _default_get_code_fn(rpc_url: str) -> GetCodeFn:
    async def _get_code(address: str) -> bytes:
        from web3 import Web3

        w3, _ = _make_manager_contract(rpc_url)
        return bytes(await asyncio.to_thread(w3.eth.get_code, Web3.to_checksum_address(address)))

    return _get_code


def _default_is_approved_fn(rpc_url: str) -> IsApprovedFn:
    async def _is_approved(permission_tuple: tuple) -> bool:
        _, contract = _make_manager_contract(rpc_url)
        return bool(await asyncio.to_thread(contract.functions.isApproved(permission_tuple).call))

    return _is_approved


async def _default_submit_fn(*, to: str, data: str, network: str) -> str:
    """Submit ``approveWithSignature`` from the CDP-managed spender EOA. Gas /
    nonce are Coinbase-managed (the documented purpose of
    ``TransactionRequestEIP1559``). Reads no secret -- ``CdpClient`` picks up the
    CDP credentials from the environment itself (SDK convention)."""
    from cdp import CdpClient
    from cdp.evm_transaction_types import TransactionRequestEIP1559
    from web3 import Web3

    async with CdpClient() as cdp:
        return await cdp.evm.send_transaction(
            address=SPENDER_ADDRESS,
            transaction=TransactionRequestEIP1559(
                to=Web3.to_checksum_address(to), data=data, value=0
            ),
            network=network,
        )


async def _obtain_owner_signature(
    bridge: Any,
    *,
    typed_data: dict[str, Any],
    chain_id: int,
    connect_timeout_seconds: float,
    sign_timeout_seconds: float,
    on_pairing_uri: PairingUriCallback | None,
) -> tuple[str | None, str, str]:
    """Drive the Tangem bridge to obtain the owner's ``eth_signTypedData_v4``
    signature over ``typed_data``. Returns ``(signature_hex | None, status,
    reason)`` where ``status`` is one of "ok" / STATUS_REJECTED / STATUS_ERROR.

    Verifies the physically-connected card is the expected aria-smart-st owner
    BEFORE requesting a signature (wrong card => wrong signer => a grant that
    would never validate; caught early with a clear message). Always disconnects
    the session when done (best-effort), so it can never be reused."""
    conn = await bridge.start_connection(timeout_seconds=connect_timeout_seconds)
    if not conn.available or not conn.connection_id:
        return None, STATUS_ERROR, f"bridge indisponible pour l'appairage : {conn.error or 'inconnu'}"

    connection_id = conn.connection_id
    try:
        if conn.uri:
            # Surface the pairing URI as PLAIN TEXT (never a QR -- unreliable in
            # this project) so the operator can open it in the Tangem app.
            logger.warning("%s -- APPAIRAGE Tangem, ouvrez ce lien dans l'app : %s",
                           _REAL_MONEY_LOG_PREFIX, conn.uri)
            if on_pairing_uri is not None:
                try:
                    on_pairing_uri(conn.uri)
                except Exception as exc:  # noqa: BLE001 -- a display callback must never break the flow
                    logger.warning("%s -- on_pairing_uri callback failed: %s", _REAL_MONEY_LOG_PREFIX, exc)

        status = await bridge.wait_for_connection(connection_id, timeout_seconds=connect_timeout_seconds)
        if not status.available or status.status != "connected":
            return None, STATUS_ERROR, (
                f"appairage Tangem non confirmé (statut={getattr(status, 'status', None)}, "
                f"err={getattr(status, 'error', None)})"
            )

        connected = (status.address or "").strip()
        if connected.lower() != TANGEM_ST_OWNER.lower():
            return None, STATUS_ERROR, (
                f"carte Tangem connectée {connected!r} ≠ propriétaire attendu d'aria-smart-st "
                f"({TANGEM_ST_OWNER}) -- mauvaise carte ? Aucune signature demandée."
            )

        sig = await bridge.request_signature(
            connection_id,
            method=_SIGN_METHOD,
            params=[TANGEM_ST_OWNER, json.dumps(typed_data)],
            chain_id=f"eip155:{chain_id}",
            timeout_seconds=sign_timeout_seconds,
        )
        if not sig.available or not sig.result:
            # Bridge treats an operator decline AND a relay/timeout the same
            # (non-200) -- either way, no signature obtained. Never assume success.
            return None, STATUS_REJECTED, (
                f"signature non obtenue (refus de l'opérateur ou expiration) : {sig.error or 'aucun résultat'}"
            )
        return str(sig.result), "ok", ""
    finally:
        try:
            await bridge.disconnect(connection_id)
        except Exception as exc:  # noqa: BLE001 -- disconnect is best-effort, never fatal
            logger.warning("%s -- disconnect failed (non-fatal): %s", _REAL_MONEY_LOG_PREFIX, exc)


# ── Orchestrator ─────────────────────────────────────────────────────────────


async def grant_spend_permission_via_tangem(
    *,
    dry_run: bool = True,
    rpc_url: str | None = None,
    network: str = NETWORK,
    connect_timeout_seconds: float = 180.0,
    sign_timeout_seconds: float = 180.0,
    confirm_poll_attempts: int = 12,
    confirm_poll_interval_seconds: float = 5.0,
    on_pairing_uri: PairingUriCallback | None = None,
    # Injected seams -- default to the real implementations; overridden ONLY in tests.
    bridge: Any = None,
    get_hash_fn: GetHashFn | None = None,
    get_code_fn: GetCodeFn | None = None,
    is_approved_fn: IsApprovedFn | None = None,
    submit_fn: SubmitFn | None = None,
) -> SpendPermissionGrantResult:
    """Perform (or, by default, DRY-RUN) the one-time Spend Permission grant on
    ``aria-smart-st`` via the operator's Tangem card.

    Sequence (each external touchpoint is an injected seam so the whole flow is
    unit-testable without a network / a real card):

      1. Freeze the reviewed permission ONCE (``build_frozen_permission``) and
         build its ABI tuple. Nothing here is a free parameter -- the
         account/spender/token/allowance/period all come from
         ``build_spend_permission_input``.
      2. Read the authoritative permission hash on-chain
         (``getHash``) and the account's deployment state (``get_code``).
         Read-only -- always safe, even in ``dry_run``.
      3. Build the replay-safe EIP-712 typed data the owner must sign
         (``CoinbaseSmartWalletMessage{ hash=getHash }``).
      4. If ``dry_run`` (DEFAULT): stop here and return the full payload for the
         operator to inspect -- NO Tangem tap, NO submission.
      5. Otherwise: relay that EXACT typed data through the Tangem bridge
         (``eth_signTypedData_v4``), verifying the connected card is the expected
         owner first. Normalize ``v``, wrap into a SignatureWrapper (+ ERC-6492 if
         the account is undeployed), ABI-encode ``approveWithSignature`` and
         submit it from the spender.
      6. Confirm by polling the on-chain ``isApproved(perm)`` view. ``granted`` is
         True ONLY if that reads true; a submitted-but-unconfirmed grant is
         reported as ``submitted_not_confirmed`` (never as success -- the prime
         suspect on a persistent false is the ERC-6492 address derivation D1).

    Returns a ``SpendPermissionGrantResult`` describing exactly what happened and
    what was (or would be) authorized. Any failure before a confirmed grant is a
    non-``granted`` result with a real reason -- never an exception bubbling to
    the operator mid-tap, never an assumed success."""
    if bridge is None:
        from aria_core import tangem_bridge as bridge  # real bridge client, reused (never a 2nd one)

    from cdp.network_config import NETWORK_TO_RPC_URL, get_chain_id

    chain_id = get_chain_id(network)
    resolved_rpc = rpc_url or NETWORK_TO_RPC_URL.get(network)
    if not resolved_rpc:
        return SpendPermissionGrantResult(
            status=STATUS_ERROR, granted=False,
            reason=f"aucune URL RPC pour le réseau {network!r} (fournir rpc_url explicitement)",
        )

    get_hash_fn = get_hash_fn or _default_get_hash_fn(resolved_rpc)
    get_code_fn = get_code_fn or _default_get_code_fn(resolved_rpc)
    is_approved_fn = is_approved_fn or _default_is_approved_fn(resolved_rpc)
    submit_fn = submit_fn or _default_submit_fn

    # ── Step 1: freeze the reviewed permission ONCE ──
    perm = build_frozen_permission()
    permission_tuple = build_permission_tuple(perm)
    base_fields = dict(
        allowance_atomic=int(perm.allowance),
        period_seconds=int(perm.period),
        salt=int(perm.salt),
    )

    # ── Step 2: authoritative on-chain reads (read-only, safe in dry_run) ──
    try:
        permission_hash = await get_hash_fn(permission_tuple)
    except Exception as exc:  # noqa: BLE001 -- a read failure must never be read as success
        return SpendPermissionGrantResult(
            status=STATUS_ERROR, granted=False,
            reason=f"lecture on-chain getHash échouée : {exc}", **base_fields,
        )
    try:
        code = await get_code_fn(SMART_ST_ADDRESS)
    except Exception as exc:  # noqa: BLE001
        return SpendPermissionGrantResult(
            status=STATUS_ERROR, granted=False, permission_hash=permission_hash,
            reason=f"lecture on-chain get_code échouée : {exc}", **base_fields,
        )
    account_deployed = len(code) > 0

    # ── Step 3: build the replay-safe typed data the owner signs ──
    typed_data = build_replay_safe_typed_data(permission_hash, chain_id, SMART_ST_ADDRESS)

    # ── Step 4: dry-run stops here (no tap, no submit) ──
    if dry_run:
        logger.info(
            "%s -- DRY-RUN: permission résolue et payload de signature construit, aucun tap ni "
            "soumission. account_deployed=%s permission_hash=%s",
            _REAL_MONEY_LOG_PREFIX, account_deployed, permission_hash,
        )
        return SpendPermissionGrantResult(
            status=STATUS_DRY_RUN, granted=False,
            reason="dry-run : aucun tap Tangem ni transaction envoyée -- payload prêt à inspecter",
            permission_hash=permission_hash, account_deployed=account_deployed,
            replay_safe_typed_data=typed_data, **base_fields,
        )

    # ── Step 5: Tangem sign -> wrap -> submit ──
    owner_sig, sign_status, sign_reason = await _obtain_owner_signature(
        bridge, typed_data=typed_data, chain_id=chain_id,
        connect_timeout_seconds=connect_timeout_seconds, sign_timeout_seconds=sign_timeout_seconds,
        on_pairing_uri=on_pairing_uri,
    )
    if owner_sig is None:
        return SpendPermissionGrantResult(
            status=sign_status, granted=False, reason=sign_reason,
            permission_hash=permission_hash, account_deployed=account_deployed,
            replay_safe_typed_data=typed_data, **base_fields,
        )

    normalized = normalize_signature_v(owner_sig)
    final_sig, used_6492 = wrap_owner_signature(
        normalized, account_deployed=account_deployed, owner_address=TANGEM_ST_OWNER
    )
    calldata = encode_approve_with_signature_calldata(permission_tuple, final_sig)

    try:
        tx_hash = await submit_fn(to=SPEND_PERMISSION_MANAGER_ADDRESS, data=calldata, network=network)
    except Exception as exc:  # noqa: BLE001 -- a submit failure is never a grant
        return SpendPermissionGrantResult(
            status=STATUS_ERROR, granted=False,
            reason=(
                f"soumission approveWithSignature échouée : {exc} "
                "(si la Policy swap-only est déjà attachée au spender, elle refuse cet appel -- "
                "faire le grant AVANT d'attacher la Policy, cf. docstring du module)"
            ),
            permission_hash=permission_hash, account_deployed=account_deployed,
            used_erc6492=used_6492, replay_safe_typed_data=typed_data, **base_fields,
        )
    tx_hash = str(tx_hash or "")
    logger.warning("%s -- approveWithSignature soumise (tx=%s, erc6492=%s) -- confirmation en cours…",
                   _REAL_MONEY_LOG_PREFIX, tx_hash, used_6492)

    # ── Step 6: confirm by the on-chain isApproved view (the ONLY success proof) ──
    approved = False
    last_err = ""
    for attempt in range(max(1, confirm_poll_attempts)):
        try:
            approved = await is_approved_fn(permission_tuple)
        except Exception as exc:  # noqa: BLE001 -- a read failure isn't a confirmed grant
            last_err = str(exc)
            approved = False
        if approved:
            break
        if attempt < confirm_poll_attempts - 1:
            await asyncio.sleep(confirm_poll_interval_seconds)

    if approved:
        logger.warning("%s -- GRANT CONFIRMÉ on-chain (isApproved=true, tx=%s)", _REAL_MONEY_LOG_PREFIX, tx_hash)
        return SpendPermissionGrantResult(
            status=STATUS_GRANTED, granted=True,
            reason=f"spend permission accordée et confirmée on-chain (tx={tx_hash})",
            permission_hash=permission_hash, account_deployed=account_deployed,
            used_erc6492=used_6492, tx_hash=tx_hash, replay_safe_typed_data=typed_data, **base_fields,
        )

    return SpendPermissionGrantResult(
        status=STATUS_SUBMITTED_NOT_CONFIRMED, granted=False,
        reason=(
            f"transaction soumise (tx={tx_hash}) mais isApproved() encore false après "
            f"{confirm_poll_attempts} lectures"
            + (f" (dernière erreur de lecture : {last_err})" if last_err else "")
            + ". Ne PAS considérer comme accordée. Suspects : (1) tx pas encore minée -- revérifier "
            "isApproved() dans quelques minutes ; (2) dérivation d'adresse ERC-6492 (D1) si le compte "
            "était non déployé ; (3) mauvaise carte / mauvais owner."
        ),
        permission_hash=permission_hash, account_deployed=account_deployed,
        used_erc6492=used_6492, tx_hash=tx_hash, replay_safe_typed_data=typed_data, **base_fields,
    )
