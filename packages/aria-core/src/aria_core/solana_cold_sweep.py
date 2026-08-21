"""Automatic sweep of excess capital to the operator's cold wallet (21/08).

Operator decision: anything above 500$ on the hot Solana wallet leaves for his
Tangem, in batches of at least 5$ so fees never eat the transfer.

This module REDUCES exposure -- that is its whole justification. On 17/08 the
operator had lowered a proposed 500$ hot-wallet ceiling to 200$ after three
warnings (unproven architecture, public strategy parameters, attacker
incentive). 500$ as a SWEEP THRESHOLD is a different thing from 500$ sitting
hot: the first caps how much can ever be at risk, the second raises it. That
distinction is why this exists and why the earlier caution still stands for the
hot ceiling itself.

Doctrine, identical to `agent_wallet_pilot.attempt_transfer`:

  - the destination is a HARD-CODED constant, never a parameter, never an env
    var. A compromised VPS can at worst send the operator his own money;
  - gate dedicated and OFF by default, checked before anything else;
  - both kill-switches consulted on every attempt;
  - the real balance is read fresh and blocks on failure -- never assumed;
  - every attempt is journalled, including refusals.

It never decides to TRADE and it never touches `wallet_guard`. It moves excess
in one direction only, to one address only.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from aria_core import agent_wallet_log, custody_pause, outgoing_pause

logger = logging.getLogger(__name__)

WALLET_PRODUCT = "solana_cold_sweep"

# Operator's Tangem cold wallet. HARD-CODED on purpose: the single most
# important line of this module. If it is ever empty the sweep refuses rather
# than sending anywhere else -- an unset destination must never mean "skip the
# check", which is how funds end up at address zero.
COLD_WALLET_ADDRESS = ""

# Above this, the surplus goes cold. Not a hot-wallet allowance: the hot
# ceiling itself stays governed by the far more conservative pilot caps.
SWEEP_THRESHOLD_USD = 500.0

# Operator, 21/08: "met lot de 5$ minimum pour eviter des milliers de swap".
# Without it, every dollar above the threshold triggers its own transfer and
# Solana fees plus rent-exemption turn the sweep into a slow leak.
MIN_SWEEP_USD = 5.0

_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] solana cold sweep"

BalanceFn = Callable[[], Awaitable[float | None]]
TransferFn = Callable[..., Awaitable[dict]]


@dataclass(frozen=True)
class SweepResult:
    ok: bool
    reason: str = ""
    tx_hash: str = ""
    amount_usd: float = 0.0


def cold_sweep_enabled() -> bool:
    """Fail-closed: only an explicit "true" opens it."""
    return (os.environ.get("ARIA_SOLANA_COLD_SWEEP_ENABLED", "") or "").strip().lower() == "true"


def surplus_above_threshold(balance_usd: float) -> float:
    """How much is sweepable. Never negative, and the threshold itself always
    stays behind -- the sweep empties the excess, never the working float."""
    return max(0.0, balance_usd - SWEEP_THRESHOLD_USD)


async def _blocked(amount_usd: float, *, reason: str) -> SweepResult:
    logger.info("%s -- BLOCKED (%.2f$) : %s", _REAL_MONEY_LOG_PREFIX, amount_usd, reason)
    try:
        await agent_wallet_log.record_transaction(
            wallet_product=WALLET_PRODUCT, chain="solana", action_type="transfer",
            amount_in=amount_usd, status="blocked", reason=reason,
            to_address=COLD_WALLET_ADDRESS,
        )
    except Exception as exc:  # noqa: BLE001 -- a logging failure never hides a refusal
        logger.warning("%s -- could not record refusal: %s", _REAL_MONEY_LOG_PREFIX, exc)
    return SweepResult(ok=False, reason=reason, amount_usd=amount_usd)


async def attempt_sweep(*, balance_fn: BalanceFn, transfer_fn: TransferFn) -> SweepResult:
    """Sweep the surplus if there is one. A no-op is the normal outcome."""
    if not cold_sweep_enabled():
        return await _blocked(0.0, reason="ARIA_SOLANA_COLD_SWEEP_ENABLED désactivé (fail-closed)")

    if not COLD_WALLET_ADDRESS:
        return await _blocked(0.0, reason="COLD_WALLET_ADDRESS non renseignée -- refus par défaut")

    if outgoing_pause.is_paused(strict=True):
        return await _blocked(0.0, reason=outgoing_pause.blocked_notice("Ce balayage vers le froid"))
    if custody_pause.is_paused():
        return await _blocked(0.0, reason=custody_pause.blocked_notice("Ce balayage vers le froid"))

    try:
        balance_usd = await balance_fn()
    except Exception as exc:  # noqa: BLE001
        return await _blocked(0.0, reason=f"solde réel indisponible (fail-closed) : {exc}")
    if balance_usd is None:
        return await _blocked(0.0, reason="solde réel indisponible (fail-closed) : balance_fn a renvoyé None")

    surplus = surplus_above_threshold(balance_usd)
    if surplus <= 0:
        return SweepResult(ok=False, reason=f"solde {balance_usd:.2f}$ sous le seuil {SWEEP_THRESHOLD_USD}$")
    if surplus < MIN_SWEEP_USD:
        return SweepResult(
            ok=False,
            reason=f"excédent {surplus:.2f}$ sous le lot minimum {MIN_SWEEP_USD}$ -- on attend",
            amount_usd=surplus,
        )

    try:
        result = await transfer_fn(to_address=COLD_WALLET_ADDRESS, amount_usd=surplus)
    except Exception as exc:  # noqa: BLE001
        return await _blocked(surplus, reason=f"transfert échoué : {type(exc).__name__}: {exc}")

    tx_hash = str((result or {}).get("tx_hash") or "")
    if not tx_hash:
        return await _blocked(surplus, reason="transfert sans hash -- traité comme un échec")

    logger.info("%s -- SWEPT %.2f$ to cold, tx=%s", _REAL_MONEY_LOG_PREFIX, surplus, tx_hash)
    try:
        await agent_wallet_log.record_transaction(
            wallet_product=WALLET_PRODUCT, chain="solana", action_type="transfer",
            amount_in=surplus, tx_hash=tx_hash, status="ok", to_address=COLD_WALLET_ADDRESS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s -- could not record success: %s", _REAL_MONEY_LOG_PREFIX, exc)
    return SweepResult(ok=True, tx_hash=tx_hash, amount_usd=surplus)
