"""ARIA's homemade agent wallet, Robinhood Chain leg (17/08) — FIRST MILESTONE
ONLY: read-only verification against the real testnet, NO private key, NO
signing logic yet.

Context: the operator validated (17/08) a self-built wallet architecture over
every commercial provider evaluated (Coinbase CDP/MetaMask Agent
Wallet/Turnkey/MoonPay PayBox — see
``docs/HANDOFF_COINBASE_CDP.md``/memory ``project_wallet_provider_diligence_
solana_robinhood``) for a future modest real-capital pilot (~100$) on
Robinhood Chain + Solana. The chosen pattern (industry-standard dual-key
smart-contract-wallet): Safe{Wallet} + the official ``AllowanceModule``
(github.com/safe-global/safe-modules) — an "agent key" gets a spending
allowance structurally bounded ON-CHAIN (never just application-level), the
"owner key" (operator-held) keeps ultimate override control.

This module is deliberately scoped to the SAME "pre-mainnet rehearsal"
doctrine already established by ``sepolia_wallet.py``: prove the
infrastructure is real and reachable on a network where funds are worthless,
BEFORE any signing code is written. Both contract addresses below were
reported by a research agent (17/08 diligence) and then INDEPENDENTLY
RE-VERIFIED live against the real testnet RPC in the same session (real
``eth_getCode`` calls, non-empty bytecode confirmed for both, real
``eth_chainId`` confirmed == 46630) — never taken on the agent's word alone.
Re-run ``verify_contracts_deployed()`` before trusting these addresses again
in a future session; a redeploy or address change would break this silently
otherwise.

Chain ID is LOCKED to the TESTNET (46630) for this whole module — there is no
mainnet code path here at all yet. Mainnet (4663) is a deliberate future
step, gated behind its own explicit operator validation and its own CLAUDE.md
paragraph, exactly like the CDP pilot before it.

NOT built yet, in order: (1) AllowanceModule ABI + web3.py wiring to read a
live allowance state, (2) Safe creation + module activation on testnet via
``safe-eth-py``, (3) EIP-712 allowance-transfer signing/execution by the
agent key, (4) real end-to-end testnet cycle (owner sets an allowance, agent
key spends within it, an over-limit spend is rejected on-chain), (5) only
after (4) is proven and reviewed: a mainnet proposal.
"""
from __future__ import annotations

import os

# Locked — this module never touches mainnet (4663). See module docstring.
ROBINHOOD_TESTNET_CHAIN_ID = 46630
_DEFAULT_TESTNET_RPC_URL = "https://rpc.testnet.chain.robinhood.com"

# Safe v1.4.1 canonical singleton — same CREATE2 address as most EVM chains
# (safe-global/safe-deployments registry). Re-verified live 17/08: real
# bytecode present at this address on the testnet RPC above (23579 bytes).
SAFE_SINGLETON_V141_ADDRESS = "0x41675C099F32341bf84BFc5382aF534df5C7461a"

# AllowanceModule v0.1.1 — same CREATE2 address across 52 networks including
# Robinhood Chain mainnet (4663) and testnet (46630). Re-verified live 17/08:
# real bytecode present at this address on the testnet RPC above (14908
# bytes). NOTE (diligence finding, unresolved): the only audit report found
# covers v0.1.0 (Oct 2020) — no report identified yet for v0.1.1, the version
# actually deployed at this address. Flag this again before any mainnet use.
ALLOWANCE_MODULE_ADDRESS = "0xAA46724893dedD72658219405185Fb0Fc91e091C"


def _rpc_url() -> str:
    return (os.environ.get("ARIA_SAFE_ROBINHOOD_TESTNET_RPC_URL", "") or "").strip() or _DEFAULT_TESTNET_RPC_URL


def verify_contracts_deployed(*, w3=None) -> dict:
    """Read-only sanity check: confirms both contracts above actually have
    bytecode on the configured (testnet-only) RPC right now, and that the RPC
    itself really answers for chain 46630 -- never assumed from a stale
    address list. Returns a dict, never raises on a network failure (mirrors
    ``sepolia_wallet.get_code``'s fail-safe pattern) -- a caller must check
    ``error`` explicitly rather than assume success from a truthy return."""
    from web3 import Web3

    if w3 is None:
        w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 15}))

    try:
        chain_id = w3.eth.chain_id
    except Exception as exc:  # noqa: BLE001 -- network failure, never fabricate a result
        return {"error": f"RPC unreachable ({exc})", "chain_id_ok": None}

    chain_id_ok = chain_id == ROBINHOOD_TESTNET_CHAIN_ID
    result = {"error": None, "chain_id": chain_id, "chain_id_ok": chain_id_ok}

    for label, address in (
        ("safe_singleton", SAFE_SINGLETON_V141_ADDRESS),
        ("allowance_module", ALLOWANCE_MODULE_ADDRESS),
    ):
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(address))
            result[label] = {"address": address, "bytecode_len": len(code), "deployed": len(code) > 0}
        except Exception as exc:  # noqa: BLE001
            result[label] = {"address": address, "error": str(exc), "deployed": None}

    return result
