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
    recent_priority_fee,
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
    """Every RPC call this module makes, through the shared gateway.

    HIGH priority throughout: this path signs and sends real money. A sell
    queued behind a price refresh is the failure that cost 80% twice on 22/08,
    and `rpc_http_url` is now only a hint -- the gateway owns endpoint choice,
    rate, and failover.
    """
    from aria_core.services import solana_gateway
    from aria_core.services.solana_rpc_budget import Priority

    data = await solana_gateway.call(
        method, params, priority=Priority.HIGH, client=client,
    )
    if data is None:
        raise SwapSignerError(f"no endpoint could serve {method}")
    if "error" in data:
        raise SwapSignerError(f"RPC {method} failed: {data['error']}")
    return data


# Commitment levels, and what each one really costs.
#
# `finalized` is ~31 slots past confirmation -- MEASURED at 12-13 seconds on
# every real trade, consistently, on a paid Helius endpoint. That is the
# consensus, not the provider: no RPC can make it faster.
#
# It was imposed after a real race on the Squads leg, where state read right
# after `confirmed` was still pre-transaction. That reasoning holds ONLY when
# the caller goes on to read on-chain state. A trade does not: it records a
# price the quote already returned. Paying 13 seconds of a collapsing bonding
# curve for a guarantee the path never uses is what made real trading lose
# 10.5% where the simulation, which assumes instant fills, showed +35%.
#
# So the level is the caller's choice, and `finalized` stays the default --
# anything that reads state afterwards keeps the old behaviour untouched.
COMMITMENT_FINALIZED = "finalized"
COMMITMENT_CONFIRMED = "confirmed"

# `sent` returns as soon as the chain ACCEPTED the transaction, without waiting
# for any confirmation at all (22/08, operator target: a buy under 500ms).
#
# The arithmetic that forces this choice: a Solana slot is ~400ms, so nothing
# can be confirmed sooner than that. Preparation measures 58ms and the send
# ~80ms, putting the floor WITH confirmation at ~540ms. Under 500ms is
# therefore only reachable by not waiting.
#
# What it costs, stated plainly: the swap is simulated against live state
# moments earlier and carries a priority fee, so a send that the RPC accepts
# almost always lands -- but "almost" is not "always", and a caller using this
# level MUST reconcile afterwards. The pocket does: an open position holding
# nothing on-chain is detected and cancelled, and `verifier-trades.py` reports
# the same mismatch independently. Never use this level without that safety
# net, and never for anything that cannot be undone.
COMMITMENT_SENT = "sent"
_ACCEPTED_AT = {
    COMMITMENT_SENT: (),  # nothing is awaited -- see the constant's comment
    COMMITMENT_FINALIZED: ("finalized",),
    # `finalized` also satisfies a caller asking for `confirmed`: a stricter
    # status must never be read as "not there yet".
    COMMITMENT_CONFIRMED: ("confirmed", "finalized"),
}

# Polling every 2s to catch a ~13s event is fine; to catch a ~1s one it wastes
# most of the gain. A Solana slot is ~400ms, so polling AT 400ms was the worst
# possible choice: a confirmation landing at 410ms was only seen at 800ms,
# doubling the very latency this level exists to remove. 100ms costs a handful
# of extra status calls -- nothing against a 0.10$ trade -- and bounds the
# detection error to a tenth of a slot.
_POLL_INTERVAL_CONFIRMED_S = 0.1


async def _await_finalized(
    signature: str, *, rpc_http_url: str, client: httpx.AsyncClient,
    commitment: str = COMMITMENT_FINALIZED,
) -> str:
    """Polls until the signature reaches `commitment`.

    Returns `ok`, `failed` (the chain rejected it), or `unknown` -- never a
    truthy result for a transaction still in limbo, whatever the level.
    """
    accepted = _ACCEPTED_AT.get(commitment)
    if accepted is None:
        raise SwapSignerError(f"unknown commitment {commitment!r}")
    if commitment == COMMITMENT_SENT:
        # Reported as `ok` because the chain accepted it, NOT because it is
        # known to have executed. The caller's reconciliation owns that.
        return "ok"
    interval = (
        _POLL_INTERVAL_CONFIRMED_S if commitment == COMMITMENT_CONFIRMED
        else _POLL_INTERVAL_S
    )
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
            if status.get("confirmationStatus") in accepted:
                return "ok"
        await asyncio.sleep(interval)
    return "unknown"


async def execute_swap(
    quote: dict, key_path: str, *, rpc_http_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    commitment: str = COMMITMENT_FINALIZED,
    priority_fee_lamports: int | None = None,
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
        if priority_fee_lamports is None and commitment != COMMITMENT_FINALIZED:
            # Latency-sensitive path: size the fee against the network rather
            # than a constant. A flat figure was 20% of a 0.10$ trade while the
            # network's own p90 sat at zero.
            priority_fee_lamports = await recent_priority_fee(
                rpc_http_url=rpc_http_url, client=client,
            )
        unsigned = await build_swap_transaction(
            quote, pubkey, client=client, priority_fee_lamports=priority_fee_lamports,
        )

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
            # skipPreflight mirrors the commitment choice: this function ALWAYS
            # simulates the transaction itself a few lines above, so the RPC's
            # own preflight is a second identical simulation. Skipping it on the
            # racing path removes a round trip without removing a check; the
            # careful path keeps both.
            [signed, {
                "encoding": "base64",
                "skipPreflight": commitment in (COMMITMENT_CONFIRMED, COMMITMENT_SENT),
                "maxRetries": 3,
            }],
            rpc_http_url=rpc_http_url, client=client,
        )
        signature = data.get("result")
        if not signature:
            raise SwapSignerError("sendTransaction returned no signature")

        logger.warning("%s: sent %s", _REAL_MONEY_LOG_PREFIX, signature)
        status = await _await_finalized(
            signature, rpc_http_url=rpc_http_url, client=client, commitment=commitment,
        )
        return {
            "status": status,
            "tx": signature,
            "out_amount": int(quote["outAmount"]),
            "simulated_units": sim.get("compute_units"),
        }
    finally:
        if owns:
            await client.aclose()
