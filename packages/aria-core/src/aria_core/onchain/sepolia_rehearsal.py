"""Triggers the Sepolia anchor — prepares the Merkle root, requests Telegram confirmation.

Single entry point for the pre-mainnet rehearsal: signs nothing here (same principle
as ``onchain/anchor.py`` — preparation only). The real signature lives exclusively in
``onchain/sepolia_wallet.py``, reachable only from ``wallet_guard.resolve_spend`` after
a real Telegram click.
"""
from __future__ import annotations

from typing import Any

from aria_core.onchain.anchor import anchor_enabled, ledger_address
from aria_core.onchain.attestation import merkle_root
from aria_core.onchain.sepolia_wallet import SEPOLIA_CHAIN_ID


async def escalate_sepolia_anchor(records: list[dict[str, Any]]) -> str | None:
    """Prepares the Merkle root of ``records`` and sends the Telegram Yes/No prompt.

    Fail-closed like ``build_anchor_request``: ``None`` if the anchor seam is OFF, if
    no contract is configured, or if there is nothing to anchor. Explicitly locks
    ``chain_id=SEPOLIA_CHAIN_ID`` (84532) regardless of the ``ARIA_ONCHAIN_CHAIN_ID``
    setting — this path never asks for anything other than testnet.
    """
    if not anchor_enabled():
        return None
    contract = ledger_address()
    if not contract or not records:
        return None

    from aria_core.wallet_guard import escalate_spend

    root = merkle_root(records)
    return await escalate_spend(
        "onchain_anchor_sepolia",
        amount="0 ETH (contrat sans transfert de valeur)",
        counterparty=contract,
        description=(
            f"Ancrer la racine Merkle de {len(records)} enregistrement(s) sur Sepolia "
            f"(testnet, rehearsal pré-mainnet) — anchor({root}) sur {contract}."
        ),
        payload={"contract": contract, "root": root, "chain_id": SEPOLIA_CHAIN_ID},
    )
