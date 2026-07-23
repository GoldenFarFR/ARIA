"""Pilote agent-wallet réel ~10-15$ (Coinbase Agentic Wallet) — exécution SEULE,
sans clic Telegram par transaction. Exception nommée, décidée explicitement par
l'opérateur (16/07) sur la question ouverte de `docs/pilote-agent-wallet-10usd.md`
§4 : le modèle "plafond + wallet isolé + swap uniquement, vérifié après coup" est
accepté pour CE pilote précisément borné — jamais une dérogation silencieuse à la
règle absolue de validation humaine sur le capital réel, qui reste inchangée
partout ailleurs (mainnet Vanguard ZHC, tout futur palier au-delà de ce pilote).

Garde-fous non négociables (doc §3, tous appliqués ici) :
  1. Plafond dur vérifié contre le solde RÉEL du wallet avant chaque tentative
     (jamais un réglage UI de l'outil) -- fail-closed si le solde est indisponible.
  2. Aucune capacité de transfert/retrait GÉNÉRIQUE -- voir §9 ci-dessous pour
     l'exception nommée du 16/07 qui ajoute UN SEUL transfert autorisé.
  3. Slippage TOUJOURS forcé à `MAX_SLIPPAGE_BPS` (10%), quel que soit ce que
     l'appelant fournit -- jamais la valeur par défaut d'un outil externe.
  4. Kill-switch `/stop` (`outgoing_pause.is_paused(strict=True)`) vérifié avant
     CHAQUE tentative -- pas de mécanisme parallèle.
  5. Structurellement séparé de `wallet_guard.py` -- aucun import, aucun partage
     d'état. Même doctrine que `sepolia_autonomous.py`/`bonding_trade_log.py`.
  6. Journalisation complète via `agent_wallet_log.record_transaction` -- chaque
     tentative (ok/failed/blocked), jamais seulement les succès.
  7. Gate dédié, OFF par défaut (`ARIA_AGENT_WALLET_PILOT_ENABLED`) -- séparé des
     flags Sepolia/Arena/wallet_guard existants.
  8. Wallet dédié et isolé -- ce module ne connaît qu'une adresse/un solde fournis
     par l'appelant, jamais le wallet Vanguard ZHC principal.

  9. **Exception nommée #4 (transfert, décision opérateur explicite, 16/07)** :
     le pilote gagne UNE capacité de transfert USDC, structurellement bornée pour
     ne jamais devenir un vecteur de vol générique :
       - Adresse de destination UNIQUE, codée EN DUR ci-dessous
         (`ALLOWED_TRANSFER_ADDRESS`) -- jamais un paramètre libre, jamais lue
         depuis une variable d'environnement modifiable sans revue de code.
         Tout appel vers une autre adresse est bloqué et journalisé.
       - Gate SUPPLÉMENTAIRE et distinct (`ARIA_AGENT_WALLET_TRANSFER_ENABLED`),
         OFF par défaut, EN PLUS du gate pilote global -- un transfert exige les
         DEUX flags actifs, jamais un seul.
       - Même plafond dur `MAX_TRANSACTION_USD`, même vérification de solde réel,
         même kill-switch, même journalisation systématique que le swap.
       - Aucune fonction de retrait vers une adresse d'exchange/CEX -- seulement
         ce wallet précis, choisi et communiqué explicitement par l'opérateur.

Aucune clé privée ici (même doctrine que tout le reste du dôme) : l'exécution
réelle (`swap_fn`/`transfer_fn`) est injectée par l'appelant -- le vrai appel au
SDK CDP tourne côté VPS/opérateur, jamais dans ce module ni dans une session
cloud.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aria_core import agent_wallet_log, outgoing_pause

logger = logging.getLogger(__name__)

WALLET_PRODUCT = "coinbase_agentic_wallet"
MAX_TRANSACTION_USD = 15.0
MAX_SLIPPAGE_BPS = 1000  # 10% -- règle absolue, jamais la valeur par défaut d'un outil.

# 20/07 -- extraction directe de la thèse qu'ARIA a écrite elle-même (aria-brain,
# chapitre 1) : « la tentation la plus dangereuse... c'est de confondre un résultat
# simulé avec un résultat réel parce que les deux ressemblent à la même ligne dans
# un log ». Audit confirmé : les logs de CE module (seul chemin qui touche du vrai
# capital) ne disaient jamais "réel" nulle part -- indiscernables d'un module de
# test. Préfixe systématique sur CHAQUE ligne de log de ce fichier, jamais sur
# paper_trader.py (qui a déjà son propre marqueur "🧪 SIMULATION").
_REAL_MONEY_LOG_PREFIX = "[ARGENT RÉEL] pilote agent-wallet"

# Exception nommée #4 (16/07) -- SEULE adresse vers laquelle un transfert peut être
# tenté. Codée en dur (pas une variable d'environnement) : tout changement exige un
# commit revu, jamais un simple réglage `.env` modifiable sans trace.
#
# CHANGÉE le 23/07 (décision opérateur explicite) : l'ancienne adresse
# (0x33783cCb570Cb279C25F836806B5c4C3C8309777) était en réalité une Tangem
# personnelle, entre-temps réutilisée comme owner du Smart Account
# `aria-smart-st` (cf. docs/HANDOFF_COINBASE_CDP.md) -- la nouvelle destination
# est le wallet CDP `aria-wallet-transfert` (ex-"aria-agent-wallet-pilot",
# renommé le 23/07, cf. même HANDOFF), un wallet dédié distinct de tout autre
# wallet actif du dôme.
ALLOWED_TRANSFER_ADDRESS = "0x584b2B35dac347B2317da0d21b95063de51257Ef"

BalanceFn = Callable[[], Awaitable[float | None]]
SwapFn = Callable[..., Awaitable[dict[str, Any]]]
TransferFn = Callable[..., Awaitable[dict[str, Any]]]


def agent_wallet_transfer_enabled() -> bool:
    """Gate DISTINCT du gate pilote global -- un transfert exige les DEUX actifs
    (§9, exception nommée du 16/07). Fail-closed tant que non posé explicitement."""
    return os.environ.get("ARIA_AGENT_WALLET_TRANSFER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def agent_wallet_pilot_enabled() -> bool:
    """Gate dédié, OFF par défaut -- fail-closed tant que non posé explicitement."""
    return os.environ.get("ARIA_AGENT_WALLET_PILOT_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


@dataclass(frozen=True)
class SwapAttemptResult:
    status: str  # "ok" | "blocked" | "failed"
    reason: str = ""
    tx_hash: str = ""
    amount_out: float = 0.0


@dataclass(frozen=True)
class TransferAttemptResult:
    status: str  # "ok" | "blocked" | "failed"
    reason: str = ""
    tx_hash: str = ""


async def attempt_swap(
    *,
    chain: str,
    token_in: str,
    token_out: str,
    amount_in_usd: float,
    wallet_address: str,
    balance_fn: BalanceFn,
    swap_fn: SwapFn,
    slippage_bps: int | None = None,
) -> SwapAttemptResult:
    """Tente un swap borné. Ordre strict (doc §5) : kill-switch -> plafond (solde
    réel) -> slippage forcé -> exécution -> journalisation systématique.

    ``slippage_bps`` n'est accepté que pour signaler un éventuel écart avec la
    valeur forcée dans les logs -- il n'est JAMAIS transmis tel quel à ``swap_fn``.
    """
    if slippage_bps is not None and slippage_bps != MAX_SLIPPAGE_BPS:
        logger.warning(
            "%s -- slippage_bps=%s ignoré, forcé à %s (règle absolue 09/07)",
            _REAL_MONEY_LOG_PREFIX, slippage_bps, MAX_SLIPPAGE_BPS,
        )

    if not agent_wallet_pilot_enabled():
        return await _blocked(
            chain, token_in, token_out, amount_in_usd,
            reason="ARIA_AGENT_WALLET_PILOT_ENABLED désactivé (fail-closed par défaut)",
        )

    if outgoing_pause.is_paused(strict=True):
        return await _blocked(
            chain, token_in, token_out, amount_in_usd,
            reason=outgoing_pause.blocked_notice("Ce swap agent-wallet"),
        )

    if amount_in_usd <= 0:
        return await _blocked(
            chain, token_in, token_out, amount_in_usd,
            reason="montant nul ou négatif",
        )

    try:
        balance_usd = await balance_fn()
    except Exception as exc:
        return await _blocked(
            chain, token_in, token_out, amount_in_usd,
            reason=f"solde réel indisponible (fail-closed) : {exc}",
        )
    if balance_usd is None:
        return await _blocked(
            chain, token_in, token_out, amount_in_usd,
            reason="solde réel indisponible (fail-closed) : balance_fn a renvoyé None",
        )

    if amount_in_usd > MAX_TRANSACTION_USD:
        return await _blocked(
            chain, token_in, token_out, amount_in_usd,
            reason=f"montant {amount_in_usd}$ > plafond dur {MAX_TRANSACTION_USD}$",
        )
    if amount_in_usd > balance_usd:
        return await _blocked(
            chain, token_in, token_out, amount_in_usd,
            reason=f"montant {amount_in_usd}$ > solde réel {balance_usd}$",
        )

    try:
        result = await swap_fn(
            chain=chain,
            token_in=token_in,
            token_out=token_out,
            amount_in_usd=amount_in_usd,
            wallet_address=wallet_address,
            slippage_bps=MAX_SLIPPAGE_BPS,
        )
    except Exception as exc:
        logger.error("%s -- échec d'exécution du swap : %s", _REAL_MONEY_LOG_PREFIX, exc)
        await agent_wallet_log.record_transaction(
            wallet_product=WALLET_PRODUCT,
            chain=chain,
            action_type="swap",
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in_usd,
            slippage_bps=MAX_SLIPPAGE_BPS,
            status="failed",
            reason=str(exc),
        )
        return SwapAttemptResult(status="failed", reason=str(exc))

    tx_hash = str(result.get("tx_hash") or "")
    amount_out = float(result.get("amount_out") or 0.0)
    logger.info(
        "%s -- swap RÉUSSI : %s -> %s (%.2f$ -> %.6g), tx=%s",
        _REAL_MONEY_LOG_PREFIX, token_in, token_out, amount_in_usd, amount_out, tx_hash,
    )
    await agent_wallet_log.record_transaction(
        wallet_product=WALLET_PRODUCT,
        chain=chain,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in_usd,
        amount_out=amount_out,
        slippage_bps=MAX_SLIPPAGE_BPS,
        tx_hash=tx_hash,
        status="ok",
    )
    return SwapAttemptResult(status="ok", tx_hash=tx_hash, amount_out=amount_out)


async def _blocked(
    chain: str, token_in: str, token_out: str, amount_in_usd: float, *, reason: str
) -> SwapAttemptResult:
    logger.warning("%s -- swap bloqué : %s", _REAL_MONEY_LOG_PREFIX, reason)
    await agent_wallet_log.record_transaction(
        wallet_product=WALLET_PRODUCT,
        chain=chain,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in_usd,
        slippage_bps=MAX_SLIPPAGE_BPS,
        status="blocked",
        reason=reason,
    )
    return SwapAttemptResult(status="blocked", reason=reason)


async def attempt_transfer(
    *,
    chain: str,
    to_address: str,
    amount_usd: float,
    balance_fn: BalanceFn,
    transfer_fn: TransferFn,
) -> TransferAttemptResult:
    """Tente un transfert USDC borné (exception nommée #4, §9). Ordre strict, même
    doctrine que ``attempt_swap`` : gate dédié -> allowlist d'adresse -> kill-switch
    -> plafond (solde réel) -> exécution -> journalisation systématique.

    ``to_address`` DOIT correspondre exactement à ``ALLOWED_TRANSFER_ADDRESS``
    (insensible à la casse -- une adresse EVM checksummée différemment reste la
    même adresse) -- toute autre valeur est bloquée AVANT même de vérifier le
    solde ou le kill-switch, c'est la porte la plus étroite et la plus critique.
    """
    if not agent_wallet_transfer_enabled():
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason="ARIA_AGENT_WALLET_TRANSFER_ENABLED désactivé (fail-closed par défaut)",
        )

    if not agent_wallet_pilot_enabled():
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason="ARIA_AGENT_WALLET_PILOT_ENABLED désactivé (fail-closed par défaut)",
        )

    if (to_address or "").strip().lower() != ALLOWED_TRANSFER_ADDRESS.lower():
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason=(
                f"adresse de destination {to_address!r} hors allowlist -- "
                f"seule {ALLOWED_TRANSFER_ADDRESS} est autorisée"
            ),
        )

    if outgoing_pause.is_paused(strict=True):
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason=outgoing_pause.blocked_notice("Ce transfert agent-wallet"),
        )

    if amount_usd <= 0:
        return await _blocked_transfer(
            chain, to_address, amount_usd, reason="montant nul ou négatif",
        )

    try:
        balance_usd = await balance_fn()
    except Exception as exc:
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason=f"solde réel indisponible (fail-closed) : {exc}",
        )
    if balance_usd is None:
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason="solde réel indisponible (fail-closed) : balance_fn a renvoyé None",
        )

    if amount_usd > MAX_TRANSACTION_USD:
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason=f"montant {amount_usd}$ > plafond dur {MAX_TRANSACTION_USD}$",
        )
    if amount_usd > balance_usd:
        return await _blocked_transfer(
            chain, to_address, amount_usd,
            reason=f"montant {amount_usd}$ > solde réel {balance_usd}$",
        )

    try:
        result = await transfer_fn(
            chain=chain, to_address=ALLOWED_TRANSFER_ADDRESS, amount_usd=amount_usd,
        )
    except Exception as exc:
        logger.error("%s -- échec d'exécution du transfert : %s", _REAL_MONEY_LOG_PREFIX, exc)
        await agent_wallet_log.record_transaction(
            wallet_product=WALLET_PRODUCT,
            chain=chain,
            action_type="transfer",
            amount_in=amount_usd,
            to_address=ALLOWED_TRANSFER_ADDRESS,
            status="failed",
            reason=str(exc),
        )
        return TransferAttemptResult(status="failed", reason=str(exc))

    tx_hash = str(result.get("tx_hash") or "")
    logger.info(
        "%s -- transfert RÉUSSI : %.2f$ -> %s, tx=%s",
        _REAL_MONEY_LOG_PREFIX, amount_usd, ALLOWED_TRANSFER_ADDRESS, tx_hash,
    )
    await agent_wallet_log.record_transaction(
        wallet_product=WALLET_PRODUCT,
        chain=chain,
        action_type="transfer",
        amount_in=amount_usd,
        tx_hash=tx_hash,
        to_address=ALLOWED_TRANSFER_ADDRESS,
        status="ok",
    )
    return TransferAttemptResult(status="ok", tx_hash=tx_hash)


async def _blocked_transfer(
    chain: str, to_address: str, amount_usd: float, *, reason: str
) -> TransferAttemptResult:
    logger.warning("%s -- transfert bloqué : %s", _REAL_MONEY_LOG_PREFIX, reason)
    await agent_wallet_log.record_transaction(
        wallet_product=WALLET_PRODUCT,
        chain=chain,
        action_type="transfer",
        amount_in=amount_usd,
        to_address=to_address,
        status="blocked",
        reason=reason,
    )
    return TransferAttemptResult(status="blocked", reason=reason)
