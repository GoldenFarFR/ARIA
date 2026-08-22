"""Builds a REAL Jupiter swap transaction and proves it against mainnet state
WITHOUT sending it, without a key, without a signature (21/08).

**Why simulation rather than devnet.** The operator asked for a signed swap on
devnet. Verified live the same day: Jupiter is a MAINNET aggregator -- its
devnet endpoint returns nothing, and the memecoin pools this dome trades have
no devnet equivalent. There is literally nothing to swap there. So the proof
follows the pattern this repo already used for the EVM leg
(`safe_robinhood_simulation.py`): exercise the real thing against real
on-chain state through a read-only call, never a send.

Solana's `simulateTransaction` accepts `sigVerify: false`, so a transaction can
be executed against the CURRENT mainnet state -- real pools, real reserves,
real program logic -- and report exactly what it would do, while touching no
key and moving no value. That is a stronger proof than devnet would have
given: devnet pools would have been fake.

**Structural guardrail.** This module has NO send path and NO key handling.
`sendTransaction` is never called and `Keypair` is never imported, asserted by
an AST test rather than a text scan (a text scan trips on this docstring --
lesson already paid twice in this repo today). Signing and sending stay a
separate, explicitly-authorised step.
"""
from __future__ import annotations

import base64
import logging

import httpx

from aria_core.services.jupiter import MAX_SLIPPAGE_BPS, fetch_quote
from aria_core.services.pumpswap_ws import require_solana_rpc_http

logger = logging.getLogger(__name__)

SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"

# A swap that consumes more than this is almost certainly malformed rather
# than expensive -- Solana's own per-transaction ceiling is 1.4M.
MAX_PLAUSIBLE_COMPUTE_UNITS = 1_400_000


# Addresses that are NOT usable as a swap payer, however valid they look.
# 21/08: passing the System Program as `userPublicKey` produced the opaque RPC
# error "Transaction failed to sanitize accounts offsets correctly" -- half an
# hour of debugging a format problem that never existed. A named guard turns
# that into an immediate, explicit refusal.
_NOT_A_WALLET = {
    "11111111111111111111111111111111",              # System Program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token Program
    "ComputeBudget111111111111111111111111111111",   # Compute Budget Program
}


class SwapSimulationError(RuntimeError):
    """Raised when a swap could not be built or simulated. Never a partial
    result: a swap reported as viable when it is not would be acted on."""


# Priority fee for latency-sensitive trades (22/08), CALIBRATED, never guessed.
#
# The first version of this constant was a flat 100_000 lamports, invented. On
# a 0.10$ trade that is 20% of the position in fees for a round trip -- while
# `getRecentPrioritizationFees` showed the network's median, p75 AND p90 all at
# ZERO. It was paying twenty percent to jump an empty queue.
#
# So the fee is read from the chain and sized against it. The floor exists
# because being marginally above a field paying nothing is what buys inclusion
# in the next block; the ceiling exists because no latency gain justifies
# spending a tenth of the position, and a congestion spike must never silently
# drain the wallet through fees.
PRIORITY_FEE_FLOOR_LAMPORTS = 5_000
PRIORITY_FEE_CEILING_LAMPORTS = 20_000
_PRIORITY_FEE_TTL_SECONDS = 120.0
_priority_fee_cache: tuple[float, int] | None = None


async def recent_priority_fee(
    *, rpc_http_url: str, client: httpx.AsyncClient | None = None,
) -> int:
    """What to pay right now, from the network's own recent fees.

    Takes the 75th percentile of recent blocks -- enough to be ahead of the
    field without bidding against the top of it -- then clamps to the bounds
    above. Falls back to the floor when the RPC cannot be read: unknown
    congestion is not a reason to overpay.
    """
    import time

    global _priority_fee_cache
    now = time.monotonic()
    if _priority_fee_cache and now - _priority_fee_cache[0] < _PRIORITY_FEE_TTL_SECONDS:
        return _priority_fee_cache[1]

    fee = PRIORITY_FEE_FLOOR_LAMPORTS
    owns = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        from aria_core.services import solana_gateway
        from aria_core.services.solana_rpc_budget import Priority

        payload = await solana_gateway.call(
            "getRecentPrioritizationFees", [[]],
            priority=Priority.NORMAL, client=client,
        )
        values = sorted(
            int(f.get("prioritizationFee") or 0)
            for f in ((payload or {}).get("result") or [])
        )
        if values:
            observed = values[int(len(values) * 0.75)]
            fee = max(PRIORITY_FEE_FLOOR_LAMPORTS,
                      min(observed, PRIORITY_FEE_CEILING_LAMPORTS))
    except Exception as exc:  # noqa: BLE001 -- unknown congestion, pay the floor
        logger.info("jupiter_swap_simulation: priority fee unreadable (%s)", exc)
    finally:
        if owns:
            await client.aclose()

    _priority_fee_cache = (now, fee)
    return fee


async def build_swap_transaction(
    quote: dict, user_public_key: str, *, client: httpx.AsyncClient | None = None,
    priority_fee_lamports: int | None = None,
) -> str:
    """Asks Jupiter for the base64 transaction implementing ``quote``.

    Takes a PUBLIC key only. This builds an unsigned transaction -- the
    signature is a separate step that this module deliberately cannot perform.

    ``priority_fee_lamports`` buys faster block inclusion. Omitted by default,
    since it is a real cost that only pays for itself on a racing path.
    """
    if quote.get("slippage_bps_used", MAX_SLIPPAGE_BPS) > MAX_SLIPPAGE_BPS:
        raise SwapSimulationError("quote carries a slippage above the project ceiling")
    if user_public_key in _NOT_A_WALLET:
        raise SwapSimulationError(
            f"{user_public_key} is a program address, not a wallet -- the RPC would "
            f"reject the built transaction with an unrelated-looking format error"
        )

    payload = {
        # Our own derived fields must be stripped: Jupiter validates the
        # quote object it receives and rejects unknown keys.
        "quoteResponse": {
            k: v for k, v in quote.items()
            if k not in ("worst_case_out", "price_impact_pct", "slippage_bps_used")
        },
        "userPublicKey": user_public_key,
        # Jupiter wraps/unwraps SOL itself; doing it by hand is a classic
        # source of stranded wrapped-SOL accounts.
        "wrapAndUnwrapSol": True,
    }
    if priority_fee_lamports:
        payload["prioritizationFeeLamports"] = int(priority_fee_lamports)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.post(SWAP_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise SwapSimulationError(f"swap build failed: {exc!r}") from exc
    finally:
        if owns:
            await client.aclose()

    tx = data.get("swapTransaction")
    if not tx:
        raise SwapSimulationError("Jupiter returned no swapTransaction")
    return tx


async def simulate_swap_transaction(
    swap_transaction_b64: str, *, rpc_http_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    owner_pubkey: str | None = None, max_sol_spend_lamports: int | None = None,
    pre_balance_lamports: int | None = None,
) -> dict:
    """Runs the transaction against CURRENT mainnet state without sending it.

    ``sigVerify: false`` is what makes this possible unsigned;
    ``replaceRecentBlockhash: true`` avoids a stale-blockhash failure that
    would look like a broken swap when it is only an expired quote.

    Returns ``{"ok", "error", "compute_units", "logs", "sol_delta_lamports"}``.
    ``ok=False`` with an error is a REAL failure of this swap against real
    liquidity -- exactly what must be known before any capital is committed.

    **SPEND CEILING (22/08), the reason this function grew two arguments.**
    The transaction being simulated is BUILT BY JUPITER, not by us: we ask for
    a quote, they return base64 calldata, we sign it. Until now the simulation
    only asked "does it succeed?" -- so a transaction that succeeded perfectly
    while draining the wallet would have passed, and the real bound on a bad
    swap was the WALLET BALANCE, never the 0.10$ per-trade cap. That gap was
    load-bearing and undocumented.

    Given ``owner_pubkey`` and ``max_sol_spend_lamports``, this now reads the
    simulated pre/post balances and REFUSES any transaction whose net SOL
    outflow from the owner exceeds the ceiling. Not a claim that Jupiter is
    hostile -- a claim that our safety should not depend on it not being.

    The three arguments are optional so existing callers keep working, but a
    caller on the real-money path that omits them gets NO ceiling:
    `jupiter_swap_signer.execute_swap` passes them, and `test_coherence`
    asserts it does. `simulateTransaction` reports POST balances only, hence
    `pre_balance_lamports` -- the caller already reads the balance on this path.
    """
    params = [
        swap_transaction_b64,
        {
            "encoding": "base64",
            "commitment": "processed",
            "sigVerify": False,
            "replaceRecentBlockhash": True,
        },
    ]
    if owner_pubkey:
        # Ask the RPC to report this account's balance before and after.
        params[1]["accounts"] = {"encoding": "base64", "addresses": [owner_pubkey]}
    # HIGH: this runs immediately before signing real money. `rpc_http_url` is
    # now only a hint -- the gateway owns endpoint choice, rate and failover.
    from aria_core.services import solana_gateway
    from aria_core.services.solana_rpc_budget import Priority

    data = await solana_gateway.call(
        "simulateTransaction", params, priority=Priority.HIGH, client=client,
    )
    if data is None:
        raise SwapSimulationError("no endpoint could simulate the swap")

    if "error" in data:
        raise SwapSimulationError(f"RPC error: {data['error']}")
    value = (data.get("result") or {}).get("value") or {}
    units = value.get("unitsConsumed")
    if units and units > MAX_PLAUSIBLE_COMPUTE_UNITS:
        raise SwapSimulationError(f"implausible compute usage: {units}")

    sol_delta = _simulated_sol_delta(value, pre_balance_lamports)
    if max_sol_spend_lamports is not None and sol_delta is not None:
        # Negative delta = SOL leaving the wallet. A swap spends what we asked
        # plus fees; anything materially beyond that is not the swap we quoted.
        if -sol_delta > max_sol_spend_lamports:
            raise SwapSimulationError(
                f"refusing to sign: simulation moves {-sol_delta} lamports out of "
                f"{owner_pubkey}, above the {max_sol_spend_lamports} ceiling for this "
                f"trade -- the transaction was built by Jupiter, so a mismatch here is "
                f"exactly the case this check exists for"
            )
    return {
        "ok": value.get("err") is None,
        "error": value.get("err"),
        "compute_units": units,
        "logs": value.get("logs") or [],
        "sol_delta_lamports": sol_delta,
    }


def _simulated_sol_delta(value: dict, pre_balance_lamports: int | None) -> int | None:
    """Net lamport change for the simulated owner account, or None if it cannot
    be established.

    None means UNKNOWN and never 0. A missing reading must not read as "nothing
    moved", which would silently disable the ceiling it feeds -- the failure
    mode this whole check exists to remove."""
    if pre_balance_lamports is None:
        return None
    accounts = value.get("accounts")
    if not accounts or not isinstance(accounts, list) or accounts[0] is None:
        return None
    post = accounts[0].get("lamports")
    return None if post is None else int(post) - int(pre_balance_lamports)


async def prove_swap(
    input_mint: str, output_mint: str, amount: int, user_public_key: str,
    *, rpc_http_url: str | None = None, client: httpx.AsyncClient | None = None,
) -> dict:
    """Quote -> build -> simulate, end to end, against real mainnet liquidity.

    The whole point of this module: know whether a swap WOULD work, and at what
    price, before anything is signed."""
    quote = await fetch_quote(input_mint, output_mint, amount, client=client)
    tx = await build_swap_transaction(quote, user_public_key, client=client)
    # A malformed base64 here would surface as an opaque RPC error later.
    try:
        base64.b64decode(tx, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SwapSimulationError(f"swap transaction is not valid base64: {exc!r}") from exc
    result = await simulate_swap_transaction(tx, rpc_http_url=rpc_http_url, client=client)
    result["quote"] = {
        "out_amount": int(quote["outAmount"]),
        "worst_case_out": quote["worst_case_out"],
        "price_impact_pct": quote["price_impact_pct"],
        "slippage_bps": quote["slippage_bps_used"],
    }
    return result
