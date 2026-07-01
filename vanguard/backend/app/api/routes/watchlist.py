from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.auth.visitor import require_visitor_id
from app.database import add_to_watchlist, get_watchlist, remove_from_watchlist

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistAddRequest(BaseModel):
    chain_id: str
    pair_address: str
    symbol: str


@router.get("")
async def list_watchlist(request: Request):
    visitor_id = require_visitor_id(request)
    return await get_watchlist(visitor_id)


@router.post("")
async def create_watchlist_item(body: WatchlistAddRequest, request: Request):
    visitor_id = require_visitor_id(request)
    return await add_to_watchlist(
        body.chain_id,
        body.pair_address,
        body.symbol,
        visitor_id,
    )


@router.delete("/{chain_id}/{pair_address}")
async def delete_watchlist_item(chain_id: str, pair_address: str, request: Request):
    visitor_id = require_visitor_id(request)
    removed = await remove_from_watchlist(chain_id, pair_address, visitor_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}