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
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from aria_core.services.smart_money import latest_score_for_wallet

router = APIRouter(tags=["x402-signals"])


@router.get("/walletscore/exists")
async def x402_wallet_score_exists(address: str = Query(..., min_length=10)):
    """FREE pre-check (not payment-gated -- not listed in x402_seller.mount_x402_seller's
    routes dict). Lets a caller avoid paying for a wallet ARIA has never scored."""
    score = await latest_score_for_wallet(address)
    return {"wallet": address.lower(), "scored": score is not None}


@router.get("/walletscore")
async def x402_wallet_score(address: str = Query(..., min_length=10)):
    """PAID (x402-gated when mounted). Returns ARIA's own cached composite wallet
    score -- never a live re-scan, never a raw third-party data pass-through."""
    score = await latest_score_for_wallet(address)
    if score is None:
        raise HTTPException(status_code=404, detail="wallet not yet scored by ARIA")
    return {"wallet": address.lower(), "composite_percentile": score}
