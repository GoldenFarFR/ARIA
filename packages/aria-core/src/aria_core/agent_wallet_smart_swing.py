"""Autonomous swing-pocket execution via a delegated spender (Smart Account
migration, Model B chosen by the operator 07/23, safety-envelope completed
07/24 -- see docs/HANDOFF_COINBASE_CDP.md for the full state).

Context and doctrine: the swing pocket lives in the Smart Account
``aria-smart-st`` (owner = the operator's Tangem hardware wallet, ~250$ of
real capital planned, NOT yet funded). ARIA must be able to SWAP that pocket
autonomously (no Tangem tap per trade) while NEVER being able to move funds
out to an arbitrary address. This is achieved by a delegated ``spender`` EOA
(``aria-spender-smart-st``, CDP-managed key, no Tangem) plus THREE stacked
safety layers:

  1. CDP Policy on the spender (contract-enforced by CDP's signing layer):
     allowlist ONLY a swap-router call and a token-agnostic return-transfer
     back to ``aria-smart-st`` -- rejects everything else by default,
     including a raw transfer to any other address. Built here as
     ``build_swap_only_policy`` -- BUT its real enforcement must be validated
     against live CDP on a tiny amount before any grant is trusted (see the
     function's docstring: not yet done, next hardware step).
  2. Spend Permission on ``aria-smart-st`` (contract-enforced): a HARD numeric
     cap on how much USDC the spender can ever pull per period. Operator's
     explicit 07/24 decision: set this LARGE and FIXED, well ABOVE the real
     capital (2500$/week ~= 10x the 250$ planned) so it NEVER blocks a
     legitimate trade -- the real safety brake is NOT this spend cap but a
     dedicated circuit breaker on LOSS (see ``evaluate_swing_risk`` below).
     ``build_spend_permission_input`` encodes this cap; ``_MAX_SANE_ALLOWANCE_
     USD`` keeps it from silently becoming "unlimited" even from a careless
     future edit.
  3. Application-layer guards (same proven doctrine as ``agent_wallet_pilot.py``
     EOA pilot): per-transaction cap, real-balance check before every attempt,
     slippage forced to 10%, ``/stop`` kill-switch, systematic logging. Built
     here as ``execute_smart_swing_swap`` (the guarded execution path).

On top of the three layers, a dedicated LOSS circuit breaker
(``evaluate_swing_risk``/``block_swing_swaps``/``resume_swing_swaps``) reuses
the SAME thresholds as the paper portfolio's ``risk_guard`` (-20% drawdown from
the wallet's real equity high-water mark OR 5 consecutive losses) and blocks
any new swap until a MANUAL operator resume -- never automatic. When it trips,
it runs an adversarial post-mortem (same reviewer/doctrine as
``trade_devils_advocate.py``) over the wallet's recent real buys and notifies
the operator with the result, never a mute "circuit breaker armed".

Everything here is DORMANT: gate ``ARIA_SMART_SWING_ENABLED`` is OFF, nothing
is wired to any heartbeat cycle, no real grant/policy is created by importing
this module. The one-time Spend Permission grant (a Tangem tap on
``aria-smart-st`` via the tangem-wc-bridge), the Policy creation + live
validation, funding, and the end-to-end test on a tiny real amount are all
deliberately sequenced operator/hardware steps, never done autonomously. The
real CDP calls behind ``execute_smart_swing_swap`` are INJECTED by the caller
(same no-private-key doctrine as ``agent_wallet_cdp_adapter.py``): this module
orchestrates + guards, it never touches the network or a key itself.

All CDP SDK types/addresses below were verified against the really-installed
cdp-sdk 1.47.1 (never guessed) -- see the HANDOFF entries dated 07/23-07/24."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from aria_core import agent_wallet_log, outgoing_pause
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS
from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

# ── Verified on-chain identities (07/23, read from the deployed
#    agent_wallet_monitor.MONITORED_WALLETS, never from memory) ──────────────

# Swing pocket -- Smart Account, owner = Tangem (tangem-01). Autonomous swaps
# allowed via the spender; every TRANSFER out still requires the Tangem owner.
SMART_ST_ADDRESS = "0x800027f61363EF304c5C2Afee811d9d4074B474c"
# VC pocket -- Smart Account, owner = Tangem (tangem-02). NO delegation of any
# kind: every action (swap or transfer) requires the Tangem owner's direct
# signature. Present here only for reference/guarding -- this module never
# grants a spend permission on it.
SMART_VC_ADDRESS = "0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07"
# Dedicated spender EOA (CDP-managed key, no Tangem) -- already created.
# Pulls USDC from aria-smart-st via the Spend Permission, swaps, returns the
# output back to aria-smart-st. Never reused for anything else.
SPENDER_ADDRESS = "0x8e71C3e9396ded76AdA6EA56cD3c315C3D67D79b"
# Tangem owners (the physical signing devices). Present for reference/guarding.
TANGEM_ST_OWNER = "0x33783cCb570Cb279C25F836806B5c4C3C8309777"
TANGEM_VC_OWNER = "0x85e3D8128a9b7be14065A4E36C1845041BF65d7F"

# CDP SpendPermissionManager contract on Base (verified:
# cdp.spend_permissions.SPEND_PERMISSION_MANAGER_ADDRESS, 1.47.1).
SPEND_PERMISSION_MANAGER_ADDRESS = "0xf85210B21cC50302F477BA56686d2019dC9b67Ad"

# The only network this swing pocket trades on. Solana/other chains are
# explicitly out of scope for the real capital pilot (Base-only, same as the
# existing EOA pilot's agent_wallet_cdp_adapter).
NETWORK = "base"

# ── Spend Permission cap -- the operator's explicit 07/24 decision ───────────
# Set LARGE and FIXED, well ABOVE the ~250$ of real capital planned
# (2500$/week ~= 10x), auto-renewing, so a legitimate trade is NEVER blocked by
# the spend cap. The operator's reasoning: the spend allowance is the WRONG
# place to brake -- a too-tight allowance blocks good trades, while a bug that
# tries to swap the whole wallet is already bounded by what the wallet actually
# holds. The REAL safety brake is the dedicated LOSS circuit breaker below
# (``evaluate_swing_risk``), not this number. Raising it further is still a
# deliberate operator decision, NEVER a silent code change (enforced by
# _MAX_SANE_ALLOWANCE_USD below).
SPEND_PERMISSION_ALLOWANCE_USD = 2500.0
SPEND_PERMISSION_PERIOD_DAYS = 7

# Structural guard: this builder must NEVER be able to produce an "unlimited"
# (or absurdly large) allowance, even from a future careless edit -- that would
# silently remove safety layer #2. A real increase is an operator decision made
# by editing SPEND_PERMISSION_ALLOWANCE_USD *and* this ceiling deliberately.
# 2500$ stays comfortably below this 10000$ ceiling.
_MAX_SANE_ALLOWANCE_USD = 10_000.0

_USDC_DECIMALS = 6  # USDC = 6 decimals on Base (atomic units).

# Slippage forced on every swap -- absolute project rule (09/07), never an
# external tool's default. Same value/discipline as agent_wallet_pilot.py.
MAX_SLIPPAGE_BPS = 1000  # 10%

# Dedicated wallet_product tag for the append-only journal -- NEVER mixed with
# the EOA pilot's "coinbase_agentic_wallet" rows (the wallet_product column is
# what separates the two wallets in the shared agent_wallet_tx_log table; no
# schema change needed).
WALLET_PRODUCT = "cdp_smart_account_swing"

# Systematic prefix on every real-money log line of this module (same doctrine
# as agent_wallet_pilot._REAL_MONEY_LOG_PREFIX) -- distinct string so a
# log-grep can tell the swing pocket apart from the EOA pilot.
_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] smart-swing (aria-smart-st)"

# Dedicated gate for the autonomous swing execution, OFF by default,
# fail-closed -- same doctrine as ARIA_AGENT_WALLET_PILOT_ENABLED. Gates the
# EXECUTION path only; the pure builders below never execute anything, so they
# are not gated.
_SMART_SWING_GATE = "ARIA_SMART_SWING_ENABLED"

# EVM address shape, reused for fail-closed validation of a caller-supplied
# router before it ever reaches a Policy allowlist (same regex the CDP SDK's
# EvmAddressCriterion enforces internally -- validated here first for a clear
# error, never a garbage router silently producing a default-deny-everything
# policy).
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def smart_swing_enabled() -> bool:
    """Dedicated gate, OFF by default (fail-closed). Gates the autonomous
    swing EXECUTION path -- never any real spend until this is explicitly on
    AND the Spend Permission has been granted (Tangem tap) AND the Policy has
    been validated against live CDP."""
    return os.environ.get(_SMART_SWING_GATE, "").strip().lower() in ("1", "true", "yes", "on")


def usd_to_atomic_usdc(amount_usd: float) -> int:
    """USD amount -> USDC atomic units (6 decimals). Integer by construction --
    the CDP SpendPermissionInput.allowance and use_spend_permission ``value``
    are integers of smallest units (same convention as the ``parse_units`` used
    for swaps/transfers in agent_wallet_cdp_adapter)."""
    return int(round(amount_usd * (10 ** _USDC_DECIMALS)))


def build_spend_permission_input(
    *,
    allowance_usd: float = SPEND_PERMISSION_ALLOWANCE_USD,
    period_days: int = SPEND_PERMISSION_PERIOD_DAYS,
):
    """Builds the ``SpendPermissionInput`` for the ONE-TIME grant that lets the
    spender pull USDC from ``aria-smart-st`` (safety layer #2). Pure: builds a
    config object, executes nothing, touches no network -- the actual grant
    (``cdp.evm.create_spend_permission`` + the Tangem owner's signature via the
    tangem-wc-bridge) is a separate, hardware-gated step.

    Hard structural guards (this is a real-capital safety envelope, never a
    convenience helper):
      - ``allowance_usd`` must be strictly positive and at most
        ``_MAX_SANE_ALLOWANCE_USD`` -- makes "unlimited"/absurd allowances
        impossible to produce here, even from a careless future edit (safety
        layer #2 would otherwise silently vanish). The operator's chosen
        default (2500$/week) is deliberately large so it never blocks a
        legitimate trade -- the real brake is the loss circuit breaker.
      - ``period_days`` must be strictly positive.

    ``token`` is pinned to USDC on Base (the only asset the swing pocket funds
    swaps with -- the output token varies per trade but the INPUT pulled via
    the spend permission is always USDC)."""
    if not (0 < allowance_usd <= _MAX_SANE_ALLOWANCE_USD):
        raise ValueError(
            f"spend-permission allowance {allowance_usd}$ out of the safe range "
            f"(0, {_MAX_SANE_ALLOWANCE_USD}$] -- an unlimited/absurd allowance would remove "
            "safety layer #2; a real increase is a deliberate operator decision, never a silent one"
        )
    if period_days <= 0:
        raise ValueError(f"spend-permission period {period_days}d must be strictly positive")

    from cdp.spend_permissions import SpendPermissionInput

    return SpendPermissionInput(
        account=SMART_ST_ADDRESS,
        spender=SPENDER_ADDRESS,
        token=USDC_BASE_ADDRESS,
        allowance=usd_to_atomic_usdc(allowance_usd),
        period_in_days=period_days,
    )


# ── CDP Policy: swap-only + return-to-SA (safety layer #1) ────────────────────


def build_swap_only_policy(router_address: str):
    """Builds the CDP ``CreatePolicyOptions`` (scope=account) attached to the
    SPENDER EOA that allowlists ONLY two operations and rejects everything else
    by default (safety layer #1). Pure: builds a config object, executes
    nothing, touches no network -- the actual ``cdp.policies.create_policy`` +
    attach is a separate step.

    ⚠️ Its REAL enforcement MUST be validated against live CDP on a tiny real
    amount before any grant is trusted -- NOT yet done, this is the next
    hardware/verification step. Until then, treat this policy as designed but
    UNPROVEN.

    Two accept rules (top-down first-match, everything unmatched is
    default-denied by the CDP Policy Engine -- that default-deny is what blocks
    a raw transfer to an arbitrary address):
      1. ALLOW a ``sendEvmTransaction`` whose destination (``to``) is exactly
         ``router_address`` -- the DEX router CDP's swap backend routes through.
         The router address is obtained dynamically from a real quote
         (``QuoteSwapResult.to``) and MUST be confirmed STABLE across several
         real quotes before being hardcoded/trusted here -- it is a caller
         parameter precisely so it is never guessed.
      2. ALLOW a token-agnostic ERC-20 ``transfer`` whose ``to`` parameter is
         exactly ``SMART_ST_ADDRESS`` -- the return of the swap OUTPUT back to
         the swing pocket. Token-agnostic (matches on the decoded ``to``
         argument, not on a fixed token contract) because the output token
         varies per trade. This is the single most delicate, safety-critical
         carve-out: it is the ONLY transfer the spender may ever make, and only
         ever to the swing pocket.

    Known open concern to verify during live validation (documented, never
    silently patched): a real swap through the router may ALSO require a
    preceding ERC-20 ``approve(router, amount)`` transaction (whose ``to`` is
    the USDC contract, not the router). That approve is NOT allowlisted here --
    if live testing confirms it's needed, an explicit approve carve-out
    (allow ERC-20 ``approve`` whose spender param == ``router_address``) must be
    added deliberately, not assumed. Building it blindly now would be guessing.

    Fail-closed: a malformed ``router_address`` raises ``ValueError`` rather
    than producing a policy that would either error opaquely or (worse)
    default-deny every swap."""
    router = (router_address or "").strip()
    if not _EVM_ADDRESS_RE.match(router):
        raise ValueError(
            f"router_address {router_address!r} is not a valid EVM address -- refusing to build a "
            "swap-only policy on a garbage router (would default-deny every swap or error opaquely)"
        )

    from cdp.openapi_client.models.known_abi_type import KnownAbiType
    from cdp.policies.types import (
        CreatePolicyOptions,
        EvmAddressCriterion,
        EvmDataCondition,
        EvmDataCriterion,
        EvmDataParameterConditionList,
        SendEvmTransactionRule,
    )

    allow_swap_router = SendEvmTransactionRule(
        action="accept",
        criteria=[EvmAddressCriterion(addresses=[router], operator="in")],
    )
    allow_return_to_swing_pocket = SendEvmTransactionRule(
        action="accept",
        criteria=[
            EvmDataCriterion(
                abi=KnownAbiType.ERC20,
                conditions=[
                    EvmDataCondition(
                        function="transfer",
                        params=[
                            EvmDataParameterConditionList(
                                name="to", operator="in", values=[SMART_ST_ADDRESS],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    return CreatePolicyOptions(
        scope="account",
        description=(
            "aria-smart-st swing spender: allow swap-router calls + token-agnostic "
            "return-transfer to aria-smart-st only; default-deny everything else "
            "(no raw transfer to an arbitrary address)"
        ),
        rules=[allow_swap_router, allow_return_to_swing_pocket],
    )


# ── Guarded execution path (safety layer #3) ─────────────────────────────────

BalanceFn = Callable[[], Awaitable[float | None]]
SpendPullFn = Callable[..., Awaitable[dict[str, Any]]]
SwapFn = Callable[..., Awaitable[dict[str, Any]]]
ReturnTransferFn = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class SmartSwingSwapResult:
    status: str  # "ok" | "blocked" | "failed"
    reason: str = ""
    pull_tx_hash: str = ""
    swap_tx_hash: str = ""
    return_tx_hash: str = ""
    amount_out: float = 0.0
    # True if a step failed AFTER USDC had already been pulled into the spender
    # (swap failed, or return-transfer failed) -- funds are then sitting in the
    # spender, NOT stolen (the Policy only lets the spender reach the router or
    # transfer back to aria-smart-st), but misplaced and needing a recovery
    # step. Surfaced loudly rather than silently swallowed.
    funds_stranded: bool = False


async def execute_smart_swing_swap(
    *,
    token_out: str,
    amount_in_usd: float,
    balance_fn: BalanceFn,
    spend_pull_fn: SpendPullFn,
    swap_fn: SwapFn,
    return_transfer_fn: ReturnTransferFn,
    token_in: str = USDC_BASE_ADDRESS,
    network: str = NETWORK,
    slippage_bps: int | None = None,
) -> SmartSwingSwapResult:
    """Guarded swing swap: pull USDC from aria-smart-st via the Spend Permission
    -> swap it for ``token_out`` -> return the OUTPUT back to aria-smart-st.
    Same 5 application guards as ``agent_wallet_pilot.attempt_swap`` (strict
    order): gate -> kill-switch -> per-tx cap -> real-balance check -> forced
    slippage -> execution -> systematic logging.

    The three real CDP calls are INJECTED (no key/network here, same doctrine
    as the EOA pilot):
      - ``spend_pull_fn(value_atomic=, network=)`` -> ``{"tx_hash"}`` --
        the spender's ``use_spend_permission(spend_permission=build_spend_
        permission_input(), value=<usdc atomic>, network=)`` (pulls USDC from
        aria-smart-st into the spender; needs NO Tangem tap once granted).
      - ``swap_fn(network=, token_in=, token_out=, amount_in_usd=,
        slippage_bps=)`` -> ``{"tx_hash", "amount_out", "amount_out_atomic"?}``
        -- the spender's ``swap(AccountSwapOptions(...))``. Output lands in the
        spender (``AccountSwapOptions`` has no recipient field, confirmed in
        cdp-sdk 1.47.1), which is exactly why the explicit return step below is
        mandatory.
      - ``return_transfer_fn(to_address=, token_address=, amount_out=,
        amount_out_atomic=, network=)`` -> ``{"tx_hash"}`` -- the spender's
        ``transfer(to=SMART_ST_ADDRESS, amount=<out atomic>, token=<token_out>,
        network=)``. The destination is HARDCODED to ``SMART_ST_ADDRESS`` by
        this orchestration and passed for the injected fn's convenience only --
        never a free parameter (defense-in-depth mirroring the Policy's
        return-transfer carve-out and the EOA pilot's ALLOWED_TRANSFER_ADDRESS).

    ``slippage_bps`` is only accepted to flag a mismatch in the logs -- it is
    NEVER passed through as-is to ``swap_fn`` (always forced to
    ``MAX_SLIPPAGE_BPS``).

    If a step fails AFTER the pull, ``funds_stranded`` is True and the failure
    is logged loudly: the funds are in the spender and need a recovery
    transfer back to aria-smart-st (bounded by the Policy -- recoverable, never
    stealable). No automatic recovery is attempted here (out of scope; a
    recovery flow is a separate, deliberate step)."""
    if slippage_bps is not None and slippage_bps != MAX_SLIPPAGE_BPS:
        logger.warning(
            "%s -- slippage_bps=%s ignored, forced to %s (absolute rule 09/07)",
            _REAL_MONEY_LOG_PREFIX, slippage_bps, MAX_SLIPPAGE_BPS,
        )

    if not smart_swing_enabled():
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason="ARIA_SMART_SWING_ENABLED désactivé (fail-closed par défaut)",
        )

    # Single guard that covers the global ``/stop`` kill-switch (strict,
    # fail-closed), the dedicated LOSS circuit breaker (armed -> blocked until a
    # manual resume), AND a corrupted breaker state (fail-closed). Enforced HERE
    # in the primitive as defense-in-depth: a swap can NEVER execute while the
    # loss breaker is armed, even if a future orchestration cycle forgets to
    # check it first.
    swaps_blocked, block_reason = blocks_swing_swaps()
    if swaps_blocked:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason=block_reason or "swaps swing bloqués (coupe-circuit/kill-switch)",
        )

    if amount_in_usd <= 0:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network, reason="montant nul ou négatif",
        )

    # Per-transaction cap: never above the Spend Permission allowance (a single
    # tx can never try to pull more than the whole weekly allowance). Deliberately
    # loose (the allowance is ~10x the real capital) -- the real brake is the
    # loss circuit breaker, not this cap.
    if amount_in_usd > SPEND_PERMISSION_ALLOWANCE_USD:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason=f"montant {amount_in_usd}$ > plafond par transaction {SPEND_PERMISSION_ALLOWANCE_USD}$ "
                   "(borné par l'allowance de la spend permission)",
        )

    try:
        balance_usd = await balance_fn()
    except Exception as exc:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason=f"solde réel indisponible (fail-closed) : {exc}",
        )
    if balance_usd is None:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason="solde réel indisponible (fail-closed) : balance_fn a renvoyé None",
        )
    if amount_in_usd > balance_usd:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason=f"montant {amount_in_usd}$ > solde réel {balance_usd}$",
        )

    # ── Step 1: pull USDC from aria-smart-st into the spender ──
    value_atomic = usd_to_atomic_usdc(amount_in_usd)
    try:
        pull = await spend_pull_fn(value_atomic=value_atomic, network=network)
    except Exception as exc:
        # Nothing pulled -- USDC still safely in aria-smart-st.
        return await _failed_swap(
            token_in, token_out, amount_in_usd, network, stranded=False,
            reason=f"use_spend_permission (pull) échoué avant tout mouvement : {exc}",
        )
    pull_tx = str(pull.get("tx_hash") or "")

    # ── Step 2: swap the pulled USDC for token_out (output lands in spender) ──
    try:
        swap = await swap_fn(
            network=network, token_in=token_in, token_out=token_out,
            amount_in_usd=amount_in_usd, slippage_bps=MAX_SLIPPAGE_BPS,
        )
    except Exception as exc:
        # Pulled but not swapped -- USDC is in the spender, needs recovery.
        return await _failed_swap(
            token_in, token_out, amount_in_usd, network, stranded=True,
            pull_tx=pull_tx,
            reason=f"swap échoué APRÈS pull -- USDC dans le spender, récupération vers aria-smart-st requise : {exc}",
        )
    swap_tx = str(swap.get("tx_hash") or "")
    amount_out = float(swap.get("amount_out") or 0.0)
    amount_out_atomic = swap.get("amount_out_atomic")

    # ── Step 3: MANDATORY return of the output back to aria-smart-st ──
    try:
        ret = await return_transfer_fn(
            to_address=SMART_ST_ADDRESS, token_address=token_out,
            amount_out=amount_out, amount_out_atomic=amount_out_atomic, network=network,
        )
    except Exception as exc:
        # Swapped but output NOT returned -- token_out is stranded in the spender.
        return await _failed_swap(
            token_in, token_out, amount_in_usd, network, stranded=True,
            pull_tx=pull_tx, swap_tx=swap_tx, amount_out=amount_out,
            reason=f"return-transfer échoué APRÈS swap -- {token_out} dans le spender, "
                   f"récupération vers aria-smart-st requise : {exc}",
        )
    return_tx = str(ret.get("tx_hash") or "")

    logger.info(
        "%s -- swap SUCCEEDED: %s -> %s (%.2f$ -> %.6g), pull=%s swap=%s return=%s",
        _REAL_MONEY_LOG_PREFIX, token_in, token_out, amount_in_usd, amount_out,
        pull_tx, swap_tx, return_tx,
    )
    await agent_wallet_log.record_transaction(
        wallet_product=WALLET_PRODUCT,
        chain=network,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in_usd,
        amount_out=amount_out,
        slippage_bps=MAX_SLIPPAGE_BPS,
        tx_hash=swap_tx,  # primary event; pull/return hashes kept in reason
        to_address=SMART_ST_ADDRESS,  # the output's final, only-ever destination
        status="ok",
        reason=f"pull={pull_tx} return={return_tx}",
    )
    return SmartSwingSwapResult(
        status="ok", pull_tx_hash=pull_tx, swap_tx_hash=swap_tx,
        return_tx_hash=return_tx, amount_out=amount_out,
    )


async def _blocked_swap(
    token_in: str, token_out: str, amount_in_usd: float, network: str, *, reason: str
) -> SmartSwingSwapResult:
    logger.warning("%s -- swap blocked: %s", _REAL_MONEY_LOG_PREFIX, reason)
    await agent_wallet_log.record_transaction(
        wallet_product=WALLET_PRODUCT,
        chain=network,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in_usd,
        slippage_bps=MAX_SLIPPAGE_BPS,
        status="blocked",
        reason=reason,
    )
    return SmartSwingSwapResult(status="blocked", reason=reason)


async def _failed_swap(
    token_in: str, token_out: str, amount_in_usd: float, network: str, *,
    stranded: bool, reason: str, pull_tx: str = "", swap_tx: str = "", amount_out: float = 0.0,
) -> SmartSwingSwapResult:
    log_fn = logger.error if stranded else logger.warning
    log_fn("%s -- swap failed (stranded=%s): %s", _REAL_MONEY_LOG_PREFIX, stranded, reason)
    await agent_wallet_log.record_transaction(
        wallet_product=WALLET_PRODUCT,
        chain=network,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in_usd,
        amount_out=amount_out,
        slippage_bps=MAX_SLIPPAGE_BPS,
        tx_hash=swap_tx,
        to_address=SMART_ST_ADDRESS if stranded else "",
        status="failed",
        reason=reason,
    )
    return SmartSwingSwapResult(
        status="failed", reason=reason, pull_tx_hash=pull_tx, swap_tx_hash=swap_tx,
        amount_out=amount_out, funds_stranded=stranded,
    )


# ── Dedicated LOSS circuit breaker for the real swing wallet ──────────────────
#
# The operator's explicit doctrine (07/24): the spend cap is deliberately loose
# (never the brake), so the REAL brake against a losing streak is this circuit
# breaker on the wallet's own equity/losses. It reuses the EXACT same thresholds
# as the paper portfolio's risk_guard -- imported, not re-declared, so a
# real-capital breaker can NEVER silently become laxer than the paper one (a
# divergence would be a silent safety regression). State is persisted in its OWN
# file (never mixed with risk_guard's paper state). Resume is MANUAL only, never
# automatic -- same doctrine as risk_guard.resume_new_entries.
from aria_core.risk_guard import HARD_CONSECUTIVE_LOSSES, HARD_DRAWDOWN_PCT

_SWING_BAND_NONE = "none"
_SWING_BAND_HARD = "hard"


def _swing_state_path() -> Path:
    return data_dir() / "smart_swing_risk_state.json"


def _read_swing_state() -> dict[str, Any] | None:
    """Three-state semantics (same as risk_guard/outgoing_pause): ``{}`` (file
    absent -- never triggered, not a doubt), ``dict`` (read correctly),
    ``None`` (corrupted -- UNKNOWN state, callers fail-closed)."""
    path = _swing_state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("%s -- smart_swing_risk_state unreadable/corrupted (%s) -- UNKNOWN state",
                       _REAL_MONEY_LOG_PREFIX, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("%s -- smart_swing_risk_state has unexpected shape (%r) -- UNKNOWN state",
                       _REAL_MONEY_LOG_PREFIX, type(raw).__name__)
        return None
    return raw


def _write_swing_state(payload: dict[str, Any]) -> None:
    path = _swing_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _persist_swing(updates: dict[str, Any]) -> None:
    """Read-modify-write that PRESERVES unknown keys (notably
    ``high_water_mark``) across a block/resume/hwm-update -- so arming the
    breaker never wipes the equity high-water mark, and vice versa. On a
    corrupted read, starts from ``{}`` (the corruption is already handled
    fail-closed by ``blocks_swing_swaps``)."""
    current = _read_swing_state() or {}
    current.update(updates)
    _write_swing_state(current)


def swing_breaker_status() -> dict[str, Any]:
    """Current state of the DEDICATED swing circuit breaker (never
    risk_guard's paper state): ``{blocked, since, by, reason, high_water_mark,
    readable}``. ``readable=False`` signals a corrupted file -- fail-closed on
    the caller's side (``blocks_swing_swaps``), same "money" doctrine as
    ``risk_guard.new_entry_block_status``."""
    raw = _read_swing_state()
    readable = raw is not None
    data = raw or {}
    since: datetime | None = None
    since_raw = data.get("since")
    if isinstance(since_raw, str):
        try:
            since = datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            since = None
    try:
        hwm = float(data.get("high_water_mark") or 0.0)
    except (TypeError, ValueError):
        hwm = 0.0
    return {
        "blocked": bool(data.get("blocked")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "high_water_mark": hwm,
        "readable": readable,
    }


def block_swing_swaps(reason: str, *, by: int | str | None = None) -> dict[str, Any]:
    """Arms the breaker: no more NEW swing swaps until ``resume_swing_swaps``
    is called explicitly (never automatic -- see the section docstring).
    Preserves ``high_water_mark``."""
    _persist_swing(
        {
            "blocked": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
            "last_alert_band": _SWING_BAND_HARD,
        }
    )
    logger.warning("%s -- circuit breaker ARMED -- reason=%s", _REAL_MONEY_LOG_PREFIX, reason)
    return swing_breaker_status()


def resume_swing_swaps(*, by: int | str | None = None) -> dict[str, Any]:
    """Lifts the breaker. NEVER called automatically by ``evaluate_swing_risk``
    -- reserved for an explicit human action, even if the drawdown has since
    recovered (same doctrine as risk_guard.resume_new_entries). Preserves
    ``high_water_mark`` so the drawdown reference isn't reset by a resume."""
    _persist_swing(
        {
            "blocked": False,
            "since": None,
            "by": by,
            "reason": "",
            "last_alert_band": _SWING_BAND_NONE,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("%s -- circuit breaker LIFTED (manual resume) -- by=%s", _REAL_MONEY_LOG_PREFIX, by)
    return swing_breaker_status()


def blocks_swing_swaps() -> tuple[bool, str | None]:
    """``(blocked, reason)`` -- combines the dedicated swing breaker AND
    ``outgoing_pause`` (a global ``/stop`` also blocks new swing swaps) WITHOUT
    confusing the two in the reported reason. Fail-closed on unreadable state
    ("money" doctrine, strict=True)."""
    if outgoing_pause.is_paused(strict=True):
        return True, outgoing_pause.blocked_notice("Ce swap smart-swing")

    status = swing_breaker_status()
    if not status["readable"]:
        return True, "état du coupe-circuit swing illisible/corrompu — fail-closed par sécurité"
    if status["blocked"]:
        return True, status["reason"] or "coupe-circuit swing armé — reprise manuelle requise"
    return False, None


@dataclass
class SwingRiskState:
    equity: float
    high_water_mark: float
    drawdown_pct: float             # 0..1 from the high
    consecutive_losses: int
    blocked: bool
    blocked_reason: str | None = None
    newly_triggered_hard: bool = False


def _count_consecutive_losses(recent_pnls: Sequence[float]) -> int:
    """Leading run of losses in a most-recent-first sequence of realized P&L
    (USD) -- same semantics as risk_guard (iterate newest first, stop at the
    first non-loss). Capped at ``HARD_CONSECUTIVE_LOSSES`` (no need to count
    further -- the threshold is already reached)."""
    count = 0
    for pnl in recent_pnls:
        try:
            value = float(pnl)
        except (TypeError, ValueError):
            break
        if value < 0:
            count += 1
            if count >= HARD_CONSECUTIVE_LOSSES:
                break
        else:
            break
    return count


async def evaluate_swing_risk(
    *,
    equity_usd: float,
    recent_pnls: Sequence[float],
    post_mortem_fn: Callable[[], Awaitable[str]] | None = None,
    notify_fn: Callable[[str], Awaitable[Any]] | None = None,
) -> SwingRiskState:
    """Snapshot of the real swing wallet's risk -- to be called ONCE per cycle,
    BEFORE any new swap (never before managing/exiting an already-open
    position). Arms the dedicated breaker if a hard tier is crossed for the
    first time, and on that first trigger runs an adversarial post-mortem +
    notifies the operator (never a mute arming).

    ``equity_usd`` (the wallet's real current equity: USDC + value of held
    tokens) and ``recent_pnls`` (realized P&L of recently CLOSED trades,
    most-recent-first) are INJECTED. Computing them requires real balance/price
    calls AND a "closed real position with P&L" concept that does NOT yet exist
    for this wallet (agent_wallet_log records individual swap ATTEMPTS, not
    paired entry/exit trades with realized P&L) -- see the module's HANDOFF for
    this documented gap. This function is the mechanism + seam; wiring the real
    feed is a later step, never invented here.

    Thresholds are risk_guard's (imported): -20% drawdown from the equity
    high-water mark OR 5 consecutive losses. HWM is persisted in this module's
    OWN state file (never the paper one)."""
    status = swing_breaker_status()
    readable = status["readable"]

    hwm = status["high_water_mark"]
    if readable and equity_usd > hwm:
        hwm = equity_usd
        _persist_swing({"high_water_mark": hwm})

    drawdown_pct = max(0.0, (hwm - equity_usd) / hwm) if hwm > 0 else 0.0
    consecutive_losses = _count_consecutive_losses(recent_pnls)

    already_blocked = status["blocked"]
    hard_breach = drawdown_pct >= HARD_DRAWDOWN_PCT or consecutive_losses >= HARD_CONSECUTIVE_LOSSES
    newly_triggered = False
    if hard_breach and not already_blocked and readable:
        reason = (
            f"drawdown {drawdown_pct:.1%} depuis le plus haut d'équité réelle ({hwm:,.2f} $)"
            if drawdown_pct >= HARD_DRAWDOWN_PCT
            else f"{consecutive_losses} pertes consécutives"
        )
        block_swing_swaps(reason)
        newly_triggered = True

    blocked, blocked_reason = blocks_swing_swaps()
    state = SwingRiskState(
        equity=equity_usd,
        high_water_mark=hwm,
        drawdown_pct=drawdown_pct,
        consecutive_losses=consecutive_losses,
        blocked=blocked,
        blocked_reason=blocked_reason,
        newly_triggered_hard=newly_triggered,
    )

    if newly_triggered:
        await _run_post_mortem_and_notify(state, post_mortem_fn=post_mortem_fn, notify_fn=notify_fn)

    return state


def format_swing_circuit_breaker_alert(state: SwingRiskState) -> str:
    """Operator-facing alert (REAL money -- never the paper "🧪 SIMULATION"
    marker). Says WHY it tripped and that resume is manual only."""
    return "\n".join([
        "⛔ ARGENT RÉEL — coupe-circuit swing (aria-smart-st) ARMÉ",
        f"{state.blocked_reason or 'seuil de risque franchi'}.",
        f"Équité réelle : {state.equity:,.2f} $ · plus haut : {state.high_water_mark:,.2f} $ "
        f"· drawdown {state.drawdown_pct:.1%} · pertes consécutives : {state.consecutive_losses}.",
        "Tout NOUVEAU swap swing est bloqué jusqu'à reprise manuelle explicite (jamais automatique).",
        "Positions déjà ouvertes : gérées normalement — aucune n'est fermée de force.",
    ])


async def _run_post_mortem_and_notify(
    state: SwingRiskState,
    *,
    post_mortem_fn: Callable[[], Awaitable[str]] | None,
    notify_fn: Callable[[str], Awaitable[Any]] | None,
) -> str:
    """On a fresh breaker trip: build the Telegram message = the alert PLUS the
    adversarial post-mortem result (never a mute "armed"). ``post_mortem_fn``
    (injected) produces the analysis text; if absent, the message says so
    honestly rather than pretending an analysis ran. ``notify_fn`` (injected)
    sends it to the operator. Both are best-effort -- a post-mortem/notify
    failure never crashes the risk evaluation."""
    if post_mortem_fn is not None:
        try:
            summary = (await post_mortem_fn()) or ""
        except Exception as exc:  # noqa: BLE001 -- never break the breaker on a post-mortem failure
            logger.error("%s -- post-mortem failed: %s", _REAL_MONEY_LOG_PREFIX, exc)
            summary = f"(post-mortem indisponible : {exc})"
    else:
        summary = (
            "(post-mortem non exécuté : aucun feed de positions réelles clôturées avec P&L "
            "n'existe encore pour ce wallet — voir la limite documentée dans le HANDOFF)"
        )

    message = format_swing_circuit_breaker_alert(state)
    if summary:
        message += "\n\n" + summary

    if notify_fn is not None:
        try:
            await notify_fn(message)
        except Exception as exc:  # noqa: BLE001 -- never break the breaker on a notify failure
            logger.error("%s -- notify failed: %s", _REAL_MONEY_LOG_PREFIX, exc)
    return message


async def run_swing_post_mortem(recent_buys: Sequence[dict], *, llm=None) -> str:
    """Adversarial post-mortem over this wallet's recent REAL buys, reusing the
    EXACT same adversarial reviewer and system prompt as
    ``trade_devils_advocate.py`` (imported, never a new prompt): a genuinely
    different model (DeepSeek R1 via OpenRouter, Haiku fallback) judges each
    decision "sound" vs "flawed" on what was KNOWABLE at entry -- never a
    reproach based on the outcome alone.

    ``recent_buys`` (injected) is a list of closed real buys, each a dict with
    the same fields the Devil's Advocate reads (``thesis``/``entry_price``/
    ``exit_price``/``pnl_pct``/``pnl_usd``/``close_reason``/``close_notes``).
    That real feed does NOT exist yet for this wallet (agent_wallet_log has no
    closed-position-with-P&L concept -- documented gap); this function is the
    reusable building block a future caller wraps into ``evaluate_swing_risk``'s
    ``post_mortem_fn`` once the feed exists.

    Deliberately one-shot and NON-persisting (returns a summary string for the
    Telegram alert), rather than calling ``run_trade_devils_advocate_cycle``:
    that cycle (1) is separately gated by ARIA_TRADE_DEVILS_ADVOCATE_ENABLED, so
    a safety-triggered post-mortem could be silently skipped; (2) persists into
    a table keyed by ``position_id`` UNIQUE that is shared with the paper
    portfolio -- a real position id could collide with a paper one. Reusing the
    prompt/doctrine without its paper-keyed persistence avoids both."""
    if not recent_buys:
        return "Post-mortem : aucun achat réel récent à analyser."

    from aria_core.skills import trade_devils_advocate as tda

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    reviewed = 0
    flawed_lessons: list[str] = []
    for buy in recent_buys:
        prompt = tda._format_case_for_prompt(buy)
        try:
            raw = await llm(
                prompt, tda._REVIEW_SYSTEM, max_tokens=500, temperature=0.0,
                provider="openrouter", model="deepseek/deepseek-r1",
                fallback_provider="openrouter", fallback_model="anthropic/claude-haiku-4.5",
            )
        except Exception as exc:  # noqa: BLE001 -- one failed review never breaks the batch
            logger.warning("%s -- post-mortem review failed on one buy: %s", _REAL_MONEY_LOG_PREFIX, exc)
            continue
        reviewed += 1
        verdict, lesson = _parse_devils_verdict(raw)
        if verdict == "flawed" and lesson:
            label = str(buy.get("symbol") or buy.get("contract") or "?")[:12]
            flawed_lessons.append(f"{label} : {lesson}")

    if reviewed == 0:
        return "Post-mortem : l'analyse adversariale n'a pu être obtenue (LLM indisponible)."
    if not flawed_lessons:
        return (
            f"Post-mortem ({reviewed} achats revus) : aucune faille de RAISONNEMENT identifiée — "
            "les pertes semblent du bruit de marché sur des décisions défendables, pas un biais structurel."
        )
    joined = " | ".join(flawed_lessons)
    if len(joined) > 600:
        joined = joined[:600].rstrip() + "…"
    return f"Post-mortem ({reviewed} achats revus) — failles de raisonnement trouvées : {joined}"


def _parse_devils_verdict(raw: str | None) -> tuple[str, str]:
    """Parse the Devil's Advocate JSON response -> ``(verdict, lesson)``. Same
    safe-fallback discipline as ``trade_devils_advocate._review_one`` (an
    unreadable/off-schema answer degrades to a harmless "sound"/no-lesson,
    never a fabricated flaw)."""
    if not raw:
        return "sound", ""
    try:
        data = json.loads(raw)
        verdict = str(data.get("verdict", "sound")).strip().lower()
        if verdict not in ("sound", "flawed"):
            verdict = "sound"
        lesson = str(data.get("lesson", "")).strip()
        return verdict, lesson
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return "sound", ""
