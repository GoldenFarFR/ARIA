"""Signs and sends a Jupiter swap on Solana mainnet -- REAL MONEY (21/08).

Mirrors `onchain/squads_solana_signer.py`'s doctrine exactly, because both
touch real capital and must be auditable the same way:

  - the private key is loaded from a path the CALLER supplies explicitly, with
    NO default (fail-closed) and is never read into a log or a transcript;
  - the swap is SIMULATED against real mainnet state and refused if the
    simulation fails -- an unconditional pre-flight, with no bypass parameter
    anywhere in this module;
  - slippage is forced to the project ceiling, never a tool's default;
  - success is only reported after a REAL `finalized` status. `confirmed` is
    NOT enough: a genuine race was found on the Squads leg where a read taken
    milliseconds after `confirmed` still returned pre-transaction state.

This module DECIDES nothing. It has no gate, no kill-switch and no cap of its
own -- those live in `homemade_agent_wallet.attempt_transfer`'s wrapper, which
is what production must call. Wiring this directly into a trading loop would
bypass every guardrail the dome has.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path

import httpx

from aria_core.onchain.jupiter_swap_simulation import (
    SwapSimulationError,
    build_swap_transaction,
    simulate_swap_transaction,
)
from aria_core.services.jupiter import MAX_SLIPPAGE_BPS
from aria_core.services.pumpswap_ws import require_solana_rpc_http

logger = logging.getLogger(__name__)

_REAL_MONEY_LOG_PREFIX = "REAL-MONEY solana-swap"

# How long to wait for a finalized status before giving up. A swap left in
# limbo is reported as unknown, never as success -- a truthy signature alone
# is a false positive (same lesson as the EVM leg's receipt check).
_FINALIZE_TIMEOUT_S = 90.0
_POLL_INTERVAL_S = 2.0


class SwapSignerError(RuntimeError):
    """Raised when a swap could not be signed or sent. Never partial."""


class DelegateKeyError(SwapSignerError):
    """Raised when the signing key cannot be loaded. Deliberately distinct:
    a missing key is an operator/config problem, not a market one."""


def load_keypair(key_path: str):
    """Loads a Solana keypair from the native `solana-keygen` JSON array.

    No default path on purpose. The key file is expected outside the repo,
    chmod 600, and this function returns the keypair object -- never its
    bytes, never anything printable.
    """
    if not key_path:
        raise DelegateKeyError("no key path supplied -- refusing to guess one")
    p = Path(key_path)
    if not p.exists():
        raise DelegateKeyError(f"key file not found: {key_path}")
    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        # A world- or group-readable key on a shared VPS is a real finding,
        # not a style preference.
        raise DelegateKeyError(f"key file {key_path} is readable by others (mode {mode:o})")
    try:
        from solders.keypair import Keypair

        data = json.loads(p.read_text())
        return Keypair.from_bytes(bytes(data))
    except Exception as exc:  # noqa: BLE001 -- never surface key material
        raise DelegateKeyError(f"could not load keypair from {key_path}: {type(exc).__name__}") from exc


def sign_transaction(swap_transaction_b64: str, keypair) -> str:
    """Signs Jupiter's unsigned transaction, returning it base64-encoded.

    Handles both legacy and versioned transactions: Jupiter returns either
    depending on the route, and assuming one shape produces an opaque
    deserialisation failure rather than a clear error.
    """
    try:
        from solders.message import to_bytes_versioned
        from solders.transaction import VersionedTransaction

        raw = base64.b64decode(swap_transaction_b64, validate=True)
        unsigned = VersionedTransaction.from_bytes(raw)
        signature = keypair.sign_message(to_bytes_versioned(unsigned.message))
        signed = VersionedTransaction.populate(unsigned.message, [signature])
        return base64.b64encode(bytes(signed)).decode()
    except Exception as exc:  # noqa: BLE001
        raise SwapSignerError(f"signing failed: {type(exc).__name__}: {exc}") from exc


async def _rpc(method: str, params: list, *, rpc_http_url: str, client: httpx.AsyncClient) -> dict:
    resp = await client.post(
        rpc_http_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise SwapSignerError(f"RPC {method} failed: {data['error']}")
    return data


async def _await_finalized(signature: str, *, rpc_http_url: str, client: httpx.AsyncClient) -> str:
    """Polls until the signature is FINALIZED.

    `confirmed` is deliberately not accepted: a real race was found on the
    Squads leg where state read right after `confirmed` was still
    pre-transaction. A few seconds of latency buys a guarantee that anything
    read afterwards is consistent.
    """
    deadline = asyncio.get_event_loop().time() + _FINALIZE_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        data = await _rpc(
            "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}],
            rpc_http_url=rpc_http_url, client=client,
        )
        value = (data.get("result") or {}).get("value") or [None]
        status = value[0]
        if status:
            if status.get("err"):
                return "failed"
            if status.get("confirmationStatus") == "finalized":
                return "ok"
        await asyncio.sleep(_POLL_INTERVAL_S)
    return "unknown"


async def execute_swap(
    quote: dict, key_path: str, *, rpc_http_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Simulates, signs, sends, and waits for finalization. REAL MONEY.

    Returns ``{"status", "tx", "out_amount", "simulated_units"}`` where status
    is ``ok`` / ``failed`` / ``unknown``. ``unknown`` means the transaction may
    still land -- the caller must treat it as possibly-executed, never as a
    clean failure.
    """
    if quote.get("slippage_bps_used", MAX_SLIPPAGE_BPS) > MAX_SLIPPAGE_BPS:
        raise SwapSignerError("quote slippage exceeds the project ceiling")

    keypair = load_keypair(key_path)
    pubkey = str(keypair.pubkey())
    rpc_http_url = rpc_http_url or require_solana_rpc_http()

    owns = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        unsigned = await build_swap_transaction(quote, pubkey, client=client)

        # UNCONDITIONAL pre-flight. There is no parameter to skip this: a swap
        # that fails in simulation would fail on-chain, and paying a fee to
        # discover that is a choice nobody should be able to make by accident.
        sim = await simulate_swap_transaction(unsigned, rpc_http_url=rpc_http_url, client=client)
        if not sim["ok"]:
            logger.warning(
                "%s: refused before sending -- simulation failed (%s)",
                _REAL_MONEY_LOG_PREFIX, sim["error"],
            )
            return {"status": "failed", "tx": None, "out_amount": None,
                    "simulated_units": sim.get("compute_units"), "reason": "simulation_failed"}

        signed = sign_transaction(unsigned, keypair)
        data = await _rpc(
            "sendTransaction",
            [signed, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
            rpc_http_url=rpc_http_url, client=client,
        )
        signature = data.get("result")
        if not signature:
            raise SwapSignerError("sendTransaction returned no signature")

        logger.warning("%s: sent %s", _REAL_MONEY_LOG_PREFIX, signature)
        status = await _await_finalized(signature, rpc_http_url=rpc_http_url, client=client)
        return {
            "status": status,
            "tx": signature,
            "out_amount": int(quote["outAmount"]),
            "simulated_units": sim.get("compute_units"),
        }
    finally:
        if owns:
            await client.aclose()
