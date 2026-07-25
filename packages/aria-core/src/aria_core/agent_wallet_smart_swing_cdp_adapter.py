"""Real Coinbase CDP (cdp-sdk) implementations of the injected seams that
``agent_wallet_smart_swing.py`` orchestrates -- and NOTHING more.

``agent_wallet_smart_swing.py`` is pure orchestration + guards over injected
function seams (``BalanceFn``/``SpendPullFn``/``SwapFn``/``ReturnTransferFn``/
``QuoteFn`` and the ``token_balance_fn`` variant): it never touches the network
or a key itself. This module is the ONLY place those seams get a real CDP body.
It is the sibling of ``agent_wallet_cdp_adapter.py`` (the EOA ~10-15$ pilot's
adapter) -- same doctrine, same style -- but it targets the delegated SPENDER
account (``aria-spender-smart-st``, ``SPENDER_ADDRESS``), never the pilot's EOA
and never ``aria-smart-vc``.

Everything here is a THIN, faithful wrapper over cdp-sdk 1.47.1 (the really
installed version -- every signature/return shape below was read from the
installed source, never guessed). It adds NO business logic and NO safety
decision: the gate / kill-switch / loss circuit-breaker / per-tx cap /
real-balance check / forced 10% slippage all live in
``agent_wallet_smart_swing.py``'s orchestration and run BEFORE these seams are
ever called. The single defense-in-depth carve-out here is the return-transfer
destination pin (see ``return_transfer_fn``), which mirrors the CDP Policy's own
return-transfer allowlist.

**Still fully DORMANT.** Nothing in production imports or calls this module:
the gate ``ARIA_SMART_SWING_ENABLED`` is OFF, no Spend Permission has been
granted (needs a future Tangem tap), ``aria-smart-st`` holds no swing funds yet,
and no heartbeat / cron / caller is wired to any function here. Writing this file
arms nothing.

Credentials: ``CdpClient()`` reads ``CDP_API_KEY_ID``/``CDP_API_KEY_SECRET``/
``CDP_WALLET_SECRET`` from the environment automatically (SDK convention, same
as the pilot adapter) -- this module never reads, stores, prints, or logs a
secret value. The ``cdp`` import is lazy (inside each function) so the rest of
the codebase keeps importing even without the optional ``agent_wallet`` extra.

Seam -> function map (how a future caller wires ``agent_wallet_smart_swing``):
  - ``BalanceFn`` (buy affordability) ......... ``smart_st_usdc_balance_fn``
  - ``token_balance_fn`` (sell/canary) ........ ``make_spender_token_balance_fn(token)``
  - ``SpendPullFn`` ........................... ``spend_pull_fn``
  - ``SwapFn`` (buy) .......................... ``buy_swap_fn``
  - ``SwapFn`` (sell) ......................... ``sell_swap_fn``
  - ``ReturnTransferFn`` ...................... ``return_transfer_fn``
  - ``QuoteFn`` (precheck) .................... ``quote_fn``

Amount-out sourcing (an HONEST, verified limitation, not a guess): the SDK's
``account.swap(...)`` returns an ``AccountSwapResult`` carrying ONLY
``transaction_hash`` -- no received amount -- and CDP swaps submit-and-return
(``send_transaction`` returns the hash without waiting for the receipt), so a
post-swap balance read is not reliably settled. The seams need
``amount_out``/``amount_out_atomic`` anyway, so both swap functions source them
from the pre-execution swap QUOTE's EXPECTED ``to_amount``. ``to_amount`` (not
the slippage-tolerance-bounded ``min_to_amount``) is used deliberately -- the
SAME choice ``agent_wallet_smart_swing.precheck_swap_roundtrip`` documents for
itself, because ``min_to_amount`` is ~10% below expected by construction (the
tolerance floor) and would make a round-trip look like a ~19% loss on even a
perfectly liquid token. Consequence (bounded, loud, recoverable -- never a
loss): if the real fill lands BELOW the quoted expected, the immediately
following sell/return can revert on-chain and the orchestration surfaces it via
``SmartSwingSwapResult.funds_stranded`` (funds sit in the spender, reachable
only by the Policy's return-to-aria-smart-st carve-out). Before live arming,
source the EXACT fill from the tx receipt (needs a settlement-wait mechanism,
out of scope here) for precise realized P&L and zero-strand round-trips.
"""
from __future__ import annotations

import logging
from typing import Any

from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS, _get, _parse_balance_entry
from aria_core.agent_wallet_smart_swing import (
    MAX_SLIPPAGE_BPS,
    NETWORK,
    SMART_ST_ADDRESS,
    SPENDER_ADDRESS,
    BalanceFn,
    build_spend_permission_input,
)

logger = logging.getLogger(__name__)

# Distinct real-money log prefix (a log-grep can tell this apart from the EOA
# pilot adapter's "[ARGENT REEL] adaptateur CDP" and from the orchestration's
# own "[REAL MONEY] smart-swing (aria-smart-st)").
_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] smart-swing CDP adapter (spender)"

_USDC_DECIMALS = 6  # USDC on Base = 6 decimals (atomic units), same as the pilot.
# Fallback decimals for a BOUGHT token whose true decimals aren't available to
# this thin adapter (a quote carries no decimals, and a post-swap read isn't
# reliably settled). Only affects the INFORMATIONAL human ``amount_out`` of a buy
# (the load-bearing ``amount_out_atomic`` is exact from the quote); a safe
# under-report for the orchestration's >0 and <=balance checks. The sell leg
# always outputs USDC (6 decimals), so its human amount -- the P&L-critical one
# -- is exact.
_DEFAULT_TOKEN_DECIMALS = 18


async def _get_spender_account(cdp: Any) -> Any:
    """Resolve the SPENDER ``EvmServerAccount`` by ADDRESS (never by name).

    Resolving by the immutable ``SPENDER_ADDRESS`` sidesteps the whole class of
    CDP-dashboard rename incidents that repeatedly bit the pilot's name-based
    lookup (21/07, 23/07 -- see ``agent_wallet_cdp_adapter._get_wallet_account``
    and docs/HANDOFF_COINBASE_CDP.md). ``cdp.evm.get_account(address=...)`` is
    verified in cdp-sdk 1.47.1 (evm_client.py:437). A missing address raises
    (fail-closed) -- the orchestration's seams catch it (see each function)."""
    return await cdp.evm.get_account(address=SPENDER_ADDRESS)


# ── Balance reads (real network read, fail-closed) ───────────────────────────


async def _list_balance_entries(*, address: str, network: str) -> list[Any] | None:
    """One shared ``list_token_balances`` call for an ARBITRARY address (the
    pilot's ``_fetch_raw_balance_entries`` is hardcoded to the pilot EOA, so it
    can't be reused as-is). Returns ``None`` if the SDK is absent or the call
    fails (fail-closed -- never an empty list disguised as "no token held"),
    reusing the pilot adapter's ``_get`` for the version-agnostic ``.balances``
    extraction."""
    try:
        from cdp import CdpClient
    except ImportError:
        return None
    try:
        async with CdpClient() as cdp:
            result = await cdp.evm.list_token_balances(address=address, network=network)
    except Exception:  # noqa: BLE001 -- any read failure fails closed to None
        return None
    return _get(result, "balances") or (result if isinstance(result, list) else []) or []


async def usdc_balance_usd(*, address: str, network: str = NETWORK) -> float | None:
    """REAL USDC balance (treated as USD, 1 USDC ~= 1$) of ``address``. ``None``
    if unavailable (SDK absent / call failed) -- the orchestration handles that
    fail-closed. ``0.0`` only when the address genuinely holds no USDC. Reuses
    the pilot adapter's ``_parse_balance_entry`` (never duplicated)."""
    entries = await _list_balance_entries(address=address, network=network)
    if entries is None:
        return None
    for entry in entries:
        parsed = _parse_balance_entry(entry)
        if parsed is None:
            continue
        if parsed["address"].lower() != USDC_BASE_ADDRESS.lower():
            continue
        return parsed["amount"]
    return 0.0  # USDC never found -> address holds none, not an error.


async def token_balance(*, address: str, token_address: str, network: str = NETWORK) -> float | None:
    """REAL human balance of an arbitrary ERC-20 (``token_address``) held by
    ``address`` -- generalizes ``usdc_balance_usd`` over the SAME shared CDP call
    instead of duplicating it. ``None`` if unavailable (fail-closed), ``0.0`` if
    the token genuinely isn't held -- never conflated."""
    entries = await _list_balance_entries(address=address, network=network)
    if entries is None:
        return None
    target = (token_address or "").lower()
    for entry in entries:
        parsed = _parse_balance_entry(entry)
        if parsed is None:
            continue
        if parsed["address"].lower() != target:
            continue
        return parsed["amount"]
    return 0.0  # token not held -> 0, not an error.


# ── BalanceFn bindings (zero-arg, exactly the seam shape) ─────────────────────


async def smart_st_usdc_balance_fn() -> float | None:
    """``BalanceFn`` for the BUY's affordability check -- the USDC balance of
    ``aria-smart-st`` (the pull SOURCE), NOT the spender.

    Deviation note (resolved by the orchestration, kept explicit): the task
    summary described this seam as "USDC balance of the SPENDER", but
    ``execute_smart_swing_swap`` checks affordability BEFORE the pull, against
    the account the spender pulls FROM -- ``aria-smart-st``. The spender holds
    ~0 USDC before a pull, so wiring the spender's USDC here would block every
    buy. ``run_swing_entry_canary``'s own docstring is explicit: "``balance_fn``
    = USDC balance of aria-smart-st (buy)". This binding matches that."""
    return await usdc_balance_usd(address=SMART_ST_ADDRESS, network=NETWORK)


async def spender_usdc_balance_fn() -> float | None:
    """``BalanceFn`` for the SPENDER's own USDC balance. Not consumed by the buy
    or sell primitives (the buy reads the pull source, ``aria-smart-st``; the
    sell reads a held token via ``make_spender_token_balance_fn``) -- provided
    for a future stranded-USDC recovery/inspection flow (``funds_stranded`` leaves
    pulled USDC in the spender, recoverable only to ``aria-smart-st``) and for
    completeness of the spender's real reads."""
    return await usdc_balance_usd(address=SPENDER_ADDRESS, network=NETWORK)


def make_spender_token_balance_fn(token_address: str) -> BalanceFn:
    """Build the ``token_balance_fn`` seam (sell + canary): a zero-arg
    ``BalanceFn`` bound to ``token_address`` that reads the SPENDER's real
    balance of that specific held token. A factory (not a bare function) because
    the seam is zero-arg but the token varies per position."""

    async def _spender_token_balance() -> float | None:
        return await token_balance(
            address=SPENDER_ADDRESS, token_address=token_address, network=NETWORK
        )

    return _spender_token_balance


# ── SpendPullFn: pull USDC from aria-smart-st into the spender ────────────────


async def spend_pull_fn(*, value_atomic: int, network: str = NETWORK) -> dict[str, Any]:
    """``SpendPullFn`` -- the spender pulls ``value_atomic`` (USDC smallest
    units) out of ``aria-smart-st`` via the granted Spend Permission. NO Tangem
    tap once granted; NO owner signature (verified: use_spend_permission is the
    only value-movement path in the SDK that doesn't sign the owner per use).

    Reuses ``agent_wallet_smart_swing.build_spend_permission_input()`` (never
    reconstructs the ``SpendPermissionInput`` by hand) -- reusing the SAME builder
    that will create the on-chain grant guarantees the account/spender/token/
    allowance/period match, which the contract requires.

    ``spender.use_spend_permission(spend_permission, value, network) -> str``
    (verified evm_server_account.py:658; returns the tx hash string). On failure
    it RAISES -- and that is the seam contract, verified against the call site:
    ``execute_smart_swing_swap`` catches it and marks the attempt failed &
    NOT-stranded (nothing was pulled). Returning an empty tx_hash instead would
    be read as success and wrongly proceed to swap un-pulled USDC."""
    from cdp import CdpClient

    async with CdpClient() as cdp:
        spender = await _get_spender_account(cdp)
        tx_hash = await spender.use_spend_permission(
            spend_permission=build_spend_permission_input(),
            value=int(value_atomic),
            network=network,
        )
    return {"tx_hash": str(tx_hash)}


# ── SwapFn: buy (USDC -> token) and sell (token -> USDC) ──────────────────────


async def _execute_spender_swap(
    *,
    from_token: str,
    to_token: str,
    from_amount_atomic: int,
    network: str,
    to_token_decimals: int,
) -> dict[str, Any]:
    """Shared core of both swap seams: quote -> execute the quoted swap from the
    SPENDER -> report ``{tx_hash, amount_out, amount_out_atomic}``.

    A buy and a sell are the SAME SDK call (``account.swap`` is direction-agnostic
    over ``from_token``/``to_token``/``from_amount``); the seams differ only in how
    ``from_amount`` is derived and which kwargs arrive, hence two thin public
    wrappers over this one core.

    Flow (verified against cdp-sdk 1.47.1):
      1. ``cdp.evm.create_swap_quote(...taker=SPENDER_ADDRESS, slippage_bps=
         MAX_SLIPPAGE_BPS)`` (evm_client.py:1199) -- read-only price+route quote;
         ``.execute()`` is NEVER called here.
      2. Validate liquidity + a usable ``to_amount`` BEFORE moving any funds
         (raise -> the caller's seam contract handles it; on a buy this is
         AFTER the pull, so the orchestration marks it stranded).
      3. ``spender.swap(AccountSwapOptions(swap_quote=quote))`` (evm_server_
         account.py:342) -- executes the exact quoted swap; returns
         ``AccountSwapResult`` with ONLY ``transaction_hash``.
      4. Report ``amount_out_atomic = int(quote.to_amount)`` (exact atomic; see
         the module docstring on why ``to_amount``, not ``min_to_amount``) and
         ``amount_out = amount_out_atomic / 10**to_token_decimals``.

    Slippage is FORCED to ``MAX_SLIPPAGE_BPS`` (absolute rule 09/07), never the
    caller's value."""
    from cdp import CdpClient
    from cdp.actions.evm.swap import AccountSwapOptions

    async with CdpClient() as cdp:
        quote = await cdp.evm.create_swap_quote(
            from_token=from_token,
            to_token=to_token,
            from_amount=str(from_amount_atomic),
            network=network,
            taker=SPENDER_ADDRESS,
            slippage_bps=MAX_SLIPPAGE_BPS,
        )
        # Fail-closed BEFORE executing anything: no liquidity / no usable amount.
        if quote is None or _get(quote, "liquidity_available") is False:
            raise RuntimeError(
                f"{_REAL_MONEY_LOG_PREFIX} -- swap quote unavailable "
                f"(insufficient liquidity) for {from_token} -> {to_token}"
            )
        raw_to_amount = _get(quote, "to_amount")
        try:
            amount_out_atomic = int(str(raw_to_amount))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{_REAL_MONEY_LOG_PREFIX} -- swap quote has no usable to_amount "
                f"({raw_to_amount!r}) for {from_token} -> {to_token}"
            ) from exc
        if amount_out_atomic <= 0:
            raise RuntimeError(
                f"{_REAL_MONEY_LOG_PREFIX} -- swap quote to_amount non-positive "
                f"({amount_out_atomic}) for {from_token} -> {to_token}"
            )

        spender = await _get_spender_account(cdp)
        result = await spender.swap(AccountSwapOptions(swap_quote=quote))

    tx_hash = str(_get(result, "transaction_hash", "tx_hash") or "")
    amount_out = amount_out_atomic / (10 ** to_token_decimals)
    return {"tx_hash": tx_hash, "amount_out": amount_out, "amount_out_atomic": amount_out_atomic}


def _forced_slippage(slippage_bps: int | None) -> None:
    """Log if a non-``MAX_SLIPPAGE_BPS`` value is passed -- it is always ignored
    and forced to ``MAX_SLIPPAGE_BPS`` (absolute rule 09/07), same discipline as
    the orchestration's own primitives."""
    if slippage_bps is not None and slippage_bps != MAX_SLIPPAGE_BPS:
        logger.warning(
            "%s -- slippage_bps=%s ignored, forced to %s (absolute rule 09/07)",
            _REAL_MONEY_LOG_PREFIX, slippage_bps, MAX_SLIPPAGE_BPS,
        )


async def buy_swap_fn(
    *,
    network: str,
    token_in: str,
    token_out: str,
    amount_in_usd: float,
    slippage_bps: int | None = None,
) -> dict[str, Any]:
    """``SwapFn`` for a swing BUY (``USDC -> token_out``). Called by
    ``execute_smart_swing_swap`` as ``swap_fn(network=, token_in=, token_out=,
    amount_in_usd=, slippage_bps=)`` -- these exact kwargs.

    ``token_in`` is USDC (the only asset the spend pull provides), so
    ``from_amount`` is ``parse_units(str(amount_in_usd), 6)`` -- same 6-decimal
    assumption the pilot's ``execute_swap`` makes. On failure it RAISES: the
    call site is AFTER the pull, so the orchestration marks the USDC stranded in
    the spender (recoverable only to aria-smart-st via the Policy)."""
    _forced_slippage(slippage_bps)
    from cdp import parse_units

    from_amount_atomic = parse_units(str(amount_in_usd), _USDC_DECIMALS)
    to_decimals = (
        _USDC_DECIMALS
        if (token_out or "").lower() == USDC_BASE_ADDRESS.lower()
        else _DEFAULT_TOKEN_DECIMALS
    )
    return await _execute_spender_swap(
        from_token=token_in,
        to_token=token_out,
        from_amount_atomic=from_amount_atomic,
        network=network,
        to_token_decimals=to_decimals,
    )


async def sell_swap_fn(
    *,
    network: str,
    token_in: str,
    token_out: str,
    amount_in_tokens: float,
    amount_in_tokens_atomic: int | None = None,
    slippage_bps: int | None = None,
) -> dict[str, Any]:
    """``SwapFn`` for a swing SELL (``token_in -> USDC``). Called by
    ``execute_smart_swing_sell`` as ``swap_fn(network=, token_in=, token_out=,
    amount_in_tokens=, amount_in_tokens_atomic=, slippage_bps=)`` -- these exact
    kwargs.

    ``amount_in_tokens_atomic`` (the buy's ``amount_out_atomic``, threaded by the
    orchestration) is the EXACT quantity to sell, used directly as
    ``from_amount`` -- this is why the sell needs no knowledge of ``token_in``'s
    decimals. It is REQUIRED: without it this thin adapter cannot convert the
    human ``amount_in_tokens`` to atomic (the sold token's decimals aren't
    known), so it RAISES (fail-closed) -- the seam contract handles that as a
    failed, NOT-stranded sell (the token simply stays in the spender). ``token_out``
    is USDC (6 decimals), so the reported ``amount_out`` (USDC proceeds -> P&L)
    is exact."""
    _forced_slippage(slippage_bps)
    if amount_in_tokens_atomic is None:
        raise ValueError(
            f"{_REAL_MONEY_LOG_PREFIX} -- sell requires amount_in_tokens_atomic "
            "(exact atomic quantity); the thin adapter cannot infer the sold "
            "token's decimals. The orchestration threads it from the buy's "
            "amount_out_atomic."
        )
    to_decimals = (
        _USDC_DECIMALS
        if (token_out or "").lower() == USDC_BASE_ADDRESS.lower()
        else _DEFAULT_TOKEN_DECIMALS
    )
    return await _execute_spender_swap(
        from_token=token_in,
        to_token=token_out,
        from_amount_atomic=int(amount_in_tokens_atomic),
        network=network,
        to_token_decimals=to_decimals,
    )


# ── ReturnTransferFn: return the sell's USDC back to aria-smart-st ────────────


async def return_transfer_fn(
    *,
    to_address: str,
    token_address: str,
    amount_out: float,
    amount_out_atomic: int | None = None,
    network: str = NETWORK,
) -> dict[str, Any]:
    """``ReturnTransferFn`` -- the spender returns the sell's USDC output to
    ``aria-smart-st``. The single most safety-critical function in this module.

    Destination is HARDCODED to ``SMART_ST_ADDRESS`` and is structurally
    impossible to redirect: ``to_address`` (which the orchestration passes as
    ``SMART_ST_ADDRESS`` already) is only VALIDATED here -- any value other than
    aria-smart-st RAISES, and the actual ``transfer(to=...)`` always uses the
    module constant, never the parameter. Defense-in-depth mirroring the CDP
    Policy's own return-transfer carve-out and the EOA pilot's
    ``ALLOWED_TRANSFER_ADDRESS`` pattern. A raised mismatch is handled by the
    seam contract as a stranded sell (USDC stays in the spender, recoverable),
    never sent anywhere else.

    ``spender.transfer(to, amount:int-atomic, token, network) -> HexStr``
    (verified evm_server_account.py:269). Amount is the exact atomic when
    provided, else derived from the human USDC amount (the return leg is always
    USDC, 6 decimals). On failure it RAISES: the seam contract marks the USDC
    stranded (sold but not returned)."""
    if (to_address or "").strip().lower() != SMART_ST_ADDRESS.lower():
        raise ValueError(
            f"{_REAL_MONEY_LOG_PREFIX} -- return-transfer destination must be "
            f"aria-smart-st ({SMART_ST_ADDRESS}); refusing {to_address!r} "
            "(defense-in-depth; the spender may only ever return funds home)"
        )

    from cdp import CdpClient, parse_units

    if amount_out_atomic is not None:
        amount_atomic = int(amount_out_atomic)
    else:
        amount_atomic = parse_units(str(amount_out), _USDC_DECIMALS)
    if amount_atomic <= 0:
        raise ValueError(
            f"{_REAL_MONEY_LOG_PREFIX} -- return-transfer amount must be positive "
            f"(got {amount_atomic})"
        )

    token = token_address or USDC_BASE_ADDRESS
    async with CdpClient() as cdp:
        spender = await _get_spender_account(cdp)
        tx_hash = await spender.transfer(
            to=SMART_ST_ADDRESS,  # HARDCODED -- never `to_address`.
            amount=amount_atomic,
            token=token,
            network=network,
        )
    return {"tx_hash": str(tx_hash)}


# ── QuoteFn: read-only round-trip precheck quotes ────────────────────────────


async def quote_fn(
    *,
    from_token: str,
    to_token: str,
    from_amount_atomic: int,
    network: str = NETWORK,
    slippage_bps: int | None = None,
) -> Any:
    """``QuoteFn`` for ``precheck_swap_roundtrip`` -- returns the raw
    ``cdp.evm.create_swap_quote`` result (``QuoteSwapResult`` |
    ``SwapUnavailableResult``) for the orchestration to read ``to_amount`` /
    ``min_to_amount`` / ``liquidity_available`` itself.

    PURELY read-only: ``create_swap_quote`` only fetches a price+route quote
    (verified evm_client.py:1199 -- no on-chain side effect), and this function
    NEVER calls the returned quote's ``.execute()``. Slippage forced to
    ``MAX_SLIPPAGE_BPS``. Any error propagates -- the orchestration's
    ``_fetch_quote`` catches it and rejects fail-closed (verified call site)."""
    _forced_slippage(slippage_bps)
    from cdp import CdpClient

    async with CdpClient() as cdp:
        return await cdp.evm.create_swap_quote(
            from_token=from_token,
            to_token=to_token,
            from_amount=str(from_amount_atomic),
            network=network,
            taker=SPENDER_ADDRESS,
            slippage_bps=MAX_SLIPPAGE_BPS,
        )
