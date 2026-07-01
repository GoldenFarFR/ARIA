"""Stripe billing — Aria Market Pro subscription."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.billing.subscriptions import (
    PLAN_ID,
    create_checkout_session,
    get_subscription,
    handle_stripe_event,
    is_pro_active,
    stripe_configured,
)
from app.config import settings
from app.games.scores import get_session_identity

router = APIRouter(prefix="/billing", tags=["billing"])


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _require_member(authorization: str | None) -> str:
    identity = await get_session_identity(_bearer(authorization))
    if not identity:
        raise HTTPException(
            status_code=401,
            detail="Connecte-toi via Aria Vanguard (Privy) avant de t'abonner.",
        )
    return identity[0]


class PlanResponse(BaseModel):
    plan_id: str
    name: str
    price_usd: float
    interval: str
    stripe_configured: bool
    features: list[str]


class CheckoutBody(BaseModel):
    success_url: str | None = Field(default=None, max_length=500)
    cancel_url: str | None = Field(default=None, max_length=500)


class CheckoutResponse(BaseModel):
    checkout_url: str


class SubscriptionStatus(BaseModel):
    plan: str
    active: bool
    status: str
    current_period_end: str | None = None


@router.get("/plan", response_model=PlanResponse)
async def get_plan():
    return PlanResponse(
        plan_id=PLAN_ID,
        name="Aria Market Pro",
        price_usd=float(settings.market_pro_price_usd),
        interval="month",
        stripe_configured=stripe_configured(),
        features=[
            "Alertes watchlist prioritaires",
            "Brief signaux hebdomadaire",
            "Accès membre Aria Market sans friction",
            "Support Telegram Pro (bientôt)",
        ],
    )


@router.get("/status", response_model=SubscriptionStatus)
async def subscription_status(authorization: str | None = Header(None)):
    privy_did = await _require_member(authorization)
    sub = await get_subscription(privy_did)
    active = await is_pro_active(privy_did)
    status = str((sub or {}).get("status", "none"))
    return SubscriptionStatus(
        plan=PLAN_ID,
        active=active,
        status=status,
        current_period_end=(sub or {}).get("current_period_end"),
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def start_checkout(
    body: CheckoutBody,
    authorization: str | None = Header(None),
):
    if not stripe_configured():
        raise HTTPException(
            status_code=503,
            detail="Stripe pas encore configuré — ajoute STRIPE_SECRET_KEY et STRIPE_PRICE_ID.",
        )
    privy_did = await _require_member(authorization)
    holding = settings.public_holding_url.rstrip("/")
    success = (body.success_url or f"{holding}/?sub=success#pricing").strip()
    cancel = (body.cancel_url or f"{holding}/?sub=cancel#pricing").strip()
    try:
        url = await create_checkout_session(
            privy_did=privy_did,
            success_url=success,
            cancel_url=cancel,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CheckoutResponse(checkout_url=url)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    result = await handle_stripe_event(payload, signature)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "webhook_failed"))
    return {"received": True, "type": result.get("type")}