"""Real-time sourcing of momentum candidates via the DexScreener WebSocket
(#196, fast-follow of #194). Drastically reduces sourcing latency compared to
periodic REST polling (``paper_trade_cycle`` heartbeat, 15 min) -- explicit
operator goal: "if there's money to be made ARIA needs to be there before
everyone else". NEVER introduces a second decision path: candidates detected
here go through the SAME pipeline as #194
(``momentum_entry.evaluate_momentum_entry`` -- GoPlus honeypot, golden
pocket/RSI R/R, light LLM confirmation) via ``paper_trader.run_paper_cycle``.

Verified live (16/07, VPS Principal, BEFORE writing this module -- norm #157:
never an assumed schema left unconfronted with a real call):
  - ``wss://api.dexscreener.com/token-boosts/latest/v1`` and
    ``/token-profiles/latest/v1`` accept a standard WebSocket connection
    (``websockets`` library, already used server-side in
    ``vanguard/backend``, added here as a BASE dependency of aria-core --
    read-only, no secret/capital involved, same tier as httpx/requests).
  - The FIRST message received after connecting is a full snapshot:
    ``{"limit": N, "data": [...]}``, where each element of ``data`` has
    EXACTLY the shape expected by ``services.dexscreener.parse_listing``
    (same ``chainId``/``tokenAddress``/``description``/``links`` keys as the
    equivalent REST response) -- reused as-is, no duplicated parsing.
  - Afterward, the connection stays open and sends
    ``{"type": "heartbeat"}`` heartbeat frames every ~15-30s. **No new data
    observed on a connection kept open for more than 2 minutes of continuous
    observation** -- contrary to the plan's initial assumption ("connection
    kept open, notified instantly"), the server does NOT seem to push
    incremental updates over a long-lived connection: you have to RECONNECT
    to get a fresh snapshot. The design below accounts for this -- each
    per-endpoint loop reconnects every ``DRAIN_INTERVAL_SECONDS`` to pull a
    fresh snapshot, rather than keeping 4 sockets open waiting for pushes
    that never arrive (a point-in-time observation, not a documented API
    contract -- if a future pass finds genuine incremental frames on a
    long-lived connection, this module would already handle them correctly:
    every "data" frame is diffed against the dedup set, regardless of its
    origin/frequency).
  - Only ``token-boosts/latest`` and ``token-profiles/latest`` were verified
    directly on this date; ``token-boosts/top``/``token-profiles/recent-updates``
    are assumed identical (same API family, same ``/v1`` version) -- to be
    reconfirmed if different behavior is observed in prod.

Scope strictly respected (16/07, operator-approved plan):
  - Only SOURCING new candidates. Never touches the honeypot check, the
    management of already-open positions (#186/#187), or the default
    behavior of the ``paper_trade_cycle`` heartbeat cycle (called with no
    arguments -- strictly unchanged).
  - Dedicated gate ``ARIA_MOMENTUM_WEBSOCKET_ENABLED``, OFF by default, read
    ONLY ONCE at ``start()`` (same doctrine as the rest of the dome --
    flipping it requires a restart, not a hot reload).
  - Before triggering ``run_paper_cycle``: re-checks
    ``ARIA_PAPER_TRADING_ENABLED`` (the paper-trading system itself must be
    active) AND ``outgoing_pause.is_paused()`` (``/stop`` kill-switch -- this
    path bypasses ``heartbeat._tick()``, which normally does this check, so
    it must be redone here explicitly).
  - Mandatory concurrency lock (operator fix, plan re-review):
    ``paper_trader.run_paper_cycle`` already wraps EVERY call in
    ``paper_trader._run_cycle_lock`` (shared module) -- never two cycles in
    parallel, regardless of the caller (heartbeat OR this service).
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time

from aria_core import outgoing_pause
from aria_core.momentum_entry import (
    DEFAULT_CHAINS,
    _batch_liquidity_prefilter,
    normalize_contract_case,
)
from aria_core.services.dexscreener import parse_listing

logger = logging.getLogger(__name__)

WS_BASE_URL = "wss://api.dexscreener.com"
ENDPOINTS: tuple[str, ...] = (
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/token-profiles/latest/v1",
    "/token-profiles/recent-updates/v1",
)

# Explicit operator decisions, 16/07 (#196).
DRAIN_INTERVAL_SECONDS = 30       # lower bound of the proposed range -- the goal is speed
MAX_CANDIDATES_PER_DRAIN = 20     # same order of magnitude as the cap already accepted for paper_trade_cycle
DEDUP_TTL_SECONDS = 15 * 60       # 15 minutes -- anti-spam for closely-spaced frames on
                                  # the same candidate, NOT the rescan cooldown (see
                                  # RESCAN_COOLDOWN_SECONDS below, 22/07).

# 22/07 -- explicit operator decision: "a contract doesn't need to be scanned
# every 60 seconds, every 4h is enough" -- ADAPTIVE, not rigid (operator
# clarification: "whether it's a token with no signal or with a signal it
# should adapt"): a candidate already seen within the last 4h does NOT
# retrigger a full evaluation, UNLESS its price has moved more than
# RESCAN_PRICE_MOVE_THRESHOLD_PCT since the last pass -- a real price move can
# signal a new setup worth looking at right away, not in 4h. The comparison
# price comes from _batch_liquidity_prefilter (already called for every fresh
# candidate, batched DexScreener call -- NO extra network call dedicated to
# this mechanism), never a new call just for this.
RESCAN_COOLDOWN_SECONDS = 4 * 3600  # 4h
RESCAN_PRICE_MOVE_THRESHOLD_PCT = 0.10  # 10% -- starting value proposed, adjustable
MAX_NEW_PER_DRAIN = 3             # same pacing as the heartbeat default (run_paper_cycle max_new) --
                                  # MAX_CANDIDATES_PER_DRAIN bounds candidates EVALUATED, not the
                                  # number of new positions OPENED per drain (deliberately more
                                  # conservative than a plain len(candidates), so as not to dump more
                                  # new entries per drain than the heartbeat cycle would open on its
                                  # own in 15 minutes).

# 19/07 -- rate cap added BEFORE activation (legitimate operator question:
# "won't this break the API plumbing?"). Without it, the theoretical worst
# case is MAX_CANDIDATES_PER_DRAIN (20) every DRAIN_INTERVAL_SECONDS (30s) =
# up to ~2400 candidates evaluated/hour -- a ~30x factor over the classic
# heartbeat cycle's rate (20 candidates x 4 cycles/hour = 80/hour).
# GeckoTerminal/GoPlus have a SHARED client-side throttle (protects against a
# real 429 -- calls are serialized, not parallelized), but CoinMarketCap has
# NO client throttle at all, and none of the three has an hourly/daily QUOTA
# cap coded anywhere: sustained throughput could exhaust a monthly paid quota
# within days without ever triggering a single individual 429 that would
# alert anyone. Brings the WebSocket rate back to the SAME ORDER OF MAGNITUDE
# as the current regime (80/hour) -- keeps the LATENCY advantage (near-instant
# detection) without blowing up the total VOLUME consumed by downstream APIs.
MAX_EVALUATIONS_PER_HOUR = 80

_CONNECT_TIMEOUT_SECONDS = 8
_RECV_TIMEOUT_SECONDS = 15
_BACKOFF_INITIAL_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 60.0

_ALLOWED_CHAINS = frozenset(DEFAULT_CHAINS)


def momentum_websocket_enabled() -> bool:
    """Dedicated gate, OFF by default -- fail-closed, same doctrine as the rest of the dome."""
    return os.environ.get("ARIA_MOMENTUM_WEBSOCKET_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _paper_trading_enabled() -> bool:
    """Explicitly re-checked before every trigger -- this path bypasses
    ``heartbeat._tick()``, which normally does this check for
    ``paper_trade_cycle``."""
    return os.environ.get("ARIA_PAPER_TRADING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


class MomentumWebsocketListener:
    """Background service (started/stopped by the host --
    ``vanguard/backend/app/main.py``, same pattern as ``aria_heartbeat``):
    periodically refreshes the 4 DexScreener endpoints, deduplicates, and
    triggers momentum evaluation on FRESH candidates via the existing
    pipeline -- never a second decision path."""

    def __init__(self) -> None:
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()  # protects _pending/_seen between per-endpoint loops and the drain
        self._pending: dict[tuple[str, str], float] = {}  # (contract, chain) -> first_seen ts
        # 22/07 -- (last_drained_ts, last_known_price_usd|None): the price is
        # used for the adaptive cooldown (RESCAN_COOLDOWN_SECONDS), never
        # confused with the anti-spam TTL (DEDUP_TTL_SECONDS), which blocks
        # unconditionally on price.
        self._seen: dict[tuple[str, str], tuple[float, float | None]] = {}
        # 19/07 -- 1h sliding window for MAX_EVALUATIONS_PER_HOUR (one timestamp per
        # candidate actually evaluated, not per drain -- a drain of 20 candidates counts
        # as 20, not 1).
        self._evaluation_timestamps: collections.deque[float] = collections.deque()

    def _evaluation_budget_remaining(self, now: float) -> int:
        cutoff = now - 3600.0
        while self._evaluation_timestamps and self._evaluation_timestamps[0] < cutoff:
            self._evaluation_timestamps.popleft()
        return max(0, MAX_EVALUATIONS_PER_HOUR - len(self._evaluation_timestamps))

    async def start(self) -> None:
        if self._running:
            return
        if not momentum_websocket_enabled():
            logger.info(
                "momentum_websocket: ARIA_MOMENTUM_WEBSOCKET_ENABLED disabled, service not started"
            )
            return
        self._running = True
        for endpoint in ENDPOINTS:
            self._tasks.append(asyncio.create_task(self._endpoint_loop(endpoint)))
        self._tasks.append(asyncio.create_task(self._drain_loop()))
        logger.info("momentum_websocket: started (%d endpoints)", len(ENDPOINTS))

    async def stop(self) -> None:
        self._running = False
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _endpoint_loop(self, endpoint: str) -> None:
        """One short connection per cycle (connect, read ONE snapshot, close) --
        not a kept-open connection hoping for pushes (see module docstring: no
        data observed beyond the initial snapshot + heartbeats). Reconnects
        with exponential backoff on error, never gives up for good (a
        persistent service, not a one-off call)."""
        import websockets

        backoff = _BACKOFF_INITIAL_SECONDS
        while self._running:
            try:
                url = f"{WS_BASE_URL}{endpoint}"
                async with websockets.connect(url, open_timeout=_CONNECT_TIMEOUT_SECONDS) as ws:
                    msg = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_SECONDS)
                    await self._ingest_frame(msg)
                backoff = _BACKOFF_INITIAL_SECONDS  # success -- resets the backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- never a silent loop crash
                logger.info(
                    "momentum_websocket: %s failed (%s), retrying in %.1fs", endpoint, exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_SECONDS)
                continue
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)

    async def _ingest_frame(self, raw_msg: str) -> None:
        try:
            payload = json.loads(raw_msg)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("type") == "heartbeat":
            return
        items = payload.get("data")
        if not isinstance(items, list):
            return

        now = time.time()
        async with self._lock:
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                listing = parse_listing(raw)
                chain = listing.chain_id.strip().lower()
                # 19/07 -- real bug found while activating this path for the
                # first time (never exercised before): a blind .lower() was
                # corrupting every Solana address (base58, case-sensitive --
                # unlike Base/Robinhood in EVM hex). Same bug already fixed on
                # 18/07 on the REST side (momentum_entry.normalize_contract_case),
                # never ported here -- this module was written BEFORE that
                # discovery. Symptom observed in prod: RugCheck (Solana
                # honeypot fallback, #207) was rejecting with a 400 "invalid
                # length" addresses whose real coverage was never verified
                # with the correct case.
                contract = normalize_contract_case(listing.token_address.strip(), chain)
                if not contract or not chain or chain not in _ALLOWED_CHAINS:
                    continue
                # 22/07 -- same filter as discover_momentum_candidates
                # (momentum_entry._add_candidate): WETH/stablecoins are never
                # legitimate speculative candidates, and were triggering a
                # paid x402 fallback in a loop on the holder_concentration
                # check (see the detailed comment on the momentum_entry.py
                # side -- this WebSocket path has its OWN candidate
                # addition, never covered by the classic heartbeat-side
                # filter).
                from aria_core.momentum_entry import reference_tokens_excluded

                if contract.lower() in reference_tokens_excluded(chain):
                    continue
                key = (contract, chain)
                last = self._seen.get(key)
                if last is not None and (now - last[0]) < DEDUP_TTL_SECONDS:
                    continue  # already triggered recently -- never a retrigger loop
                # 22/07 -- beyond the anti-spam TTL (15min), the candidate
                # still joins _pending -- the REAL adaptive cooldown (4h
                # unless there's a price move) is decided in _drain_once,
                # where the price is available with no dedicated network cost
                # (see RESCAN_COOLDOWN_SECONDS).
                self._pending.setdefault(key, now)

    async def _drain_loop(self) -> None:
        while self._running:
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a failed drain never kills the service
                logger.exception("momentum_websocket: drain failed (%s)", exc)

    async def _drain_once(self) -> None:
        async with self._lock:
            if not self._pending:
                return
            batch_keys = list(self._pending.keys())[:MAX_CANDIDATES_PER_DRAIN]
            # 22/07 -- captures the OLD (timestamp, price) BEFORE overwriting
            # it -- that's the reference for the adaptive cooldown below.
            # Updating _seen itself is deferred until after the prefilter
            # (where the fresh price becomes available), so it always writes
            # the most up-to-date known price.
            previous_seen = {key: self._seen.get(key) for key in batch_keys}
            for key in batch_keys:
                self._pending.pop(key, None)

        if not batch_keys:
            return
        if not _paper_trading_enabled():
            logger.info("momentum_websocket: ARIA_PAPER_TRADING_ENABLED disabled, drain skipped")
            return
        if outgoing_pause.is_paused():
            logger.info("momentum_websocket: kill-switch active, drain skipped")
            return

        raw_candidates = [{"contract": c, "chain": ch} for (c, ch) in batch_keys]
        try:
            filtered = await _batch_liquidity_prefilter(raw_candidates)
        except Exception as exc:  # noqa: BLE001 -- the prefilter must never block the drain
            logger.info("momentum_websocket: liquidity prefilter failed (%s)", exc)
            filtered = raw_candidates

        # 22/07 -- updates _seen for the WHOLE batch (regardless of who
        # survives the cooldown below): a candidate we just looked at, even
        # if rejected, must not retrigger a check before the next real
        # cooldown.
        now_ts = time.time()
        price_by_key: dict[tuple[str, str], float | None] = {}
        for c in filtered:
            key = (c["contract"], c["chain"])
            price_by_key[key] = c.get("price_usd")
        for key in batch_keys:
            # Price unknown on THIS pass (prefilter with no data) -- keeps the
            # old reference price rather than losing it (never an information
            # regression just because of a one-off prefilter outage).
            price = price_by_key.get(key)
            if price is None:
                old = previous_seen.get(key)
                price = old[1] if old is not None else None
            self._seen[key] = (now_ts, price)

        # 22/07 -- adaptive cooldown (RESCAN_COOLDOWN_SECONDS, 4h): a
        # candidate already seen recently (beyond the anti-spam TTL, under
        # the full cooldown) does NOT retrigger an evaluation, UNLESS its
        # price has moved more than RESCAN_PRICE_MOVE_THRESHOLD_PCT since the
        # last pass. Fail-open on missing data (old or new price unknown) --
        # never blocks on uncertainty, only on a comparison that's actually
        # possible.
        def _still_in_cooldown(c: dict) -> bool:
            key = (c["contract"], c["chain"])
            old = previous_seen.get(key)
            if old is None:
                return False  # never seen -- no cooldown possible
            old_ts, old_price = old
            if (now_ts - old_ts) >= RESCAN_COOLDOWN_SECONDS:
                return False  # full cooldown elapsed
            new_price = price_by_key.get(key)
            if old_price is None or new_price is None or old_price <= 0:
                return False  # comparison impossible -- fail-open, never blocking
            move_pct = abs(new_price - old_price) / old_price
            return move_pct < RESCAN_PRICE_MOVE_THRESHOLD_PCT

        before_cooldown_count = len(filtered)
        filtered = [c for c in filtered if not _still_in_cooldown(c)]
        if len(filtered) < before_cooldown_count:
            logger.info(
                "momentum_websocket: %d candidate(s) in adaptive cooldown (already "
                "seen, stable price) -- drain reduced to %d",
                before_cooldown_count - len(filtered), len(filtered),
            )

        if not filtered:
            return

        from aria_core import paper_trader

        candidates = [c["contract"] for c in filtered]

        # 19/07 -- hourly rate cap (see MAX_EVALUATIONS_PER_HOUR): truncates
        # the list rather than canceling the whole drain -- graceful
        # degradation, never all-or-nothing. Truncated candidates stay marked
        # "seen" (_seen, above): they won't be re-evaluated before
        # DEDUP_TTL_SECONDS, a deliberate tradeoff to avoid a catch-up spike
        # on the next drain.
        now = time.time()
        budget = self._evaluation_budget_remaining(now)
        if budget <= 0:
            logger.info(
                "momentum_websocket: hourly cap reached (%d/h) -- drain skipped",
                MAX_EVALUATIONS_PER_HOUR,
            )
            return
        if len(candidates) > budget:
            candidates = candidates[:budget]
        self._evaluation_timestamps.extend([now] * len(candidates))

        chain_by_contract = {c["contract"]: c["chain"] for c in filtered}
        analyzer = paper_trader._default_momentum_analyzer(chain_by_contract)
        try:
            from aria_core.gateway.telegram_bot import send_trading_notification

            # 20/07 -- real bug found in production conditions (a MAGIC
            # position bought without ever notifying Telegram, only its sale
            # by the next heartbeat arrived): this path had never passed a
            # notifier to run_paper_cycle -- any position opened via the
            # real-time WebSocket stayed silent until its close (handled by
            # the heartbeat, which already notifies). Same function as the
            # heartbeat, never a 2nd implementation.
            await paper_trader.run_paper_cycle(
                candidates=candidates,
                analyzer=analyzer,
                max_new=MAX_NEW_PER_DRAIN,
                skip_position_management=True,
                notifier=send_trading_notification,
            )
        except Exception as exc:  # noqa: BLE001 -- a failed drain never kills the service
            logger.exception("momentum_websocket: run_paper_cycle failed (%s)", exc)


momentum_websocket_listener = MomentumWebsocketListener()
