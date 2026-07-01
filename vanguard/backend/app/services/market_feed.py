from __future__ import annotations

import asyncio
import logging
import time

from app.models.schemas import MarketFeedResponse, PairSummary
from app.services import pair_store
from app.services.dexscreener import dexscreener_client

logger = logging.getLogger(__name__)

CHAIN_SEARCH_SEEDS: dict[str, list[str]] = {
    "solana": ["SOL", "BONK", "WIF", "JUP"],
    "ethereum": ["ETH", "PEPE", "SHIB"],
    "base": ["BRETT", "DEGEN", "AERO"],
    "bsc": ["BNB", "CAKE"],
    "arbitrum": ["ARB", "GMX"],
    "polygon": ["POL", "QUICK"],
    "avalanche": ["AVAX", "JOE"],
    "optimism": ["OP", "VELO"],
}


def _filter_chain(pairs: list[PairSummary], chain_id: str | None) -> list[PairSummary]:
    if not chain_id:
        return pairs
    return [p for p in pairs if p.chain_id.lower() == chain_id.lower()]


def _dedupe_pairs(pairs: list[PairSummary]) -> list[PairSummary]:
    seen: set[str] = set()
    out: list[PairSummary] = []
    for pair in pairs:
        key = f"{pair.chain_id}:{pair.pair_address.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(pair)
    return out


async def _resolve_boost_entries(
    entries: list[dict],
    *,
    limit: int,
) -> list[PairSummary]:
    pairs: list[PairSummary] = []
    for entry in entries[: max(limit * 2, 20)]:
        chain_id = entry.get("chainId") or entry.get("chain_id")
        token_address = entry.get("tokenAddress") or entry.get("token_address")
        if not chain_id or not token_address:
            continue
        try:
            pair = await dexscreener_client.resolve_token_to_best_pair(chain_id, token_address)
            if pair:
                pairs.append(pair)
        except Exception as exc:
            logger.debug("Boost resolve failed %s/%s: %s", chain_id, token_address, exc)
        if len(pairs) >= limit:
            break
    return pairs


async def _seed_search_pairs(chain_id: str | None, limit: int) -> list[PairSummary]:
    chains = [chain_id] if chain_id else list(CHAIN_SEARCH_SEEDS.keys())
    pairs: list[PairSummary] = []
    for chain in chains:
        for query in CHAIN_SEARCH_SEEDS.get(chain, ["USDC"])[:2]:
            try:
                results = await dexscreener_client.search(query)
                pairs.extend(p for p in results if p.chain_id == chain)
            except Exception as exc:
                logger.debug("Search seed failed %s/%s: %s", chain, query, exc)
    pairs = _dedupe_pairs(pairs)
    pairs.sort(key=lambda p: p.liquidity_usd or 0, reverse=True)
    return pairs[:limit]


async def get_trending_feed(
    *,
    chain_id: str | None = None,
    limit: int = 30,
) -> MarketFeedResponse:
    cached = await pair_store.get_pairs_by_feed("trending", chain_id=chain_id, limit=limit)
    if len(cached) >= min(limit, 10):
        return MarketFeedResponse(
            feed="trending",
            chain_id=chain_id,
            pairs=cached[:limit],
            total=len(cached[:limit]),
            source="index",
        )

    boosts = await dexscreener_client.get_token_boosts_top()
    pairs = _filter_chain(await _resolve_boost_entries(boosts, limit=limit), chain_id)
    pairs = _dedupe_pairs(pairs)[:limit]
    if pairs:
        await pair_store.upsert_pairs(pairs, "trending")
    return MarketFeedResponse(
        feed="trending",
        chain_id=chain_id,
        pairs=pairs,
        total=len(pairs),
        source="live",
    )


async def get_new_feed(
    *,
    chain_id: str | None = None,
    limit: int = 30,
) -> MarketFeedResponse:
    cached = await pair_store.get_new_pairs(chain_id=chain_id, limit=limit)
    if len(cached) >= min(limit, 10):
        return MarketFeedResponse(
            feed="new",
            chain_id=chain_id,
            pairs=cached[:limit],
            total=len(cached[:limit]),
            source="index",
        )

    boosts = await dexscreener_client.get_token_boosts_latest()
    profiles = await dexscreener_client.get_token_profiles_latest()
    merged = boosts + profiles
    pairs = _filter_chain(await _resolve_boost_entries(merged, limit=limit * 2), chain_id)
    pairs = _dedupe_pairs(pairs)
    pairs.sort(key=lambda p: p.pair_created_at or 0, reverse=True)
    pairs = pairs[:limit]
    if pairs:
        await pair_store.upsert_pairs(pairs, "new")
    return MarketFeedResponse(
        feed="new",
        chain_id=chain_id,
        pairs=pairs,
        total=len(pairs),
        source="live",
    )


async def _ranked_feed(
    *,
    feed: str,
    gainers: bool,
    chain_id: str | None,
    limit: int,
) -> MarketFeedResponse:
    cached = await pair_store.get_ranked_pairs(
        chain_id=chain_id,
        limit=limit,
        gainers=gainers,
    )
    if len(cached) >= min(limit, 10):
        return MarketFeedResponse(
            feed=feed,
            chain_id=chain_id,
            pairs=cached[:limit],
            total=len(cached[:limit]),
            source="index",
        )

    trending = await get_trending_feed(chain_id=chain_id, limit=limit)
    seeded = await _seed_search_pairs(chain_id, limit)
    pairs = _dedupe_pairs(trending.pairs + seeded)
    pairs = [p for p in pairs if p.price_change_h24 is not None]
    pairs.sort(key=lambda p: p.price_change_h24 or 0, reverse=gainers)
    pairs = pairs[:limit]
    if pairs:
        await pair_store.upsert_pairs(pairs, feed)
    return MarketFeedResponse(
        feed=feed,
        chain_id=chain_id,
        pairs=pairs,
        total=len(pairs),
        source="live",
    )


async def get_gainers_feed(
    *,
    chain_id: str | None = None,
    limit: int = 30,
) -> MarketFeedResponse:
    return await _ranked_feed(feed="gainers", gainers=True, chain_id=chain_id, limit=limit)


async def get_losers_feed(
    *,
    chain_id: str | None = None,
    limit: int = 30,
) -> MarketFeedResponse:
    return await _ranked_feed(feed="losers", gainers=False, chain_id=chain_id, limit=limit)


async def run_index_cycle() -> int:
    """Full index pass — trending + new + market seeds for DB enrichment."""
    started = time.monotonic()
    total = 0

    trending = await get_trending_feed(limit=40)
    total += len(trending.pairs)

    new = await get_new_feed(limit=40)
    total += len(new.pairs)

    seeded = await _seed_search_pairs(chain_id=None, limit=60)
    if seeded:
        total += await pair_store.upsert_pairs(seeded, "seed")

    gainers = await get_gainers_feed(limit=30)
    losers = await get_losers_feed(limit=30)
    total += len(gainers.pairs) + len(losers.pairs)

    logger.info(
        "Pair index cycle done — %d pairs touched in %.1fs",
        total,
        time.monotonic() - started,
    )
    return total


async def warm_feeds() -> None:
    """Prime feeds concurrently on startup (non-blocking caller)."""
    await asyncio.gather(
        get_trending_feed(limit=20),
        get_new_feed(limit=20),
        return_exceptions=True,
    )