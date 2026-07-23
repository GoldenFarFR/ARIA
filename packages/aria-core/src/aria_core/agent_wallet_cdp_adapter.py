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

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Same marker as agent_wallet_pilot.py's _REAL_MONEY_LOG_PREFIX (not imported
# directly -- this module stays a thin CDP adapter, no dependency on the
# pilot's own internals) so a log-grep for real-money events catches this too.
_REAL_MONEY_LOG_PREFIX = "[ARGENT REEL] adaptateur CDP"

# USDC natif sur Base mainnet (6 décimales) -- https://docs.base.org/base-chain/data-analytics/token-list
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# 22/07 -- corrige un vrai incident capital reel : lors d'une regeneration de la
# CDP API key (21/07, following the allowed-IP fix), get_or_create_account no
# longer found the historical account under this name and created a SECOND,
# empty one (0x584b2B35...), distinct from the one holding the real balance
# (0xF04625162b616c5ad9788811b7be8CDd425B37Ef). Verified live on the CDP
# dashboard (22/07): this real-balance address carried the label "aria-wallet"
# at the time, confirming get_or_create_account(name="aria-wallet") resolved
# exactly to it, without creating a 3rd account.
#
# RENAMED 23/07 (operator decision, direct CDP dashboard/SDK action, part of the
# Smart Account migration -- see docs/HANDOFF_COINBASE_CDP.md): this same
# real-balance address went through TWO renames the same day -- first
# "aria-wallet" -> "aria-wallet-X402" (repurposed as the x402-seller-adjacent
# wallet, and as the owner/signer of the new `aria-smart-wallet-one` Smart
# Account), then "aria-wallet-X402" -> "aria-wallet-X402-EVM" (operator's "-EVM"
# naming convention across all 4 active wallets, same day). WALLET_NAME must be
# updated to match EVERY TIME this address is renamed, AND the running
# container redeployed immediately after -- a source-only edit does nothing for
# an already-running process (confirmed the hard way: the first rename above
# already triggered exactly this failure once today before the fix was
# deployed). The formerly-orphaned second account (0x584b2B35..., previously
# labeled "aria-agent-wallet-pilot", never used by any code path per the note
# above) was renamed the same day to "aria-wallet-transfert", then
# "aria-wallet-transfert-EVM" -- still unreferenced by this constant.
WALLET_NAME = "aria-wallet-X402-EVM"


async def _get_wallet_account(cdp: Any) -> Any:
    """Fetch the account under ``WALLET_NAME`` -- NEVER auto-creates, unlike
    ``cdp.evm.get_or_create_account``. For a real-money wallet, a missing name
    means a stale ``WALLET_NAME``/CDP-dashboard rename mismatch (exactly the
    21/07 and 23/07 incidents documented above), never a legitimate first-time
    setup -- this pilot has run against a real funded wallet since 16/07.
    Logs a CRITICAL real-money-marked line and fails closed (raises) instead
    of silently creating and operating on a brand-new empty wallet."""
    from cdp.openapi_client.errors import ApiError

    try:
        return await cdp.evm.get_account(name=WALLET_NAME)
    except ApiError as exc:
        if exc.http_code == 404:
            logger.critical(
                "%s -- WALLET_NAME=%r introuvable sur CDP (ni get_account, ni ce "
                "que get_or_create_account aurait silencieusement recree) -- "
                "verifier immediatement le dashboard CDP et corriger WALLET_NAME "
                "puis REDEPLOYER avant tout nouveau cycle.",
                _REAL_MONEY_LOG_PREFIX, WALLET_NAME,
            )
            raise RuntimeError(
                f"CDP account {WALLET_NAME!r} not found -- refusing to auto-create "
                "a new empty wallet (same failure mode as 21/07 and 23/07)"
            ) from exc
        raise


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


async def _fetch_raw_balance_entries(*, network: str) -> list[Any] | None:
    """Un seul appel CDP partagé (``list_token_balances``) -- réutilisé par
    ``usdc_balance_usd`` (filtre USDC) et ``list_all_token_balances`` (tout).
    Renvoie ``None`` si le SDK est absent ou l'appel échoue (fail-closed,
    jamais une liste vide déguisée en "aucun token détenu")."""
    try:
        from cdp import CdpClient
    except ImportError:
        return None
    try:
        async with CdpClient() as cdp:
            account = await _get_wallet_account(cdp)
            result = await cdp.evm.list_token_balances(address=account.address, network=network)
    except Exception:
        return None
    return _get(result, "balances") or (result if isinstance(result, list) else []) or []


def _parse_balance_entry(entry: Any) -> dict[str, Any] | None:
    """Extrait ``{address, symbol, amount}`` d'une entrée brute CDP -- ``None``
    si le montant n'est pas exploitable (jamais un 0 inventé sur une donnée
    illisible)."""
    token = _get(entry, "token")
    address = _get(token, "contract_address", "contractAddress") or ""
    symbol = _get(token, "symbol") or "?"
    amount = _get(entry, "amount")
    raw = _get(amount, "amount")
    decimals = _get(amount, "decimals")
    if raw is None:
        return None
    try:
        value = float(raw) / (10 ** int(decimals if decimals is not None else 18))
    except (TypeError, ValueError):
        return None
    return {"address": address, "symbol": symbol, "amount": value}


async def usdc_balance_usd(*, network: str = "base") -> float | None:
    """``balance_fn`` injectable -- solde RÉEL en USDC du wallet dédié, traité
    comme un montant en dollars (1 USDC ~= 1$, aucune conversion de prix
    nécessaire). Renvoie ``None`` si indisponible -- ``agent_wallet_pilot``
    traite ça en fail-closed (refuse la transaction plutôt que de deviner)."""
    entries = await _fetch_raw_balance_entries(network=network)
    if entries is None:
        return None
    for entry in entries:
        parsed = _parse_balance_entry(entry)
        if parsed is None:
            continue
        if parsed["address"].lower() != USDC_BASE_ADDRESS.lower():
            continue
        return parsed["amount"]
    return 0.0  # USDC jamais trouvé dans les soldes -- wallet vide en USDC, pas une erreur.


async def list_all_token_balances(*, network: str = "base") -> list[dict[str, Any]] | None:
    """Tous les tokens réellement détenus par le wallet (#204 suite, demande
    opérateur 16/07 : "je veux tous voir meme les futurs token achetés") --
    généralise ``usdc_balance_usd`` au lieu de le dupliquer (même appel CDP
    partagé). Chaque entrée : ``{"address", "symbol", "amount"}``. ``None`` si
    indisponible (SDK absent/appel échoué), ``[]`` si le wallet est
    réellement vide -- jamais confondu."""
    entries = await _fetch_raw_balance_entries(network=network)
    if entries is None:
        return None
    parsed = [_parse_balance_entry(e) for e in entries]
    return [p for p in parsed if p is not None]


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
    défaut d'outil, règle absolue 09/07).

    Bug réel corrigé le 17/07 (trouvé par Secondaire en vérifiant AVANT de coder,
    jamais exercé contre un vrai appel jusqu'ici -- ce chemin reste non exercé en
    réel, seule la lecture (`usdc_balance_usd`) l'a été le 16/07) : `from_amount`
    attend un montant en UNITÉS ATOMIQUES (confirmé dans le SDK installé,
    `cdp/actions/evm/swap/types.py::AccountSwapOptions.from_amount`, "Amount to
    swap in smallest units") -- passer `str(amount_in_usd)` (ex. "10.5") aurait
    fait échouer ou mal-interpréter CHAQUE swap réel dès le premier essai.
    Corrigé avec `cdp.parse_units`, même patron que `transfer_usdc` ci-dessus.
    Hypothèse assumée (documentée, pas cachée) : `amount_in_usd` est une quantité
    d'USDC (6 décimales) -- cohérent avec la doctrine du plan (ETH natif comme
    `token_in` explicitement rejeté pour cette première version, aucun autre
    `token_in` que USDC n'est envisagé)."""
    from cdp import CdpClient, parse_units
    from cdp.actions.evm.swap import AccountSwapOptions

    async with CdpClient() as cdp:
        account = await _get_wallet_account(cdp)
        result = await account.swap(
            AccountSwapOptions(
                network=chain,
                from_token=token_in,
                to_token=token_out,
                from_amount=parse_units(str(amount_in_usd), 6),
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


async def transfer_usdc(*, chain: str, to_address: str, amount_usd: float) -> dict[str, Any]:
    """``transfer_fn`` injectable pour `agent_wallet_pilot.attempt_transfer`
    (exception nommée #4, 16/07) -- transfère de l'USDC réel vers ``to_address``.

    API vérifiée dans la doc officielle CDP SDK avant d'écrire cette fonction
    (jamais devinée) : ``account.transfer(to=, amount=, token="usdc", network=)``,
    montant en unités atomiques via ``cdp.parse_units(str(montant), 6)`` (USDC =
    6 décimales) -- https://docs.cdp.coinbase.com/server-wallets/v2/using-the-wallet-api/transfers.

    ``to_address`` n'est JAMAIS un paramètre libre côté appelant réel : c'est
    `agent_wallet_pilot.ALLOWED_TRANSFER_ADDRESS` (allowlist codée en dur, vérifiée
    AVANT que cette fonction ne soit jamais appelée) -- ce module ne revérifie pas
    l'allowlist lui-même, il exécute ce qu'on lui donne, la garde est en amont."""
    from cdp import CdpClient, parse_units

    async with CdpClient() as cdp:
        account = await _get_wallet_account(cdp)
        tx_hash = await account.transfer(
            to=to_address,
            amount=parse_units(str(amount_usd), 6),
            token="usdc",
            network=chain,
        )
    return {"tx_hash": str(tx_hash)}
