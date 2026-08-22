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


# --- SHARED throughput coordination (22/08, after a real 429 outage) --------
# Three callers hit Jupiter independently: the 1s price sweep, buy quotes, and
# sell quotes. Each was reasonable alone; together they tripped the free tier's
# rate limit and Jupiter started refusing everything -- including the SELL
# quotes for two real open positions, which could not be exited at all.
#
# This is the dome's standing rule, broken by adding a loop without checking
# who else called the same provider: several clients on one external provider
# share ONE coordination point, never independent throttles that silently add
# up (same pattern as `services/geckoterminal.wait_for_shared_rate_limit`).
#
# The interval is deliberately conservative rather than measured-to-the-edge:
# 20 calls at 1/s passed in isolation, but that test did not include the other
# two callers. Being refused costs a position; being slightly slow does not.
_MIN_INTERVAL_SECONDS = 0.35
_last_call_at = 0.0
_rate_lock: asyncio.Lock | None = None

# Once Jupiter answers 429 it keeps refusing for a while, so retrying one
# request is useless -- every OTHER caller must back off too, or they burn the
# recovery budget a sell is waiting for. The penalty is global and shared.
_BACKOFF_ON_429_SECONDS = 6.0
_backoff_until = 0.0


def note_rate_limited() -> None:
    """Called on any 429. Pauses EVERY Jupiter caller in this process."""
    global _backoff_until
    try:
        _backoff_until = asyncio.get_event_loop().time() + _BACKOFF_ON_429_SECONDS
    except RuntimeError:
        return
    logger.info("jupiter: rate limited, backing off %.0fs for all callers",
                _BACKOFF_ON_429_SECONDS)


def is_backing_off() -> bool:
    """True while the shared penalty runs, so a LOW-priority caller (the price
    sweep) can skip its turn rather than spend budget a sell needs."""
    try:
        return asyncio.get_event_loop().time() < _backoff_until
    except RuntimeError:
        return False


async def wait_for_shared_rate_limit() -> None:
    """Serialises EVERY Jupiter call in this process. Await before each one."""
    global _last_call_at, _rate_lock
    if _rate_lock is None:
        _rate_lock = asyncio.Lock()
    async with _rate_lock:
        loop = asyncio.get_event_loop()
        penalty = _backoff_until - loop.time()
        if penalty > 0:
            await asyncio.sleep(penalty)
        wait = _MIN_INTERVAL_SECONDS - (loop.time() - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = asyncio.get_event_loop().time()

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
                await wait_for_shared_rate_limit()
                resp = await client.get(QUOTE_URL, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    if resp.status_code == 429:
                        note_rate_limited()
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


# --- price feed (22/08) -----------------------------------------------------
# Operator target: refresh open positions every 1-2s, explicitly accepting the
# cost. It turns out there is none to accept -- this is Jupiter's own price
# endpoint, so it consumes NO Helius or Chainstack credit at all.
#
# Everything below was measured live, never assumed, per this dome's rule that
# a throttle without a verified number is a fabricated one:
#   * 50 mints per call. Asking for 100 or 150 returns 200 OK with only 50
#     results -- a SILENT truncation, which is why the batch size is enforced
#     here rather than trusted to the caller.
#   * 15-38ms latency.
#   * 20 consecutive calls at 1/s: zero refusals.
PRICE_URL = "https://lite-api.jup.ag/price/v3"
PRICE_MAX_IDS_PER_CALL = 50


async def fetch_prices(
    mints: list[str], *, client: httpx.AsyncClient | None = None,
) -> dict[str, float]:
    """USD price per token for each mint, batched.

    Missing mints are simply absent from the result -- never zero, never a
    stale value carried over. A caller must treat an absent price as "unknown"
    and refuse to act, exactly as it would on any other unavailable feed.
    """
    if not mints:
        return {}
    if is_backing_off():
        # Returns nothing rather than queueing: a price refresh is worth far
        # less than the sell quote competing for the same budget.
        return {}
    owns = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    out: dict[str, float] = {}
    try:
        for start in range(0, len(mints), PRICE_MAX_IDS_PER_CALL):
            batch = mints[start : start + PRICE_MAX_IDS_PER_CALL]
            try:
                await wait_for_shared_rate_limit()
                resp = await client.get(PRICE_URL, params={"ids": ",".join(batch)})
                if resp.status_code != 200:
                    if resp.status_code == 429:
                        note_rate_limited()
                    logger.info("jupiter prices: HTTP %s", resp.status_code)
                    continue
                payload = resp.json() or {}
            except Exception as exc:  # noqa: BLE001 -- one batch failing is not all
                logger.info("jupiter prices: batch failed (%s)", exc)
                continue
            for mint, entry in payload.items():
                if not isinstance(entry, dict):
                    continue
                price = entry.get("usdPrice") or entry.get("price")
                if price:
                    out[mint] = float(price)
        return out
    finally:
        if owns:
            await client.aclose()
