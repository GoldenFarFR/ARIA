"""Aria Market Pro — abonnements Stripe liés au Privy DID."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.auth.access_code import DB_PATH, init_auth_db
from app.config import settings

logger = logging.getLogger(__name__)

PLAN_ID = "dexpulse_pro"
ACTIVE_STATUSES = frozenset({"active", "trialing"})


async def init_billing_db() -> None:
    await init_auth_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                privy_did TEXT PRIMARY KEY,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                status TEXT NOT NULL DEFAULT 'inactive',
                plan TEXT NOT NULL DEFAULT 'dexpulse_pro',
                current_period_end TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def get_subscription(privy_did: str) -> dict[str, Any] | None:
    await init_billing_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM subscriptions WHERE privy_did = ?",
            (privy_did,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def is_pro_active(privy_did: str) -> bool:
    sub = await get_subscription(privy_did)
    if not sub:
        return False
    return str(sub.get("status", "")).lower() in ACTIVE_STATUSES


async def upsert_subscription(
    *,
    privy_did: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    status: str = "inactive",
    plan: str = PLAN_ID,
    current_period_end: str | None = None,
) -> None:
    await init_billing_db()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO subscriptions (
                privy_did, stripe_customer_id, stripe_subscription_id,
                status, plan, current_period_end, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(privy_did) DO UPDATE SET
                stripe_customer_id = COALESCE(excluded.stripe_customer_id, subscriptions.stripe_customer_id),
                stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, subscriptions.stripe_subscription_id),
                status = excluded.status,
                plan = excluded.plan,
                current_period_end = excluded.current_period_end,
                updated_at = excluded.updated_at
            """,
            (
                privy_did,
                stripe_customer_id,
                stripe_subscription_id,
                status,
                plan,
                current_period_end,
                now,
            ),
        )
        await db.commit()


def stripe_configured() -> bool:
    return bool((settings.stripe_secret_key or "").strip() and (settings.stripe_price_id or "").strip())


def _stripe():
    import stripe

    stripe.api_key = settings.stripe_secret_key.strip()
    return stripe


async def create_checkout_session(
    *,
    privy_did: str,
    success_url: str,
    cancel_url: str,
) -> str:
    if not stripe_configured():
        raise RuntimeError("Stripe is not configured (STRIPE_SECRET_KEY, STRIPE_PRICE_ID).")

    stripe = _stripe()
    existing = await get_subscription(privy_did)
    customer_id = (existing or {}).get("stripe_customer_id")

    if not customer_id:
        customer = stripe.Customer.create(metadata={"privy_did": privy_did})
        customer_id = customer.id
        await upsert_subscription(
            privy_did=privy_did,
            stripe_customer_id=customer_id,
            status="pending_checkout",
        )

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": settings.stripe_price_id.strip(), "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"privy_did": privy_did, "plan": PLAN_ID},
        subscription_data={"metadata": {"privy_did": privy_did, "plan": PLAN_ID}},
        allow_promotion_codes=True,
    )
    if not session.url:
        raise RuntimeError("Stripe Checkout session has no URL.")
    return session.url


async def handle_stripe_event(payload: bytes, signature: str | None) -> dict[str, Any]:
    if not stripe_configured():
        return {"ok": False, "error": "stripe_not_configured"}
    secret = (settings.stripe_webhook_secret or "").strip()
    if not secret:
        return {"ok": False, "error": "webhook_secret_missing"}

    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(payload, signature or "", secret)
    except Exception as exc:
        logger.warning("Stripe webhook verify failed: %s", exc)
        return {"ok": False, "error": "invalid_signature"}

    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        privy_did = (data.get("metadata") or {}).get("privy_did")
        sub_id = data.get("subscription")
        cust_id = data.get("customer")
        if privy_did:
            await upsert_subscription(
                privy_did=privy_did,
                stripe_customer_id=str(cust_id) if cust_id else None,
                stripe_subscription_id=str(sub_id) if sub_id else None,
                status="active",
            )

    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        privy_did = (data.get("metadata") or {}).get("privy_did")
        if not privy_did and data.get("customer"):
            privy_did = await _privy_from_customer(str(data["customer"]))
        status = str(data.get("status", "inactive"))
        period_end = None
        if data.get("current_period_end"):
            period_end = datetime.fromtimestamp(
                int(data["current_period_end"]), tz=timezone.utc,
            ).isoformat()
        if privy_did:
            await upsert_subscription(
                privy_did=privy_did,
                stripe_customer_id=str(data.get("customer") or "") or None,
                stripe_subscription_id=str(data.get("id") or "") or None,
                status=status,
                current_period_end=period_end,
            )

    elif etype == "customer.subscription.deleted":
        privy_did = (data.get("metadata") or {}).get("privy_did")
        if not privy_did and data.get("customer"):
            privy_did = await _privy_from_customer(str(data["customer"]))
        if privy_did:
            await upsert_subscription(
                privy_did=privy_did,
                stripe_subscription_id=str(data.get("id") or "") or None,
                status="canceled",
            )

    return {"ok": True, "type": etype}


async def _privy_from_customer(customer_id: str) -> str | None:
    await init_billing_db()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT privy_did FROM subscriptions WHERE stripe_customer_id = ?",
            (customer_id,),
        )
        row = await cursor.fetchone()
    return str(row[0]) if row else None