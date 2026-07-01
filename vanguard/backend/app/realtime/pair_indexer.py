from __future__ import annotations

import asyncio
import logging

from app.services import market_feed, pair_store

logger = logging.getLogger(__name__)


class PairIndexer:
    def __init__(self, interval_seconds: int = 300) -> None:
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await pair_store.init_pair_store()
        self._task = asyncio.create_task(self._loop())
        asyncio.create_task(market_feed.warm_feeds())
        logger.info("Pair indexer started (interval=%ds)", self.interval_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await market_feed.run_index_cycle()
            except Exception as exc:
                logger.exception("Pair index cycle failed: %s", exc)
            await asyncio.sleep(self.interval_seconds)


pair_indexer = PairIndexer()