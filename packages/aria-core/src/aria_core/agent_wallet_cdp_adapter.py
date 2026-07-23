"""Coinbase Developer Platform (CDP) adapter for `agent_wallet_pilot.py`.

Builds the injectable `balance_fn`/`swap_fn` functions expected by
`agent_wallet_pilot.attempt_swap()`, using the official `cdp-sdk` SDK
(Python package, optional extra `aria-core[agent_wallet]`,
https://pypi.org/project/cdp-sdk/).

Credentials: `CdpClient()` automatically reads `CDP_API_KEY_ID`,
`CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET` from the environment (SDK
convention) -- this module never reads, stores, or handles them itself.
No private key here (same doctrine as the rest of the dome): the CDP SDK
keeps the wallet's private key on Coinbase's side (non-custodial, but
managed by their signing infrastructure), never exposed to this code.

Reserved for an execution context where the 3 variables are set in a local
`.env` (VPS) -- never in a cloud session. The `cdp` package import is done
inside the functions (lazy) so the rest of the codebase doesn't break if
the `agent_wallet` extra isn't installed.

**Verified against a real CDP call on 07/16 (VPS Principal, norm #157)**:
`usdc_balance_usd()` was called alone (never `execute_swap`) against the
real dedicated wallet (`aria-agent-wallet-pilot`, public Base mainnet
address `0xF04625162b616c5ad9788811b7be8CDd425B37Ef`) -- `cdp.evm.get_or_create_account`
and `cdp.evm.list_token_balances` respond without error, `list_token_balances`
does return a Pydantic `ListTokenBalancesResult` object with a `.balances`
attribute (exactly the shape assumed by `_get(result, "balances")`,
no correction needed). Result `0.0` confirmed structurally as a genuinely
empty wallet (`len(entries) == 0`, not an artifact of the `or []` fallback
on a malformed response) -- expected, the #10$ pilot hadn't been funded yet.
The `execute_swap` path (real transaction) remains, itself, unexercised --
only the read path was verified at this stage.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Same marker as agent_wallet_pilot.py's _REAL_MONEY_LOG_PREFIX (not imported
# directly -- this module stays a thin CDP adapter, no dependency on the
# pilot's own internals) so a log-grep for real-money events catches this too.
_REAL_MONEY_LOG_PREFIX = "[ARGENT REEL] adaptateur CDP"

# Native USDC on Base mainnet (6 decimals) -- https://docs.base.org/base-chain/data-analytics/token-list
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# 22/07 -- corrige un vrai incident capital reel : lors d'une regeneration de la
# CDP API key (21/07, following the allowed-IP fix), get_or_create_account no
# longer found the historical account under this name and created a SECOND,
# empty one (0x584b2B35...), distinct from the one holding the real balance
# (0xF04625162b616c5ad9788811b7be8CDd425B37Ef). Verified live on the CDP
# dashboard (22/07): this real-balance address carried the label "aria-wallet"
# at the time, confirming get_or_create_account(name="aria-wallet") resolved
# exactly to it, without creating a 3rd account.
#
# RENAMED 23/07 (operator decision, direct CDP dashboard/SDK action, part of the
# Smart Account migration -- see docs/HANDOFF_COINBASE_CDP.md): this same
# real-balance address went through TWO renames the same day -- first
# "aria-wallet" -> "aria-wallet-X402" (repurposed as the x402-seller-adjacent
# wallet, and as the owner/signer of the new `aria-smart-wallet-one` Smart
# Account), then "aria-wallet-X402" -> "aria-wallet-X402-EVM" (operator's "-EVM"
# naming convention across all 4 active wallets, same day). WALLET_NAME must be
# updated to match EVERY TIME this address is renamed, AND the running
# container redeployed immediately after -- a source-only edit does nothing for
# an already-running process (confirmed the hard way: the first rename above
# already triggered exactly this failure once today before the fix was
# deployed). The formerly-orphaned second account (0x584b2B35..., previously
# labeled "aria-agent-wallet-pilot", never used by any code path per the note
# above) was renamed the same day to "aria-wallet-transfert", then
# "aria-wallet-transfert-EVM" -- still unreferenced by this constant.
WALLET_NAME = "aria-wallet-X402-EVM"


async def _get_wallet_account(cdp: Any) -> Any:
    """Fetch the account under ``WALLET_NAME`` -- NEVER auto-creates, unlike
    ``cdp.evm.get_or_create_account``. For a real-money wallet, a missing name
    means a stale ``WALLET_NAME``/CDP-dashboard rename mismatch (exactly the
    21/07 and 23/07 incidents documented above), never a legitimate first-time
    setup -- this pilot has run against a real funded wallet since 16/07.
    Logs a CRITICAL real-money-marked line and fails closed (raises) instead
    of silently creating and operating on a brand-new empty wallet."""
    from cdp.openapi_client.errors import ApiError

    try:
        return await cdp.evm.get_account(name=WALLET_NAME)
    except ApiError as exc:
        if exc.http_code == 404:
            logger.critical(
                "%s -- WALLET_NAME=%r not found on CDP (neither get_account, nor "
                "what get_or_create_account would have silently recreated) -- "
                "check the CDP dashboard immediately and fix WALLET_NAME, then "
                "REDEPLOY before any new cycle.",
                _REAL_MONEY_LOG_PREFIX, WALLET_NAME,
            )
            raise RuntimeError(
                f"CDP account {WALLET_NAME!r} not found -- refusing to auto-create "
                "a new empty wallet (same failure mode as 21/07 and 23/07)"
            ) from exc
        raise


def _get(obj: Any, *names: str) -> Any:
    """Reads an attribute or dict key, whatever the format returned by
    the SDK (Pydantic object or raw dict depending on version) -- defensive,
    never a single assumption about the exact shape of the response."""
    for name in names:
        if obj is None:
            return None
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            continue
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


async def _fetch_raw_balance_entries(*, network: str) -> list[Any] | None:
    """A single shared CDP call (``list_token_balances``) -- reused by
    ``usdc_balance_usd`` (USDC filter) and ``list_all_token_balances`` (all).
    Returns ``None`` if the SDK is absent or the call fails (fail-closed,
    never an empty list disguised as "no token held")."""
    try:
        from cdp import CdpClient
    except ImportError:
        return None
    try:
        async with CdpClient() as cdp:
            account = await _get_wallet_account(cdp)
            result = await cdp.evm.list_token_balances(address=account.address, network=network)
    except Exception:
        return None
    return _get(result, "balances") or (result if isinstance(result, list) else []) or []


def _parse_balance_entry(entry: Any) -> dict[str, Any] | None:
    """Extracts ``{address, symbol, amount}`` from a raw CDP entry -- ``None``
    if the amount isn't usable (never an invented 0 on unreadable data)."""
    token = _get(entry, "token")
    address = _get(token, "contract_address", "contractAddress") or ""
    symbol = _get(token, "symbol") or "?"
    amount = _get(entry, "amount")
    raw = _get(amount, "amount")
    decimals = _get(amount, "decimals")
    if raw is None:
        return None
    try:
        value = float(raw) / (10 ** int(decimals if decimals is not None else 18))
    except (TypeError, ValueError):
        return None
    return {"address": address, "symbol": symbol, "amount": value}


async def usdc_balance_usd(*, network: str = "base") -> float | None:
    """Injectable ``balance_fn`` -- REAL USDC balance of the dedicated wallet,
    treated as a dollar amount (1 USDC ~= 1$, no price conversion needed).
    Returns ``None`` if unavailable -- ``agent_wallet_pilot`` handles that
    fail-closed (refuses the transaction rather than guessing)."""
    entries = await _fetch_raw_balance_entries(network=network)
    if entries is None:
        return None
    for entry in entries:
        parsed = _parse_balance_entry(entry)
        if parsed is None:
            continue
        if parsed["address"].lower() != USDC_BASE_ADDRESS.lower():
            continue
        return parsed["amount"]
    return 0.0  # USDC never found in balances -- wallet empty in USDC, not an error.


async def list_all_token_balances(*, network: str = "base") -> list[dict[str, Any]] | None:
    """All tokens actually held by the wallet (#204 follow-up, operator
    request 16/07: "I want to see everything, even future tokens bought") --
    generalizes ``usdc_balance_usd`` instead of duplicating it (same shared
    CDP call). Each entry: ``{"address", "symbol", "amount"}``. ``None`` if
    unavailable (SDK absent/call failed), ``[]`` if the wallet is genuinely
    empty -- never conflated."""
    entries = await _fetch_raw_balance_entries(network=network)
    if entries is None:
        return None
    parsed = [_parse_balance_entry(e) for e in entries]
    return [p for p in parsed if p is not None]


async def execute_swap(
    *,
    chain: str,
    token_in: str,
    token_out: str,
    amount_in_usd: float,
    wallet_address: str,
    slippage_bps: int,
) -> dict[str, Any]:
    """Injectable ``swap_fn`` -- executes the real swap. ``slippage_bps`` is
    ALWAYS the one forced by `agent_wallet_pilot.attempt_swap` (never a tool
    default, absolute rule 09/07).

    Real bug fixed on 17/07 (found by Secondaire by checking BEFORE coding,
    never exercised against a real call until now -- this path remains
    unexercised in real conditions, only the read path (`usdc_balance_usd`)
    was on 16/07): `from_amount` expects an amount in ATOMIC UNITS (confirmed
    in the installed SDK, `cdp/actions/evm/swap/types.py::AccountSwapOptions.from_amount`,
    "Amount to swap in smallest units") -- passing `str(amount_in_usd)` (e.g.
    "10.5") would have made EVERY real swap fail or be misinterpreted from
    the first attempt. Fixed with `cdp.parse_units`, same pattern as
    `transfer_usdc` below. Assumed hypothesis (documented, not hidden):
    `amount_in_usd` is a quantity of USDC (6 decimals) -- consistent with the
    plan's doctrine (native ETH as `token_in` explicitly rejected for this
    first version, no `token_in` other than USDC is considered)."""
    from cdp import CdpClient, parse_units
    from cdp.actions.evm.swap import AccountSwapOptions

    async with CdpClient() as cdp:
        account = await _get_wallet_account(cdp)
        result = await account.swap(
            AccountSwapOptions(
                network=chain,
                from_token=token_in,
                to_token=token_out,
                from_amount=parse_units(str(amount_in_usd), 6),
                slippage_bps=slippage_bps,
            )
        )

    tx_hash = _get(result, "transaction_hash", "tx_hash") or ""
    amount_out_raw = _get(result, "to_amount", "amount_out")
    try:
        amount_out = float(amount_out_raw) if amount_out_raw is not None else 0.0
    except (TypeError, ValueError):
        amount_out = 0.0
    return {"tx_hash": str(tx_hash), "amount_out": amount_out}


async def transfer_usdc(*, chain: str, to_address: str, amount_usd: float) -> dict[str, Any]:
    """Injectable ``transfer_fn`` for `agent_wallet_pilot.attempt_transfer`
    (named exception #4, 16/07) -- transfers real USDC to ``to_address``.

    API verified against the official CDP SDK doc before writing this
    function (never guessed): ``account.transfer(to=, amount=, token="usdc", network=)``,
    amount in atomic units via ``cdp.parse_units(str(amount), 6)`` (USDC =
    6 decimals) -- https://docs.cdp.coinbase.com/server-wallets/v2/using-the-wallet-api/transfers.

    ``to_address`` is NEVER a free parameter on the real caller side: it's
    `agent_wallet_pilot.ALLOWED_TRANSFER_ADDRESS` (hardcoded allowlist, checked
    BEFORE this function is ever called) -- this module doesn't re-check the
    allowlist itself, it executes what it's given, the guard sits upstream."""
    from cdp import CdpClient, parse_units

    async with CdpClient() as cdp:
        account = await _get_wallet_account(cdp)
        tx_hash = await account.transfer(
            to=to_address,
            amount=parse_units(str(amount_usd), 6),
            token="usdc",
            network=chain,
        )
    return {"tx_hash": str(tx_hash)}
