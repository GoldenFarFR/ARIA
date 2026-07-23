"""Autonomous decision loop for the real agent-wallet pilot (18/07, explicit
operator decision "option 2" -- ARIA decides AND executes ALONE, no Telegram
command needed per transaction). Full design and decision history:
`docs/pilote-agent-wallet-10usd.md` §8.

Fully reuses the momentum pipeline already built and tested for
paper-trading (GoPlus honeypot check + golden pocket/RSI R/R + LLM safety
guard, `momentum_entry.py`) -- no new decision logic invented here, only
the wiring to bounded real execution (`agent_wallet_pilot.attempt_swap`).
This module only knows about ORCHESTRATION: reading the real balance,
checking no position is already open, sizing (rule already decided on
16/07, #203), sourcing a candidate, attempting the swap if BUY is
confirmed.

Base only -- `agent_wallet_cdp_adapter.py` is structurally Base-only
(`USDC_BASE_ADDRESS` hardcoded), also consistent with the operator's 17/07
decision to keep Solana at the same security standard (in practice more
restrictive, hence fewer candidates -- not an issue here, this pilot only
needs ONE candidate per cycle anyway).

v1 (18/07): one entry at a time, NO automatic exit -- an already-open
position (any token other than USDC held) blocks any new attempt until a
future decision (manual, or a v2 with exit logic, not built here). The
"x402 unlocks a decision blocked by missing data" angle (requested by the
operator on 18/07) is DEFERRED -- `ethereum-token-verification` (the only
endpoint that could have helped) remains confirmed broken since 17/07, cf.
doc §8.7.
"""
from __future__ import annotations

import logging

from aria_core import agent_wallet_cdp_adapter, agent_wallet_log, agent_wallet_pilot, agent_wallet_sizing
from aria_core.agent_wallet_monitor import get_wallet_balance_summary

logger = logging.getLogger(__name__)

CHAIN = "base"
SWAP_FAILURE_COOLDOWN_MINUTES = 60
# 19/07 -- real URANUS incident (2 consecutive failures, Pydantic `ValidationError`
# on `CommonSwapResponseFees.gasFee`, bug confirmed on the Coinbase CDP SDK side, cf.
# CLAUDE.md): the 60min cooldown coincides with the heartbeat cycle cadence, so a
# STRUCTURAL failure (will reproduce identically, unlike a transient network outage)
# could make the same token retry indefinitely, once per cycle, never progressing to
# another candidate. 7 days -- long enough to avoid wasting one cycle per hour on a
# structurally broken token, short enough to give it another chance if Coinbase fixes
# its SDK in the meantime. No possible loss of funds in either case (the swap fails
# BEFORE any signing) -- a PROGRESS improvement, not a change to a security guardrail.
STRUCTURAL_SWAP_FAILURE_COOLDOWN_MINUTES = 7 * 24 * 60
MAX_CANDIDATES_PER_CYCLE = 5

# HOLD reasons that signal a lack of DATA rather than a hard rejection -- single
# reference for a future x402-unlock feature (deferred, doc §8.7), not yet used in
# this v1. Kept here so this list doesn't need to be redefined elsewhere the day
# that feature gets built.
DATA_GAP_HOLD_REASONS = frozenset({"ohlcv_unavailable"})


async def run_agent_wallet_pilot_cycle() -> dict:
    """One decision round. Never raises (soft degradation, same doctrine as the
    rest of the heartbeat) -- any failure translates into an explicit
    ``outcome``, never a silent crash of the heartbeat tick."""
    if not agent_wallet_pilot.agent_wallet_pilot_enabled():
        return {"outcome": "disabled"}

    try:
        summary = await get_wallet_balance_summary()
    except Exception as exc:  # noqa: BLE001 -- fail-closed, never a fabricated balance
        logger.warning("agent_wallet_pilot_cycle: balance unavailable (%s)", exc)
        return {"outcome": "balance_unavailable", "reason": str(exc)}

    other_tokens = summary.get("other_tokens")
    if other_tokens is None:
        return {"outcome": "balance_unavailable"}
    if other_tokens:
        return {
            "outcome": "position_open",
            "held": [t.get("symbol") for t in other_tokens],
        }

    sized_usd = await agent_wallet_sizing.size_trade_usd(
        balance_fn=agent_wallet_cdp_adapter.usdc_balance_usd,
    )
    if sized_usd is None:
        return {"outcome": "no_balance"}

    from aria_core import momentum_entry

    try:
        found = await momentum_entry.discover_momentum_candidates(chains=(CHAIN,))
    except Exception as exc:  # noqa: BLE001 -- a sourcing outage is not fatal
        logger.info("agent_wallet_pilot_cycle: sourcing failed (%s)", exc)
        return {"outcome": "sourcing_failed", "reason": str(exc)}

    checked = 0
    for candidate in found[:MAX_CANDIDATES_PER_CYCLE]:
        contract = (candidate.get("contract") or "").strip().lower()
        if not contract:
            continue
        checked += 1

        if await agent_wallet_log.recent_failed_swap(
            contract,
            within_minutes=SWAP_FAILURE_COOLDOWN_MINUTES,
            structural_within_minutes=STRUCTURAL_SWAP_FAILURE_COOLDOWN_MINUTES,
        ):
            continue

        try:
            sig = await momentum_entry.evaluate_momentum_entry(contract, CHAIN)
        except Exception as exc:  # noqa: BLE001 -- a broken evaluation doesn't block the cycle
            logger.info("agent_wallet_pilot_cycle: evaluation of %s failed (%s)", contract, exc)
            continue
        if not sig or sig.get("action") != "BUY":
            continue

        result = await agent_wallet_pilot.attempt_swap(
            chain=CHAIN,
            token_in=agent_wallet_cdp_adapter.USDC_BASE_ADDRESS,
            token_out=contract,
            amount_in_usd=sized_usd,
            wallet_address=summary.get("wallet_address") or "",
            balance_fn=agent_wallet_cdp_adapter.usdc_balance_usd,
            swap_fn=agent_wallet_cdp_adapter.execute_swap,
        )
        return {
            "outcome": result.status,
            "contract": contract,
            "symbol": sig.get("symbol", ""),
            "amount_usd": sized_usd,
            "reason": result.reason,
            "tx_hash": result.tx_hash,
        }

    return {"outcome": "no_candidate", "checked": checked}


def format_agent_wallet_swap_alert(result: dict) -> str:
    """Telegram alert -- REAL CAPITAL, never confused with the "🧪 SIMULATION"
    alerts from paper-trading (deliberately different prefix and wording).
    ``""`` if nothing notable enough to notify about (e.g. no candidate found
    this cycle -- avoid noise over "nothing happened")."""
    outcome = result.get("outcome")
    if outcome in ("disabled", "no_candidate", "position_open"):
        return ""
    symbol = result.get("symbol") or (result.get("contract") or "")[:10]
    if outcome == "ok":
        return (
            "🔴 ARGENT RÉEL — pilote agent-wallet\n"
            f"SWAP RÉUSSI {symbol}\n"
            f"Contrat {result.get('contract', '')}\n"
            f"Montant {result.get('amount_usd', 0):.2f} $ · tx {result.get('tx_hash', '')}"
        )
    if outcome == "failed":
        return (
            "🔴 ARGENT RÉEL — pilote agent-wallet\n"
            f"SWAP ÉCHOUÉ {symbol}\n"
            f"Contrat {result.get('contract', '')}\n"
            f"Raison : {result.get('reason', '')}"
        )
    if outcome == "blocked":
        return (
            "🔴 ARGENT RÉEL — pilote agent-wallet\n"
            f"Swap bloqué : {result.get('reason', '')}"
        )
    return ""
