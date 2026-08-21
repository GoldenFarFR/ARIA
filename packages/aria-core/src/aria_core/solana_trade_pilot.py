"""Bounded REAL-capital swaps on Solana for the late-bonding pocket (21/08).

Deliberately a SEPARATE module from `agent_wallet_pilot.py`, not an extension
of it. That one governs the Coinbase agentic wallet on Base/EVM with its own
10-25$ ceiling and its own hard-coded transfer address; mixing a second chain,
a second key and a second cap into it would make a single file responsible for
two independent pots of real money. The doctrine is shared, the state is not.

Same strict order as its sibling, and the order itself is the guarantee:

    gate -> kill-switch -> amount -> REAL balance -> cap -> execution -> log

Every step fails CLOSED. A balance that cannot be read blocks the swap; it
never defaults to "probably fine". Nothing here is skippable by a parameter:
there is no `force`, no `dry_run=False` shortcut, no way for a caller to pass
its own cap.

What this module does NOT do, on purpose:
  - it never chooses WHAT to buy (the pocket decides, this only bounds it);
  - it never touches `wallet_guard`, which knows nothing of Solana as of today
    and is a protected file requiring explicit operator approval to change;
  - it holds no key. `swap_fn` is injected, so the signing path
    (`onchain/jupiter_swap_signer.py`) stays independently auditable and this
    module stays testable without ever touching mainnet.

Ships with its gate OFF. Turning it on is an operator decision, and funding
the wallet is a separate one.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from aria_core import agent_wallet_log, custody_pause, outgoing_pause

logger = logging.getLogger(__name__)

WALLET_PRODUCT = "solana_late_bonding_wallet"

# Operator's figure for the first live tests (21/08): "je ne compte pas trader
# plus de 0.1$ par trade". Hard ceiling, checked against the REAL balance on
# every attempt -- never a default a caller can raise.
MAX_TRADE_USD = 0.10

# Absolute project rule (09/07, "grave le dans la roche"): never above 10%,
# always explicit, never a tool's default value.
MAX_SLIPPAGE_BPS = 1000

# Below this the swap is refused outright: Solana charges rent-exemption on a
# new token account (~0.0020 SOL) plus fees, so a trade too small is a
# guaranteed loss whatever the price does.
MIN_TRADE_USD = 0.02

_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] solana late-bonding pilot"

BalanceFn = Callable[[], Awaitable[float | None]]
SwapFn = Callable[..., Awaitable[dict]]


@dataclass(frozen=True)
class SwapAttemptResult:
    ok: bool
    reason: str = ""
    tx_hash: str = ""
    amount_in_usd: float = 0.0
    entry_price: float | None = None


def solana_trade_pilot_enabled() -> bool:
    """Fail-closed: anything other than an explicit "true" keeps it off."""
    return (os.environ.get("ARIA_SOLANA_TRADE_PILOT_ENABLED", "") or "").strip().lower() == "true"


async def _blocked(mint: str, amount_in_usd: float, *, reason: str) -> SwapAttemptResult:
    """Refuses AND records. A refusal that leaves no trace is indistinguishable
    from a swap that never happened, which is exactly what an audit needs to
    tell apart."""
    logger.info("%s -- BLOCKED %s (%.4f$) : %s", _REAL_MONEY_LOG_PREFIX, mint, amount_in_usd, reason)
    try:
        await agent_wallet_log.record_transaction(
            wallet_product=WALLET_PRODUCT, chain="solana", action_type="swap",
            token_in="USDC", token_out=mint, amount_in=amount_in_usd,
            slippage_bps=MAX_SLIPPAGE_BPS, status="blocked", reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 -- logging must never mask the refusal
        logger.warning("%s -- could not record refusal: %s", _REAL_MONEY_LOG_PREFIX, exc)
    return SwapAttemptResult(ok=False, reason=reason, amount_in_usd=amount_in_usd)


async def attempt_buy(
    *,
    mint: str,
    amount_in_usd: float,
    balance_fn: BalanceFn,
    swap_fn: SwapFn,
    quoted_price: float | None = None,
) -> SwapAttemptResult:
    """One bounded real buy. Returns a refusal rather than raising: a blocked
    swap is a normal outcome here, not an error."""
    if not solana_trade_pilot_enabled():
        return await _blocked(mint, amount_in_usd,
                              reason="ARIA_SOLANA_TRADE_PILOT_ENABLED désactivé (fail-closed)")

    # Both switches, either one blocks: the manual /stop and the custody
    # auto-arm. Checked BEFORE the balance call so a paused system never even
    # queries the wallet.
    if outgoing_pause.is_paused(strict=True):
        return await _blocked(mint, amount_in_usd,
                              reason=outgoing_pause.blocked_notice("Cet achat Solana"))
    if custody_pause.is_paused():
        return await _blocked(mint, amount_in_usd,
                              reason=custody_pause.blocked_notice("Cet achat Solana"))

    if amount_in_usd <= 0:
        return await _blocked(mint, amount_in_usd, reason="montant nul ou négatif")
    if amount_in_usd < MIN_TRADE_USD:
        return await _blocked(
            mint, amount_in_usd,
            reason=f"montant {amount_in_usd:.4f}$ sous le plancher {MIN_TRADE_USD}$ "
                   "(rent-exemption + frais rendraient la perte certaine)")
    if amount_in_usd > MAX_TRADE_USD:
        return await _blocked(
            mint, amount_in_usd,
            reason=f"montant {amount_in_usd:.4f}$ au-dessus du plafond dur {MAX_TRADE_USD}$")

    try:
        balance_usd = await balance_fn()
    except Exception as exc:  # noqa: BLE001 -- unreadable balance blocks, never assumes
        return await _blocked(mint, amount_in_usd,
                              reason=f"solde réel indisponible (fail-closed) : {exc}")
    if balance_usd is None:
        return await _blocked(mint, amount_in_usd,
                              reason="solde réel indisponible (fail-closed) : balance_fn a renvoyé None")
    if amount_in_usd > balance_usd:
        return await _blocked(
            mint, amount_in_usd,
            reason=f"montant {amount_in_usd:.4f}$ supérieur au solde réel {balance_usd:.4f}$")

    try:
        result = await swap_fn(
            mint=mint, amount_in_usd=amount_in_usd, slippage_bps=MAX_SLIPPAGE_BPS,
        )
    except Exception as exc:  # noqa: BLE001
        return await _blocked(mint, amount_in_usd, reason=f"swap échoué : {type(exc).__name__}: {exc}")

    tx_hash = str((result or {}).get("tx_hash") or "")
    entry_price = (result or {}).get("entry_price")
    if not tx_hash:
        return await _blocked(mint, amount_in_usd,
                              reason="swap sans hash de transaction -- traité comme un échec")

    logger.info("%s -- EXECUTED %s (%.4f$) tx=%s", _REAL_MONEY_LOG_PREFIX, mint, amount_in_usd, tx_hash)
    try:
        await agent_wallet_log.record_transaction(
            wallet_product=WALLET_PRODUCT, chain="solana", action_type="swap",
            token_in="USDC", token_out=mint, amount_in=amount_in_usd,
            slippage_bps=MAX_SLIPPAGE_BPS, tx_hash=tx_hash, status="ok",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s -- could not record success: %s", _REAL_MONEY_LOG_PREFIX, exc)
    return SwapAttemptResult(ok=True, tx_hash=tx_hash, amount_in_usd=amount_in_usd,
                             entry_price=entry_price if isinstance(entry_price, (int, float)) else quoted_price)
