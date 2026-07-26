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
paid response (dome doctrine, wrapped in try/except)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from aria_core.services.smart_money import latest_score_for_wallet

logger = logging.getLogger(__name__)

router = APIRouter(tags=["x402-signals"])


async def _record_sale_if_paid(request: Request | None, product: str) -> None:
    payment_payload = getattr(getattr(request, "state", None), "payment_payload", None)
    if payment_payload is None:
        return  # no payment on this request (gate off, or free pre-check route)
    try:
        from aria_core.x402_revenue_ledger import record_sale
        from aria_core.x402_seller import price_for

        price = price_for(product)
        amount_usd = float(price.lstrip("$")) if price else 0.0
        payer = ""
        raw_payload = getattr(payment_payload, "payload", None) or {}
        authorization = raw_payload.get("authorization") or {}
        payer = str(authorization.get("from") or "")
        await record_sale(
            product=product, payer_address=payer, amount_usd=amount_usd, status="ok",
        )
    except Exception as exc:  # noqa: BLE001 -- never breaks the paid response
        logger.warning("x402_signals: failed to record sale for %s (%s)", product, exc)


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
    score = await latest_score_for_wallet(address)
    if score is None:
        raise HTTPException(status_code=404, detail="wallet not yet scored by ARIA")
    await _record_sale_if_paid(request, "wallet_score")
    return {"wallet": address.lower(), "composite_percentile": score}
