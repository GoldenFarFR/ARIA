from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings
from app.models.schemas import PairSocial, PairSummary, PairTxns, TokenInfo, TxnPeriod


class DexScreenerClient:
    """DEXScreener API client with rate limiting (~60 req/min)."""

    def __init__(self) -> None:
        self.base_url = settings.dexscreener_base_url
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = 1.05 - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    async def _get_json(self, path: str, *, params: dict | None = None) -> Any:
        await self._throttle()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()

    async def search(self, query: str) -> list[PairSummary]:
        data = await self._get_json("/latest/dex/search", params={"q": query})
        return [self._parse_pair(item) for item in data.get("pairs", []) or []]

    async def get_pair(self, chain_id: str, pair_address: str) -> PairSummary | None:
        data = await self._get_json(f"/latest/dex/pairs/{chain_id}/{pair_address}")
        pairs = data.get("pairs") or data.get("pair")
        if isinstance(pairs, list):
            if not pairs:
                return None
            return self._parse_pair(pairs[0])
        if isinstance(pairs, dict):
            return self._parse_pair(pairs)
        return None

    async def get_token_pairs(self, chain_id: str, token_address: str) -> list[PairSummary]:
        data = await self._get_json(f"/token-pairs/v1/{chain_id}/{token_address}")
        if isinstance(data, list):
            return [self._parse_pair(item) for item in data]
        return []

    async def get_token_boosts_top(self) -> list[dict]:
        data = await self._get_json("/token-boosts/top/v1")
        return data if isinstance(data, list) else []

    async def get_token_boosts_latest(self) -> list[dict]:
        data = await self._get_json("/token-boosts/latest/v1")
        return data if isinstance(data, list) else []

    async def get_token_profiles_latest(self) -> list[dict]:
        data = await self._get_json("/token-profiles/latest/v1")
        return data if isinstance(data, list) else []

    async def resolve_token_to_best_pair(
        self,
        chain_id: str,
        token_address: str,
        *,
        min_liquidity: float = 1_000,
    ) -> PairSummary | None:
        # 19/07 -- bug réel trouvé côté aria-core (position paper-trading PLAZM,
        # en fait ESHARE) et corrigé au même endroit ici : /token-pairs/v1
        # renvoie TOUTE paire impliquant ``token_address``, y compris comme
        # simple QUOTE du pool d'un autre token de base. Sans ce filtre, la
        # vitrine publique pourrait afficher le prix/graphique d'un token
        # totalement différent de celui demandé.
        pairs = await self.get_token_pairs(chain_id, token_address)
        token_lower = (token_address or "").strip().lower()
        own_pairs = [p for p in pairs if (p.base_token.address or "").lower() == token_lower]
        eligible = [p for p in own_pairs if (p.liquidity_usd or 0) >= min_liquidity]
        if not eligible:
            eligible = own_pairs
        if not eligible:
            return None
        return max(eligible, key=lambda p: p.liquidity_usd or 0)

    def _parse_txns(self, raw: dict | None) -> PairTxns | None:
        if not raw:
            return None

        def period(key: str) -> TxnPeriod | None:
            block = raw.get(key)
            if not isinstance(block, dict):
                return None
            return TxnPeriod(buys=int(block.get("buys") or 0), sells=int(block.get("sells") or 0))

        txns = PairTxns(m5=period("m5"), h1=period("h1"), h6=period("h6"), h24=period("h24"))
        if not any((txns.m5, txns.h1, txns.h6, txns.h24)):
            return None
        return txns

    def _parse_pair(self, raw: dict) -> PairSummary:
        base = raw.get("baseToken", {})
        quote = raw.get("quoteToken", {})
        price_change = raw.get("priceChange") or {}
        volume = raw.get("volume") or {}
        liquidity = raw.get("liquidity") or {}
        info = raw.get("info") or {}
        boosts = raw.get("boosts") or {}

        price_usd = raw.get("priceUsd")
        socials: list[PairSocial] = []
        for item in info.get("socials") or []:
            if not isinstance(item, dict):
                continue
            socials.append(
                PairSocial(
                    platform=str(item.get("platform") or item.get("type") or ""),
                    handle=item.get("handle"),
                    url=item.get("url"),
                )
            )
        for link in raw.get("links") or []:
            if not isinstance(link, dict):
                continue
            link_type = str(link.get("type") or "website").lower()
            url = link.get("url")
            if url and link_type in ("twitter", "telegram", "discord"):
                socials.append(PairSocial(platform=link_type, url=url))

        websites = [
            str(w.get("url"))
            for w in (info.get("websites") or [])
            if isinstance(w, dict) and w.get("url")
        ]

        return PairSummary(
            chain_id=raw.get("chainId", ""),
            dex_id=raw.get("dexId", ""),
            pair_address=raw.get("pairAddress", ""),
            url=raw.get("url", ""),
            base_token=TokenInfo(
                address=base.get("address", ""),
                name=base.get("name", ""),
                symbol=base.get("symbol", ""),
            ),
            quote_token=TokenInfo(
                address=quote.get("address", ""),
                name=quote.get("name", ""),
                symbol=quote.get("symbol", ""),
            ),
            price_usd=float(price_usd) if price_usd is not None else None,
            price_native=raw.get("priceNative"),
            price_change_m5=price_change.get("m5"),
            price_change_h1=price_change.get("h1"),
            price_change_h6=price_change.get("h6"),
            price_change_h24=price_change.get("h24"),
            volume_m5=volume.get("m5"),
            volume_h1=volume.get("h1"),
            volume_h6=volume.get("h6"),
            volume_h24=volume.get("h24"),
            liquidity_usd=liquidity.get("usd"),
            liquidity_base=liquidity.get("base"),
            liquidity_quote=liquidity.get("quote"),
            market_cap=raw.get("marketCap"),
            fdv=raw.get("fdv"),
            pair_created_at=raw.get("pairCreatedAt"),
            labels=[str(x) for x in (raw.get("labels") or []) if x],
            txns=self._parse_txns(raw.get("txns")),
            boosts_active=int(boosts.get("active")) if boosts.get("active") is not None else None,
            image_url=info.get("imageUrl") or raw.get("icon"),
            websites=websites,
            socials=socials,
        )


dexscreener_client = DexScreenerClient()