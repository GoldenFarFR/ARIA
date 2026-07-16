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
  2. Aucune capacité de transfert/retrait générique -- seule l'action `swap` existe
     dans ce module.
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

Aucune clé privée ici (même doctrine que tout le reste du dôme) : l'exécution
réelle (`swap_fn`) est injectée par l'appelant -- le vrai appel au SDK CDP tourne
côté VPS/opérateur, jamais dans ce module ni dans une session cloud.
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

BalanceFn = Callable[[], Awaitable[float | None]]
SwapFn = Callable[..., Awaitable[dict[str, Any]]]


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
            "attempt_swap: slippage_bps=%s ignoré, forcé à %s (règle absolue 09/07)",
            slippage_bps,
            MAX_SLIPPAGE_BPS,
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
    logger.warning("agent_wallet_pilot swap bloqué : %s", reason)
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
