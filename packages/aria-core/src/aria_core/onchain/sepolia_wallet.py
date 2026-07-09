"""Wallet Sepolia d'ARIA — SEULE exception documentée à « clé privée jamais sur le serveur ».

``send_test_swap_transaction`` (ajouté 09/07, décision opérateur explicite : « swap réel
sur Sepolia, actif de test ») exécute un VRAI swap Uniswap V3 (wrap WETH -> approve ->
exactInputSingle, trois transactions signées réelles) mais sur une paire de TEST
configurée (``ARIA_SEPOLIA_SWAP_TOKEN_OUT``), jamais sur le token candidat réellement
analysé par ARIA (qui n'existe pas sur ce testnet, chaîne différente de Base mainnet).
Objectif borné : prouver que le mécanisme de signature/diffusion/confirmation d'un swap
fonctionne réellement (gas, slippage, nonce, échecs RPC) — PAS valider une stratégie de
marché. Adresse routeur/token de sortie non fournies par défaut : doivent être vérifiées
on-chain (bytecode + liquidité réelle) avant d'armer ``ARIA_SEPOLIA_SWAP_ENABLED``, cette
vérification n'a pas pu être faite depuis cette session (pas d'accès RPC direct dans cet
environnement — voir HANDOFF).

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

# Fragments ABI minimaux pour le swap de test — uniquement les fonctions appelées
# (WETH9 standard : deposit/approve ; Uniswap V3 SwapRouter02 standard : exactInputSingle).
_WETH_ABI = [
    {
        "inputs": [], "name": "deposit", "outputs": [],
        "stateMutability": "payable", "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable", "type": "function",
    },
]

_SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params", "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable", "type": "function",
    }
]

MAX_TEST_SWAP_WEI = 2 * 10**15  # plafond dur ~0.002 ETH testnet (sans valeur réelle) par swap


def sepolia_swap_enabled() -> bool:
    """Gate additif dédié au swap de test — au-dessus de sepolia_wallet_enabled, jamais
    actif seul. Le wallet peut ancrer des décisions sans jamais swapper."""
    if not sepolia_wallet_enabled():
        return False
    return os.environ.get("ARIA_SEPOLIA_SWAP_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def swap_router_address() -> str:
    return (os.environ.get("ARIA_SEPOLIA_SWAP_ROUTER", "") or "").strip()


def swap_token_in() -> str:
    """WETH prédéploiement OP-stack — même adresse sur toutes les chaînes OP-stack
    (Base, Base Sepolia inclus), pas besoin de vérification par environnement."""
    return (
        os.environ.get("ARIA_SEPOLIA_SWAP_TOKEN_IN", "") or ""
    ).strip() or "0x4200000000000000000000000000000000000006"


def swap_token_out() -> str:
    return (os.environ.get("ARIA_SEPOLIA_SWAP_TOKEN_OUT", "") or "").strip()


def swap_fee_tier() -> int:
    return int(os.environ.get("ARIA_SEPOLIA_SWAP_FEE_TIER", "3000") or 3000)


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


def send_test_swap_transaction(
    *,
    amount_in_wei: int,
    chain_id: int,
    router: str | None = None,
    token_in: str | None = None,
    token_out: str | None = None,
    fee: int | None = None,
    w3=None,
    account_cls=None,
) -> dict:
    """Wrap WETH -> approve -> exactInputSingle : trois transactions réellement signées
    et diffusées sur Sepolia UNIQUEMENT, sur la paire de TEST configurée — jamais le token
    candidat qu'ARIA analyse réellement (inexistant sur ce testnet). Teste le mécanisme
    d'exécution (signature, gas, nonce, confirmation), pas une décision de marché.

    Lève (jamais de dégradation silencieuse, comme ``send_anchor_transaction``) si le seam
    est OFF, hors Sepolia, le montant dépasse ``MAX_TEST_SWAP_WEI``, ou si routeur/token de
    sortie ne sont pas configurés — pas de valeur par défaut inventée pour un contrat non
    vérifié.
    """
    if not sepolia_swap_enabled():
        raise RuntimeError("swap de test Sepolia désactivé (ARIA_SEPOLIA_SWAP_ENABLED)")
    if int(chain_id) != SEPOLIA_CHAIN_ID:
        raise RuntimeError(
            f"refusé : chain_id {chain_id} != Sepolia ({SEPOLIA_CHAIN_ID}) — "
            "ce wallet ne signe jamais en dehors du testnet"
        )
    if amount_in_wei <= 0 or amount_in_wei > MAX_TEST_SWAP_WEI:
        raise RuntimeError(
            f"montant refusé : {amount_in_wei} wei hors bornes (0, {MAX_TEST_SWAP_WEI}] — "
            "plafond de sécurité mécanique, pas un montant de trading"
        )

    router = (router or swap_router_address()).strip()
    token_in = (token_in or swap_token_in()).strip()
    token_out = (token_out or swap_token_out()).strip()
    fee = fee if fee is not None else swap_fee_tier()
    if not router or not token_out:
        raise RuntimeError(
            "routeur ou token de sortie non configurés (ARIA_SEPOLIA_SWAP_ROUTER / "
            "ARIA_SEPOLIA_SWAP_TOKEN_OUT) — vérification on-chain requise avant swap réel"
        )

    account = _account(account_cls=account_cls)
    if account is None:
        raise RuntimeError("ARIA_SEPOLIA_PRIVATE_KEY absente — rien à signer")

    if w3 is None:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 20}))

    router_cs = w3.to_checksum_address(router)
    token_in_cs = w3.to_checksum_address(token_in)
    token_out_cs = w3.to_checksum_address(token_out)

    weth = w3.eth.contract(address=token_in_cs, abi=_WETH_ABI)
    swap_router = w3.eth.contract(address=router_cs, abi=_SWAP_ROUTER_ABI)

    def _sign_and_send(built_tx) -> str:
        signed = account.sign_transaction(built_tx)
        return w3.eth.send_raw_transaction(signed.raw_transaction).hex()

    nonce = w3.eth.get_transaction_count(account.address)

    deposit_tx = weth.functions.deposit().build_transaction({
        "from": account.address, "value": amount_in_wei,
        "nonce": nonce, "chainId": SEPOLIA_CHAIN_ID,
    })
    deposit_hash = _sign_and_send(deposit_tx)

    approve_tx = weth.functions.approve(router_cs, amount_in_wei).build_transaction({
        "from": account.address, "nonce": nonce + 1, "chainId": SEPOLIA_CHAIN_ID,
    })
    approve_hash = _sign_and_send(approve_tx)

    swap_params = (
        token_in_cs, token_out_cs, fee, account.address,
        amount_in_wei, 0, 0,
    )
    swap_tx = swap_router.functions.exactInputSingle(swap_params).build_transaction({
        "from": account.address, "nonce": nonce + 2, "chainId": SEPOLIA_CHAIN_ID,
    })
    swap_hash = _sign_and_send(swap_tx)

    return {"deposit_tx": deposit_hash, "approve_tx": approve_hash, "swap_tx": swap_hash}
