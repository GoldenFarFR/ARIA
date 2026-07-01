from fastapi import APIRouter

from app.database import get_recent_alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(limit: int = 50):
    return await get_recent_alerts(limit)