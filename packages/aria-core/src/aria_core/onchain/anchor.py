"""Ancrage onchain du track-record sur Base — PRÉPARATION seulement (le serveur ne signe rien).

Le différenciateur d'ARIA pour Base : un track-record dont la racine Merkle est ancrée onchain,
donc vérifiable et inviolable. Ce module produit la DEMANDE d'ancrage (racine + adresse du
contrat + réseau Base) prête à être signée et diffusée **localement** par l'opérateur.

Dôme (non négociable) :
  - **Clé privée JAMAIS sur le serveur** : ce module ne détient, ne lit et ne génère aucune clé.
    Il ne construit AUCUNE transaction signée, n'encode pas de calldata, n'émet AUCUN appel
    réseau. Il prépare une instruction sémantique ; la signature + diffusion se font en local
    (comme l'acp-cli) ou via un relais qui garde la clé hors serveur.
  - **Gaté OFF par défaut** (`ARIA_ONCHAIN_ANCHOR_ENABLED`) : rien ne se prépare sans ce flag.
  - **Fail-closed / dégradation gracieuse** : sans contrat déployé ou sans enregistrement,
    renvoie `None` (jamais d'exception qui remonte).

Câblage vivant (le jour venu, geste opérateur) :
  1. Déployer `contracts/AriaLedger.sol` sur Base (en local, clé + ETH de l'opérateur).
  2. Poser `ARIA_LEDGER_ADDRESS=0x…` et `ARIA_ONCHAIN_ANCHOR_ENABLED=1`.
  3. `build_anchor_request(records)` fournit la racine + l'instruction ; l'opérateur signe et
     diffuse `anchor(bytes32 root)` depuis son wallet local.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from aria_core.onchain.attestation import merkle_root

# Base mainnet (l'écosystème visé). Le testnet Base Sepolia = 84532 (surchargable via env).
_DEFAULT_CHAIN_ID = 8453
_ANCHOR_FN = "anchor"  # AriaLedger.anchor(bytes32 root) onlyOwner, sans transfert de valeur


def anchor_enabled() -> bool:
    """Seam gaté OFF par défaut. Aucune préparation d'ancrage sans ce flag."""
    return os.environ.get("ARIA_ONCHAIN_ANCHOR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _ledger_address() -> str:
    """Adresse du contrat AriaLedger déployé (publique, jamais une clé)."""
    return (os.environ.get("ARIA_LEDGER_ADDRESS", "") or "").strip()


def _chain_id() -> int:
    raw = (os.environ.get("ARIA_ONCHAIN_CHAIN_ID", "") or "").strip()
    try:
        return int(raw) if raw else _DEFAULT_CHAIN_ID
    except ValueError:
        return _DEFAULT_CHAIN_ID


@dataclass
class AnchorRequest:
    """Demande d'ancrage prête pour une signature LOCALE. Aucune clé, aucun calldata signé."""
    chain_id: int
    network: str
    contract: str
    function: str
    root: str            # racine Merkle, hex bytes32 (0x + 64)
    record_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "chainId": self.chain_id, "network": self.network, "contract": self.contract,
            "function": self.function, "args": [self.root], "root": self.root,
            "recordCount": self.record_count, "requiresLocalSigning": True,
        }

    def as_json(self) -> str:
        """JSON à piper vers l'outil de signature local (cast/ethers)."""
        return json.dumps(self.as_dict(), ensure_ascii=False)

    def as_operator_instruction(self) -> str:
        """Instruction lisible : ce que l'opérateur exécute depuis son wallet local."""
        return (
            f"Ancrage onchain (signature LOCALE requise) : appeler {self.function}({self.root}) "
            f"sur AriaLedger {self.contract} (Base, chain {self.chain_id}), "
            f"racine de {self.record_count} enregistrement(s). Diffuser depuis ton wallet local."
        )


def build_anchor_request(records: list[dict[str, Any]]) -> AnchorRequest | None:
    """Prépare la demande d'ancrage de la racine Merkle de `records`.

    Fail-closed : renvoie `None` si le seam est OFF, si aucun contrat n'est configuré, ou s'il
    n'y a rien à ancrer. Ne signe rien, n'émet aucun appel réseau."""
    if not anchor_enabled():
        return None
    contract = _ledger_address()
    if not contract or not records:
        return None
    root = merkle_root(records)  # "0x" + sha256 hex = bytes32
    return AnchorRequest(
        chain_id=_chain_id(), network="base", contract=contract,
        function=_ANCHOR_FN, root=root, record_count=len(records),
    )
