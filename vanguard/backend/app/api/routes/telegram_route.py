import logging
import secrets as _secrets

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from aria_core.gateway import telegram_bot
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


async def _process_update_safely(payload: dict) -> None:
    """Traite l'update en arrière-plan. On a DÉJÀ répondu 200 à Telegram, donc
    une exception ici ne doit pas provoquer de retry (sinon boucle de spam) :
    on la logue et on l'absorbe."""
    try:
        await telegram_bot.process_webhook_update(payload)
    except Exception as exc:
        logger.error("Webhook Telegram — traitement en arrière-plan échoué: %s", exc)


@router.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    if not telegram_bot.is_running():
        raise HTTPException(status_code=503, detail="Telegram bot not started")

    # Fail-CLOSED : sans secret configuré, l'endpoint accepterait des updates FORGÉS
    # (n'importe qui pourrait POSTer un faux message Telegram et piloter le bot). On refuse
    # plutôt que d'exposer le bot. Prérequis : TELEGRAM_WEBHOOK_SECRET dans le .env du VPS,
    # puis relancer setWebhook (le bot y injecte automatiquement secret_token).
    configured = (settings.telegram_webhook_secret or "").strip()
    if not configured:
        logger.error(
            "Webhook Telegram REFUSÉ : TELEGRAM_WEBHOOK_SECRET absent. "
            "Configure-le dans le .env puis relance setWebhook."
        )
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    # Comparaison à temps constant : évite une attaque temporelle sur le secret.
    if not _secrets.compare_digest(provided, configured):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        payload = await request.json()
    except Exception as exc:
        logger.error("Webhook Telegram — payload invalide: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid payload") from exc

    # Accusé de réception IMMÉDIAT : Telegram exige un 200 rapide, sinon il
    # redélivre le même update en boucle (une analyse /vc dure 20-40 s). On rend
    # la main tout de suite et l'analyse tourne en tâche de fond.
    background_tasks.add_task(_process_update_safely, payload)
    return {"ok": True}