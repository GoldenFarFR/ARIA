"""ARIA's homemade agent wallet, Solana leg (17/08) — FIRST MILESTONE ONLY:
read-only verification against the real devnet, NO private key, NO signing
logic yet. Mirrors ``safe_robinhood_wallet.py``'s scope and doctrine exactly
-- read that module's docstring for the full architecture context (dual-key
smart-contract-wallet, operator validated 17/08, pre-mainnet rehearsal).

Chosen pattern for Solana: Squads Protocol v4
(github.com/Squads-Protocol/v4) -- a ``SpendingLimit`` PDA account lets an
"agent key" spend within a bound (per member/mint/amount/period/destinations)
WITHOUT multisig approval as long as it stays inside that bound, enforced
on-chain by the program itself; the "owner key" (operator-held) retains
ultimate multisig control over the vault and over creating/removing spending
limits (a ``ConfigTransaction``, which DOES require multisig approval).
Formally verified, audited (OtterSec + Solana Foundation), secures >$10B.

The Squads v4 program ID below is REPORTED IDENTICAL on mainnet and devnet
(17/08 diligence agent finding) and was INDEPENDENTLY RE-VERIFIED live in the
same session via a real ``getAccountInfo`` RPC call against
``api.devnet.solana.com`` -- confirmed ``executable=true``, owned by the
standard upgradeable BPF loader, never taken on the agent's word alone.
Re-run ``verify_program_deployed()`` before trusting this again later.

No official Python SDK exists for Squads v4 (diligence finding) -- the
planned integration path is ``anchorpy`` + ``solana-py`` against the public
IDL, NOT a Node.js sidecar (same "everything in Python" doctrine as the
Robinhood Chain leg, for the same reasons: smaller attack surface, no second
runtime to patch/monitor on the VPS). Flagged risk to verify before
committing further: ``anchorpy``'s official PyPI releases look stale (last
tagged 0.21.0, March 2025) -- a community fork exists; must be smoke-tested
against Squads v4's actual Anchor 0.29.0 IDL on devnet before relying on it
for anything beyond this read-only check.

NOT built yet, in order: (1) anchorpy IDL client wired against the public
Squads v4 IDL, (2) multisig + SpendingLimit creation on devnet, (3) an agent
key spend within the limit + an over-limit spend rejected on-chain, (4) only
after (3) is proven and reviewed: a mainnet proposal.
"""
from __future__ import annotations

import os

_DEFAULT_DEVNET_RPC_URL = "https://api.devnet.solana.com"

# Squads v4 program -- identical address on mainnet and devnet (diligence
# finding, re-verified live 17/08: real executable account on devnet RPC
# above, owner == BPFLoaderUpgradeab1e11111111111111111111111).
SQUADS_V4_PROGRAM_ID = "SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf"


def _rpc_url() -> str:
    return (os.environ.get("ARIA_SQUADS_SOLANA_DEVNET_RPC_URL", "") or "").strip() or _DEFAULT_DEVNET_RPC_URL


def verify_program_deployed(*, client=None) -> dict:
    """Read-only sanity check: confirms the Squads v4 program above is a
    real, executable account on the configured (devnet-only) RPC right now
    -- never assumed from a stale address. Returns a dict, never raises on a
    network failure (mirrors ``safe_robinhood_wallet.verify_contracts_
    deployed``'s fail-safe pattern) -- a caller must check ``error``
    explicitly rather than assume success from a truthy return."""
    from solders.pubkey import Pubkey  # type: ignore[import]

    try:
        import httpx
    except ImportError as exc:  # pragma: no cover -- httpx is a transitive dep of solana-py
        return {"error": f"httpx unavailable ({exc})", "deployed": None}

    program_id = str(Pubkey.from_string(SQUADS_V4_PROGRAM_ID))
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
        "params": [program_id, {"encoding": "base64"}],
    }

    try:
        if client is not None:
            resp = client.post(_rpc_url(), json=payload, timeout=15)
        else:
            resp = httpx.post(_rpc_url(), json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 -- network failure, never fabricate a result
        return {"error": f"RPC unreachable ({exc})", "deployed": None}

    value = (data.get("result") or {}).get("value")
    if value is None:
        return {"error": None, "deployed": False, "program_id": program_id}

    return {
        "error": None,
        "deployed": True,
        "program_id": program_id,
        "executable": value.get("executable"),
        "owner": value.get("owner"),
    }
