"""PumpPortal real-time "new token creation" websocket feed (19/08, promoted
from a live scratchpad probe, see module docstring below for the full
verification trail). First consumer:
``solana_fresh_launch_fast_discovery_shadow.py``.

**Why this exists**: the currently-active ``solana_fresh_launch_shadow.py``
discovers candidates via 60s REST polling against DexPaprika (never faster
than the poll cadence itself, plus DexPaprika's own indexing lag -- a real
12-minute live measurement the same day found this combined gap runs a
median ~97s between a pump.fun token's real on-chain creation and its first
appearance on DexPaprika, almost entirely DexPaprika's own indexing delay,
not the poll cadence). A parallel ``logsSubscribe`` RPC probe the same day
confirmed pump.fun creates events in real time (395 creations/12min, 0
disconnects) but a hand-rolled borsh decoder failed on 34% of them (206/601)
-- not reliable enough to build on directly.

**PumpPortal verified live 19/08, chosen over the ``logsSubscribe`` fallback**:
a real 45-second connection to ``wss://pumpportal.fun/api/data`` with
``{"method": "subscribeNewToken"}`` (no API key) returned 30 real, ALREADY-
DECODED creation events (mint/name/symbol/bondingCurveKey/vSolInBondingCurve/
marketCapSol/... -- see ``parse_new_token_message`` for the exact field list
kept), ~40/min, zero parse failures. Per the official docs
(https://pumpportal.fun/data-api/real-time, fetched live 19/08):
``subscribeNewToken`` is explicitly free and requires no API key (only the
separate per-trade subscriptions -- ``subscribeTokenTrade``/
``subscribeAccountTrade`` -- are metered, 0.01 SOL/10000 events, irrelevant
here). No numeric rate limit is documented for this method; the one
documented constraint is structural, not a throughput number: never open
multiple simultaneous websocket connections (repeated attempts risk an
hourly ban) -- this module's ``_run_loop`` already only ever holds one
connection open at a time (same discipline as ``services/pumpswap_ws.py``'s
own reconnect loop), so this is satisfied by construction, not by an added
counter.

**A second live check the same day (real REST call) confirmed
``bondingCurveKey`` -- the field PumpPortal's creation event carries -- IS
the same ``pool_address`` DexPaprika indexes pump.fun bonding-curve pools
under**: ``GET /networks/solana/pools/{bondingCurveKey}`` returned HTTP 200
with ``dex_id: "pumpfun"``, a real ``liquidity_usd``, ``last_price_usd``, and
``created_at`` for a token first seen via this exact feed a few minutes
earlier. This is what lets the consumer poll DexPaprika directly by
``bonding_curve_key`` for a REST liquidity fallback, with no extra
resolution step.

**No timestamp field in the raw message** -- PumpPortal's creation payload
carries no on-chain block time, so ``PumpPortalNewTokenEvent.detected_at`` is
this module's OWN wall-clock reading (``time.time()``) at the moment the
event is received, not a value taken from the payload. Given the real-time
nature of the feed (median well under a second per the live probe above),
this is treated as a close proxy for "how young was this token" until a
consumer resolves the real on-chain ``created_at`` via a REST detail call
(see ``solana_fresh_launch_fast_discovery_shadow.py``'s own comment on why
the REST-confirmed value is preferred once available).

Read-only throughout: no signature, no trade, no write to any ARIA-owned
persistence from this module itself (the caller decides what to persist),
same doctrine as ``services/pumpswap_ws.py``."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Verified live 19/08 (https://pumpportal.fun/data-api/real-time): the base
# URL alone is sufficient for `subscribeNewToken` -- no `?api-key=` needed
# for this free method (only the metered per-trade subscriptions require
# one). Overridable for a future alternate deployment without a code change.
import os

PUMPPORTAL_WS_URL_DEFAULT = os.environ.get("ARIA_PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data")

_RECV_POLL_TIMEOUT_SECONDS = 5.0
_RECONNECT_BACKOFF_INITIAL_SECONDS = 1.0
_RECONNECT_BACKOFF_MAX_SECONDS = 30.0

# Generous but bounded -- a queue consumer that falls behind (e.g. every
# candidate-tracking slot busy) must never grow this without bound; a full
# queue drops the newest event rather than blocking the read loop (a
# dropped creation event during a real backlog is an honest, logged
# degradation, never a silent hang of the whole feed).
DEFAULT_QUEUE_MAXSIZE = 2000


@dataclass(frozen=True)
class PumpPortalNewTokenEvent:
    """One real pump.fun token-creation event, fields kept as-observed live
    19/08 (see module docstring) -- never fabricated, ``None`` for anything
    the payload didn't carry."""

    mint: str
    symbol: str | None
    name: str | None
    pool: str | None  # "pump" for a fresh bonding-curve launch, observed live
    bonding_curve_key: str | None
    market_cap_sol: float | None
    v_sol_in_bonding_curve: float | None
    v_tokens_in_bonding_curve: float | None
    sol_amount: float | None
    initial_buy: float | None
    signature: str | None
    detected_at: float  # time.time() at receipt -- see module docstring


def parse_new_token_message(data: object) -> PumpPortalNewTokenEvent | None:
    """Pure, no I/O. Returns ``None`` for anything that isn't a real
    ``txType == "create"`` payload (the initial subscribe-ack message, a
    malformed frame, or -- defensively -- any future message shape this
    module doesn't yet know about) rather than guessing. ``mint`` is the
    only field treated as strictly required; every other field degrades to
    ``None`` independently so a partially-populated real event is still
    usable (never all-or-nothing on a field PumpPortal might omit)."""
    if not isinstance(data, dict):
        return None
    if data.get("txType") != "create":
        return None
    mint = data.get("mint")
    if not isinstance(mint, str) or not mint:
        return None

    def _str(key: str) -> str | None:
        v = data.get(key)
        return v if isinstance(v, str) and v else None

    def _num(key: str) -> float | None:
        v = data.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    return PumpPortalNewTokenEvent(
        mint=mint,
        symbol=_str("symbol"),
        name=_str("name"),
        pool=_str("pool"),
        bonding_curve_key=_str("bondingCurveKey"),
        market_cap_sol=_num("marketCapSol"),
        v_sol_in_bonding_curve=_num("vSolInBondingCurve"),
        v_tokens_in_bonding_curve=_num("vTokensInBondingCurve"),
        sol_amount=_num("solAmount"),
        initial_buy=_num("initialBuy"),
        signature=_str("signature"),
        detected_at=time.time(),
    )


class PumpPortalNewTokenFeed:
    """Maintains ONE persistent websocket connection subscribed to pump.fun
    token-creation events, pushing each parsed ``PumpPortalNewTokenEvent``
    onto an in-memory queue for a consumer to drain via ``next_event``.
    Auto-reconnects with exponential backoff on any disconnect -- same
    structure as ``services/pumpswap_ws.py``'s own ``PumpSwapWebSocketFeed``,
    deliberately not shared code (a token-creation feed and a reserve-account
    feed subscribe to structurally different things), but the same
    reconnect/backoff/single-connection discipline throughout.

    ``connect_fn`` is injectable purely for tests (never touches the real
    network) -- production code never needs to pass it."""

    def __init__(
        self,
        *,
        ws_url: str = PUMPPORTAL_WS_URL_DEFAULT,
        connect_fn=None,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        self._ws_url = ws_url
        self._connect_fn = connect_fn
        self._queue: asyncio.Queue[PumpPortalNewTokenEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self.dropped_count = 0

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn(self._ws_url)
        import websockets

        return websockets.connect(self._ws_url, ping_interval=20, ping_timeout=20)

    async def start(self) -> None:
        self._stop_event.clear()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 -- shutdown must never raise
                pass
            self._task = None

    def pending_count(self) -> int:
        return self._queue.qsize()

    async def next_event(self, timeout: float | None = None) -> PumpPortalNewTokenEvent | None:
        """Pulls one event, or ``None`` on timeout (never raises
        ``TimeoutError`` into the caller) -- a consumer loop can poll this in
        a plain ``while`` without its own try/except."""
        try:
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def _read_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_POLL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event = parse_new_token_message(data)
            if event is None:
                continue
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_count += 1
                logger.info(
                    "pumpportal_ws: queue full (%d), dropping event for mint=%s",
                    self._queue.qsize(), event.mint,
                )

    async def _run_loop(self) -> None:
        backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
        while not self._stop_event.is_set():
            try:
                async with self._connect() as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
                    await self._read_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a single connection error must never kill the feed
                logger.info("pumpportal_ws: feed loop error (%s) -- reconnecting in %.1fs", exc, backoff)
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_SECONDS)
