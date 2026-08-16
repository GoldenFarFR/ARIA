"""DORMANT CDP Policy DEFINITION for the REAL agent-wallet pilot EOA.

Context: `agent_wallet_pilot.py` enforces two real-capital bounds -- the hard
per-transaction cap (`MAX_TRANSACTION_USD`) and the single authorized transfer
destination (`ALLOWED_TRANSFER_ADDRESS`) -- but BOTH live purely in this Python
application layer. Nothing on Coinbase's side enforces them: a code path that
skipped `attempt_swap`/`attempt_transfer` and called
`agent_wallet_cdp_adapter.execute_swap`/`transfer_usdc` directly would be signed
by CDP without objection. This module builds the CDP-side counterpart (Policy
Engine, enforced by Coinbase's own signing infrastructure BEFORE a signature is
ever produced) so the same two bounds also exist outside ARIA's own code.

**Nothing here is applied.** `build_pilot_bounded_policy()` is pure: it builds a
`CreatePolicyOptions` object and touches no network. `create_pilot_bounded_policy()`
exists but takes an INJECTED `policies_client` (never constructs a `CdpClient`
itself) and refuses to run without an explicit `operator_ack=True` -- the real
creation on the real CDP account is a separate, explicitly-authorized operator
step, never taken autonomously. No production module imports this file (a
dedicated test asserts it). Same doctrine as `agent_wallet_smart_swing.py`'s own
`build_swap_only_policy` (built 07/23-07/25, still dormant, gate OFF): that one
covers the SMART ACCOUNT swing spender, this one covers the EOA pilot -- two
distinct wallets, two distinct policies, never conflated.

Scope: what this Policy protects / does NOT protect
---------------------------------------------------
PROTECTS against a compromise of the API KEY or of the CODE: a stolen/leaked
`.env`, a compromised VPS, a rogue or buggy code path calling the CDP adapter
directly, a future refactor that forgets the application-layer guards. In all
those cases the caller holds only CDP API credentials, and the Policy Engine
rejects anything outside the two allowlisted shapes before signing.

Does **NOT** protect against a compromise of the operator's COINBASE ACCOUNT
(the CDP web dashboard login and its own 2FA). From the dashboard an attacker
can recreate an API key with "Export" enabled, edit or delete this very Policy,
or export the wallet's private key outright -- which is precisely how the
operator performed a legitimate export on 29/07 (documented in
`docs/HANDOFF_COINBASE_CDP.md`: not via an API key, via the dashboard), and an
exported EOA private key bypasses every CDP-side control permanently, since the
chain never checks who produced a valid signature. That vector's mitigation is
account hygiene (Coinbase 2FA, operator discipline), a human subject -- no code
in this repo can close it. Stated explicitly so this Policy is never mistaken
for a total protection.

Two allowlisted rules (top-down first-match, everything unmatched is
default-denied by the CDP Policy Engine -- that default-deny is the actual
guardrail)
---------------------------------------------------
1. ALLOW `sendEvmTransaction` whose destination is one of the caller-supplied
   swap routers AND whose net USD moved is at most the per-transaction cap.
2. ALLOW an ERC-20 `transfer` whose decoded `to` parameter is exactly
   `agent_wallet_pilot.ALLOWED_TRANSFER_ADDRESS`, same USD cap.

Why the transfer destination is NOT an `EvmAddressCriterion` (verified, not
assumed): `agent_wallet_cdp_adapter.transfer_usdc` calls `account.transfer(...,
token="usdc")`, and the installed cdp-sdk 1.47.1 builds that as a transaction
whose `to` is the **USDC contract** with `transfer(to, value)` calldata
(`cdp/actions/evm/transfer/account_transfer_strategy.py`, `_encode_erc20_function_call`
+ `TransactionRequestEIP1559(to=erc20_address, data=transfer_data)`). Putting
`ALLOWED_TRANSFER_ADDRESS` in an address criterion would therefore match
NOTHING and silently deny every legitimate transfer, while an address criterion
on the USDC contract would allow a transfer to ANY recipient. Only a decoded
`EvmDataCriterion` on the `to` parameter pins the real destination.

Why `NetUSDChangeCriterion` and not `EthValueCriterion`: the pilot moves USDC
(an ERC-20), and an ERC-20 transfer/swap leaves `tx.value` at 0 -- an
`ethValue` bound would never fire. Same reasoning already applied on the swing
policy (see `docs/HANDOFF_COINBASE_CDP.md`, 07/25 entry).

Open verification points (documented, never guessed or silently patched)
---------------------------------------------------
- **Router addresses are a caller PARAMETER, never hardcoded here.** The real
  swap destination is resolved dynamically by CDP's swap backend
  (`QuoteSwapResult.to`, cdp-sdk 1.47.1) -- `agent_wallet_cdp_adapter.py`
  contains no router address at all (grep-verified). It must be observed across
  several real quotes and confirmed STABLE before being trusted in an allowlist.
- **Exact semantics of `netUSDChange` on a SWAP are not documented in the
  installed SDK** beyond "the total value of a transaction's asset transfer"
  (`cdp/policies/types.py::NetUSDChangeCriterion`). Whether that means the
  notional moved or the net delta (near zero on a fair swap, where value leaves
  in USDC and returns in tokens) is unverified. If it is the net delta, this
  criterion bounds far less than its name suggests. MUST be validated against
  live CDP on a tiny real amount before this Policy is trusted as a cap.
- **A one-time ERC-20 `approve` to the canonical Permit2 contract may be
  required and is NOT allowlisted here.** CDP's swap path signs a per-quote
  Permit2 message but never sends an approval itself (see the allowance finding
  below); if the wallet has never approved Permit2 for USDC, that approval must
  come from somewhere, and its transaction's `to` is the USDC contract --
  default-denied by this Policy. If live testing confirms it is needed, add a
  deliberate carve-out (allow ERC-20 `approve` whose `spender` parameter is
  exactly the canonical Permit2 address
  `0x000000000022D473030F116dDEE9F6B43aC78BA3`, present in the installed SDK at
  `cdp/actions/evm/sign_and_wrap_typed_data_for_smart_account.py`) -- never
  assume it, never widen the policy blindly.
- The policy `description` is constrained by CDP's own API to
  `^[A-Za-z0-9 ,.]{1,50}$` (verified in the installed
  `cdp/openapi_client/models/create_policy_request.py`) -- a longer or
  punctuation-rich description is rejected at creation time, not at build time.

ERC-20 allowance verdict for the real swap path (backlog #310 point 2, SwapNet)
---------------------------------------------------
Re-verified independently against the actually-installed cdp-sdk 1.47.1 (not
cited from memory): `account.swap()` (the only real swap path,
`agent_wallet_cdp_adapter.execute_swap`) grants **no ERC-20 allowance at all**,
bounded or unlimited. The entire `cdp/actions/evm/swap/` tree contains zero
`approve`/`allowance`/`MAX_UINT256` call (grep-verified). The mechanism is
Uniswap's **Permit2**: when `swap_data.requires_signature` is true, the account
signs an EIP-712 `PermitTransferFrom` message scoped to that ONE quote (fixed
token, fixed amount, fixed nonce, fixed deadline), and the signature is appended
to the swap calldata (`cdp/actions/evm/swap/send_swap_transaction.py`). No
standing approval is ever granted to a router, so a compromised or malicious
router cannot replay an old approval -- structurally the mitigation for the
SwapNet failure class. The only persistent on-chain approval possible is the
wallet's own one-time approval to the canonical Permit2 contract itself
(Uniswap-deployed infrastructure, identical address on every EVM chain), which
is categorically different from a standing approval to an exploitable router.
Honest scope of this verdict: it covers the swap MECHANISM as implemented in
the installed SDK (which governs every future swap through this path), not a
live on-chain snapshot of the wallet's current allowances.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS, WALLET_NAME
from aria_core.agent_wallet_pilot import ALLOWED_TRANSFER_ADDRESS, MAX_TRANSACTION_USD

logger = logging.getLogger(__name__)

# Attached to a single account (the pilot EOA resolved by
# ``agent_wallet_cdp_adapter.WALLET_NAME``), never project-wide -- a project
# scope would also govern every other CDP account of this project (the x402
# wallet, the smart-swing spender), which is explicitly NOT the intent.
POLICY_SCOPE = "account"

# The CDP account this policy is MEANT for -- imported symbolically so a future
# rename of the pilot wallet can never leave a stale name here (the 21/07 and
# 23/07 rename incidents, cf. the adapter's own docstring). Informational: the
# attach target is chosen at creation time, a CreatePolicyOptions carries no
# account itself.
TARGET_ACCOUNT_NAME = WALLET_NAME

# CDP constrains this field to ^[A-Za-z0-9 ,.]{1,50}$ (verified in the installed
# openapi model) -- no hyphen, no parenthesis, 50 chars max.
POLICY_DESCRIPTION = "ARIA pilot. Swap router plus one transfer only."

# Same shape check the SDK's EvmAddressCriterion enforces internally -- applied
# first so a garbage router raises a clear error instead of producing a policy
# that would default-deny every swap or fail opaquely at attach time.
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def per_tx_cap_cents() -> int:
    """The per-transaction cap in whole cents for ``NetUSDChangeCriterion``.

    Derived from ``agent_wallet_pilot.MAX_TRANSACTION_USD`` (imported, never
    re-typed) so the CDP-side bound and the application-side bound can never
    drift apart. ``changeCents`` must be a non-negative integer (cdp-sdk field
    constraint ``ge=0``)."""
    return int(round(MAX_TRANSACTION_USD * 100))


def _validated_routers(router_addresses: Sequence[str] | None) -> list[str]:
    """Fail-closed validation of the caller-supplied swap-router allowlist.

    Rejects (rather than silently producing a dangerous or useless policy):
      - an empty list -- would leave rule 1 matching nothing, denying every swap;
      - a malformed address;
      - the USDC contract -- a transaction to a token contract is an ERC-20
        call, so allowlisting it by ADDRESS would accept ``transfer(anyone,
        anything)`` and completely bypass rule 2's destination pin;
      - ``ALLOWED_TRANSFER_ADDRESS`` -- the transfer path is governed by rule 2
        (decoded ``to`` parameter), never by a raw address allow.
    """
    routers = [str(a).strip() for a in (router_addresses or []) if str(a or "").strip()]
    if not routers:
        raise ValueError(
            "router_addresses is empty -- refusing to build a policy whose swap rule would "
            "match nothing (every swap would be default-denied)"
        )
    for router in routers:
        if not _EVM_ADDRESS_RE.match(router):
            raise ValueError(
                f"router address {router!r} is not a valid EVM address -- refusing to build a "
                "bounded policy on a garbage router"
            )
        if router.lower() == USDC_BASE_ADDRESS.lower():
            raise ValueError(
                "the USDC contract can never be an allowlisted router: allowing it by address "
                "would accept any ERC-20 transfer to any recipient, bypassing the single "
                "authorized transfer destination"
            )
        if router.lower() == ALLOWED_TRANSFER_ADDRESS.lower():
            raise ValueError(
                "ALLOWED_TRANSFER_ADDRESS can never be an allowlisted router: the transfer path "
                "is governed by the decoded ERC-20 transfer rule, never by a raw address allow"
            )
    return routers


def build_pilot_bounded_policy(router_addresses: Sequence[str]):
    """Build the ``CreatePolicyOptions`` mirroring the pilot's two application
    bounds on CDP's side. Pure: builds a config object, executes nothing,
    touches no network.

    ``router_addresses`` is a caller parameter precisely so it is never guessed:
    the real swap destination comes from a real quote (``QuoteSwapResult.to``)
    and must be confirmed stable across several quotes first -- see the module
    docstring's open verification points.

    Both rules carry the SAME ``NetUSDChangeCriterion`` cap (criteria within one
    rule are AND-ed by the Policy Engine), reusing
    ``agent_wallet_pilot.MAX_TRANSACTION_USD`` -- no second, driftable number.
    Everything not matching either rule is default-denied.
    """
    routers = _validated_routers(router_addresses)

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

    cap_cents = per_tx_cap_cents()

    allow_swap_router = SendEvmTransactionRule(
        action="accept",
        criteria=[
            EvmAddressCriterion(addresses=routers, operator="in"),
            NetUSDChangeCriterion(type="netUSDChange", changeCents=cap_cents, operator="<="),
        ],
    )
    allow_single_transfer_destination = SendEvmTransactionRule(
        action="accept",
        criteria=[
            EvmDataCriterion(
                abi=KnownAbiType.ERC20,
                conditions=[
                    EvmDataCondition(
                        function="transfer",
                        params=[
                            EvmDataParameterConditionList(
                                name="to", operator="in", values=[ALLOWED_TRANSFER_ADDRESS],
                            )
                        ],
                    )
                ],
            ),
            NetUSDChangeCriterion(type="netUSDChange", changeCents=cap_cents, operator="<="),
        ],
    )
    return CreatePolicyOptions(
        scope=POLICY_SCOPE,
        description=POLICY_DESCRIPTION,
        rules=[allow_swap_router, allow_single_transfer_destination],
    )


async def create_pilot_bounded_policy(
    *,
    policies_client: Any,
    router_addresses: Sequence[str],
    operator_ack: bool = False,
    idempotency_key: str | None = None,
) -> Any:
    """Create the policy on a REAL CDP account -- deliberately hard to fire.

    Two structural brakes, both mandatory:
      - ``policies_client`` is INJECTED (mirrors ``cdp.policies``, i.e. a
        ``PoliciesClient`` with ``create_policy(policy=, idempotency_key=)``,
        verified against the installed cdp-sdk 1.47.1). This module never builds
        a ``CdpClient``, never reads a credential, so importing or calling it
        without deliberately handing it a live client cannot reach Coinbase.
      - ``operator_ack`` must be explicitly ``True``. Creating a policy on the
        real pilot account changes what Coinbase will sign for real capital;
        that is an operator decision requiring a separate, explicit "ok", never
        an autonomous action. Fail-closed by default.

    As of this module's creation NO policy has been created on the real account
    through this path (or any other) -- the pilot runs with application-layer
    bounds only.
    """
    if not operator_ack:
        raise PermissionError(
            "creating a CDP Policy on the real pilot account requires an explicit, separate "
            "operator authorization (operator_ack=True) -- refusing (fail-closed by default)"
        )
    if policies_client is None:
        raise ValueError(
            "policies_client must be injected -- this module never builds a CdpClient itself"
        )

    policy = build_pilot_bounded_policy(router_addresses)
    logger.warning(
        "[REAL MONEY] agent-wallet pilot -- creating a CDP Policy on the real account %r "
        "(routers=%s, cap=%s cents) -- explicitly authorized by the operator",
        TARGET_ACCOUNT_NAME, list(router_addresses), per_tx_cap_cents(),
    )
    return await policies_client.create_policy(policy=policy, idempotency_key=idempotency_key)
