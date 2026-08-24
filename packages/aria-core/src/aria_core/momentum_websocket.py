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

27/07 -- 3-pocket architecture plan, Phase 2 (``paper_trader.
multi_pocket_sourcing_enabled()``): gate OFF (default) keeps this module's
historical single-analyzer behavior, byte-for-byte -- ``_drain_new_
candidates`` still drives ONE analyzer off the portfolio-wide
``trading_mode`` switch and books into "swing" via ``run_paper_cycle``. Gate
ON dispatches the SAME WebSocket-detected candidates to all 3 pockets
(scalping/swing/vc) independently every drain -- see ``_drain_multi_pocket``'s
own docstring for the mechanics (it bypasses ``run_paper_cycle`` on purpose).
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
# 31/07 -- raised 20 -> 50 (explicit operator decision, matches the same-day
# raise of paper_trader._momentum_candidates_and_chain_map's own limit).
# MAX_EVALUATIONS_PER_HOUR below remains the real ceiling either way (still
# far under this number x drains/hour), unchanged -- the operator only asked
# to raise the per-drain/per-cycle figure, not the hourly safety cap.
MAX_CANDIDATES_PER_DRAIN = 50
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
# case is MAX_CANDIDATES_PER_DRAIN every DRAIN_INTERVAL_SECONDS (30s) --
# with the 31/07 value (50), up to ~6000 candidates evaluated/hour -- a huge
# multiple over the classic heartbeat cycle's rate (used to be 20 candidates
# x 4 cycles/hour = 80/hour, back when that cycle ran every 15min; it now
# runs hourly). GeckoTerminal/GoPlus have a SHARED client-side throttle
# (protects against a real 429 -- calls are serialized, not parallelized),
# but CoinMarketCap has NO client throttle at all, and none of the three has
# an hourly/daily QUOTA cap coded anywhere: sustained throughput could
# exhaust a monthly paid quota within days without ever triggering a single
# individual 429 that would alert anyone.
#
# 31/07 -- raised 80 -> 200 (explicit operator decision, same day as the
# 20->50 per-drain raise above), after verifying the real ceiling this
# actually competes against: GeckoTerminal (the lowest-throughput OHLCV
# source in the cascade, ~27 req/min = ~1620/h calibrated at 90%,
# docs/api-rate-limit-calibration.md) is shared with other system consumers,
# not dedicated to this pipeline. Each drained candidate costs 2 real
# GeckoTerminal calls (scalping + swing evaluated separately) -- even at 200
# candidates/hour, that's ~400 calls/hour, still only ~25% of GeckoTerminal's
# calibrated capacity, a real, verified margin (the old 80 figure used only
# ~10%). Known, flagged gap NOT resolved by this change: the LLM call now
# made for EVERY swing setup (R/R floor removed, same day) has no calibrated
# rate/quota documented anywhere in this codebase -- it could become the real
# bottleneck before GeckoTerminal ever does. Observe real latency/cost over
# the following days before raising this further.
#
# 04/08 -- raised 200 -> 500, then REVERTED minutes later alongside
# geckoterminal._AUTHENTICATED_MIN_INTERVAL (same operator decision/incident
# -- see its comment: the adaptive circuit breaker tripped sustained within
# ~10s of the 75 req/min deploy, plus a real Cloudflare-block risk the
# operator flagged). Back to 200, matched to GeckoTerminal's own reverted
# 15 req/min capacity. The LLM-call gap flagged above is still unresolved.
MAX_EVALUATIONS_PER_HOUR = 200

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
    ``paper_trade_cycle``. Item #64 (08/03): also honors the runtime
    ``/offpaper`` toggle (``paper_pause``) -- distinct from
    ``ARIA_PAPER_TRADING_ENABLED`` (env var, needs a redeploy), this one
    flips instantly for a manual debugging pause."""
    from aria_core import paper_pause

    if paper_pause.is_paused():
        return False
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
                # 09/08 -- signal cascade stage 1 enqueue REMOVED from this raw
                # feed on explicit operator instruction the same day ("oublie
                # tout critere sur la liste de scan du sourcing et pioche
                # directement dans la liste dexscreener... cette liste des 2k
                # est deja filtree comme il faut") -- the cascade now enqueues
                # exclusively from goplus_watchlist (see momentum_entry.
                # _check_honeypot), never from this raw WebSocket ingestion.
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
        """Checks limit orders FIRST, independently of new-candidate
        detection (07/23) -- the early-return in ``_drain_new_candidates``
        below on an empty ``_pending`` would otherwise starve limit-order
        watching on a quiet day with no new WebSocket candidate. Same gates
        as the classic drain (paper trading enabled + kill-switch) -- never
        triggers a buy the rest of this service wouldn't."""
        if _paper_trading_enabled() and not outgoing_pause.is_paused():
            try:
                from aria_core import limit_orders, paper_trader
                from aria_core.gateway.telegram_bot import send_trading_notification

                await limit_orders.process_active_orders(
                    paper_trader._default_price_lookup, notifier=send_trading_notification,
                    pair_lookup=paper_trader._default_pair_lookup,
                )
            except Exception as exc:  # noqa: BLE001 -- never kills the drain/websocket
                logger.exception("momentum_websocket: limit-order processing failed (%s)", exc)

        await self._drain_new_candidates()

    async def _drain_new_candidates(self) -> None:
        """Original ``_drain_once`` body (07/23, renamed when the limit-order
        check above was extracted) -- unchanged behavior: sourcing/evaluating
        NEW candidates from the WebSocket feed."""
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
        # Item #64 (08/03), operator feedback: distinguish the two sources
        # _paper_trading_enabled() combines -- the env var (needs a redeploy)
        # vs the runtime /offpaper toggle (instant) -- rather than one ambiguous
        # log line always naming the env var even when /offpaper was the real
        # cause. Checked explicitly here, ahead of the combined helper, same
        # pattern as the outgoing_pause check right below.
        from aria_core import paper_pause

        if paper_pause.is_paused():
            logger.info("momentum_websocket: paper trading paused via /offpaper, drain skipped")
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
        # 26/07 -- real bug found alongside Item #117 (same class): this drain
        # never resolved trading_mode/current_regime, silently defaulting to
        # "standard"/neutral regardless of the portfolio-wide switches the
        # periodic cycle (_run_paper_cycle_locked) already resolves once per
        # cycle -- a candidate caught by the real-time WebSocket path got a
        # DIFFERENT evaluation (wrong RSI period/timeframe, full
        # conviction_research diligence never skipped in scalping mode) than
        # the SAME candidate would get from the periodic scan. weekly_context
        # stays None here (unchanged, pre-existing) -- its computation is
        # cycle-cadence specific and not critical to this fix's scope.
        try:
            from aria_core.skills import market_sentiment

            current_regime = await market_sentiment.resolve_meta_regime()
        except Exception as exc:  # noqa: BLE001 -- never blocking, degrades to neutral
            logger.info("momentum_websocket: meta-regime lookup failed (%s)", exc)
            current_regime = None

        # 27/07 -- 3-pocket architecture plan, Phase 2 (same gate as
        # paper_trader._run_paper_cycle_locked's own multi-pocket branch):
        # scalping/swing/vc drain independently, sourced from THIS module's
        # WebSocket-detected candidates for scalping/swing (vc sources
        # separately) -- see _drain_multi_pocket's own docstring for why this
        # bypasses run_paper_cycle() below rather than passing it these
        # candidates directly.
        if paper_trader.multi_pocket_sourcing_enabled():
            await self._drain_multi_pocket(candidates, chain_by_contract, current_regime)
            return

        # gate OFF (default): EXACT unchanged historical behavior -- a single
        # analyzer driven by the portfolio-wide trading_mode switch, booked
        # into "swing" via run_paper_cycle's own single-pocket path (an
        # explicit candidates/analyzer caller always stays "swing" there
        # regardless of the gate, see test_multi_pocket_gate_on_never_splits_
        # an_explicit_caller in test_paper_trader.py -- this module only
        # diverges from that contract in the branch above, by never reaching
        # run_paper_cycle at all once the gate is ON).
        try:
            trading_mode = await paper_trader.get_trading_mode()
        except Exception as exc:  # noqa: BLE001 -- never blocking, degrades to "standard"
            logger.info("momentum_websocket: trading_mode lookup failed (%s)", exc)
            trading_mode = "standard"
        analyzer = paper_trader._default_momentum_analyzer(
            chain_by_contract, current_regime=current_regime, mode=trading_mode,
        )
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
                # 07/23 -- performance-breakdown tracking: any position opened
                # via this path was detected in ~30s, not the periodic scan.
                discovery_channel="websocket",
            )
        except Exception as exc:  # noqa: BLE001 -- a failed drain never kills the service
            logger.exception("momentum_websocket: run_paper_cycle failed (%s)", exc)

    async def _drain_multi_pocket(
        self, candidates: list[str], chain_by_contract: dict[str, str], current_regime,
    ) -> None:
        """27/07 -- 3-pocket architecture plan, Phase 2. Mirrors
        ``paper_trader._run_paper_cycle_locked``'s own multi-pocket branch:
        scalping and swing share THIS module's WebSocket-detected
        ``candidates`` (real-time momentum feed, different analyzer ``mode``
        only) -- the vc pocket sources INDEPENDENTLY from
        ``candidate_ranking.top_candidates()`` (a wholly separate candidate
        universe, screened_pool VC theses, never fed by the WebSocket
        momentum feed), exactly as the periodic heartbeat cycle already does.

        Deliberately bypasses ``paper_trader.run_paper_cycle()``: that
        entrypoint's OWN multi-pocket branch only activates on its "default
        sourcing" case (``candidates=None`` AND ``analyzer=None``) -- passing
        it THIS module's WebSocket-detected candidates explicitly would
        instead hit its "explicit caller" branch (see
        ``test_multi_pocket_gate_on_never_splits_an_explicit_caller`` in
        test_paper_trader.py) and silently discard them, re-fetching its OWN
        momentum candidates via REST and booking everything into "swing"
        only -- exactly the stale-latency problem this whole module exists to
        avoid. Calls ``paper_trader._open_new_entries_for_wallet`` directly
        for each pocket instead (the SAME helper the periodic cycle uses),
        replicating the depeg-check/risk-state/funnel bookkeeping
        ``run_paper_cycle`` would otherwise have applied -- protected by the
        SAME ``paper_trader._run_cycle_lock`` (never two cycles opening
        positions in parallel, regardless of caller).

        Simplifications carried over from the gate-OFF path above (never a
        NEW behavior introduced here): ``weekly_context`` stays ``None`` for
        all 3 pockets (26/07 comment, "not critical to this fix's scope");
        this drain never closes positions itself (position management stays
        the periodic heartbeat's job), so ``closed_this_cycle`` is always
        empty."""
        from aria_core import momentum_funnel_log, paper_trader, risk_guard
        from aria_core import paper_trader_risk as risk
        from aria_core.gateway.telegram_bot import send_trading_notification
        from aria_core.skills.candidate_ranking import top_candidates

        async with paper_trader._run_cycle_lock:
            try:
                vc_candidates = [c.contract for c in await top_candidates(20)]
            except Exception as exc:  # noqa: BLE001 -- never blocks the scalping/swing pockets
                logger.info("momentum_websocket: top_candidates lookup failed (%s)", exc)
                vc_candidates = []

            # USDC depeg check (#187) -- same guard run_paper_cycle applies,
            # all 3 pockets share this pricing assumption. Skipped entirely
            # if nothing to buy anywhere (avoids a needless network call),
            # same avoidance as the periodic cycle's own depeg check.
            if candidates or vc_candidates:
                try:
                    depeg_pct = await risk.usdc_depeg_pct()
                except Exception as exc:  # noqa: BLE001
                    logger.info("momentum_websocket: USDC depeg check failed (%s)", exc)
                    depeg_pct = None
                if depeg_pct is not None and depeg_pct > risk.USDC_DEPEG_THRESHOLD_PCT:
                    logger.warning(
                        "momentum_websocket: USDC depegged %.2f%% (> threshold %.2f%%) -- "
                        "multi-pocket drain skipped this pass",
                        depeg_pct * 100, risk.USDC_DEPEG_THRESHOLD_PCT * 100,
                    )
                    return

            # 27/07 -- Phase 3: MACRO circuit breaker, same doctrine as
            # paper_trader._run_paper_cycle_locked's own multi-pocket branch
            # -- checked ONCE per drain, BEFORE any per-pocket risk check
            # below. Best-effort: a failure here degrades to "not triggered",
            # never blocks the drain on an unrelated error (same doctrine as
            # the depeg check just above).
            try:
                macro_state = await risk_guard.evaluate_macro_risk(
                    price_lookup=paper_trader._default_price_lookup,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info("momentum_websocket: macro circuit breaker check failed (%s)", exc)
                macro_state = None
            if macro_state is not None and macro_state.newly_triggered:
                try:
                    await send_trading_notification(
                        risk_guard.format_macro_circuit_breaker_alert(macro_state)
                    )
                except Exception:  # noqa: BLE001
                    pass
                return

            swing_analyzer = paper_trader._default_momentum_analyzer(
                chain_by_contract, current_regime=current_regime, mode="standard",
            )

            closed_this_cycle: set[str] = set()
            funnel: dict[str, int] = {}

            # 18/08 -- the scalping-variant slot (v1-v8) is retired; only
            # swing/vc remain (see docs/HANDOFF_PIPELINE_MOMENTUM.md).
            for pocket_wallet, pocket_candidates, pocket_analyzer, pocket_mode, pocket_cap in (
                ("swing", candidates, swing_analyzer, "standard", paper_trader.MAX_POSITIONS_SWING),
                ("vc", vc_candidates, paper_trader._default_analyzer, "standard", paper_trader.MAX_POSITIONS_VC),
            ):
                # 08/05 -- operator focus decision: paused pockets never
                # source here either (same filter as the heartbeat loop,
                # paper_trader.SOURCING_PAUSED_WALLETS -- the two loops must
                # never diverge on this).
                if paper_trader.sourcing_paused(pocket_wallet):
                    continue
                try:
                    # 27/07 -- Phase 3: independent per-pocket risk state --
                    # a drawdown/losing streak on ONE pocket alone must never
                    # block the other two (mirrors paper_trader._run_paper_
                    # cycle_locked's own multi-pocket loop). Skip THIS pocket
                    # only (``continue``), never the whole drain.
                    pocket_risk_state = await risk_guard.evaluate_portfolio_risk(
                        pocket_wallet, price_lookup=paper_trader._default_price_lookup,
                    )
                    if pocket_risk_state.newly_triggered_hard:
                        try:
                            await send_trading_notification(
                                risk_guard.format_hard_circuit_breaker_alert(pocket_risk_state, pocket_wallet)
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    elif pocket_risk_state.newly_triggered_soft:
                        try:
                            await send_trading_notification(
                                risk_guard.format_soft_drawdown_alert(pocket_risk_state, pocket_wallet)
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    if pocket_risk_state.blocked:
                        continue

                    # 08/02 -- see paper_trader.vc_pocket_sourcing_enabled()'s
                    # own docstring -- same real gap, same fix, mirrored here
                    # so this drain and the periodic heartbeat can never
                    # silently diverge on this gate either.
                    if pocket_wallet == "vc" and not paper_trader.vc_pocket_sourcing_enabled():
                        continue

                    await paper_trader._open_new_entries_for_wallet(
                        pocket_wallet, pocket_candidates, pocket_analyzer,
                        price_lookup=paper_trader._default_price_lookup,
                        notifier=send_trading_notification, max_new=MAX_NEW_PER_DRAIN,
                        using_default_price_lookup=True, closed_this_cycle=closed_this_cycle,
                        weekly_context=None, risk_state=pocket_risk_state, discovery_channel="websocket",
                        trading_mode=pocket_mode, max_positions_cap=pocket_cap, funnel=funnel,
                    )
                except Exception as exc:  # noqa: BLE001 -- a failed pocket never blocks the others
                    logger.exception(
                        "momentum_websocket: multi-pocket drain failed for wallet=%s (%s)",
                        pocket_wallet, exc,
                    )

            if funnel:
                logger.info("momentum_websocket funnel (multi-pocket drain): %s", funnel)
                try:
                    await momentum_funnel_log.record_funnel(funnel)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("momentum_websocket: funnel persistence failed (%s)", exc)


momentum_websocket_listener = MomentumWebsocketListener()
