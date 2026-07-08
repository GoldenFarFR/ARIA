"""Déclenche l'ancrage Sepolia — prépare la racine Merkle, demande la confirmation Telegram.

Point d'entrée unique du rehearsal pré-mainnet : ne signe rien ici (même principe que
``onchain/anchor.py`` — préparation seule). La signature réelle vit exclusivement dans
``onchain/sepolia_wallet.py``, atteignable uniquement depuis ``wallet_guard.resolve_spend``
après un clic Telegram réel.
"""
from __future__ import annotations

from typing import Any

from aria_core.onchain.anchor import anchor_enabled, ledger_address
from aria_core.onchain.attestation import merkle_root
from aria_core.onchain.sepolia_wallet import SEPOLIA_CHAIN_ID


async def escalate_sepolia_anchor(records: list[dict[str, Any]]) -> str | None:
    """Prépare la racine Merkle de ``records`` et envoie le prompt Telegram Oui/Non.

    Fail-closed comme ``build_anchor_request`` : ``None`` si le seam d'ancrage est OFF, si
    aucun contrat n'est configuré, ou s'il n'y a rien à ancrer. Verrouille explicitement
    ``chain_id=SEPOLIA_CHAIN_ID`` (84532) quel que soit le réglage de
    ``ARIA_ONCHAIN_CHAIN_ID`` — ce chemin ne demande jamais autre chose que du testnet.
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
