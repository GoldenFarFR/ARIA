"""x402-payable signal endpoints -- ARIA's own composite judgment, sold to other
agents via the x402 protocol. Only mounted on the app when x402_seller_ready()
is True (app/x402_seller.py) -- gated OFF by default, never registered on a
deployment that hasn't explicitly enabled and configured it.

v0 scope: wallet score only (aria_core.services.smart_money.latest_score_for_wallet,
a pure read of the already-cached wallet_score_log -- never a live re-scan, never
a third-party raw-data pass-through). Extending to the substance signals
(GitHub/Website/Docs/X) waits on the persisted cache layer (backlog #40) and on
written provider ToS clearance (docs/conformite-dossier-avocat.md §7).

Known v0 limitation, not yet resolved: the x402 payment middleware charges BEFORE
this handler runs, based on route match alone -- a caller paying for a wallet
ARIA has never scored still gets charged, even though the answer is "not found".
The free /walletscore/exists pre-check below exists specifically so a
well-behaved caller can avoid that outcome, but nothing forces them to use it.
Worth revisiting (refund logic, or a free-tier existence check enforced some
other way) before this ever accepts a real payment.

26/07 -- ``x402_revenue_ledger.record_sale()`` existed since 07/24 but was never
actually CALLED anywhere on the payment path (found while finishing #39): the
ledger would have stayed empty forever even after real sales started settling.
``_record_sale_if_paid`` closes that gap, reading the payer address off
``request.state.payment_payload`` (set by the x402 middleware on
``payment-verified``, see ``x402.http.middleware.fastapi.payment_middleware``).
Known limitation, same honesty as the pre-charge issue above: the SDK settles
the payment AFTER this handler returns (no post-settlement hook is exposed at
the app level in x402 2.16.0), so this records "ok" the moment a payment-
verified request is served successfully, not after settlement is technically
confirmed on-chain -- verification is a strong cryptographic guarantee already
(the buyer's EIP-3009 signature was checked before the handler ever runs), and
a facilitator-side settlement failure would itself surface as an error response
before reaching this line. Never let a ledger-write failure break the actual
paid response (dome doctrine, wrapped in try/except).

31/07 -- B20 route (services/b20.py) added, with anti-abuse guardrails baked
in from the start rather than patched on after (operator's explicit request,
before the B20 route existed): strict address format validation (never lets
a malformed string reach the RPC/cache layer), a per-payer rate limit (reuses
``x402_revenue_ledger``'s already-recorded sales, no new table), and no raw
error detail ever reaches the client (every internal failure degrades to a
generic message, the real detail only in server logs). Known, SAME structural
limitation as the wallet_score pre-charge issue above -- unresolved by this
SDK version: the rate-limit check runs AFTER the payment middleware has
already charged the buyer (there's no hook to check it before), so a payer
who's already over quota is charged and then told so -- documented honestly
here, not silently accepted as "fine"."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query, Request

from aria_core.services.smart_money import latest_score_for_wallet

logger = logging.getLogger(__name__)

router = APIRouter(tags=["x402-signals"])

# Plain 20-byte hex address, 0x-prefixed -- no checksum validation here
# (web3.py's to_checksum_address already normalizes case downstream; this is
# purely a cheap, pre-payment format gate to reject garbage before it ever
# reaches the RPC/cache layer).
_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# 31/07 -- B20 route anti-abuse: starting value, NOT empirically calibrated
# against real volume (no real traffic exists yet) -- same doctrine as any
# other uncalibrated threshold in this project (docs/api-rate-limit-
# calibration.md): documented as a starting point, revisit once real usage
# data exists rather than treating this as a measured fact.
_B20_RATE_LIMIT_MAX_REQUESTS = 30
_B20_RATE_LIMIT_WINDOW_SECONDS = 3600.0


def _extract_payer_address(request: Request | None) -> str:
    """The buyer's address from the x402 middleware's verified payment
    payload, or "" if there is none (gate off, free pre-check route, or the
    payload shape changes in a future SDK version -- never raises)."""
    payment_payload = getattr(getattr(request, "state", None), "payment_payload", None)
    if payment_payload is None:
        return ""
    raw_payload = getattr(payment_payload, "payload", None) or {}
    authorization = raw_payload.get("authorization") or {}
    return str(authorization.get("from") or "")


async def _record_sale_if_paid(request: Request | None, product: str) -> None:
    payer = _extract_payer_address(request)
    if not payer:
        return  # no payment on this request (gate off, or free pre-check route)
    try:
        from aria_core.x402_revenue_ledger import record_sale
        from aria_core.x402_seller import price_for

        price = price_for(product)
        amount_usd = float(price.lstrip("$")) if price else 0.0
        await record_sale(
            product=product, payer_address=payer, amount_usd=amount_usd, status="ok",
        )
    except Exception as exc:  # noqa: BLE001 -- never breaks the paid response
        logger.warning("x402_signals: failed to record sale for %s (%s)", product, exc)
        return
    await _notify_sale(product=product, payer=payer, amount_usd=amount_usd)


async def _notify_sale(*, product: str, payer: str, amount_usd: float) -> None:
    """Telegram alert on every real x402 sale (05/08, operator request).
    Best-effort, same dome doctrine as the ledger write above: a notify
    failure never breaks the already-paid response, only logged.

    Includes the product's rolling success rate (05/08, operator request:
    "éviter de faire payer un x402 cassé") -- a broken product is visible on
    every sale notification, not just discoverable by someone digging into
    a separate dashboard."""
    try:
        from aria_core.gateway.telegram_bot import send_message
        from aria_core.x402_product_health import success_rate

        health = await success_rate(product)
        rate_line = (
            f"Taux de réussite (50 derniers appels) : {health['rate_pct']}% "
            f"({health['successes']}/{health['attempts']})"
            if health["rate_pct"] is not None
            else "Taux de réussite : pas encore de donnée"
        )
        await send_message(
            f"\U0001f4b0 Vente x402 réelle : {product} (${amount_usd:.2f})\n"
            f"Payeur : {payer}\n"
            f"{rate_line}"
        )
    except Exception as exc:  # noqa: BLE001 -- never breaks the paid response
        logger.warning("x402_signals: sale notify failed for %s (%s)", product, exc)


@router.get("/walletscore/exists")
async def x402_wallet_score_exists(address: str = Query(..., min_length=10)):
    """FREE pre-check (not payment-gated -- not listed in x402_seller.mount_x402_seller's
    routes dict). Lets a caller avoid paying for a wallet ARIA has never scored."""
    score = await latest_score_for_wallet(address)
    return {"wallet": address.lower(), "scored": score is not None}


@router.get("/walletscore")
async def x402_wallet_score(address: str = Query(..., min_length=10), request: Request = None):
    """PAID (x402-gated when mounted). Returns ARIA's own cached composite wallet
    score -- never a live re-scan, never a raw third-party data pass-through."""
    from aria_core.x402_product_health import record_attempt

    score = await latest_score_for_wallet(address)
    if score is None:
        await record_attempt("wallet_score", "no_result")
        raise HTTPException(status_code=404, detail="wallet not yet scored by ARIA")
    await record_attempt("wallet_score", "success")
    await _record_sale_if_paid(request, "wallet_score")
    return {"wallet": address.lower(), "composite_percentile": score}


@router.get("/b20score/exists")
async def x402_b20_score_exists(contract: str = Query(..., min_length=10)):
    """FREE pre-check (not payment-gated). Validates the address format and
    resolves ``isB20()`` -- cheap (one RPC call, no role-history scan) -- so a
    well-behaved caller can confirm the target is a real B20 before paying
    for the full role-holder verdict."""
    if not _ETH_ADDRESS_RE.match(contract or ""):
        raise HTTPException(status_code=400, detail="invalid contract address format")
    from aria_core.services import b20

    try:
        confirmed = await b20.is_b20(contract)
    except Exception as exc:  # noqa: BLE001 -- never a raw error to the client
        logger.warning("x402_signals: b20 exists check failed for %s (%s)", contract, exc)
        confirmed = None
    return {"contract": contract.lower(), "is_b20": confirmed}


@router.get("/b20score")
async def x402_b20_score(contract: str = Query(..., min_length=10), request: Request = None):
    """PAID (x402-gated when mounted). Returns ARIA's own B20 role-holder
    safety verdict (services/b20.py, cache-first -- most requests are served
    from the 3h cache, not a fresh RPC scan). Never a raw third-party data
    pass-through -- every field here is ARIA's own computed judgment
    (isB20()'s own authority + a replayed on-chain role-grant history).

    Address format is validated BEFORE anything else -- a malformed string
    never reaches the RPC/cache layer. The per-payer rate limit is checked
    next: same structural limitation as ``_record_sale_if_paid``'s own
    known-limitation note (the SDK already charged the buyer before this
    handler runs), documented honestly rather than pretended away."""
    from aria_core.x402_product_health import record_attempt

    if not _ETH_ADDRESS_RE.match(contract or ""):
        await record_attempt("b20_safety", "error")
        raise HTTPException(status_code=400, detail="invalid contract address format")

    payer = _extract_payer_address(request)
    if payer:
        try:
            from aria_core.x402_revenue_ledger import recent_sale_count

            recent = await recent_sale_count(
                payer, "b20_safety", window_seconds=_B20_RATE_LIMIT_WINDOW_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 -- never blocks on a rate-limit check failure
            logger.warning("x402_signals: b20 rate-limit check failed for payer (%s)", exc)
            recent = 0
        if recent >= _B20_RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(status_code=429, detail="rate limit exceeded for this product")

    from aria_core.services import b20

    try:
        verdict = await b20.evaluate_b20_safety(contract)
    except Exception as exc:  # noqa: BLE001 -- never a raw error/stack trace to a paying client
        logger.warning("x402_signals: b20 scan failed for %s (%s)", contract, exc)
        await record_attempt("b20_safety", "error")
        raise HTTPException(status_code=502, detail="scan temporarily unavailable") from None

    await record_attempt("b20_safety", "no_result" if verdict.verdict == "opaque" else "success")
    await _record_sale_if_paid(request, "b20_safety")
    scanned_at = await b20.cached_scan_timestamp(contract)
    return {
        "contract": contract.lower(),
        "b20_verdict": verdict.verdict,
        "reason": verdict.reason,
        "role_holders": {name: sorted(holders) for name, holders in verdict.role_holders.items()},
        "scanned_at": scanned_at,
    }
