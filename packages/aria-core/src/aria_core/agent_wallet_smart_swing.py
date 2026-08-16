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

from aria_core import agent_wallet_log, custody_pause, outgoing_pause
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

# Canary probe rows land under their OWN wallet_product so the position pairer
# (agent_wallet_positions, which reads only WALLET_PRODUCT) never turns a
# deliberately-small probe round-trip into a "closed position" with a P&L --
# that would pollute the real-position feed the loss circuit breaker consumes
# (every canary loses the round-trip tax by design; a run of them must never
# trip the consecutive-loss brake). Same table, distinct tag, no schema change.
CANARY_WALLET_PRODUCT = "cdp_smart_account_swing_canary"

# ── Entry canary (VOLET 4) -- probe the REAL buy+sell tax before full size ────
# Small fixed test buy: large enough that a real pool's fees + price impact
# register as a measurable round-trip cost, small enough that repeating the
# probe is cheap (a few cents of real tax per probe) and it is a rounding error
# against the ~250$ real capital and well under the per-tx cap. 3$ is the chosen
# default.
CANARY_TEST_AMOUNT_USD = 3.0

# Reject a full-size entry if the MEASURED canary round-trip loss exceeds this.
# A round trip is TWO real swaps, so it inherently costs ~2x a one-way swap;
# at the module's 10% (MAX_SLIPPAGE_BPS) per-swap ceiling the worst *legitimate*
# round trip approaches ~19% (1 - 0.9*0.9). 20% therefore cleanly separates a
# genuine liquid pair (a 3$ probe round-trips well under ~5%: two swap fees +
# negligible impact) from a stealth-tax token / soft honeypot (which taxes far
# more, or the sell reverts outright -> also a canary failure), WITHOUT
# false-rejecting a legit token whose two fills happen to land near the max
# slippage tolerance. Deliberately looser than MAX_SLIPPAGE_BPS (that bounds ONE
# swap; a round trip is two) but far tighter than a total loss.
CANARY_MAX_ROUNDTRIP_LOSS_BPS = 2000  # 20%

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


def _per_tx_cap_cents() -> int:
    """The per-transaction USD cap in whole cents, for the Policy's
    ``NetUSDChangeCriterion`` (safety layer #1). Derived from the SAME constant
    the application layer enforces (``SPEND_PERMISSION_ALLOWANCE_USD``) so the
    contract-level bound and the app-level bound can never drift apart.
    ``changeCents`` must be a non-negative integer (cdp-sdk field constraint)."""
    return int(round(SPEND_PERMISSION_ALLOWANCE_USD * 100))


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
         ``router_address`` -- the DEX router CDP's swap backend routes through
         -- AND whose net USD value moved is at most the per-transaction cap
         (``NetUSDChangeCriterion(operator="<=", changeCents=<cap>)``, verified
         to exist in cdp-sdk 1.47.1's ``SendEvmTransactionRule.criteria``). Both
         criteria in a rule are AND-ed by the Policy Engine, so a router call is
         accepted ONLY if it targets the router AND stays within the cap. The
         cap reuses ``SPEND_PERMISSION_ALLOWANCE_USD`` (the same per-tx ceiling
         ``execute_smart_swing_swap`` enforces at the application layer) -- no
         second, driftable number. The router address is obtained dynamically
         from a real quote (``QuoteSwapResult.to``) and MUST be confirmed STABLE
         across several real quotes before being hardcoded/trusted here -- it is
         a caller parameter precisely so it is never guessed.
      2. ALLOW a token-agnostic ERC-20 ``transfer`` whose ``to`` parameter is
         exactly ``SMART_ST_ADDRESS`` -- the return of a swap OUTPUT back to
         the swing pocket. Token-agnostic (matches on the decoded ``to``
         argument, not on a fixed token contract) because the output token
         varies per trade. This is the single most delicate, safety-critical
         carve-out: it is the ONLY transfer the spender may ever make, and only
         ever to the swing pocket.

    REAL bound under a spender-key compromise (stated honestly -- the older
    "never stealable" claim was WRONG): rule 1 alone previously accepted ANY
    calldata to the router, so a compromised spender key could have drained up
    to the FULL Spend Permission allowance through router-shaped calls. The
    ``NetUSDChangeCriterion`` added here caps the net USD a single accepted
    router call can move to the per-transaction ceiling
    (``SPEND_PERMISSION_ALLOWANCE_USD``); combined with the Spend Permission's
    own period allowance (safety layer #2) the loss under compromise is BOUNDED,
    not impossible. The honest worst case is bounded by these caps, never zero.

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
        NetUSDChangeCriterion,
        SendEvmTransactionRule,
    )

    allow_swap_router = SendEvmTransactionRule(
        action="accept",
        criteria=[
            EvmAddressCriterion(addresses=[router], operator="in"),
            # Bound the net USD a single router call may move to the per-tx cap
            # (SPEND_PERMISSION_ALLOWANCE_USD, in cents) -- the same ceiling the
            # application layer enforces, reused, never a new number. This is
            # what turns rule 1 from "any calldata to the router" into a
            # value-bounded allow (the calldata hole fix).
            NetUSDChangeCriterion(
                type="netUSDChange",
                changeCents=_per_tx_cap_cents(),
                operator="<=",
            ),
        ],
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
        # CDP's own API constrains description to ^[A-Za-z0-9 ,.]{1,50}$ -- a
        # longer or punctuation-rich string is rejected at creation time, not
        # at build time. Full rule semantics are documented in this
        # function's docstring.
        description="ARIA swing spender, swap plus return only.",
        rules=[allow_swap_router, allow_return_to_swing_pocket],
    )


# ── Guarded execution path (safety layer #3) ─────────────────────────────────

BalanceFn = Callable[[], Awaitable[float | None]]
SpendPullFn = Callable[..., Awaitable[dict[str, Any]]]
SwapFn = Callable[..., Awaitable[dict[str, Any]]]
ReturnTransferFn = Callable[..., Awaitable[dict[str, Any]]]
# Swap-quote seam (VOLET 3). Mirrors ``cdp.evm.create_swap_quote`` (verified
# signature, cdp-sdk 1.47.1): returns a ``QuoteSwapResult`` (fields ``to_amount``
# /``min_to_amount`` as decimal-string atomic units, ``liquidity_available``) or
# a ``SwapUnavailableResult`` (``liquidity_available=False``). Injected, never a
# live CDP call in this module -- same doctrine as every other seam here.
QuoteFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class SmartSwingSwapResult:
    status: str  # "ok" | "blocked" | "failed"
    reason: str = ""
    pull_tx_hash: str = ""   # only a BUY pulls; empty on a SELL
    swap_tx_hash: str = ""
    return_tx_hash: str = ""  # only a SELL returns cash; empty on a BUY (VOLET 2a)
    amount_out: float = 0.0   # BUY: tokens received; SELL: USDC received
    # Exact atomic quantity of ``amount_out`` when the swap adapter reports it
    # (BUY: token atomic held in the spender -- lets a later SELL pass the exact
    # amount through; SELL: USDC atomic). ``None`` if the adapter didn't provide
    # it. Never guessed from ``amount_out`` (the token's decimals aren't known
    # here).
    amount_out_atomic: int | None = None
    # True if a step failed and left funds somewhere they must be RECOVERED from
    # (a swap failed after a BUY pulled USDC -> USDC sits in the spender; or a
    # SELL's return-transfer failed -> USDC sits in the spender). NOT stolen (the
    # Policy only lets the spender reach the router or transfer back to
    # aria-smart-st -- bounded loss under compromise, never zero), but misplaced
    # and needing a recovery transfer. Surfaced loudly, never silently swallowed.
    # A SELL whose swap itself fails is NOT "stranded": the token simply stays in
    # the spender where the prior BUY already deliberately left it (VOLET 2a) --
    # the position is still open, not newly misplaced.
    funds_stranded: bool = False


def _trading_blocked_reason() -> str | None:
    """The single security-critical "is a swing trade allowed RIGHT NOW"
    checkpoint, shared by BOTH the buy and the sell primitives so the two
    real-money paths can NEVER drift apart on it: gate OFF (fail-closed) ->
    global ``/stop`` kill-switch (strict) -> dedicated LOSS circuit breaker
    (armed, or corrupted state = fail-closed). Returns a block reason, or
    ``None`` if clear. Defense-in-depth: enforced in the primitives themselves,
    so a swap can never execute while blocked even if a future orchestration
    cycle forgets to check first."""
    if not smart_swing_enabled():
        return "ARIA_SMART_SWING_ENABLED désactivé (fail-closed par défaut)"
    swaps_blocked, block_reason = blocks_swing_swaps()
    if swaps_blocked:
        return block_reason or "swaps swing bloqués (coupe-circuit/kill-switch)"
    return None


async def _resolve_real_balance(balance_fn: BalanceFn) -> tuple[float | None, str | None]:
    """Fail-closed real-balance resolution shared by both primitives: returns
    ``(balance, None)`` on success, ``(None, reason)`` if the balance is
    unavailable (the fn raised, or returned ``None``). A real-money path never
    fails open on an unknown balance -- same doctrine for the buy (USDC balance
    of aria-smart-st) and the sell (the spender's balance of the held token)."""
    try:
        balance = await balance_fn()
    except Exception as exc:  # noqa: BLE001 -- any balance failure blocks, never fails open
        return None, f"solde réel indisponible (fail-closed) : {exc}"
    if balance is None:
        return None, "solde réel indisponible (fail-closed) : balance_fn a renvoyé None"
    return balance, None


async def execute_smart_swing_swap(
    *,
    token_out: str,
    amount_in_usd: float,
    balance_fn: BalanceFn,
    spend_pull_fn: SpendPullFn,
    swap_fn: SwapFn,
    token_in: str = USDC_BASE_ADDRESS,
    network: str = NETWORK,
    slippage_bps: int | None = None,
    wallet_product: str = WALLET_PRODUCT,
) -> SmartSwingSwapResult:
    """Guarded swing BUY (``USDC -> token_out``): pull USDC from aria-smart-st
    via the Spend Permission -> swap it for ``token_out``. The output token then
    deliberately STAYS in the spender (VOLET 2a, operator-approved temporal-hold
    redesign 07/25) -- there is NO auto-return to aria-smart-st.

    Why the token is left in the spender by design (this is a primitive, not an
    oversight): the Spend Permission can only ever pull USDC out of aria-smart-st
    (``token`` is hardcoded to USDC), so a token returned to aria-smart-st could
    NEVER be pulled back to be sold. Leaving the bought token in the spender is
    exactly what makes a later real SELL possible (``execute_smart_swing_sell``,
    which needs the spender to already hold the token) -- it closes the
    "no sell leg can exist" gap ``agent_wallet_positions`` documented. Between
    the buy and its eventual sell the token rests in the spender, protected by
    the CDP Policy (the spender may only reach the swap router or transfer back
    to aria-smart-st -- bounded, recoverable, never freely transferable out).

    Same 4 application guards as ``agent_wallet_pilot.attempt_swap`` (strict
    order): gate/kill-switch/breaker (``_trading_blocked_reason``) -> per-tx cap
    -> real-balance check -> forced slippage -> execution -> systematic logging.

    The two real CDP calls are INJECTED (no key/network here, same doctrine as
    the EOA pilot):
      - ``spend_pull_fn(value_atomic=, network=)`` -> ``{"tx_hash"}`` -- the
        spender's ``use_spend_permission(spend_permission=build_spend_permission_
        input(), value=<usdc atomic>, network=)`` (pulls USDC from aria-smart-st
        into the spender; needs NO Tangem tap once granted).
      - ``swap_fn(network=, token_in=, token_out=, amount_in_usd=,
        slippage_bps=)`` -> ``{"tx_hash", "amount_out", "amount_out_atomic"?}``
        -- the spender's ``swap(...)``. ``amount_out`` is the token quantity
        received (kept in the spender); ``amount_out_atomic`` (if provided) is
        the exact atomic quantity a later sell can pass straight through.

    ``slippage_bps`` is only accepted to flag a mismatch in the logs -- it is
    NEVER passed through as-is (always forced to ``MAX_SLIPPAGE_BPS``).
    ``wallet_product`` defaults to the real swing tag; the entry canary passes
    ``CANARY_WALLET_PRODUCT`` so probe round-trips stay out of the real-position
    P&L feed.

    If the swap fails AFTER the pull, ``funds_stranded`` is True and it is
    logged loudly: the pulled USDC sits in the spender and needs a recovery
    transfer back to aria-smart-st (bounded by the Policy). A SUCCESSFUL buy is
    never "stranded" -- the token resting in the spender is the intended open
    state, not a misplacement. No automatic recovery is attempted here (out of
    scope; a recovery flow is a separate, deliberate step)."""
    if slippage_bps is not None and slippage_bps != MAX_SLIPPAGE_BPS:
        logger.warning(
            "%s -- slippage_bps=%s ignored, forced to %s (absolute rule 09/07)",
            _REAL_MONEY_LOG_PREFIX, slippage_bps, MAX_SLIPPAGE_BPS,
        )

    blocked_reason = _trading_blocked_reason()
    if blocked_reason is not None:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason=blocked_reason, wallet_product=wallet_product,
        )

    if amount_in_usd <= 0:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network, reason="montant nul ou négatif",
            wallet_product=wallet_product,
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
            wallet_product=wallet_product,
        )

    balance_usd, balance_err = await _resolve_real_balance(balance_fn)
    if balance_err is not None:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network, reason=balance_err,
            wallet_product=wallet_product,
        )
    if amount_in_usd > balance_usd:
        return await _blocked_swap(
            token_in, token_out, amount_in_usd, network,
            reason=f"montant {amount_in_usd}$ > solde réel {balance_usd}$",
            wallet_product=wallet_product,
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
            wallet_product=wallet_product,
        )
    pull_tx = str(pull.get("tx_hash") or "")

    # ── Step 2: swap the pulled USDC for token_out (output STAYS in spender) ──
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
            wallet_product=wallet_product,
        )
    swap_tx = str(swap.get("tx_hash") or "")
    amount_out = float(swap.get("amount_out") or 0.0)
    amount_out_atomic = swap.get("amount_out_atomic")

    # No Step 3: the bought token intentionally rests in the spender (VOLET 2a),
    # ready to be sold later by execute_smart_swing_sell. Nothing is returned to
    # aria-smart-st on a buy.
    logger.info(
        "%s -- BUY SUCCEEDED: %s -> %s (%.2f$ -> %.6g held in spender), pull=%s swap=%s",
        _REAL_MONEY_LOG_PREFIX, token_in, token_out, amount_in_usd, amount_out,
        pull_tx, swap_tx,
    )
    await agent_wallet_log.record_transaction(
        wallet_product=wallet_product,
        chain=network,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in_usd,     # USD spent -> positions reads as cost_usd
        amount_out=amount_out,       # tokens received (now held in the spender)
        slippage_bps=MAX_SLIPPAGE_BPS,
        tx_hash=swap_tx,             # primary event; pull hash kept in reason
        to_address="",               # nothing returned on a buy (token held)
        status="ok",
        reason=f"pull={pull_tx} (token held in spender)",
    )
    return SmartSwingSwapResult(
        status="ok", pull_tx_hash=pull_tx, swap_tx_hash=swap_tx, amount_out=amount_out,
        amount_out_atomic=int(amount_out_atomic) if amount_out_atomic is not None else None,
    )


async def execute_smart_swing_sell(
    *,
    token_in: str,
    amount_in_tokens: float,
    value_in_usd: float,
    balance_fn: BalanceFn,
    swap_fn: SwapFn,
    return_transfer_fn: ReturnTransferFn,
    amount_in_tokens_atomic: int | None = None,
    token_out: str = USDC_BASE_ADDRESS,
    network: str = NETWORK,
    slippage_bps: int | None = None,
    wallet_product: str = WALLET_PRODUCT,
) -> SmartSwingSwapResult:
    """Guarded swing SELL (``token_in -> USDC``), the exit counterpart of the
    buy (VOLET 2b). The spender ALREADY holds ``token_in`` from a prior buy
    (VOLET 2a left it there), so there is NO pull step: swap the held token for
    USDC, then MANDATORILY return the USDC to aria-smart-st.

    Runs the SAME security checkpoint as the buy (``_trading_blocked_reason`` ->
    per-tx cap -> real-balance check -> forced slippage) in the SAME strict
    order -- factored, never copy-pasted, so the two real-money paths can't
    drift. The differences are only in WHAT the value-checks compare:
      - the per-tx cap is checked against ``value_in_usd`` (the estimated USD
        value of the position being sold) -- there is no USDC pull to bound, but
        a single sell is still capped at ``SPEND_PERMISSION_ALLOWANCE_USD`` for
        symmetry with the buy;
      - the real-balance check compares ``amount_in_tokens`` (the human token
        quantity to sell) against ``balance_fn()`` (the spender's real balance
        of ``token_in``, human units) -- you can never try to sell more of the
        token than the spender actually holds.

    Injected CDP calls:
      - ``swap_fn(network=, token_in=, token_out=, amount_in_tokens=,
        amount_in_tokens_atomic=, slippage_bps=)`` -> ``{"tx_hash",
        "amount_out", "amount_out_atomic"?}`` -- the spender's ``swap(...)`` of
        the held token; ``amount_out`` is the USDC received (lands in the
        spender). ``amount_in_tokens_atomic`` (optional, e.g. the buy's
        ``amount_out_atomic``) lets the adapter sell the exact atomic quantity
        without needing the token's decimals here.
      - ``return_transfer_fn(to_address=, token_address=, amount_out=,
        amount_out_atomic=, network=)`` -> ``{"tx_hash"}`` -- the spender's
        ``transfer`` of the USDC output back to aria-smart-st. Destination
        HARDCODED to ``SMART_ST_ADDRESS`` (defense-in-depth, mirrors the Policy
        carve-out), passed only for the injected fn's convenience.

    Stranding semantics (see ``SmartSwingSwapResult.funds_stranded``): a failed
    SELL swap is NOT stranded (the token stays in the spender where the buy left
    it -- the position is simply still open, a loud failure but nothing new to
    recover). A failed RETURN after a successful swap IS stranded (USDC sits in
    the spender, needs a recovery transfer to aria-smart-st)."""
    if slippage_bps is not None and slippage_bps != MAX_SLIPPAGE_BPS:
        logger.warning(
            "%s -- slippage_bps=%s ignored, forced to %s (absolute rule 09/07)",
            _REAL_MONEY_LOG_PREFIX, slippage_bps, MAX_SLIPPAGE_BPS,
        )

    blocked_reason = _trading_blocked_reason()
    if blocked_reason is not None:
        return await _blocked_swap(
            token_in, token_out, amount_in_tokens, network,
            reason=blocked_reason, wallet_product=wallet_product,
        )

    if amount_in_tokens <= 0:
        return await _blocked_swap(
            token_in, token_out, amount_in_tokens, network,
            reason="quantité de token à vendre nulle ou négative", wallet_product=wallet_product,
        )
    if value_in_usd <= 0:
        return await _blocked_swap(
            token_in, token_out, amount_in_tokens, network,
            reason="valeur USD estimée de la vente nulle ou négative "
                   "(impossible de borner un ordre non valorisé, fail-closed)",
            wallet_product=wallet_product,
        )
    if value_in_usd > SPEND_PERMISSION_ALLOWANCE_USD:
        return await _blocked_swap(
            token_in, token_out, amount_in_tokens, network,
            reason=f"valeur {value_in_usd}$ > plafond par transaction {SPEND_PERMISSION_ALLOWANCE_USD}$ "
                   "(même plafond dur que l'achat)",
            wallet_product=wallet_product,
        )

    token_balance, balance_err = await _resolve_real_balance(balance_fn)
    if balance_err is not None:
        return await _blocked_swap(
            token_in, token_out, amount_in_tokens, network, reason=balance_err,
            wallet_product=wallet_product,
        )
    if amount_in_tokens > token_balance:
        return await _blocked_swap(
            token_in, token_out, amount_in_tokens, network,
            reason=f"quantité {amount_in_tokens} > solde réel du token dans le spender {token_balance}",
            wallet_product=wallet_product,
        )

    # ── Step 1: swap the held token for USDC (no pull -- already held) ──
    try:
        swap = await swap_fn(
            network=network, token_in=token_in, token_out=token_out,
            amount_in_tokens=amount_in_tokens, amount_in_tokens_atomic=amount_in_tokens_atomic,
            slippage_bps=MAX_SLIPPAGE_BPS,
        )
    except Exception as exc:
        # Sell swap failed -- the token stays in the spender (position still
        # open, exactly where the buy left it). NOT newly stranded.
        return await _failed_swap(
            token_in, token_out, amount_in_tokens, network, stranded=False,
            reason=f"swap de vente échoué -- {token_in} reste dans le spender "
                   f"(position ouverte, bornée par la Policy) : {exc}",
            wallet_product=wallet_product,
        )
    swap_tx = str(swap.get("tx_hash") or "")
    amount_out = float(swap.get("amount_out") or 0.0)  # USDC received
    amount_out_atomic = swap.get("amount_out_atomic")

    # ── Step 2: MANDATORY return of the USDC output back to aria-smart-st ──
    try:
        ret = await return_transfer_fn(
            to_address=SMART_ST_ADDRESS, token_address=token_out,
            amount_out=amount_out, amount_out_atomic=amount_out_atomic, network=network,
        )
    except Exception as exc:
        # Sold but USDC NOT returned -- USDC stranded in the spender.
        return await _failed_swap(
            token_in, token_out, amount_in_tokens, network, stranded=True,
            swap_tx=swap_tx, amount_out=amount_out,
            reason=f"return-transfer (USDC) échoué APRÈS la vente -- USDC dans le spender, "
                   f"récupération vers aria-smart-st requise : {exc}",
            wallet_product=wallet_product,
        )
    return_tx = str(ret.get("tx_hash") or "")

    logger.info(
        "%s -- SELL SUCCEEDED: %s -> %s (%.6g tokens -> %.2f$ USDC), swap=%s return=%s",
        _REAL_MONEY_LOG_PREFIX, token_in, token_out, amount_in_tokens, amount_out,
        swap_tx, return_tx,
    )
    await agent_wallet_log.record_transaction(
        wallet_product=wallet_product,
        chain=network,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in_tokens,  # tokens sold -> positions reads as qty_sold
        amount_out=amount_out,       # USDC received -> positions reads as proceeds_usd
        slippage_bps=MAX_SLIPPAGE_BPS,
        tx_hash=swap_tx,             # primary event; return hash kept in reason
        to_address=SMART_ST_ADDRESS,  # USDC's final destination
        status="ok",
        reason=f"return={return_tx}",
    )
    return SmartSwingSwapResult(
        status="ok", swap_tx_hash=swap_tx, return_tx_hash=return_tx, amount_out=amount_out,
        amount_out_atomic=int(amount_out_atomic) if amount_out_atomic is not None else None,
    )


async def _blocked_swap(
    token_in: str, token_out: str, amount_in: float, network: str, *, reason: str,
    wallet_product: str = WALLET_PRODUCT,
) -> SmartSwingSwapResult:
    """Log + return a blocked attempt. ``amount_in`` is the input amount of the
    attempted swap (USD for a buy, token quantity for a sell -- exactly what the
    journal's ``amount_in`` column means for that leg)."""
    logger.warning("%s -- swap blocked: %s", _REAL_MONEY_LOG_PREFIX, reason)
    await agent_wallet_log.record_transaction(
        wallet_product=wallet_product,
        chain=network,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        slippage_bps=MAX_SLIPPAGE_BPS,
        status="blocked",
        reason=reason,
    )
    return SmartSwingSwapResult(status="blocked", reason=reason)


async def _failed_swap(
    token_in: str, token_out: str, amount_in: float, network: str, *,
    stranded: bool, reason: str, wallet_product: str = WALLET_PRODUCT,
    pull_tx: str = "", swap_tx: str = "", amount_out: float = 0.0,
) -> SmartSwingSwapResult:
    """Log + return a failed attempt. ``stranded`` -> funds sit in the spender
    and need a recovery transfer to aria-smart-st (surfaced via ``to_address``);
    otherwise nothing new is misplaced."""
    log_fn = logger.error if stranded else logger.warning
    log_fn("%s -- swap failed (stranded=%s): %s", _REAL_MONEY_LOG_PREFIX, stranded, reason)
    await agent_wallet_log.record_transaction(
        wallet_product=wallet_product,
        chain=network,
        action_type="swap",
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
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


# ── VOLET 3: dual-quote round-trip pre-check (pure, non-executing) ───────────


@dataclass(frozen=True)
class SwapPrecheckResult:
    accepted: bool
    reason: str = ""
    # Decision metric: implied round-trip loss from the quotes' EXPECTED amounts.
    round_trip_loss_bps: float = 0.0
    # Informational only (never drives the verdict): the guaranteed-minimum
    # round trip, using each quote's slippage-bounded ``min_to_amount``.
    worst_case_loss_bps: float = 0.0
    buy_out_atomic: int = 0   # expected token atomic received on the buy quote
    sell_out_atomic: int = 0  # expected USDC atomic received on the sell quote


def _quote_field(quote: Any, name: str) -> Any:
    """Read a field off an injected quote result that may be a pydantic
    ``QuoteSwapResult``/``SwapUnavailableResult`` OR a plain dict (test seam)."""
    if quote is None:
        return None
    if isinstance(quote, dict):
        return quote.get(name)
    return getattr(quote, name, None)


def _quote_amount_atomic(quote: Any, name: str) -> int | None:
    """A quote amount (``to_amount``/``min_to_amount``) is a decimal STRING of
    atomic units in the CDP SDK -- parse it to int, ``None`` if absent/garbage."""
    raw = _quote_field(quote, name)
    if raw is None:
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


async def _fetch_quote(
    quote_fn: QuoteFn, *, from_token: str, to_token: str, from_amount_atomic: int, network: str,
) -> tuple[Any | None, str | None]:
    """Fetch one swap quote, fail-closed. ``(quote, None)`` on a usable quote,
    ``(None, reason)`` on any error or an unavailable quote. Forces
    ``MAX_SLIPPAGE_BPS`` (same 10% discipline as every real leg here)."""
    try:
        quote = await quote_fn(
            from_token=from_token, to_token=to_token,
            from_amount_atomic=from_amount_atomic, network=network,
            slippage_bps=MAX_SLIPPAGE_BPS,
        )
    except Exception as exc:  # noqa: BLE001 -- any quote failure blocks, never fails open
        return None, str(exc)
    if quote is None:
        return None, "devis vide (None)"
    # SwapUnavailableResult carries liquidity_available=False and no to_amount.
    if _quote_field(quote, "liquidity_available") is False:
        return None, "liquidité insuffisante (SwapUnavailableResult)"
    return quote, None


async def precheck_swap_roundtrip(
    *,
    token: str,
    notional_usd: float,
    quote_fn: QuoteFn,
    max_roundtrip_loss_bps: int = MAX_SLIPPAGE_BPS,
    network: str = NETWORK,
) -> SwapPrecheckResult:
    """Pure, NON-executing first-pass filter (VOLET 3): fetch a BUY quote
    (USDC -> token) and a SELL quote (token -> USDC) for the SAME notional and
    measure the implied round-trip cost from the quotes ALONE -- how much value
    a buy-then-immediately-sell would lose per the quoted prices. No state
    change, no funds moved.

    What it CATCHES: price/liquidity problems -- a thin pool, a lopsided price,
    a token quoted so poorly a round trip already bleeds past the ceiling. What
    it does NOT catch: an ACTIVE honeypot / hidden sell-tax that only trips on a
    real state-changing transaction (a quote is a read-only price estimate; a
    scam contract can quote a fair price and still tax or deny the real sell).
    That real behaviour is what VOLET 4's executed micro-canary measures -- this
    is a cheap first pass to run BEFORE paying for a canary, never a replacement
    for it.

    Measurement: the verdict uses the quotes' EXPECTED amounts (``to_amount``),
    NOT the slippage-tolerance-bounded ``min_to_amount`` -- deliberately, so it
    reflects real fees + price impact and is NOT inflated by the two legs' 10%
    tolerances stacking (which would false-reject a legit token; this is the
    precise handling of "unless the two quotes' own individual slippage already
    exceeds it"). The guaranteed-minimum round trip (from ``min_to_amount``) is
    reported as ``worst_case_loss_bps`` for transparency but never drives the
    accept/reject.

    Verdict: ACCEPT iff ``round_trip_loss_bps <= max_roundtrip_loss_bps``
    (default ``MAX_SLIPPAGE_BPS`` = the same 10% ceiling used elsewhere here).

    Fail-closed (never fail-open on a real-money path): any quote fetch error,
    an unavailable quote (``liquidity_available`` False / SwapUnavailableResult),
    a missing/zero/garbage amount, or a non-positive notional -> REJECT with a
    clear reason. The injected ``quote_fn`` mirrors ``cdp.evm.create_swap_quote``
    (verified signature, cdp-sdk 1.47.1); a caller binds it, e.g.::

        async def quote_fn(*, from_token, to_token, from_amount_atomic, network, slippage_bps):
            return await cdp.evm.create_swap_quote(
                from_token=from_token, to_token=to_token,
                from_amount=str(from_amount_atomic), network=network,
                taker=SPENDER_ADDRESS, slippage_bps=slippage_bps,
                signer_address=<smart-account owner>,
            )
    """
    if notional_usd <= 0:
        return SwapPrecheckResult(accepted=False, reason="notional nul ou négatif (fail-closed)")

    notional_atomic = usd_to_atomic_usdc(notional_usd)

    buy_quote, buy_err = await _fetch_quote(
        quote_fn, from_token=USDC_BASE_ADDRESS, to_token=token,
        from_amount_atomic=notional_atomic, network=network,
    )
    if buy_err is not None:
        return SwapPrecheckResult(accepted=False, reason=f"devis d'achat indisponible : {buy_err}")
    buy_out_atomic = _quote_amount_atomic(buy_quote, "to_amount")
    if not buy_out_atomic or buy_out_atomic <= 0:
        return SwapPrecheckResult(
            accepted=False,
            reason="devis d'achat sans montant de sortie exploitable (fail-closed)",
        )
    buy_min_atomic = _quote_amount_atomic(buy_quote, "min_to_amount") or buy_out_atomic

    sell_quote, sell_err = await _fetch_quote(
        quote_fn, from_token=token, to_token=USDC_BASE_ADDRESS,
        from_amount_atomic=buy_out_atomic, network=network,
    )
    if sell_err is not None:
        return SwapPrecheckResult(accepted=False, reason=f"devis de vente indisponible : {sell_err}")
    sell_out_atomic = _quote_amount_atomic(sell_quote, "to_amount")
    if not sell_out_atomic or sell_out_atomic <= 0:
        return SwapPrecheckResult(
            accepted=False,
            reason="devis de vente sans montant de sortie exploitable (fail-closed)",
        )
    sell_min_atomic = _quote_amount_atomic(sell_quote, "min_to_amount") or sell_out_atomic

    scale = float(10 ** _USDC_DECIMALS)
    recovered_usd = sell_out_atomic / scale
    round_trip_loss_bps = (notional_usd - recovered_usd) / notional_usd * 10_000.0
    # Approximate worst case: the guaranteed-minimum USDC out of the sell quote
    # (informational, ignores that the buy could also yield only its min).
    worst_recovered_usd = sell_min_atomic / scale
    worst_case_loss_bps = (notional_usd - worst_recovered_usd) / notional_usd * 10_000.0

    accepted = round_trip_loss_bps <= max_roundtrip_loss_bps
    if accepted:
        reason = (
            f"round-trip devis {round_trip_loss_bps:.0f} bps <= plafond {max_roundtrip_loss_bps} bps "
            f"(dépense {notional_usd:.2f}$, récupération estimée {recovered_usd:.2f}$)"
        )
    else:
        reason = (
            f"round-trip devis {round_trip_loss_bps:.0f} bps > plafond {max_roundtrip_loss_bps} bps "
            f"-- prix/liquidité défavorable (dépense {notional_usd:.2f}$, récupération estimée "
            f"{recovered_usd:.2f}$) ; NB : ne détecte pas un honeypot actif, cf. canary VOLET 4"
        )
    return SwapPrecheckResult(
        accepted=accepted, reason=reason,
        round_trip_loss_bps=round_trip_loss_bps, worst_case_loss_bps=worst_case_loss_bps,
        buy_out_atomic=int(buy_out_atomic), sell_out_atomic=int(sell_out_atomic),
    )


# ── VOLET 4: executed micro-canary buy+sell before committing full size ──────


@dataclass(frozen=True)
class CanaryResult:
    passed: bool
    reason: str = ""
    amount_spent_usd: float = 0.0      # USDC put in on the canary buy
    amount_recovered_usd: float = 0.0  # USDC recovered on the canary sell
    round_trip_loss_bps: float = 0.0   # the REAL measured combined buy+sell tax
    # A mid-flight failure left a (small, canary-sized) amount in the spender to
    # recover -- bounded by the Policy, tiny by construction.
    funds_stranded: bool = False
    buy_result: SmartSwingSwapResult | None = None
    sell_result: SmartSwingSwapResult | None = None


async def run_swing_entry_canary(
    *,
    token: str,
    balance_fn: BalanceFn,
    spend_pull_fn: SpendPullFn,
    buy_swap_fn: SwapFn,
    token_balance_fn: BalanceFn,
    sell_swap_fn: SwapFn,
    return_transfer_fn: ReturnTransferFn,
    canary_amount_usd: float = CANARY_TEST_AMOUNT_USD,
    max_roundtrip_loss_bps: int = CANARY_MAX_ROUNDTRIP_LOSS_BPS,
    network: str = NETWORK,
) -> CanaryResult:
    """Executed micro-canary (VOLET 4): the REAL buy+sell tax probe that MUST
    pass before any full-size entry -- so the full amount is never committed to
    a scam token that only reveals itself on a real transaction (operator's
    07/25 correction: "éviter l'achat", not merely detect after the fact).

    Steps:
      1. REAL buy of ``canary_amount_usd`` of ``token`` via the VOLET-2a buy
         primitive, logged under ``CANARY_WALLET_PRODUCT`` so the probe never
         counts as a real position P&L (every canary loses the round-trip tax
         by design -- a run of them must never trip the loss breaker). The token
         is left in the spender (VOLET 2a).
      2. REAL sell of exactly what was received via the VOLET-2b sell primitive
         (same ``CANARY_WALLET_PRODUCT``, exact atomic quantity threaded from
         the buy when the adapter reported it).
      3. Compare USDC spent vs USDC recovered = the REAL combined buy+sell tax --
         more trustworthy than GoPlus's self-reported ``buy_tax``/``sell_tax``,
         which a well-configured scam can misreport.
      4. REFUSE (``passed=False``, loud/logged) if the sell fails/blocks outright
         (a token you can buy but not sell is the exact honeypot signature) OR
         the measured round-trip loss exceeds ``max_roundtrip_loss_bps``.
      5. Only on a clean canary (``passed=True``) may the caller proceed to buy
         the FULL position size via ``execute_smart_swing_swap`` (full amount,
         token stays in the spender, normal open-position lifecycle from there).

    THERE IS NO full-size buy here and NO heartbeat wiring (deliberately out of
    scope -- not asked for): a future "open a swing position" orchestrator MUST
    call this first and gate the full-size ``execute_smart_swing_swap`` on
    ``result.passed``. This file exposes the canary standalone precisely because
    no single "open a position" entry point exists yet.

    Seams mirror the two primitives' needs: ``balance_fn`` = USDC balance of
    aria-smart-st (buy), ``token_balance_fn`` = the spender's balance of
    ``token`` (sell), ``buy_swap_fn``/``sell_swap_fn`` = the two swap adapters
    (a buy and a sell are distinct CDP swap calls), ``spend_pull_fn`` /
    ``return_transfer_fn`` as in the primitives. A FAILED canary can leave a
    tiny (``canary_amount_usd``) token or USDC amount in the spender -- surfaced
    via ``funds_stranded``, bounded by the Policy, recoverable-only-to-aria-smart-st."""
    buy = await execute_smart_swing_swap(
        token_out=token, amount_in_usd=canary_amount_usd, balance_fn=balance_fn,
        spend_pull_fn=spend_pull_fn, swap_fn=buy_swap_fn, network=network,
        wallet_product=CANARY_WALLET_PRODUCT,
    )
    if buy.status != "ok":
        reason = f"canary REFUSÉ -- l'achat test a échoué/bloqué : {buy.reason}"
        logger.warning("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
        return CanaryResult(
            passed=False, reason=reason, amount_spent_usd=canary_amount_usd,
            funds_stranded=buy.funds_stranded, buy_result=buy,
        )
    tokens_bought = buy.amount_out
    if tokens_bought <= 0:
        reason = "canary REFUSÉ -- achat test 'réussi' mais 0 token reçu (fail-closed)"
        logger.warning("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
        return CanaryResult(
            passed=False, reason=reason, amount_spent_usd=canary_amount_usd, buy_result=buy,
        )

    sell = await execute_smart_swing_sell(
        token_in=token, amount_in_tokens=tokens_bought, value_in_usd=canary_amount_usd,
        balance_fn=token_balance_fn, swap_fn=sell_swap_fn,
        return_transfer_fn=return_transfer_fn, amount_in_tokens_atomic=buy.amount_out_atomic,
        network=network, wallet_product=CANARY_WALLET_PRODUCT,
    )
    if sell.status != "ok":
        # A token you can BUY but not SELL is the exact honeypot signature.
        reason = f"canary REFUSÉ -- vente test échouée/bloquée (signature honeypot) : {sell.reason}"
        logger.error("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
        return CanaryResult(
            passed=False, reason=reason, amount_spent_usd=canary_amount_usd,
            funds_stranded=sell.funds_stranded, buy_result=buy, sell_result=sell,
        )

    recovered_usd = sell.amount_out
    round_trip_loss_bps = (canary_amount_usd - recovered_usd) / canary_amount_usd * 10_000.0
    if round_trip_loss_bps > max_roundtrip_loss_bps:
        reason = (
            f"canary REFUSÉ -- taxe aller-retour RÉELLE {round_trip_loss_bps:.0f} bps > plafond "
            f"{max_roundtrip_loss_bps} bps (dépensé {canary_amount_usd:.2f}$, récupéré "
            f"{recovered_usd:.2f}$) -- token probablement à taxe cachée / honeypot mou"
        )
        logger.warning("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
        return CanaryResult(
            passed=False, reason=reason, amount_spent_usd=canary_amount_usd,
            amount_recovered_usd=recovered_usd, round_trip_loss_bps=round_trip_loss_bps,
            buy_result=buy, sell_result=sell,
        )

    reason = (
        f"canary OK -- taxe aller-retour RÉELLE {round_trip_loss_bps:.0f} bps <= plafond "
        f"{max_roundtrip_loss_bps} bps (dépensé {canary_amount_usd:.2f}$, récupéré {recovered_usd:.2f}$)"
    )
    logger.info("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
    return CanaryResult(
        passed=True, reason=reason, amount_spent_usd=canary_amount_usd,
        amount_recovered_usd=recovered_usd, round_trip_loss_bps=round_trip_loss_bps,
        buy_result=buy, sell_result=sell,
    )


# ── Guarded swing entry: precheck -> canary -> full-size buy (VOLET 4 wiring) ─
#
# The consumer the adversarial audit flagged as missing: the canary only
# PRODUCES a verdict, so a full-size real entry must be GATED on it by an actual
# caller -- otherwise the verdict is inert. This orchestrator is that gate. It
# chains the three already-tested primitives and aborts the instant any stage
# refuses, so the full-size real buy is reached ONLY after both the free
# precheck AND the executed micro-canary have passed.
#
# Pure orchestration: it threads the SAME low-level seams the three primitives
# already use, adds NO new network/CDP call, and does NOT re-implement any of
# the 5 application guards (gate/kill-switch/breaker/balance/slippage) -- each
# composed primitive enforces those internally (defense-in-depth). Its ONLY new
# logic is the abort-early sequencing + a single structured result saying WHERE
# it stopped and why. Sizing is deliberately NOT decided here (see the required
# ``amount_in_usd``), and it has NO caller yet: "who decides to open a swing
# position, with what size/thesis" is a separate, larger chantier this
# orchestrator has no opinion on and is not wired to.

# Stage markers for ``GuardedSwingEntryResult.stage`` -- WHERE the guarded entry
# ended (an early abort stops at "precheck"/"canary"; a reached buy is "buy",
# whether it then opened or itself failed).
ENTRY_STAGE_PRECHECK = "precheck"
ENTRY_STAGE_CANARY = "canary"
ENTRY_STAGE_BUY = "buy"

# Composed-primitive seams. Default to the real module primitives; overridable
# ONLY so a unit test can inject call-trackers proving the abort-early
# sequencing (e.g. the full-size buy is NEVER reached on a failed canary). Never
# rebound in production -- the real money flows through the low-level seams
# (balance_fn/spend_pull_fn/... below), exactly as the primitives use them.
PrecheckFn = Callable[..., Awaitable[SwapPrecheckResult]]
CanaryFn = Callable[..., Awaitable[CanaryResult]]
FullSizeBuyFn = Callable[..., Awaitable[SmartSwingSwapResult]]


@dataclass(frozen=True)
class GuardedSwingEntryResult:
    # True ONLY when the full-size buy actually opened the position. Any early
    # abort (precheck reject / canary fail) or a full-size-buy failure/block
    # leaves this False -- never a silent "maybe".
    opened: bool
    # WHERE the guarded entry ended: it aborted at "precheck" or "canary", or it
    # reached "buy" (which then either opened -- opened=True -- or itself
    # failed/blocked -- opened=False).
    stage: str
    reason: str = ""
    # The underlying stage results, in run order, for inspection/logging --
    # whichever stages actually ran (a precheck reject carries only
    # precheck_result; a canary fail carries precheck+canary; a reached buy
    # carries all three). Never swallowed: this is what an operator or a future
    # review reads to understand a refusal.
    precheck_result: SwapPrecheckResult | None = None
    canary_result: CanaryResult | None = None
    buy_result: SmartSwingSwapResult | None = None


async def execute_guarded_swing_entry(
    *,
    token: str,
    amount_in_usd: float,
    quote_fn: QuoteFn,
    balance_fn: BalanceFn,
    spend_pull_fn: SpendPullFn,
    buy_swap_fn: SwapFn,
    token_balance_fn: BalanceFn,
    sell_swap_fn: SwapFn,
    return_transfer_fn: ReturnTransferFn,
    canary_amount_usd: float = CANARY_TEST_AMOUNT_USD,
    canary_max_roundtrip_loss_bps: int = CANARY_MAX_ROUNDTRIP_LOSS_BPS,
    precheck_max_roundtrip_loss_bps: int = MAX_SLIPPAGE_BPS,
    network: str = NETWORK,
    precheck_fn: PrecheckFn = precheck_swap_roundtrip,
    canary_fn: CanaryFn = run_swing_entry_canary,
    full_size_buy_fn: FullSizeBuyFn = execute_smart_swing_swap,
) -> GuardedSwingEntryResult:
    """Guarded full-size swing entry: precheck (free) -> canary (real ~3$) ->
    full-size buy (real), aborting the instant any stage refuses -- so real
    money is NEVER committed at full size to a token that failed the cheap
    price/liquidity precheck OR the executed honeypot/round-trip-tax canary.
    This turns the canary's verdict into an actual pre-arming requirement (the
    gap the adversarial audit flagged: the canary only produced a verdict, with
    no consumer that gated on it).

    Order & abort-early contract (never proceeds past a refusal):
      1. PRECHECK (free, non-executing): ``precheck_swap_roundtrip`` on the SAME
         ``amount_in_usd`` we intend to buy -- price impact is size-dependent, so
         the precheck reflects the REAL intended size, not the canary size.
         Rejected -> return immediately (stage="precheck"); the canary is NOT
         run, and NO spend permission / balance / real call is touched.
      2. CANARY (real, ``canary_amount_usd`` ~3$): ``run_swing_entry_canary``.
         ``passed`` False for ANY reason (buy blocked, buy-swap failure/stranded,
         0 tokens received, sell failed/blocked = the honeypot signature,
         measured round-trip tax too high) -> return immediately (stage="canary")
         with the canary attached; the full-size buy is NEVER called.
      3. FULL-SIZE BUY (real): ``execute_smart_swing_swap`` with the caller's
         ``amount_in_usd`` passed straight through -- NEVER resized (sizing is not
         this orchestrator's decision). The bought token stays in the spender by
         design (normal open-position lifecycle); this orchestrator's job ends
         once the position is open.

    Pure orchestration: threads the SAME low-level seams the three primitives
    already use (``quote_fn`` for the precheck; ``balance_fn``/``spend_pull_fn``/
    ``buy_swap_fn``/``token_balance_fn``/``sell_swap_fn``/``return_transfer_fn``
    for the canary; ``balance_fn``/``spend_pull_fn``/``buy_swap_fn`` for the
    full-size buy). It adds NO new network call and does NOT re-implement the 5
    application guards -- each primitive enforces them internally (so a gate-off
    run, for instance, is caught by the canary's own buy and reported as a canary
    refusal, never bypassed here). The full-size buy logs under the real
    ``WALLET_PRODUCT`` while the canary's legs log under ``CANARY_WALLET_PRODUCT``
    (kept out of the real-position feed) -- both inherited unchanged from the
    primitives.

    ``amount_in_usd`` is REQUIRED (this orchestrator does not decide sizing).
    ``precheck_fn``/``canary_fn``/``full_size_buy_fn`` default to the real module
    primitives and exist only as composition seams for isolated unit testing of
    the sequencing (call-tracking) -- never rebound in production. Returns a
    single ``GuardedSwingEntryResult`` that says WHERE it stopped, why, and
    carries every stage result that ran (never swallowed)."""
    # ── Stage 1: PRECHECK (free, non-executing) ──
    precheck = await precheck_fn(
        token=token, notional_usd=amount_in_usd, quote_fn=quote_fn,
        max_roundtrip_loss_bps=precheck_max_roundtrip_loss_bps, network=network,
    )
    if not precheck.accepted:
        reason = f"entrée refusée au PRECHECK (aucun fonds engagé, canary non lancé) : {precheck.reason}"
        logger.info("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
        return GuardedSwingEntryResult(
            opened=False, stage=ENTRY_STAGE_PRECHECK, reason=reason, precheck_result=precheck,
        )

    # ── Stage 2: CANARY (real micro buy+sell tax probe) ──
    canary = await canary_fn(
        token=token, balance_fn=balance_fn, spend_pull_fn=spend_pull_fn,
        buy_swap_fn=buy_swap_fn, token_balance_fn=token_balance_fn,
        sell_swap_fn=sell_swap_fn, return_transfer_fn=return_transfer_fn,
        canary_amount_usd=canary_amount_usd,
        max_roundtrip_loss_bps=canary_max_roundtrip_loss_bps, network=network,
    )
    if not canary.passed:
        reason = f"entrée refusée au CANARY (achat pleine taille NON exécuté) : {canary.reason}"
        logger.warning("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
        return GuardedSwingEntryResult(
            opened=False, stage=ENTRY_STAGE_CANARY, reason=reason,
            precheck_result=precheck, canary_result=canary,
        )

    # ── Stage 3: FULL-SIZE BUY (real, caller's amount, NEVER resized) ──
    buy = await full_size_buy_fn(
        token_out=token, amount_in_usd=amount_in_usd, balance_fn=balance_fn,
        spend_pull_fn=spend_pull_fn, swap_fn=buy_swap_fn, network=network,
    )
    opened = buy.status == "ok"
    if opened:
        reason = (
            f"position swing OUVERTE pleine taille {amount_in_usd:.2f}$ sur {token} "
            f"(precheck OK + canary taxe réelle {canary.round_trip_loss_bps:.0f} bps)"
        )
        logger.info("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
    else:
        reason = (
            f"precheck + canary passés mais l'achat pleine taille a échoué (statut={buy.status}) : "
            f"{buy.reason}"
        )
        logger.error("%s -- %s", _REAL_MONEY_LOG_PREFIX, reason)
    return GuardedSwingEntryResult(
        opened=opened, stage=ENTRY_STAGE_BUY, reason=reason,
        precheck_result=precheck, canary_result=canary, buy_result=buy,
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
    ("money" doctrine, strict=True). Item #62 (08/03): also checks the
    dedicated ``custody_pause`` auto-arm flag -- either one blocks."""
    if outgoing_pause.is_paused(strict=True):
        return True, outgoing_pause.blocked_notice("Ce swap smart-swing")
    if custody_pause.is_paused():
        return True, custody_pause.blocked_notice("Ce swap smart-swing")

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
