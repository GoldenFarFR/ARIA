from fastapi import APIRouter, HTTPException, Query

from app.analysis.engine import analysis_engine
from app.models.schemas import PairAnalysis, Timeframe
from app.services.dexscreener import dexscreener_client

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/{chain_id}/{pair_address}", response_model=PairAnalysis)
async def analyze_pair(
    chain_id: str,
    pair_address: str,
    timeframes: str | None = Query(None, description="Comma-separated timeframes, e.g. 5m,1h,4h"),
):
    pair = await dexscreener_client.get_pair(chain_id, pair_address)
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")

    selected: list[Timeframe] | None = None
    if timeframes:
        try:
            selected = [Timeframe(tf.strip()) for tf in timeframes.split(",") if tf.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid timeframe") from exc
        if len(selected) < 2 or len(selected) > 6:
            raise HTTPException(status_code=400, detail="Select between 2 and 6 timeframes")

    return await analysis_engine.analyze_pair(pair, selected)