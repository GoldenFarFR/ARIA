import logging

from fastapi import APIRouter, HTTPException, Request

from aria_core.gateway import telegram_bot
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(request: Request):
    if not telegram_bot.is_running():
        raise HTTPException(status_code=503, detail="Telegram bot not started")

    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if settings.telegram_webhook_secret and secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        payload = await request.json()
        await telegram_bot.process_webhook_update(payload)
    except Exception as exc:
        logger.error("Webhook Telegram error: %s", exc)
        raise HTTPException(status_code=500, detail="Webhook processing error") from exc

    return {"ok": True}