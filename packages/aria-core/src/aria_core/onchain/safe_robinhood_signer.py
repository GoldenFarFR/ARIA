"""ARIA's homemade agent wallet, Robinhood Chain leg — REAL signing module
(18/08). Promotes the one-off script proven live in the "FIRST REAL on-chain
cycle" milestone (``docs/HANDOFF_AGENT_WALLET.md``) into committed, tested
code. Defaults to testnet ONLY (chain 46630, see
``safe_robinhood_wallet.ROBINHOOD_TESTNET_CHAIN_ID``) — every caller today
still passes nothing, so behavior is unchanged. ``allowed_chain_ids`` exists
(24/08) purely as the seam that makes opting a specific future caller into
mainnet a one-parameter change instead of a rewrite — see
``safe_robinhood_wallet.require_expected_chain`` docstring; no caller anywhere
in this dome passes mainnet today.

Reuses, never reimplements, the EIP-712 digest logic already proven
byte-for-byte against the real deployed contract in
``safe_robinhood_simulation.compute_transfer_digest`` (matched the module's
own ``generateTransferHash`` live, 17/08). This file adds only the piece that
was missing: turning that digest plus a real signature into an actually SENT
transaction, always against a freshly re-read on-chain allowance (never a
caller-supplied or cached figure — the same "remaining is a snapshot, the
contract is the only real authority" doctrine already documented in
``safe_robinhood_wallet.read_allowance``, applied here at the one place that
actually spends).

No private key is ever hardcoded or accepted as a literal parameter —
``_load_delegate_key`` reads a ``{"address", "private_key"}`` JSON file from a
path the caller supplies explicitly (no default: an unset path fails closed).
The loaded key is never logged, returned, or otherwise surfaced — only the
address is (same established doctrine as the rest of the dome, after two real
secret-display incidents on 22-23/07).

This module has NO gate of its own and must never be called directly from a
production/heartbeat path — bounding when/how much it may spend is the job of
a guardrail wrapper one layer up (mirrors ``agent_wallet_pilot.py``'s
``swap_fn``/``transfer_fn`` injection: this file's ``send_allowance_transfer``
is meant to be injected as that wrapper's ``transfer_fn``, never called on its
own from anywhere that isn't itself gated + kill-switch-checked).
"""
from __future__ import annotations

import json
import logging

from web3 import Web3

from aria_core.onchain.safe_robinhood_simulation import compute_transfer_digest
from aria_core.onchain.safe_robinhood_wallet import (
    ALLOWANCE_MODULE_ADDRESS,
    ROBINHOOD_TESTNET_CHAIN_ID,
    _rpc_url,
    read_allowance,
    require_expected_chain,
)

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Write-function ABI slice — allowed here only, same rationale as
# ``safe_robinhood_simulation.py``'s own copy: keeping it OUT of
# ``safe_robinhood_wallet.py`` is what makes that module's read-only ABI a
# structural (not just conventional) guardrail.
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


class DelegateKeyError(RuntimeError):
    """Raised on any problem loading the delegate key — never falls back to a
    default or a degraded mode, matching the fail-closed doctrine applied to
    every other guard on real capital in this repo."""


def _load_delegate_key(key_path: str):
    """Reads ``{"address": ..., "private_key": ...}`` from ``key_path``.
    Returns ``(checksum_address, eth_account.LocalAccount)``. Never logs the
    private key value — only the address, same as everywhere else in the
    dome (two real secret-display incidents, 22-23/07, are why this is
    stated explicitly rather than assumed obvious)."""
    from eth_account import Account

    if not key_path:
        raise DelegateKeyError("aucun chemin de clé délégué fourni (fail-closed)")
    try:
        with open(key_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise DelegateKeyError(f"clé délégué illisible ({exc})") from exc

    private_key = data.get("private_key")
    if not private_key:
        raise DelegateKeyError("champ 'private_key' absent du fichier de clé")

    account = Account.from_key(private_key)
    declared = (data.get("address") or "").strip()
    if declared and Web3.to_checksum_address(declared) != Web3.to_checksum_address(account.address):
        raise DelegateKeyError(
            "l'adresse déclarée dans le fichier ne correspond pas à la clé privée -- fichier corrompu"
        )
    return Web3.to_checksum_address(account.address), account


def _w3(w3=None):
    return w3 if w3 is not None else Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 20}))


async def send_allowance_transfer(
    *,
    safe: str,
    token: str,
    to: str,
    amount: int,
    delegate_key_path: str | None = None,
    account=None,
    payment_token: str = ZERO_ADDRESS,
    payment: int = 0,
    w3=None,
    wait_for_receipt: bool = True,
    allowed_chain_ids=frozenset({ROBINHOOD_TESTNET_CHAIN_ID}),
) -> dict:
    """Sends a REAL, signed ``executeAllowanceTransfer`` — the production
    equivalent of the one-off script already proven live (18/08 HANDOFF
    entry). Never raises past key-loading; a network/send failure is
    reported as ``{"error": ..., "tx_hash": None}`` so a caller (the
    guardrail wrapper) can log and classify it rather than crash.

    Order, matching the doctrine already established elsewhere in this repo
    (``agent_wallet_pilot.attempt_transfer``'s own ordering): chain preflight
    -> load delegate key -> re-read the REAL remaining allowance on-chain
    right now (never trust a caller-supplied or cached figure) -> reject if
    ``amount`` exceeds what is actually left -> sign the exact digest the
    contract itself generates -> send -> wait for and report the real
    receipt status (a mined transaction can still revert; a truthy
    ``tx_hash`` alone would be a false positive).

    Exactly ONE of ``delegate_key_path`` (reads a ``{"address",
    "private_key"}`` JSON file, the original mechanism) or ``account`` (an
    already-loaded ``eth_account.LocalAccount`` — e.g. from
    ``safe_robinhood_deploy.deployer_account()``, which reads the same
    testnet-only env var this dome already uses, 24/08) must be provided.
    Passing both or neither is a caller bug, never silently resolved by
    picking one — this dome never guesses which key material to trust.

    Declared ``async`` purely to match the injectable ``send_fn`` interface
    used across the dome (``agent_wallet_pilot.py``'s ``swap_fn``/
    ``transfer_fn``) — web3.py's HTTP provider is synchronous, so this
    function never actually yields control mid-call."""
    w3 = _w3(w3)
    require_expected_chain(w3, allowed_chain_ids)

    if (delegate_key_path is None) == (account is None):
        return {
            "error": "fournir exactement un de delegate_key_path OU account, jamais les deux ni aucun",
            "tx_hash": None,
        }
    if account is not None:
        address = Web3.to_checksum_address(account.address)
    else:
        address, account = _load_delegate_key(delegate_key_path)

    live = read_allowance(safe, address, token, w3=w3)
    if live.get("error"):
        return {"error": f"allowance réelle illisible ({live['error']})", "tx_hash": None}
    remaining = live.get("remaining")
    if remaining is None or amount > remaining:
        return {
            "error": (
                f"montant {amount} > allowance restante réelle {remaining} "
                "(lue on-chain à l'instant, jamais supposée)"
            ),
            "tx_hash": None,
        }

    digest = compute_transfer_digest(
        safe=safe, token=token, to=to, amount=amount, nonce=int(live["nonce"]),
        payment_token=payment_token, payment=payment,
        chain_id=w3.eth.chain_id, module_address=ALLOWANCE_MODULE_ADDRESS,
    )
    from eth_account import Account

    signed_digest = Account.unsafe_sign_hash(digest, account.key)

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS),
        abi=_ALLOWANCE_TRANSFER_ABI,
    )
    try:
        tx = contract.functions.executeAllowanceTransfer(
            Web3.to_checksum_address(safe), Web3.to_checksum_address(token),
            Web3.to_checksum_address(to), amount,
            Web3.to_checksum_address(payment_token), payment,
            address, bytes(signed_digest.signature),
        ).build_transaction({
            "from": address,
            "nonce": w3.eth.get_transaction_count(address),
            "chainId": w3.eth.chain_id,
        })
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    except Exception as exc:  # noqa: BLE001 -- network/send failure, never fabricate a result
        logger.error("safe_robinhood_signer -- send failed: %s", exc)
        return {"error": str(exc), "tx_hash": None}

    tx_hash_hex = "0x" + tx_hash.hex() if not tx_hash.hex().startswith("0x") else tx_hash.hex()
    result = {"error": None, "tx_hash": tx_hash_hex, "status": None}
    if wait_for_receipt:
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            result["status"] = "ok" if receipt.status == 1 else "reverted"
        except Exception as exc:  # noqa: BLE001 -- receipt lookup failure; tx may still be pending
            result["status"] = f"unknown ({exc})"
    return result
