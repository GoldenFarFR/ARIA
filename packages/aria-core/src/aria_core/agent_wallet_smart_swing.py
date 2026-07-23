"""Autonomous swing-pocket execution via a delegated spender (Smart Account
migration, Model B chosen by the operator 07/23).

Context and doctrine (see docs/HANDOFF_COINBASE_CDP.md for the full state):
the swing pocket lives in the Smart Account ``aria-smart-st`` (owner = the
operator's Tangem hardware wallet). ARIA must be able to SWAP that pocket
autonomously (no Tangem tap per trade) while NEVER being able to move funds
out to an arbitrary address. This is achieved by a delegated ``spender`` EOA
(``aria-spender-smart-st``, CDP-managed key, no Tangem) plus THREE stacked
safety layers:

  1. CDP Policy on the spender (contract-enforced by CDP's signing layer):
     allowlist ONLY a swap-router call and a return-transfer back to
     ``aria-smart-st`` -- rejects everything else, including a raw transfer
     to any other address. Built + validated in a later step (see the
     module-level note on ``build_swap_only_policy`` -- deliberately NOT in
     this module yet: its real enforcement must be verified against live CDP
     on a tiny amount before any grant is trusted).
  2. Spend Permission on ``aria-smart-st`` (contract-enforced): a HARD numeric
     cap on how much USDC the spender can ever pull per period. This is the
     guard against a sizing bug that would otherwise try to swap 100% of the
     wallet. The operator's explicit decision (07/23): $50 per week,
     auto-renewing -- deliberately NOT "unlimited" (unlimited would defeat the
     whole safety net, since neither the Policy nor the Spend Permission gates
     on amount otherwise). ``build_spend_permission_input`` below encodes this
     cap as a structural invariant.
  3. Application-layer guards (same doctrine as the proven
     ``agent_wallet_pilot.py`` EOA pilot): per-transaction cap, real-balance
     check before every attempt, slippage forced to 10%, ``/stop``
     kill-switch, systematic logging. Built with the execution wiring in the
     next step.

Everything here is DORMANT: no gate is on, nothing is wired to any heartbeat
cycle, no real grant/policy is created by importing this module. The one-time
Spend Permission grant (a Tangem tap on ``aria-smart-st``), the Policy
creation + live validation, funding, and the end-to-end test on a tiny real
amount are all deliberately sequenced operator/hardware steps, never done
autonomously.

All CDP SDK types/addresses below were verified against the really-installed
cdp-sdk 1.47.1 (never guessed) -- see the HANDOFF entry dated 07/23."""
from __future__ import annotations

import logging
import os

from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

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

# ── Spend Permission cap -- the operator's explicit 07/23 decision ───────────
# The autonomous swing pocket can pull at most this much USDC per period,
# auto-renewing (no new Tangem tap on renewal). "Prudent to start, scale once
# proven in real conditions" -- raising it later is a deliberate operator
# decision, NEVER a silent code change (mirrors the project's guard-rail
# doctrine; enforced by _MAX_SANE_ALLOWANCE_USD below).
SPEND_PERMISSION_ALLOWANCE_USD = 50.0
SPEND_PERMISSION_PERIOD_DAYS = 7

# Structural guard: this builder must NEVER be able to produce an "unlimited"
# (or absurdly large) allowance, even from a future careless edit -- that would
# silently remove safety layer #2. A real increase is an operator decision made
# by editing SPEND_PERMISSION_ALLOWANCE_USD *and* this ceiling deliberately.
_MAX_SANE_ALLOWANCE_USD = 10_000.0

_USDC_DECIMALS = 6  # USDC = 6 decimals on Base (atomic units).

# Dedicated gate for the autonomous swing execution, OFF by default,
# fail-closed -- same doctrine as ARIA_AGENT_WALLET_PILOT_ENABLED. Gates the
# EXECUTION path only (built in the next step); the pure builders below never
# execute anything, so they are not gated.
_SMART_SWING_GATE = "ARIA_SMART_SWING_ENABLED"


def smart_swing_enabled() -> bool:
    """Dedicated gate, OFF by default (fail-closed). Gates the autonomous
    swing EXECUTION path (not yet built) -- never any real spend until this is
    explicitly on AND the Spend Permission has been granted (Tangem tap) AND
    the Policy has been validated against live CDP."""
    return os.environ.get(_SMART_SWING_GATE, "").strip().lower() in ("1", "true", "yes", "on")


def usd_to_atomic_usdc(amount_usd: float) -> int:
    """USD amount -> USDC atomic units (6 decimals). Integer by construction --
    the CDP SpendPermissionInput.allowance is an integer of smallest units
    (same convention as the ``parse_units`` used for swaps/transfers in
    agent_wallet_cdp_adapter)."""
    return int(round(amount_usd * (10 ** _USDC_DECIMALS)))


def build_spend_permission_input(
    *,
    allowance_usd: float = SPEND_PERMISSION_ALLOWANCE_USD,
    period_days: int = SPEND_PERMISSION_PERIOD_DAYS,
):
    """Builds the ``SpendPermissionInput`` for the ONE-TIME grant that lets the
    spender pull USDC from ``aria-smart-st`` (safety layer #2). Pure: builds a
    config object, executes nothing, touches no network -- the actual grant
    (``cdp.evm.create_spend_permission`` + the Tangem owner's signature) is a
    separate, hardware-gated step.

    Hard structural guards (this is a real-capital safety envelope, never a
    convenience helper):
      - ``allowance_usd`` must be strictly positive and at most
        ``_MAX_SANE_ALLOWANCE_USD`` -- makes "unlimited"/absurd allowances
        impossible to produce here, even from a careless future edit (safety
        layer #2 would otherwise silently vanish).
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
