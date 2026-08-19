"""ARIA's homemade agent wallet — guardrail wrapper (18/08). Mirrors
``agent_wallet_pilot.py``'s doctrine (dedicated gate, kill-switch, systematic
logging, structural isolation from ``wallet_guard.py``) so both real-capital-
adjacent wallets in this repo are audited the same way — even though their
PRIMARY guarantee is different:

- ``agent_wallet_pilot.py`` (Coinbase CDP): the $ cap enforced in Python IS
  the only guardrail — CDP itself has no on-chain spending limit.
- This module (Safe{Wallet}+AllowanceModule on Robinhood Chain / Squads v4
  SpendingLimit on Solana): the cap is ALREADY enforced ON-CHAIN by the smart
  contract itself, proven live on both legs (18/08 HANDOFF entries — a
  request past the on-chain cap reverts even with a perfect signature). What
  lives here is DEFENSE IN DEPTH plus the same
  gate/kill-switch/logging/isolation doctrine as the rest of the dome —
  never the sole safety net for this specific wallet.

Chain-specific execution (EIP-712 signing + real send for the EVM leg,
Anchor instruction signing + real send for the Solana leg) is injected via
``send_fn``, exactly like ``agent_wallet_pilot.py``'s own ``swap_fn``/
``transfer_fn`` pattern — this module never touches a private key itself.
Wired today against ``onchain.safe_robinhood_signer.send_allowance_transfer``
(EVM leg, only ever reaches testnet — chain-id preflight there raises on
anything else) and ``onchain.squads_solana_signer.send_spending_limit_
transfer`` (Solana leg, devnet only — the RPC URL defaults to devnet and is
never pointed at mainnet by this module, 19/08). Both legs share this exact
same wrapper, distinguished at call time via ``wallet_product`` (defaults to
``WALLET_PRODUCT`` for the EVM leg; pass ``WALLET_PRODUCT_SOLANA`` for the
Solana leg) so ``agent_wallet_log`` rows can be told apart per chain without
a second copy of the gate/kill-switch/cap/logging logic.

Gate OFF by default, no production/heartbeat caller wired anywhere — moving
past testnet/devnet needs its own explicit, separate operator decision
(mainnet activation, plus the AllowanceModule v0.1.1 audit-gap point still
open in ``docs/HANDOFF_AGENT_WALLET.md``) before this module means anything
for real capital.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aria_core import agent_wallet_log, custody_pause, outgoing_pause

logger = logging.getLogger(__name__)

WALLET_PRODUCT = "homemade_agent_wallet"
# Distinct `wallet_product` value for the Solana (Squads v4) leg, 19/08 —
# same wrapper/doctrine as the EVM leg, only the logged product name differs
# so `agent_wallet_log` rows can be told apart per chain.
WALLET_PRODUCT_SOLANA = "homemade_agent_wallet_solana"

# Native-unit cap (wei on the EVM/Robinhood leg, lamports on the Solana leg).
# DELIBERATELY the same order of magnitude as what has actually been proven
# on real testnet/devnet infrastructure so far (0.003 ETH / 0.003 SOL
# on-chain SpendingLimit caps, 18/08 HANDOFF entries) — NOT the 200$ target
# the operator set for AFTER the architecture is proven end-to-end
# (docs/HANDOFF_AGENT_WALLET.md, memory project_agent_wallet_cap_200usd:
# "the number is a one-line change when the day comes, what takes time is
# the proof"). Raising this toward that target is a distinct, explicit
# future operator step — never something to bump casually while wiring the
# plumbing that will eventually enforce it.
MAX_TRANSACTION_NATIVE_UNITS = 3_000_000_000_000_000

_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] homemade agent wallet"

RemainingFn = Callable[[], Awaitable[int | None]]
SendFn = Callable[..., Awaitable[dict[str, Any]]]


def homemade_agent_wallet_enabled() -> bool:
    """Dedicated gate, OFF by default — fail-closed until explicitly set.
    Distinct from ``ARIA_AGENT_WALLET_PILOT_ENABLED`` (the CDP pilot)."""
    return os.environ.get("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


@dataclass(frozen=True)
class TransferAttemptResult:
    status: str  # "ok" | "blocked" | "failed"
    reason: str = ""
    tx_hash: str = ""


async def attempt_transfer(
    *,
    chain: str,
    amount: int,
    remaining_fn: RemainingFn,
    send_fn: SendFn,
    wallet_product: str = WALLET_PRODUCT,
    **send_kwargs: Any,
) -> TransferAttemptResult:
    """Attempt a bounded, on-chain-limited transfer. Order mirrors
    ``agent_wallet_pilot.attempt_transfer``: gate -> kill-switch -> app-level
    cap (defense in depth; the real cap is the on-chain contract itself) ->
    fresh real-remaining check -> execution -> systematic logging.

    ``remaining_fn`` must return the REAL remaining allowance/spending-limit
    read fresh from-chain right now (e.g.
    ``onchain.safe_robinhood_wallet.read_allowance(...)["remaining"]`` on the
    EVM leg, ``onchain.squads_solana_signer.read_spending_limit(...)
    ["remaining_amount"]`` on the Solana leg) — never a cached or assumed
    figure, same doctrine applied everywhere else in the dome before a real
    spend.

    ``wallet_product`` (19/08) distinguishes the ``agent_wallet_log`` rows
    per chain — defaults to ``WALLET_PRODUCT`` (EVM); pass
    ``WALLET_PRODUCT_SOLANA`` for the Solana leg. Same gate/kill-switch/cap/
    logging doctrine either way, only the logged product name differs.

    ``send_kwargs`` are passed through verbatim to ``send_fn`` (chain-specific
    — e.g. ``safe``/``token``/``to``/``delegate_key_path`` for the EVM leg,
    ``multisig_create_key``/``spending_limit_create_key``/``vault_index``/
    ``destination``/``delegate_key_path`` for the Solana leg).
    """
    if not homemade_agent_wallet_enabled():
        return await _blocked(
            chain, amount, wallet_product=wallet_product,
            reason="ARIA_HOMEMADE_AGENT_WALLET_ENABLED désactivé (fail-closed par défaut)",
        )

    if outgoing_pause.is_paused(strict=True):
        return await _blocked(
            chain, amount, wallet_product=wallet_product,
            reason=outgoing_pause.blocked_notice("Ce transfert wallet maison"),
        )
    if custody_pause.is_paused():
        return await _blocked(
            chain, amount, wallet_product=wallet_product,
            reason=custody_pause.blocked_notice("Ce transfert wallet maison"),
        )

    if amount <= 0:
        return await _blocked(chain, amount, wallet_product=wallet_product, reason="montant nul ou négatif")

    if amount > MAX_TRANSACTION_NATIVE_UNITS:
        return await _blocked(
            chain, amount, wallet_product=wallet_product,
            reason=f"montant {amount} > plafond dur applicatif {MAX_TRANSACTION_NATIVE_UNITS}",
        )

    try:
        remaining = await remaining_fn()
    except Exception as exc:
        return await _blocked(
            chain, amount, wallet_product=wallet_product,
            reason=f"allowance réelle indisponible (fail-closed) : {exc}",
        )
    if remaining is None or amount > remaining:
        return await _blocked(
            chain, amount, wallet_product=wallet_product,
            reason=f"montant {amount} > allowance restante réelle {remaining}",
        )

    try:
        result = await send_fn(amount=amount, **send_kwargs)
    except Exception as exc:
        logger.error("%s -- send execution failed: %s", _REAL_MONEY_LOG_PREFIX, exc)
        await agent_wallet_log.record_transaction(
            wallet_product=wallet_product, chain=chain, action_type="transfer",
            amount_in=float(amount), status="failed", reason=str(exc),
        )
        return TransferAttemptResult(status="failed", reason=str(exc))

    if result.get("error"):
        reason = str(result["error"])
        logger.warning("%s -- send_fn reported an error: %s", _REAL_MONEY_LOG_PREFIX, reason)
        await agent_wallet_log.record_transaction(
            wallet_product=wallet_product, chain=chain, action_type="transfer",
            amount_in=float(amount), status="failed", reason=reason,
        )
        return TransferAttemptResult(status="failed", reason=reason)

    tx_hash = str(result.get("tx_hash") or "")
    onchain_status = str(result.get("status") or "")
    if onchain_status == "reverted":
        reason = "transaction envoyée mais REVERTED on-chain (le contrat a rejeté -- cf. tx_hash)"
        logger.warning("%s -- %s: tx=%s", _REAL_MONEY_LOG_PREFIX, reason, tx_hash)
        await agent_wallet_log.record_transaction(
            wallet_product=wallet_product, chain=chain, action_type="transfer",
            amount_in=float(amount), tx_hash=tx_hash, status="failed", reason=reason,
        )
        return TransferAttemptResult(status="failed", reason=reason, tx_hash=tx_hash)

    logger.info(
        "%s -- transfer SUCCEEDED: %s (%s), tx=%s",
        _REAL_MONEY_LOG_PREFIX, amount, chain, tx_hash,
    )
    await agent_wallet_log.record_transaction(
        wallet_product=wallet_product, chain=chain, action_type="transfer",
        amount_in=float(amount), tx_hash=tx_hash, status="ok",
    )
    return TransferAttemptResult(status="ok", tx_hash=tx_hash)


async def _blocked(chain: str, amount: int, *, wallet_product: str, reason: str) -> TransferAttemptResult:
    logger.warning("%s -- transfer blocked: %s", _REAL_MONEY_LOG_PREFIX, reason)
    await agent_wallet_log.record_transaction(
        wallet_product=wallet_product, chain=chain, action_type="transfer",
        amount_in=float(amount), status="blocked", reason=reason,
    )
    return TransferAttemptResult(status="blocked", reason=reason)
