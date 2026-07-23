"""Real ~$10-15 agent-wallet pilot (Coinbase Agentic Wallet) — executes ALONE,
no Telegram click per transaction. Named exception, explicitly decided by the
operator (16/07) on the open question from `docs/pilote-agent-wallet-10usd.md`
§4: the "hard cap + isolated wallet + swap only, verified after the fact" model
is accepted for THIS precisely-bounded pilot — never a silent exemption from the
absolute rule of human validation on real capital, which remains unchanged
everywhere else (Vanguard ZHC mainnet, any future tier beyond this pilot).

Non-negotiable guardrails (doc §3, all applied here):
  1. Hard cap checked against the wallet's REAL balance before every attempt
     (never a tool's UI setting) -- fail-closed if the balance is unavailable.
  2. No GENERIC transfer/withdrawal capability -- see §9 below for the named
     exception from 16/07 that adds ONE SINGLE authorized transfer.
  3. Slippage ALWAYS forced to `MAX_SLIPPAGE_BPS` (10%), whatever the caller
     supplies -- never an external tool's default value.
  4. Kill-switch `/stop` (`outgoing_pause.is_paused(strict=True)`) checked before
     EVERY attempt -- no parallel mechanism.
  5. Structurally separate from `wallet_guard.py` -- no import, no shared
     state. Same doctrine as `sepolia_autonomous.py`/`bonding_trade_log.py`.
  6. Complete logging via `agent_wallet_log.record_transaction` -- every
     attempt (ok/failed/blocked), never only the successes.
  7. Dedicated gate, OFF by default (`ARIA_AGENT_WALLET_PILOT_ENABLED`) -- separate
     from the existing Sepolia/Arena/wallet_guard flags.
  8. Dedicated, isolated wallet -- this module only knows an address/balance
     supplied by the caller, never the main Vanguard ZHC wallet.

  9. **Named exception #4 (transfer, explicit operator decision, 16/07)**:
     the pilot gains ONE USDC transfer capability, structurally bounded so it
     can never become a generic theft vector:
       - SINGLE destination address, HARD-CODED below
         (`ALLOWED_TRANSFER_ADDRESS`) -- never a free parameter, never read
         from an environment variable that could be changed without code review.
         Any call to another address is blocked and logged.
       - ADDITIONAL, distinct gate (`ARIA_AGENT_WALLET_TRANSFER_ENABLED`),
         OFF by default, ON TOP OF the global pilot gate -- a transfer requires
         BOTH flags active, never just one.
       - Same hard cap `MAX_TRANSACTION_USD`, same real-balance check,
         same kill-switch, same systematic logging as the swap.
       - No withdrawal function to an exchange/CEX address -- only
         this specific wallet, chosen and explicitly communicated by the operator.

No private key here (same doctrine as the rest of the dome): the actual
execution (`swap_fn`/`transfer_fn`) is injected by the caller -- the real CDP
SDK call runs on the VPS/operator side, never in this module nor in a cloud
session.
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
MAX_SLIPPAGE_BPS = 1000  # 10% -- absolute rule, never a tool's default value.

# 20/07 -- direct extraction of a thesis ARIA wrote herself (aria-brain,
# chapter 1): "the most dangerous temptation... is to confuse a simulated result
# with a real one because both look like the same line in a log." Audit
# confirmed: the logs of THIS module (the only path that touches real
# capital) never said "real" anywhere -- indistinguishable from a test module.
# Systematic prefix on EVERY log line of this file, never on
# paper_trader.py (which already has its own "🧪 SIMULATION" marker).
_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] agent-wallet pilot"

# Named exception #4 (16/07) -- the ONLY address a transfer can be attempted to.
# Hard-coded (not an environment variable): any change requires a reviewed
# commit, never a simple `.env` setting changeable without a trace.
#
# CHANGED on 23/07 (explicit operator decision): the old address
# (0x33783cCb570Cb279C25F836806B5c4C3C8309777) was actually a personal Tangem,
# meanwhile reused as owner of the Smart Account
# `aria-smart-st` (cf. docs/HANDOFF_COINBASE_CDP.md) -- the new destination
# is the CDP wallet `aria-wallet-transfert` (formerly "aria-agent-wallet-pilot",
# renamed on 23/07, cf. same HANDOFF), a dedicated wallet distinct from any
# other active wallet in the dome.
ALLOWED_TRANSFER_ADDRESS = "0x584b2B35dac347B2317da0d21b95063de51257Ef"

BalanceFn = Callable[[], Awaitable[float | None]]
SwapFn = Callable[..., Awaitable[dict[str, Any]]]
TransferFn = Callable[..., Awaitable[dict[str, Any]]]


def agent_wallet_transfer_enabled() -> bool:
    """Gate DISTINCT from the global pilot gate -- a transfer requires BOTH active
    (§9, named exception from 16/07). Fail-closed until explicitly set."""
    return os.environ.get("ARIA_AGENT_WALLET_TRANSFER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def agent_wallet_pilot_enabled() -> bool:
    """Dedicated gate, OFF by default -- fail-closed until explicitly set."""
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
    """Attempt a bounded swap. Strict order (doc §5): kill-switch -> cap (real
    balance) -> forced slippage -> execution -> systematic logging.

    ``slippage_bps`` is only accepted to flag a possible discrepancy with the
    value forced in the logs -- it is NEVER passed through as-is to ``swap_fn``.
    """
    if slippage_bps is not None and slippage_bps != MAX_SLIPPAGE_BPS:
        logger.warning(
            "%s -- slippage_bps=%s ignored, forced to %s (absolute rule 09/07)",
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
        logger.error("%s -- swap execution failed: %s", _REAL_MONEY_LOG_PREFIX, exc)
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
        "%s -- swap SUCCEEDED: %s -> %s (%.2f$ -> %.6g), tx=%s",
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
    logger.warning("%s -- swap blocked: %s", _REAL_MONEY_LOG_PREFIX, reason)
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
    """Attempt a bounded USDC transfer (named exception #4, §9). Strict order, same
    doctrine as ``attempt_swap``: dedicated gate -> address allowlist -> kill-switch
    -> cap (real balance) -> execution -> systematic logging.

    ``to_address`` MUST match ``ALLOWED_TRANSFER_ADDRESS`` exactly
    (case-insensitive -- a differently checksummed EVM address is still the
    same address) -- any other value is blocked BEFORE even checking the
    balance or the kill-switch, it's the narrowest and most critical gate.
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
        logger.error("%s -- transfer execution failed: %s", _REAL_MONEY_LOG_PREFIX, exc)
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
        "%s -- transfer SUCCEEDED: %.2f$ -> %s, tx=%s",
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
    logger.warning("%s -- transfer blocked: %s", _REAL_MONEY_LOG_PREFIX, reason)
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
