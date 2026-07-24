"""Python client for the local Tangem WalletConnect bridge (Node.js service,
``packages/tangem-wc-bridge/``) -- lets Python request a signature from the
operator's Tangem hardware wallet (a physical NFC tap on their phone approves
each request) without ARIA ever holding or seeing a private key.

Why a separate Node.js service instead of a pure-Python implementation: the
only maintained Python WalletConnect package (``pyWalletConnect``) implements
the WALLET role only -- ARIA needs the DAPP role (the one that proposes a
session and sends signing requests), and the only maintained SDK for that role
is the official JavaScript ``@walletconnect/sign-client`` (verified 2026-07-24,
no Python equivalent exists). Hand-rolling the WalletConnect v2 relay/crypto
protocol in Python was judged too risky for a component that will eventually
touch real capital -- see ``docs/HANDOFF_COINBASE_CDP.md`` for the full
reasoning and the operator's explicit sign-off on this approach.

Doctrine (mirrors every other real-capital-adjacent client in this project --
never invent a signature, never assume success on ambiguity):
  - Every function returns a dataclass with ``available``/``error`` fields
    on failure -- never raises for an expected failure mode (bridge
    unreachable, connection rejected by the operator, request timed out).
  - This module NEVER handles a private key -- it only relays HTTP calls to
    the local Node.js bridge, which itself only relays WalletConnect JSON-RPC
    to the Tangem app. The physical NFC tap is the only thing that produces
    a real signature.
  - One-shot setup-phase usage ONLY (matches the Node service's own
    doctrine): this is for the rare, human-supervised actions (granting the
    Spend Permission, any aria-smart-vc action) -- never wired into an
    autonomous/recurring heartbeat cycle.
  - Defaults to the bridge's own testnet-first network
    (``TANGEM_BRIDGE_NETWORK`` unset -> Node service defaults to Base Sepolia,
    eip155:84532) -- this client never overrides that silently.

STILL OPEN (deliberately not built yet, needs live hardware/CDP testing
first): wrapping this client into an ``eth_account.signers.base.BaseAccount``
subclass consumable by cdp-sdk. The exact signing payload shape CDP expects
for a Smart Account UserOperation is not yet verified against a live Tangem
tap -- building that wrapper blind, before the raw connect/request-signature
round-trip is proven to work, would risk shipping untested glue code. See
``docs/HANDOFF_COINBASE_CDP.md`` for the next concrete milestone."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BRIDGE_URL = "http://127.0.0.1:8787"


def bridge_url() -> str:
    """The local bridge's base URL. Always localhost by design -- overriding
    to a non-local host would defeat the "never exposed on the network"
    invariant the Node service itself enforces; this client does not attempt
    to second-guess an operator override, it simply uses whatever
    ``TANGEM_BRIDGE_URL`` is set to (defaults to the Node service's own
    default port)."""
    return os.environ.get("TANGEM_BRIDGE_URL", _DEFAULT_BRIDGE_URL).rstrip("/")


@dataclass
class BridgeConnectResult:
    available: bool
    connection_id: str | None = None
    uri: str | None = None
    error: str | None = None


@dataclass
class BridgeStatusResult:
    available: bool
    status: str | None = None  # "pending" | "connected" | "error"
    address: str | None = None
    error: str | None = None


@dataclass
class BridgeSignatureResult:
    available: bool
    result: object | None = None
    error: str | None = None


async def start_connection(*, timeout_seconds: float = 10.0) -> BridgeConnectResult:
    """Starts a new WalletConnect pairing. Returns a URI meant to be shown to
    the operator (as plain text, never a QR image if this is ever surfaced
    through Telegram -- QR rendering has been unreliable there in this
    project) so they can open it in the Tangem app. Pairing approval itself
    is asynchronous -- see ``wait_for_connection``."""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(f"{bridge_url()}/wc/connect")
        if resp.status_code != 200:
            return BridgeConnectResult(available=False, error=f"bridge returned HTTP {resp.status_code}")
        data = resp.json()
        return BridgeConnectResult(available=True, connection_id=data.get("connectionId"), uri=data.get("uri"))
    except httpx.HTTPError as exc:
        return BridgeConnectResult(available=False, error=f"bridge unreachable: {exc}")


async def poll_status(connection_id: str, *, timeout_seconds: float = 10.0) -> BridgeStatusResult:
    """A single status check -- ``wait_for_connection`` is the usual entry
    point for actually waiting on the operator's tap."""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(f"{bridge_url()}/wc/status", params={"connectionId": connection_id})
        if resp.status_code != 200:
            return BridgeStatusResult(available=False, error=f"bridge returned HTTP {resp.status_code}")
        data = resp.json()
        return BridgeStatusResult(available=True, status=data.get("status"), address=data.get("address"), error=data.get("error"))
    except httpx.HTTPError as exc:
        return BridgeStatusResult(available=False, error=f"bridge unreachable: {exc}")


async def wait_for_connection(
    connection_id: str, *, timeout_seconds: float = 120.0, poll_interval_seconds: float = 2.0
) -> BridgeStatusResult:
    """Polls until the operator approves (taps their Tangem card) or the
    connection errors out, up to ``timeout_seconds`` -- a real human action is
    expected on the other end, so this is a genuine wait, not a retry loop
    papering over a bug. Returns whatever the last poll saw on timeout
    (status stays "pending"), never invents a "connected" outcome."""
    deadline = time.monotonic() + timeout_seconds
    last = BridgeStatusResult(available=False, error="no poll attempted")
    while time.monotonic() < deadline:
        last = await poll_status(connection_id)
        if not last.available:
            return last
        if last.status in ("connected", "error"):
            return last
        await asyncio.sleep(poll_interval_seconds)
    return last


async def request_signature(
    connection_id: str, method: str, params: list, *, chain_id: str | None = None, timeout_seconds: float = 120.0
) -> BridgeSignatureResult:
    """Requests a signature over an already-connected session. Blocks until
    the operator approves/rejects the request on their Tangem app (a real NFC
    tap) or the request times out -- ``timeout_seconds`` defaults generously
    (2 minutes) since a human needs to notice the prompt and physically tap
    their card, not an instant round-trip."""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(
                f"{bridge_url()}/wc/request-signature",
                json={"connectionId": connection_id, "method": method, "params": params, "chainId": chain_id},
            )
        if resp.status_code != 200:
            data = {}
            try:
                data = resp.json()
            except ValueError:
                pass
            return BridgeSignatureResult(available=False, error=data.get("error") or f"bridge returned HTTP {resp.status_code}")
        data = resp.json()
        return BridgeSignatureResult(available=True, result=data.get("result"))
    except httpx.HTTPError as exc:
        return BridgeSignatureResult(available=False, error=f"bridge unreachable: {exc}")
