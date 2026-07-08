#!/usr/bin/env python3
"""Génère un wallet Base Sepolia dédié à ARIA (testnet, ETH sans valeur réelle). À lancer
UNE fois, directement sur le VPS. Aucun secret n'est stocké par ce script — il affiche la
clé une fois ; à toi de la mettre dans le .env et de fermer ce terminal ensuite.

Prérequis : aria-core installé avec sa dépendance web3 (venv du VPS ou de dev).
"""
from eth_account import Account


def main() -> None:
    account = Account.create()
    print("== Wallet Sepolia ARIA (testnet — ETH sans valeur réelle) ==\n")
    print("1) Ajoute ces lignes au .env du VPS (vanguard/backend/.env), puis chmod 600 :\n")
    print(f"   ARIA_SEPOLIA_PRIVATE_KEY={account.key.hex()}")
    print("   ARIA_SEPOLIA_WALLET_ENABLED=1\n")
    print(f"2) Adresse publique (sûre à partager, jamais la ligne au-dessus) : {account.address}\n")
    print("3) Finance cette adresse en ETH Sepolia via un robinet public (gratuit, ex.")
    print("   https://www.alchemy.com/faucets/base-sepolia) — nécessaire pour payer le gas des")
    print("   transactions de test.\n")
    print("4) Redeploie (./vanguard/deploy.sh). Sans ARIA_SEPOLIA_WALLET_ENABLED, ce wallet")
    print("   reste inerte — aucune clé n'est même lue.")


if __name__ == "__main__":
    main()
