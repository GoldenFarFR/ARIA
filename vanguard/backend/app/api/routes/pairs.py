from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    Candle,
    ChainDiscoverGroup,
    DiscoverResponse,
    MarketFeedResponse,
    PairIndexStats,
    SearchResponse,
    Timeframe,
)
from app.services import market_feed, pair_store
from app.services.chain_mapping import DEXSCREENER_TO_GECKO
from app.services.dexscreener import dexscreener_client
from app.services.geckoterminal import geckoterminal_client

router = APIRouter(prefix="/pairs", tags=["pairs"])

CHAIN_LABELS: dict[str, str] = {
    "solana": "Solana",
    "ethereum": "Ethereum",
    "base": "Base",
    "bsc": "BNB Chain",
    "arbitrum": "Arbitrum",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "optimism": "Optimism",
}

# Tokens populaires par blockchain pour la découverte
DISCOVER_QUERIES: dict[str, list[str]] = {
    "solana": ["WIF", "BONK", "JUP", "RAY"],
    "ethereum": ["PEPE", "SHIB", "LINK"],
    "base": ["BRETT", "DEGEN", "AERO"],
    "bsc": ["CAKE", "BNB"],
    "arbitrum": ["ARB", "GMX"],
    "polygon": ["POL", "QUICK"],
}


@router.get("/trending", response_model=MarketFeedResponse)
async def trending_pairs(
    chain_id: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    return await market_feed.get_trending_feed(chain_id=chain_id, limit=limit)


@router.get("/new", response_model=MarketFeedResponse)
async def new_pairs(
    chain_id: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    return await market_feed.get_new_feed(chain_id=chain_id, limit=limit)


@router.get("/gainers", response_model=MarketFeedResponse)
async def gainers_pairs(
    chain_id: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    return await market_feed.get_gainers_feed(chain_id=chain_id, limit=limit)


@router.get("/losers", response_model=MarketFeedResponse)
async def losers_pairs(
    chain_id: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    return await market_feed.get_losers_feed(chain_id=chain_id, limit=limit)


@router.get("/indexed/stats", response_model=PairIndexStats)
async def indexed_stats():
    return await pair_store.get_index_stats()


@router.get("/token/{chain_id}/{token_address}")
async def token_pairs(chain_id: str, token_address: str):
    pairs = await dexscreener_client.get_token_pairs(chain_id, token_address)
    if not pairs:
        raise HTTPException(status_code=404, detail="No pairs for token")
    return {"chain_id": chain_id, "token_address": token_address, "pairs": pairs}


@router.get("/discover", response_model=DiscoverResponse)
async def discover_pairs():
    """Paires populaires groupées par blockchain (analysables)."""
    groups: list[ChainDiscoverGroup] = []
    for chain_id, queries in DISCOVER_QUERIES.items():
        if chain_id not in DEXSCREENER_TO_GECKO:
            continue
        seen: set[str] = set()
        picked = []
        for q in queries:
            try:
                results = await dexscreener_client.search(q)
            except Exception:
                continue
            chain_pairs = [
                p for p in results
                if p.chain_id == chain_id
                and p.pair_address not in seen
                and (p.liquidity_usd or 0) >= 5_000
            ]
            if not chain_pairs:
                continue
            best = max(chain_pairs, key=lambda p: p.liquidity_usd or 0)
            seen.add(best.pair_address)
            picked.append(best)
        if picked:
            groups.append(
                ChainDiscoverGroup(
                    chain_id=chain_id,
                    label=CHAIN_LABELS.get(chain_id, chain_id.title()),
                    pairs=picked[:6],
                )
            )
    return DiscoverResponse(chains=groups)


@router.get("/search", response_model=SearchResponse)
async def search_pairs(q: str = Query(..., min_length=1)):
    pairs = await dexscreener_client.search(q)
    supported = [p for p in pairs if p.chain_id.lower() in DEXSCREENER_TO_GECKO]
    return SearchResponse(query=q, pairs=(supported or pairs)[:30])


@router.get("/{chain_id}/{pair_address}")
async def get_pair(chain_id: str, pair_address: str):
    pair = await dexscreener_client.get_pair(chain_id, pair_address)
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    return pair


@router.get("/{chain_id}/{pair_address}/candles")
async def get_candles(
    chain_id: str,
    pair_address: str,
    timeframe: Timeframe = Timeframe.M5,
    limit: int = Query(200, ge=10, le=500),
):
    candles = await geckoterminal_client.get_ohlcv(chain_id, pair_address, timeframe, limit)
    return {"timeframe": timeframe, "candles": candles}