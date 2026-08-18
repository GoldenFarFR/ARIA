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
integration path is ``anchorpy`` + ``solana-py`` against the public IDL,
NOT a Node.js sidecar (same "everything in Python" doctrine as the
Robinhood Chain leg, for the same reasons: smaller attack surface, no second
runtime to patch/monitor on the VPS).

**18/08 -- anchorpy smoke-tested and confirmed viable, now wired as a real
dependency (``agent_wallet_solana`` extra).** The stale-PyPI risk flagged
above turned out not to matter: ``Program.fetch_raw_idl(program_id,
provider)`` genuinely returns Squads v4's real on-chain IDL (verified live,
``squads_multisig_program`` v2.1.0, Anchor's own IDL-account convention --
no community fork needed). Installing it DOWNGRADED the already-present
``solana``/``solders`` (transitive via ``cdp-sdk``, the real-capital pilot's
own dependency) to satisfy anchorpy's pin -- verified safe: `pip check`
clean, and the full CDP/agent-wallet test suite (480 tests) re-run green
after the downgrade, not just assumed compatible. Separately, anchorpy
registers its own pytest plugin via a setuptools entry point that hard-
requires ``pytest_xprocess`` (unrelated to anything this project needs) --
breaks pytest COLLECTION project-wide the moment anchorpy is installed;
disabled via ``addopts = "-p no:anchorpy"`` in ``pyproject.toml`` rather
than adding an unused dependency to satisfy it.

Built, in order: (1) anchorpy IDL client wired against the public Squads v4
IDL -- ``fetch_program_idl()`` below. NOT built yet: (2) multisig +
SpendingLimit creation on devnet, (3) an agent key spend within the limit +
an over-limit spend rejected on-chain, (4) only after (3) is proven and
reviewed: a mainnet proposal.
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


async def fetch_program_idl(*, fetch_fn=None) -> dict:
    """Read-only: fetches Squads v4's REAL on-chain IDL via anchorpy against
    the configured (devnet-only) RPC -- the first concrete building block
    toward a typed program client (multisig/SpendingLimit account parsing,
    instruction building). Never signs, never sends a transaction -- this
    function cannot even reach a write path (``Program.fetch_raw_idl`` is
    itself read-only, no ``Wallet``/signer is ever constructed here).

    ``fetch_fn``, if given, replaces the real anchorpy call entirely (an
    injectable async callable returning the raw IDL JSON string) -- same
    dependency-injection pattern as ``verify_program_deployed``'s ``client``
    parameter, so tests never touch the network. Returns a dict, never
    raises -- mirrors every other read-only helper in this module; a caller
    must check ``error`` explicitly rather than assume success."""
    from solders.pubkey import Pubkey  # type: ignore[import]

    program_id = Pubkey.from_string(SQUADS_V4_PROGRAM_ID)

    try:
        if fetch_fn is not None:
            raw_idl = await fetch_fn()
        else:
            from anchorpy import Program  # type: ignore[import]
            from anchorpy.provider import Provider  # type: ignore[import]
            from solana.rpc.async_api import AsyncClient  # type: ignore[import]

            client = AsyncClient(_rpc_url())
            try:
                provider = Provider(client, None)
                raw_idl = await Program.fetch_raw_idl(program_id, provider)
            finally:
                await client.close()
    except Exception as exc:  # noqa: BLE001 -- network/parse failure, never fabricate a result
        return {"error": f"IDL fetch failed ({exc})", "idl": None}

    if raw_idl is None:
        return {"error": None, "idl": None, "program_id": str(program_id)}

    import json

    try:
        parsed = json.loads(raw_idl)
    except (TypeError, ValueError) as exc:
        return {"error": f"IDL response was not valid JSON ({exc})", "idl": None}

    return {
        "error": None,
        "idl": parsed,
        "program_id": str(program_id),
        "idl_name": parsed.get("name"),
        "idl_version": parsed.get("version"),
        "instruction_count": len(parsed.get("instructions") or []),
    }
