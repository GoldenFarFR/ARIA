from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.access_code import verify_session
from app.config import settings
from app.models.schemas import Alert
from app.realtime.scanner import realtime_scanner

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self.active.append(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.active:
                self.active.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            clients = list(self.active)
        for client in clients:
            try:
                await client.send_text(payload)
            except Exception:
                await self.disconnect(client)


manager = ConnectionManager()


async def _on_alert(alert: Alert) -> None:
    await manager.broadcast(
        {
            "type": "alert",
            "payload": alert.model_dump(),
        }
    )


async def _authenticate(websocket: WebSocket) -> bool:
    if not settings.access_code_enabled:
        return True
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        data = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, ValueError):
        return False
    if data.get("type") != "auth":
        return False
    token = data.get("token")
    return await verify_session(token if isinstance(token, str) else None)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    if not await _authenticate(websocket):
        await websocket.close(code=4401, reason="Access code required")
        return

    await manager.connect(websocket)
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "connected",
                    "payload": {"message": "Aria Market connected — real-time alerts active"},
                }
            )
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as exc:
        logger.debug("WebSocket closed: %s", exc)
        await manager.disconnect(websocket)


def setup_scanner_broadcast() -> None:
    realtime_scanner.subscribe(_on_alert)