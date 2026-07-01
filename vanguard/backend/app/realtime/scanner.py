from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.analysis.engine import analysis_engine
from app.config import settings
from app.database import get_watchlist, has_recent_alert, save_alert
from app.models.schemas import Alert, SignalType, Timeframe
from app.services.dexscreener import dexscreener_client

logger = logging.getLogger(__name__)


class RealtimeScanner:
    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self._subscribers: list[Callable[[Alert], Awaitable[None]]] = []

    def subscribe(self, callback: Callable[[Alert], Awaitable[None]]) -> None:
        self._subscribers.append(callback)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._scan_watchlist()
            except Exception as exc:
                logger.exception("Watchlist scan failed: %s", exc)
            await asyncio.sleep(settings.scan_interval_seconds)

    async def _scan_watchlist(self) -> None:
        watchlist = await get_watchlist()
        for item in watchlist:
            pair = await dexscreener_client.get_pair(item.chain_id, item.pair_address)
            if not pair:
                continue

            analysis = await analysis_engine.analyze_pair(
                pair,
                timeframes=[Timeframe.M5, Timeframe.H1],
            )
            for tf_analysis in analysis.timeframes:
                signal = tf_analysis.buy_signal
                if signal.signal_type not in (SignalType.BUY, SignalType.SELL):
                    continue
                if signal.score < 70 and signal.signal_type == SignalType.BUY:
                    continue

                tf_value = tf_analysis.timeframe.value
                st_value = signal.signal_type.value
                if await has_recent_alert(
                    item.chain_id,
                    item.pair_address,
                    st_value,
                    tf_value,
                    within_hours=settings.alert_cooldown_hours,
                ):
                    continue

                message = (
                    f"{pair.base_token.symbol} — {st_value.upper()} "
                    f"({tf_value}) score {signal.score}"
                )
                alert_id = await save_alert(
                    chain_id=item.chain_id,
                    pair_address=item.pair_address,
                    symbol=pair.base_token.symbol,
                    signal_type=st_value,
                    score=signal.score,
                    timeframe=tf_value,
                    message=message,
                )
                alert = Alert(
                    id=alert_id,
                    pair_address=item.pair_address,
                    chain_id=item.chain_id,
                    symbol=pair.base_token.symbol,
                    signal_type=signal.signal_type,
                    score=signal.score,
                    timeframe=tf_analysis.timeframe,
                    message=message,
                    created_at=datetime.now(timezone.utc),
                )
                for subscriber in self._subscribers:
                    await subscriber(alert)


realtime_scanner = RealtimeScanner()