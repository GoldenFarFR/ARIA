"""Onchain anchoring of the track record on Base — PREPARATION only (the server signs nothing).

ARIA's differentiator for Base: a track record whose Merkle root is anchored onchain, hence
verifiable and tamper-proof. This module produces the anchoring REQUEST (root + contract
address + Base network) ready to be signed and broadcast **locally** by the operator.

Guardrails (non-negotiable):
  - **Private key NEVER on the server**: this module holds, reads, or generates no key. It
    builds NO signed transaction, encodes no calldata, makes NO network call. It prepares a
    semantic instruction; signing + broadcasting happen locally (like acp-cli) or via a relay
    that keeps the key off the server.
  - **Gated OFF by default** (`ARIA_ONCHAIN_ANCHOR_ENABLED`): nothing is prepared without this
    flag.
  - **Fail-closed / graceful degradation**: without a deployed contract or a record, returns
    `None` (never a bubbling exception).

Live wiring (when the day comes, operator gesture):
  1. Deploy `contracts/AriaLedger.sol` on Base (locally, operator's key + ETH).
  2. Set `ARIA_LEDGER_ADDRESS=0x...` and `ARIA_ONCHAIN_ANCHOR_ENABLED=1`.
  3. `build_anchor_request(records)` supplies the root + instruction; the operator signs and
     broadcasts `anchor(bytes32 root)` from their local wallet.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from aria_core.onchain.attestation import merkle_root

# Base mainnet (the targeted ecosystem). Base Sepolia testnet = 84532 (overridable via env).
_DEFAULT_CHAIN_ID = 8453
_ANCHOR_FN = "anchor"  # AriaLedger.anchor(bytes32 root) onlyOwner, no value transfer


def anchor_enabled() -> bool:
    """Seam gated OFF by default. No anchoring preparation without this flag."""
    return os.environ.get("ARIA_ONCHAIN_ANCHOR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def ledger_address() -> str:
    """Address of the deployed AriaLedger contract (public, never a key)."""
    return (os.environ.get("ARIA_LEDGER_ADDRESS", "") or "").strip()


def _chain_id() -> int:
    raw = (os.environ.get("ARIA_ONCHAIN_CHAIN_ID", "") or "").strip()
    try:
        return int(raw) if raw else _DEFAULT_CHAIN_ID
    except ValueError:
        return _DEFAULT_CHAIN_ID


@dataclass
class AnchorRequest:
    """Anchoring request ready for a LOCAL signature. No key, no signed calldata."""
    chain_id: int
    network: str
    contract: str
    function: str
    root: str            # Merkle root, hex bytes32 (0x + 64)
    record_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "chainId": self.chain_id, "network": self.network, "contract": self.contract,
            "function": self.function, "args": [self.root], "root": self.root,
            "recordCount": self.record_count, "requiresLocalSigning": True,
        }

    def as_json(self) -> str:
        """JSON to pipe into the local signing tool (cast/ethers)."""
        return json.dumps(self.as_dict(), ensure_ascii=False)

    def as_operator_instruction(self) -> str:
        """Human-readable instruction: what the operator executes from their local wallet."""
        return (
            f"Ancrage onchain (signature LOCALE requise) : appeler {self.function}({self.root}) "
            f"sur AriaLedger {self.contract} (Base, chain {self.chain_id}), "
            f"racine de {self.record_count} enregistrement(s). Diffuser depuis ton wallet local."
        )


def build_anchor_request(records: list[dict[str, Any]]) -> AnchorRequest | None:
    """Prepares the anchoring request for the Merkle root of `records`.

    Fail-closed: returns `None` if the seam is OFF, if no contract is configured, or if there
    is nothing to anchor. Signs nothing, makes no network call."""
    if not anchor_enabled():
        return None
    contract = ledger_address()
    if not contract or not records:
        return None
    root = merkle_root(records)  # "0x" + sha256 hex = bytes32
    return AnchorRequest(
        chain_id=_chain_id(), network="base", contract=contract,
        function=_ANCHOR_FN, root=root, record_count=len(records),
    )
