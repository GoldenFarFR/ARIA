"""Lecture on-chain Base mainnet, lecture seule -- aucune clé, aucun signing (audit 11/07).

Complète ``services/virtuals.py::graduation_progress`` (qui reste `None` faute de donnée
API) avec une vraie lecture on-chain, gatée OFF par défaut. Contrairement à
``onchain/sepolia_wallet.py`` (détient une clé, signe des transactions testnet), ce module
ne fait QUE des ``eth_call`` publics (``getReserves``, ``tokenGradThreshold``) -- même
garantie que ``services/blockscout.py``, aucun risque financier possible par construction.

## Ce qui est confirmé (investigation 11/07, cf. commentaire au-dessus de
## ``_VIRTUAL_RAISED_KEYS`` dans ``virtuals.py`` pour le détail complet)

- La réserve initiale du token agent dans la paire de bonding vaut TOUJOURS
  ``INITIAL_TOKEN_RESERVE`` (1 milliard, aussi confirmé par le champ API `totalSupply`)
  -- vérifié identique sur plusieurs tokens fraîchement lancés.
- Le seuil de graduation est PAR TOKEN (``tokenGradThreshold(address)`` sur le contrat
  Bonding V5, pas une constante globale) -- formule validée empiriquement sur un vrai
  token gradué : `reserve0 <= tokenGradThreshold` au moment de la graduation, exactement
  la condition du contrat source (`BondingV5.sol::_buy`).

## Limite honnête -- PAS résolue

Plusieurs instances du contrat Bonding V5 coexistent ; ``BONDING_V5_CONTRACT`` ci-dessous
n'en couvre qu'UNE (celle où un vrai `Graduated` event a été trouvé par balayage de logs).
Un token géré par une autre instance renvoie `tokenGradThreshold == 0` (jamais enregistré)
-- traité comme "pas de donnée", dégradation vers `None`, jamais une fausse valeur.
Couverture partielle honnête, pas un bug.
"""
from __future__ import annotations

import os

_DEFAULT_RPC_URL = "https://mainnet.base.org"

# Une seule instance connue du contrat Bonding V5 (Base mainnet), confirmée par balayage
# direct des logs on-chain (`Graduated(address,address)`) et vérifiée deux fois : un vrai
# `tokenInfo()` non vide pour un token gradué réel, et un vrai `tokenGradThreshold()` non
# nul pour ce même token ET deux tokens fraîchement lancés différents (WOODY, CRASHCAT).
BONDING_V5_CONTRACT = "0x1A540088125d00dD3990f9dA45CA0859af4d3B01"

# Réserve initiale du token agent dans la paire -- constante confirmée (pas une supposition)
# sur 4 tokens fraîchement lancés différents, et cohérente avec le champ API `totalSupply`.
INITIAL_TOKEN_RESERVE = 1_000_000_000.0

# Fragments ABI minimaux -- seules les fonctions lues par ce module.
_GET_RESERVES_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint256", "name": "reserve0", "type": "uint256"},
            {"internalType": "uint256", "name": "reserve1", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
_TOKEN_GRAD_THRESHOLD_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "tokenGradThreshold",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def onchain_graduation_enabled() -> bool:
    """Gate additif -- aucun eth_call n'est même tenté sans ce flag (fail-closed)."""
    return os.environ.get("ARIA_ONCHAIN_GRADUATION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _rpc_url() -> str:
    return (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _client(*, w3=None):
    if w3 is not None:
        return w3
    from web3 import Web3

    return Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 10}))


def fetch_pair_reserve0(pair_address: str, *, w3=None) -> float | None:
    """Réserve courante du token agent (``reserve0``) dans la paire de bonding, en unités
    entières (déjà divisée par 1e18). `None` si l'adresse est invalide, le RPC échoue, ou
    la lecture échoue pour toute autre raison -- jamais bloquant, jamais d'exception qui
    remonte à l'appelant."""
    if not pair_address:
        return None
    try:
        client = _client(w3=w3)
        contract = client.eth.contract(
            address=client.to_checksum_address(pair_address), abi=_GET_RESERVES_ABI
        )
        reserve0, _reserve1 = contract.functions.getReserves().call()
        return float(reserve0) / 1e18
    except Exception:
        return None


def fetch_token_grad_threshold(token_address: str, *, w3=None) -> float | None:
    """Seuil de graduation propre à ce token, lu sur `BONDING_V5_CONTRACT`. `None` si le
    token n'est pas enregistré sur CETTE instance du contrat (couverture partielle connue,
    pas une erreur) ou si la lecture échoue."""
    if not token_address:
        return None
    try:
        client = _client(w3=w3)
        contract = client.eth.contract(
            address=client.to_checksum_address(BONDING_V5_CONTRACT),
            abi=_TOKEN_GRAD_THRESHOLD_ABI,
        )
        threshold_wei = contract.functions.tokenGradThreshold(
            client.to_checksum_address(token_address)
        ).call()
        threshold = float(threshold_wei) / 1e18
        # threshold == 0 -> jamais enregistré sur cette instance (pas un vrai seuil nul).
        if threshold <= 0:
            return None
        return threshold
    except Exception:
        return None


def onchain_graduation_progress(
    *, pair_address: str | None, token_address: str | None, w3=None
) -> float | None:
    """Progression réelle (0.0-1.0) lue on-chain, ou `None` si indisponible pour CE token
    (couverture partielle honnête -- cf. docstring du module). Gaté par
    `onchain_graduation_enabled()`, jamais appelé sinon. Formule validée empiriquement le
    11/07 sur un vrai token gradué (voir `virtuals.py`)."""
    if not onchain_graduation_enabled():
        return None
    if not pair_address or not token_address:
        return None
    reserve0 = fetch_pair_reserve0(pair_address, w3=w3)
    threshold = fetch_token_grad_threshold(token_address, w3=w3)
    if reserve0 is None or threshold is None:
        return None
    if threshold >= INITIAL_TOKEN_RESERVE:
        # Configuration incohérente (jamais observée en pratique) -- ne pas diviser par
        # zéro ni produire un ratio absurde, dégrader proprement.
        return None
    progress = (INITIAL_TOKEN_RESERVE - reserve0) / (INITIAL_TOKEN_RESERVE - threshold)
    return max(0.0, min(progress, 1.0))
