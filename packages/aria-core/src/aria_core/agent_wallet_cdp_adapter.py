"""Adaptateur Coinbase Developer Platform (CDP) pour `agent_wallet_pilot.py`.

Construit les fonctions injectables `balance_fn`/`swap_fn` attendues par
`agent_wallet_pilot.attempt_swap()`, en utilisant le SDK officiel `cdp-sdk`
(package Python, extra optionnel `aria-core[agent_wallet]`,
https://pypi.org/project/cdp-sdk/).

Identifiants : `CdpClient()` lit automatiquement `CDP_API_KEY_ID`,
`CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET` depuis l'environnement (convention du
SDK) -- ce module ne les lit, ne les stocke et ne les manipule jamais lui-même.
Aucune clé privée ici (même doctrine que tout le dôme) : le SDK CDP garde la
clé privée du wallet côté Coinbase (non-custodial, mais gérée par leur
infrastructure de signature), jamais exposée à ce code.

Réservé à une exécution où les 3 variables sont posées dans un `.env` local
(VPS) -- jamais dans une session cloud. Import du package `cdp` fait à
l'intérieur des fonctions (lazy) pour que le reste du codebase ne casse pas si
l'extra `agent_wallet` n'est pas installé.

**Vérifié contre un vrai appel CDP le 16/07 (VPS Principal, norme #157)** :
`usdc_balance_usd()` a été appelée seule (jamais `execute_swap`) contre le
wallet dédié réel (`aria-agent-wallet-pilot`, adresse Base mainnet publique
`0xF04625162b616c5ad9788811b7be8CDd425B37Ef`) -- `cdp.evm.get_or_create_account`
et `cdp.evm.list_token_balances` répondent sans erreur, `list_token_balances`
renvoie bien un objet Pydantic `ListTokenBalancesResult` avec un attribut
`.balances` (forme exactement celle supposée par `_get(result, "balances")`,
aucune correction nécessaire). Résultat `0.0` confirmé structurellement comme
un wallet réellement vide (`len(entries) == 0`, pas un artefact du repli
`or []` sur une réponse mal formée) -- normal, le pilote #10$ n'a pas encore
été financé. Le chemin `execute_swap` (transaction réelle) reste, lui, non
exercé -- seule la lecture a été vérifiée à ce stade.
"""
from __future__ import annotations

from typing import Any

# USDC natif sur Base mainnet (6 décimales) -- https://docs.base.org/base-chain/data-analytics/token-list
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WALLET_NAME = "aria-agent-wallet-pilot"


def _get(obj: Any, *names: str) -> Any:
    """Lit un attribut ou une clé de dict, quel que soit le format renvoyé par
    le SDK (objet Pydantic ou dict brut selon la version) -- défensif, jamais
    une supposition unique sur la forme exacte de la réponse."""
    for name in names:
        if obj is None:
            return None
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            continue
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


async def usdc_balance_usd(*, network: str = "base") -> float | None:
    """``balance_fn`` injectable -- solde RÉEL en USDC du wallet dédié, traité
    comme un montant en dollars (1 USDC ~= 1$, aucune conversion de prix
    nécessaire). Renvoie ``None`` si indisponible -- ``agent_wallet_pilot``
    traite ça en fail-closed (refuse la transaction plutôt que de deviner)."""
    try:
        from cdp import CdpClient
    except ImportError:
        return None
    try:
        async with CdpClient() as cdp:
            account = await cdp.evm.get_or_create_account(name=WALLET_NAME)
            result = await cdp.evm.list_token_balances(address=account.address, network=network)
    except Exception:
        return None

    entries = _get(result, "balances") or (result if isinstance(result, list) else []) or []
    for entry in entries:
        token = _get(entry, "token")
        address = _get(token, "contract_address", "contractAddress")
        if (address or "").lower() != USDC_BASE_ADDRESS.lower():
            continue
        amount = _get(entry, "amount")
        raw = _get(amount, "amount")
        decimals = _get(amount, "decimals")
        if raw is None:
            return None
        try:
            return float(raw) / (10 ** int(decimals if decimals is not None else 6))
        except (TypeError, ValueError):
            return None
    return 0.0  # USDC jamais trouvé dans les soldes -- wallet vide en USDC, pas une erreur.


async def execute_swap(
    *,
    chain: str,
    token_in: str,
    token_out: str,
    amount_in_usd: float,
    wallet_address: str,
    slippage_bps: int,
) -> dict[str, Any]:
    """``swap_fn`` injectable -- exécute le swap réel. ``slippage_bps`` est
    TOUJOURS celui forcé par `agent_wallet_pilot.attempt_swap` (jamais un
    défaut d'outil, règle absolue 09/07)."""
    from cdp import CdpClient
    from cdp.actions.evm.swap import AccountSwapOptions

    async with CdpClient() as cdp:
        account = await cdp.evm.get_or_create_account(name=WALLET_NAME)
        result = await account.swap(
            AccountSwapOptions(
                network=chain,
                from_token=token_in,
                to_token=token_out,
                from_amount=str(amount_in_usd),
                slippage_bps=slippage_bps,
            )
        )

    tx_hash = _get(result, "transaction_hash", "tx_hash") or ""
    amount_out_raw = _get(result, "to_amount", "amount_out")
    try:
        amount_out = float(amount_out_raw) if amount_out_raw is not None else 0.0
    except (TypeError, ValueError):
        amount_out = 0.0
    return {"tx_hash": str(tx_hash), "amount_out": amount_out}
