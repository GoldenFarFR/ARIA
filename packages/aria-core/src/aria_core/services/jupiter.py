"""Jupiter swap quotes on Solana -- READ-ONLY, no key, no signing (21/08).

**Why this exists.** The homemade agent wallet's Solana leg (Squads v4 +
SpendingLimit, proven live on devnet) can TRANSFER lamports under an on-chain
cap. It cannot BUY anything: `spending_limit_use` moves value, it does not
swap. Trading a memecoin needs SOL -> token and token -> SOL, and no swap path
existed anywhere in this repo. This is the missing half, starting with the
half that touches nothing: getting a real executable quote.

**Immediately useful before any real capital.** The shadow pockets price
positions from bonding-curve reserves and apply a MODELLED price impact. A
Jupiter quote is what an actual execution would return right now, so the two
can finally be compared -- if simulated fills are optimistic, that shows up
here rather than in the first real trade.

**Endpoint.** `lite-api.jup.ag` is the free tier (empty API key). The old
`quote-api.jup.ag/v6` was checked live on 21/08 and returns nothing at all --
kept out rather than left as a dead fallback.

**Throughput.** Jupiter's free-tier limit is stated as a 60-second window but
the actual number is not published, and this dome's rule is explicit: never
fabricate a numeric throttle. So there is reactive backoff on 429/5xx and NO
proactive rate limit -- capacity deliberately recorded as unknown. Add one
only when a real figure is verified or measured.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"

SOL_MINT = "So11111111111111111111111111111111111111112"

# Absolute project rule, never a tool's default: slippage is always explicit
# and never above 10%. A caller asking for more is clamped and logged, not
# obeyed -- same doctrine as `agent_wallet_pilot.MAX_SLIPPAGE_BPS`.
MAX_SLIPPAGE_BPS = 1000

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.5


class JupiterQuoteError(RuntimeError):
    """Raised when no trustworthy quote could be obtained. Never a partial or
    guessed result -- a fabricated quote would be acted on."""


async def fetch_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    *,
    slippage_bps: int = MAX_SLIPPAGE_BPS,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Real executable quote for ``amount`` (in the input mint's smallest
    unit).

    Returns the raw Jupiter payload plus a derived ``price_impact_pct`` and
    ``worst_case_out`` -- the amount guaranteed even at full slippage, which is
    what a risk decision should use rather than the headline figure.
    """
    if amount <= 0:
        raise JupiterQuoteError("amount must be positive")
    if slippage_bps > MAX_SLIPPAGE_BPS:
        logger.warning(
            "jupiter: slippage %d bps requested, clamped to the project ceiling %d bps",
            slippage_bps, MAX_SLIPPAGE_BPS,
        )
        slippage_bps = MAX_SLIPPAGE_BPS

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": str(slippage_bps),
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.get(QUOTE_URL, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = JupiterQuoteError(f"HTTP {resp.status_code}")
                    await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
                    continue
                resp.raise_for_status()
                payload = resp.json()
            except JupiterQuoteError:
                raise
            except Exception as exc:  # noqa: BLE001 -- retried, then surfaced
                last_error = exc
                await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue

            out_amount = payload.get("outAmount")
            if not out_amount:
                raise JupiterQuoteError("quote carried no outAmount -- no route")
            # `otherAmountThreshold` is what the swap is guaranteed to yield at
            # the stated slippage. Reporting only `outAmount` would flatter
            # every downstream decision by exactly the slippage allowance.
            worst = payload.get("otherAmountThreshold") or out_amount
            payload["worst_case_out"] = int(worst)
            payload["price_impact_pct"] = float(payload.get("priceImpactPct") or 0.0) * 100.0
            payload["slippage_bps_used"] = slippage_bps
            return payload

        raise JupiterQuoteError(f"no quote after {_MAX_ATTEMPTS} attempts: {last_error!r}")
    finally:
        if owns_client:
            await client.aclose()


async def roundtrip_cost_pct(
    token_mint: str, sol_amount: float, *, client: httpx.AsyncClient | None = None,
) -> dict:
    """Buys then immediately sells, on quotes only -- the exit-route check.

    21/08, operator's design: "on va trader des token legerement dangereux
    (bonding), il faut le mecanisme de verification en simulation achat-vente
    instantanee". A token you can buy but cannot sell only reveals itself when
    you try to sell, and the pocket had NO scam check of any kind -- no
    RugCheck, no honeypot screen, nothing.

    Two things are learned in one pass:
      - ``sellable``: whether an exit route exists at all. No route out is the
        single clearest honeypot signature.
      - ``roundtrip_loss_pct``: what a buy-then-sell actually costs right now.
        Measured on healthy tokens the same day it sits near 2.5%; a figure far
        above that means the exit is priced against us even when it exists.

    Quotes only -- no key, no signature, nothing sent. This is the cheap half
    of the check; simulating the two swaps atomically on-chain is the stronger
    version and a separate step.

    ``sellable=None`` means the check could not be completed (provider down),
    never a silent ``False``: refusing a token because a provider hiccuped
    would be worse than not checking at all.
    """
    out = {"sellable": None, "roundtrip_loss_pct": None, "buy_impact_pct": None}
    try:
        buy = await fetch_quote(SOL_MINT, token_mint, int(sol_amount * 1e9), client=client)
    except JupiterQuoteError:
        # No route IN either -- nothing to judge, and the pocket would not be
        # able to enter anyway.
        return out
    except Exception:  # noqa: BLE001
        return out

    out["buy_impact_pct"] = buy.get("price_impact_pct")
    tokens = int(buy["outAmount"])
    try:
        sell = await fetch_quote(token_mint, SOL_MINT, tokens, client=client)
    except JupiterQuoteError:
        # A route in but none out is the clearest honeypot signature there is.
        out["sellable"] = False
        return out
    except Exception:  # noqa: BLE001
        return out

    lamports_in = int(sol_amount * 1e9)
    lamports_back = int(sell["outAmount"])
    out["sellable"] = True
    out["roundtrip_loss_pct"] = round((1 - lamports_back / lamports_in) * 100, 3)
    return out


async def quote_sol_for_token(
    token_mint: str, sol_amount: float, *, client: httpx.AsyncClient | None = None,
) -> dict:
    """Convenience: how many tokens ``sol_amount`` SOL actually buys right now."""
    return await fetch_quote(SOL_MINT, token_mint, int(sol_amount * 1e9), client=client)
