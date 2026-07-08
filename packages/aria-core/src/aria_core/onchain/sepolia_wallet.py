"""Wallet Sepolia d'ARIA — SEULE exception documentée à « clé privée jamais sur le serveur ».

Contrairement à ``onchain/anchor.py`` (préparation seule, signature 100% locale par
l'opérateur) et ``services/x402.py`` (aucune clé, jamais), ce module DÉTIENT une clé
privée sur le serveur et SIGNE réellement des transactions. Décision opérateur explicite
(08/07) : rehearsal pré-mainnet — anticiper et régler les problèmes (RPC, gas, nonce,
échecs de diffusion) sur un réseau où l'ETH ne vaut rien, avant d'envisager un jour la
même mécanique sur des fonds réels.

Dôme :
  - **Chain ID verrouillé** à ``SEPOLIA_CHAIN_ID`` (84532) — toute demande pour un autre
    chain_id est refusée avant même de toucher la clé (fail-closed). Empêche
    structurellement que ce code signe un jour sur mainnet par accident.
  - **Gaté OFF par défaut** (``ARIA_SEPOLIA_WALLET_ENABLED``) : aucune clé n'est même lue
    sans ce flag.
  - **Jamais appelé directement** : uniquement depuis ``wallet_guard.resolve_spend``,
    atteignable uniquement après un clic Telegram réel (même garde-fou que
    ``client_fund_job``/``trade_tokens``).
  - La clé (``ARIA_SEPOLIA_PRIVATE_KEY``) vit uniquement dans le ``.env`` du VPS — jamais
    dans le repo, jamais loggée, jamais renvoyée par aucune fonction de ce module.
"""
from __future__ import annotations

import os

SEPOLIA_CHAIN_ID = 84532
_DEFAULT_RPC_URL = "https://sepolia.base.org"

# Fragment d'ABI minimal — seule la fonction utilisée par ce module (AriaLedger.anchor,
# sans transfert de valeur, cf. contracts/AriaLedger.sol).
_ANCHOR_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "root", "type": "bytes32"}],
        "name": "anchor",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def sepolia_wallet_enabled() -> bool:
    """Seam gaté OFF par défaut. Aucune clé lue, aucune connexion RPC sans ce flag."""
    return os.environ.get("ARIA_SEPOLIA_WALLET_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _private_key() -> str:
    return (os.environ.get("ARIA_SEPOLIA_PRIVATE_KEY", "") or "").strip()


def _rpc_url() -> str:
    return (os.environ.get("ARIA_SEPOLIA_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _account(*, account_cls=None):
    """Compte dérivé de la clé privée. ``account_cls`` injectable (tests hors-ligne)."""
    if not sepolia_wallet_enabled():
        return None
    key = _private_key()
    if not key:
        return None
    if account_cls is None:
        from eth_account import Account as account_cls  # noqa: N813
    return account_cls.from_key(key)


def get_address(*, account_cls=None) -> str | None:
    """Adresse publique du wallet Sepolia d'ARIA — sûr à exposer (jamais la clé elle-même)."""
    account = _account(account_cls=account_cls)
    return account.address if account else None


def get_balance_eth(*, w3=None, account_cls=None) -> float | None:
    """Solde en ETH Sepolia (sans valeur réelle). None si non configuré/indisponible —
    jamais d'exception qui remonte pour une simple lecture de solde."""
    address = get_address(account_cls=account_cls)
    if not address:
        return None
    try:
        if w3 is None:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 15}))
        wei = w3.eth.get_balance(w3.to_checksum_address(address))
        return float(w3.from_wei(wei, "ether"))
    except Exception:
        return None


def send_anchor_transaction(
    *, contract: str, root: str, chain_id: int, w3=None, account_cls=None,
) -> str:
    """Signe et diffuse ``anchor(bytes32 root)`` sur Sepolia UNIQUEMENT.

    Lève (ne renvoie jamais silencieusement) si le seam est OFF, si ``chain_id`` n'est pas
    Sepolia, ou si la diffusion échoue — contrairement au reste du dôme onchain (préparation
    seule, dégradation gracieuse), une transaction réellement signée doit toujours faire
    remonter une erreur claire à l'opérateur plutôt que disparaître.
    """
    if not sepolia_wallet_enabled():
        raise RuntimeError("wallet Sepolia désactivé (ARIA_SEPOLIA_WALLET_ENABLED)")
    if int(chain_id) != SEPOLIA_CHAIN_ID:
        raise RuntimeError(
            f"refusé : chain_id {chain_id} != Sepolia ({SEPOLIA_CHAIN_ID}) — "
            "ce wallet ne signe jamais en dehors du testnet"
        )
    account = _account(account_cls=account_cls)
    if account is None:
        raise RuntimeError("ARIA_SEPOLIA_PRIVATE_KEY absente — rien à signer")

    if w3 is None:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 20}))

    root_hex = root[2:] if root.startswith("0x") else root
    root_bytes = bytes.fromhex(root_hex)

    ledger = w3.eth.contract(address=w3.to_checksum_address(contract), abi=_ANCHOR_ABI)
    tx = ledger.functions.anchor(root_bytes).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": SEPOLIA_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()
