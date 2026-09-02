"""Live signal observer (specs/017-live-signal-observer) -- discovers
momentum candidates and evaluates them through the EXISTING pipeline while
paper-trading is paused, then sends a dedicated, non-executional Telegram
signal. Decouples SIGNAL from EXECUTION (operator decision, 01/09).

Why a dedicated service: ``momentum_websocket._drain_new_candidates`` fuses
signal computation and position opening in one ``paper_trader.run_paper_
cycle`` call, and refuses to run at all while ``paper_pause.is_paused()``
-- so specs/016's observation layer captured nothing after deploy. This
module reuses every proven discovery building block BY IMPORT (constants,
parsers, prefilter, cooldown -- never a restated value, constitution
§1bis), owns only the loop plumbing, and calls
``momentum_entry.evaluate_momentum_entry`` directly: pure computation whose
return value specs/016's wrapper already persists as an observation.

Structural guarantees (each locked by a test in test_live_signal_observer.py):
- never calls run_paper_cycle / open_position / _default_momentum_analyzer /
  process_active_orders / send_trading_notification (source-level assertion
  + zero-row-delta test on paper_position and pending_limit_order);
- never checks paper_pause (FR-003) nor outgoing_pause -- the kill-switch's
  own docstring excludes operator Telegram messaging from its scope, and
  this service executes nothing financial (research.md §1);
- a message is built only from a PERSISTED observation row, never from a
  decision dict that failed to persist;
- a family whose inputs are mostly unavailable shows data quality LOW and
  no numeric figure -- absence of signal is never rendered as a weak signal.

Every presentation constant below is an explicitly UNCALIBRATED starting
value; calibrating them against forward performance is exactly what the
observation layer exists for. Never used to gate anything.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core import momentum_signal_observation
from aria_core.momentum_entry import (
    DEFAULT_CHAINS,
    _batch_liquidity_prefilter,
    normalize_contract_case,
    reference_tokens_excluded,
)
from aria_core.momentum_websocket import (
    _BACKOFF_INITIAL_SECONDS,
    _BACKOFF_MAX_SECONDS,
    _CONNECT_TIMEOUT_SECONDS,
    _RECV_TIMEOUT_SECONDS,
    DEDUP_TTL_SECONDS,
    DRAIN_INTERVAL_SECONDS,
    ENDPOINTS,
    MAX_CANDIDATES_PER_DRAIN,
    MAX_EVALUATIONS_PER_HOUR,
    RESCAN_COOLDOWN_SECONDS,
    RESCAN_PRICE_MOVE_THRESHOLD_PCT,
    WS_BASE_URL,
)
from aria_core.paths import aria_db_path
from aria_core.risk_guard import DEX_SECURITY_WEAK_THRESHOLD, FUNDAMENTAL_WEAK_THRESHOLD
from aria_core.services.dexscreener import parse_listing

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

_ALLOWED_CHAINS = frozenset(DEFAULT_CHAINS)

# -- presentation constants (initial, uncalibrated -- see module docstring) --
QUALITY_HIGH_RATIO = 0.75
QUALITY_MEDIUM_RATIO = 0.50
FAVORABLE_FIGURE = 65
UNFAVORABLE_FIGURE = 35
# Per-source freshness: a social value older than this is STALE, derived at
# presentation time from specs/016's stored data_timestamp -- never a new
# stored state (FR-007). conviction_research is synchronous (fresh by
# construction); radar_x is permanently not_available (no persisted state).
STALE_AFTER = {"signal_cascade_convergence": timedelta(hours=24)}
# Only the sub-signals the pipeline can actually expose today count toward a
# family's quality denominator. holder_concentration_top10_pct /
# smart_money_rescue_triggered / radar_x_signal are permanently
# not_available by construction (specs/016) and would otherwise cap on-chain
# and social quality at MEDIUM forever, hiding a real HIGH.
_FAMILY_SUBSIGNALS = {
    "onchain": ("composite_score", "composite_pillars"),
    "chart": (
        "golden_pocket_present", "rsi_divergence_present", "risk_reward_ratio",
        "technical_align_score", "rvol_confirmed", "market_regime",
    ),
    "social": ("conviction_research_score", "signal_cascade_convergence"),
}
SENDABLE_STATUSES = frozenset({"CONVERGENCE", "DIVERGENCE"})
NOTIFY_COOLDOWN_SECONDS = RESCAN_COOLDOWN_SECONDS  # never notify more often than the pipeline re-evaluates

_BANNED_WORDS = re.compile(r"(?i)\b(BUY|ENTRY|OPENED|FILLED)\b")

_NOTIFICATION_DDL = """
CREATE TABLE IF NOT EXISTS live_signal_notification (
    contract TEXT NOT NULL,
    chain TEXT NOT NULL,
    observation_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (contract, chain)
)
"""


def live_signal_observer_enabled() -> bool:
    """Dedicated gate, OFF by default -- read once at start(), same doctrine as momentum_websocket."""
    return os.environ.get("ARIA_LIVE_SIGNAL_OBSERVER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _signal_chat_id() -> int | None:
    """ARIA_SIGNAL_TELEGRAM_CHAT_ID as int; None = operator channel fallback (operator's v1 choice)."""
    raw = os.environ.get("ARIA_SIGNAL_TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("live_signal_observer: ARIA_SIGNAL_TELEGRAM_CHAT_ID is not an int, falling back")
        return None


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_NOTIFICATION_DDL)
        await db.commit()


# ---------------------------------------------------------------------------
# Presentation (pure, unit-testable)
# ---------------------------------------------------------------------------

def _parse_ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_stale(name: str, sub: dict, now: datetime) -> bool:
    threshold = STALE_AFTER.get(name)
    if threshold is None:
        return False
    ts = _parse_ts(sub.get("data_timestamp"))
    return ts is None or (now - ts) > threshold


def _is_favorable(family: str, name: str, value) -> bool:
    try:
        if family == "onchain":
            return name == "composite_score" and float(value) >= DEX_SECURITY_WEAK_THRESHOLD
        if family == "chart":
            if name in ("golden_pocket_present", "rsi_divergence_present"):
                return bool(value)
            if name == "risk_reward_ratio":
                return float(value) >= 1.0
            if name == "technical_align_score":
                return int(value) >= 2
            if name == "rvol_confirmed":
                return bool((value or {}).get("confirmed"))
            if name == "market_regime":
                return str(value) != "peur"
        if family == "social":
            if name == "conviction_research_score":
                return float(value) >= FUNDAMENTAL_WEAK_THRESHOLD
            if name == "signal_cascade_convergence":
                return any(bool(e.get("accelerating")) for e in (value or []))
    except (TypeError, ValueError):
        return False
    return False


def build_presentation(observation: dict, *, now: datetime | None = None) -> dict[str, dict]:
    """Per-family tally from the specs/016 row (decoded ``onchain``/``chart``/
    ``social`` dicts). Returns {family: {total, fresh, stale, favorable,
    quality, figure}} -- ``figure`` is None at LOW quality (FR-008)."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    for family, names in _FAMILY_SUBSIGNALS.items():
        block = observation.get(family) or {}
        total = len(names)
        fresh = stale = favorable = 0
        composite = None
        for name in names:
            sub = block.get(name) or {}
            if not sub.get("available"):
                continue
            if _is_stale(name, sub, now):
                stale += 1
                continue
            fresh += 1
            value = sub.get("value")
            if family == "onchain" and name == "composite_score":
                composite = value
            if _is_favorable(family, name, value):
                favorable += 1
        ratio = fresh / total if total else 0.0
        quality = "HIGH" if ratio >= QUALITY_HIGH_RATIO else "MEDIUM" if ratio >= QUALITY_MEDIUM_RATIO else "LOW"
        figure: int | None = None
        if quality != "LOW":
            if family == "onchain":
                figure = int(round(float(composite))) if composite is not None else None
            elif fresh:
                figure = int(round(100 * favorable / fresh))
        out[family] = {
            "total": total, "fresh": fresh, "stale": stale, "favorable": favorable,
            "quality": quality, "figure": figure,
        }
    return out


def classify(presentation: dict[str, dict]) -> str:
    """DATA_INCOMPLETE whenever any family is LOW (precedence, FR-009); else
    from the agreement of the judgeable families -- never from one combined number."""
    if any(p["quality"] == "LOW" or p["figure"] is None for p in presentation.values()):
        return "DATA_INCOMPLETE"
    figures = [p["figure"] for p in presentation.values()]
    favorable = [f >= FAVORABLE_FIGURE for f in figures]
    unfavorable = [f <= UNFAVORABLE_FIGURE for f in figures]
    if all(favorable):
        return "CONVERGENCE"
    if any(favorable) and any(unfavorable):
        return "DIVERGENCE"
    return "MIXED"


_STATUS_LINE = {
    "CONVERGENCE": "🟢 CONVERGENCE",
    "MIXED": "🟡 MIXED",
    "DIVERGENCE": "🔴 DIVERGENCE",
    "DATA_INCOMPLETE": "⚪ DATA INCOMPLETE",
}
_FAMILY_LABEL = {"onchain": "ON-CHAIN", "social": "SOCIAL", "chart": "CHART"}


def _family_lines(family: str, p: dict) -> list[str]:
    label = _FAMILY_LABEL[family]
    if p["figure"] is None:
        return [label, "n/a (data incomplete)"]
    mark = "✅" if p["figure"] >= FAVORABLE_FIGURE else "⚠️" if p["figure"] > UNFAVORABLE_FIGURE else "❌"
    return [label, f"{p['figure']}/100 {mark}"]


def format_signal(
    *, symbol: str | None, contract: str, chain: str, presentation: dict[str, dict],
    status: str, decision_ts: str, forward_rows: list[dict],
) -> str:
    """Plain text, dedicated live-signal format (operator's final layout).
    Never trade vocabulary -- a test asserts the banned-word regex."""
    lines = ["⚡ ARIA LIVE SIGNAL", "", f"${symbol or '?'}", f"Contract: {contract}", f"Chain: {chain}", ""]
    for family in ("onchain", "social", "chart"):
        lines += _family_lines(family, presentation[family]) + [""]
    lines += ["STATUS", _STATUS_LINE[status], "", "DATA"]
    for family in ("onchain", "social", "chart"):
        p = presentation[family]
        extra = f" ({p['stale']} stale)" if p["stale"] else ""
        lines.append(f"{_FAMILY_LABEL[family].title():<9} {p['quality']}{extra}")
    if forward_rows:
        lines += ["", "Forward:"]
        for r in forward_rows:
            lines.append(f"+{r['horizon']:<4} {r['status']}")
    ts = _parse_ts(decision_ts)
    lines += ["", f"⏱ {ts.strftime('%H:%M:%S') if ts else decision_ts} UTC"]
    text = "\n".join(lines)
    assert not _BANNED_WORDS.search(text), "live signal text must never carry trade vocabulary"
    return text


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class LiveSignalObserver:
    """Background service (start/stop from vanguard/backend/app/main.py, same
    pattern as ``momentum_websocket_listener``). Discovery/evaluation only."""

    def __init__(self) -> None:
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._pending: dict[tuple[str, str], float] = {}
        self._seen: dict[tuple[str, str], tuple[float, float | None]] = {}
        self._evaluation_timestamps: collections.deque[float] = collections.deque()

    async def start(self) -> None:
        if self._running:
            return
        if not live_signal_observer_enabled():
            logger.info("live_signal_observer: ARIA_LIVE_SIGNAL_OBSERVER_ENABLED disabled, service not started")
            return
        self._running = True
        for endpoint in ENDPOINTS:
            self._tasks.append(asyncio.create_task(self._endpoint_loop(endpoint)))
        self._tasks.append(asyncio.create_task(self._drain_loop()))
        logger.info("live_signal_observer: started (%d endpoints)", len(ENDPOINTS))

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
        import websockets

        backoff = _BACKOFF_INITIAL_SECONDS
        while self._running:
            try:
                async with websockets.connect(f"{WS_BASE_URL}{endpoint}", open_timeout=_CONNECT_TIMEOUT_SECONDS) as ws:
                    msg = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_SECONDS)
                    await self._ingest_frame(msg)
                backoff = _BACKOFF_INITIAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- never a silent loop crash
                logger.info("live_signal_observer: %s failed (%s), retrying in %.1fs", endpoint, exc, backoff)
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
                contract = normalize_contract_case(listing.token_address.strip(), chain)
                if not contract or not chain or chain not in _ALLOWED_CHAINS:
                    continue
                if contract.lower() in reference_tokens_excluded(chain):
                    continue
                key = (contract, chain)
                last = self._seen.get(key)
                if last is not None and (now - last[0]) < DEDUP_TTL_SECONDS:
                    continue
                self._pending.setdefault(key, now)

    async def _drain_loop(self) -> None:
        while self._running:
            await asyncio.sleep(DRAIN_INTERVAL_SECONDS)
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a failed drain never kills the service
                logger.exception("live_signal_observer: drain failed (%s)", exc)

    def _evaluation_budget_remaining(self, now: float) -> int:
        cutoff = now - 3600.0
        while self._evaluation_timestamps and self._evaluation_timestamps[0] < cutoff:
            self._evaluation_timestamps.popleft()
        return max(0, MAX_EVALUATIONS_PER_HOUR - len(self._evaluation_timestamps))

    async def _drain_once(self) -> int:
        """Returns the number of candidates evaluated. No paper_pause / outgoing_pause
        check by design (FR-003, research.md §1)."""
        async with self._lock:
            if not self._pending:
                return 0
            batch_keys = list(self._pending.keys())[:MAX_CANDIDATES_PER_DRAIN]
            previous_seen = {key: self._seen.get(key) for key in batch_keys}
            for key in batch_keys:
                self._pending.pop(key, None)

        raw_candidates = [{"contract": c, "chain": ch} for (c, ch) in batch_keys]
        try:
            filtered = await _batch_liquidity_prefilter(raw_candidates)
        except Exception as exc:  # noqa: BLE001 -- the prefilter must never block the drain
            logger.info("live_signal_observer: liquidity prefilter failed (%s)", exc)
            filtered = raw_candidates

        now_ts = time.time()
        price_by_key = {(c["contract"], c["chain"]): c.get("price_usd") for c in filtered}
        for key in batch_keys:
            price = price_by_key.get(key)
            if price is None:
                old = previous_seen.get(key)
                price = old[1] if old is not None else None
            self._seen[key] = (now_ts, price)

        def _still_in_cooldown(c: dict) -> bool:
            key = (c["contract"], c["chain"])
            old = previous_seen.get(key)
            if old is None:
                return False
            old_ts, old_price = old
            if (now_ts - old_ts) >= RESCAN_COOLDOWN_SECONDS:
                return False
            new_price = price_by_key.get(key)
            if old_price is None or new_price is None or old_price <= 0:
                return False
            return abs(new_price - old_price) / old_price < RESCAN_PRICE_MOVE_THRESHOLD_PCT

        filtered = [c for c in filtered if not _still_in_cooldown(c)]
        if not filtered:
            return 0

        budget = self._evaluation_budget_remaining(now_ts)
        if budget <= 0:
            logger.info("live_signal_observer: hourly cap reached (%d/h) -- drain skipped", MAX_EVALUATIONS_PER_HOUR)
            return 0
        filtered = filtered[:budget]
        self._evaluation_timestamps.extend([now_ts] * len(filtered))

        # Same portfolio-wide parameters the execution path resolves once per drain
        # (momentum_websocket._drain_new_candidates) -- pure DB reads, never a second decision path.
        from aria_core import momentum_entry, paper_trader
        from aria_core.skills import market_sentiment

        try:
            mode = await paper_trader.get_trading_mode()
        except Exception as exc:  # noqa: BLE001 -- degrades to "standard", never blocking
            logger.info("live_signal_observer: trading_mode lookup failed (%s)", exc)
            mode = "standard"
        try:
            current_regime = await market_sentiment.resolve_meta_regime()
        except Exception as exc:  # noqa: BLE001 -- degrades to neutral, never blocking
            logger.info("live_signal_observer: meta-regime lookup failed (%s)", exc)
            current_regime = None

        drain_started = datetime.now(timezone.utc)
        evaluated = 0
        for c in filtered:
            contract, chain = c["contract"], c["chain"]
            try:
                await momentum_entry.evaluate_momentum_entry(
                    contract, chain, current_regime=current_regime, mode=mode,
                )
                evaluated += 1
            except Exception as exc:  # noqa: BLE001 -- one candidate never stops the drain
                logger.info("live_signal_observer: evaluation failed for %s/%s (%s)", contract, chain, exc)
                continue
            try:
                await self._maybe_notify(contract, chain, since=drain_started)
            except Exception as exc:  # noqa: BLE001 -- a failed send never stops the drain
                logger.info("live_signal_observer: notify failed for %s/%s (%s)", contract, chain, exc)
        return evaluated

    async def _latest_observation(self, contract: str, chain: str, since: datetime) -> dict | None:
        rows = await momentum_signal_observation.list_recent(limit=4 * MAX_CANDIDATES_PER_DRAIN)
        for r in rows:
            if r["contract"] == contract.lower() and r["chain"] == chain:
                ts = _parse_ts(r["decision_ts"])
                return r if ts is not None and ts >= since - timedelta(seconds=5) else None
        return None

    async def _within_cooldown(self, contract: str, chain: str) -> bool:
        await _ensure_tables()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT notified_at FROM live_signal_notification WHERE contract=? AND chain=?",
                (contract.lower(), chain),
            )
            row = await cur.fetchone()
        if not row:
            return False
        ts = _parse_ts(row[0])
        return ts is not None and (datetime.now(timezone.utc) - ts).total_seconds() < NOTIFY_COOLDOWN_SECONDS

    async def _record_notification(self, contract: str, chain: str, observation_id: int, status: str) -> None:
        await _ensure_tables()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO live_signal_notification (contract, chain, observation_id, status, notified_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(contract, chain) DO UPDATE SET "
                "observation_id=excluded.observation_id, status=excluded.status, notified_at=excluded.notified_at",
                (contract.lower(), chain, observation_id, status, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def _maybe_notify(self, contract: str, chain: str, *, since: datetime) -> bool:
        """Builds the message ONLY from the persisted observation row (never from
        the decision dict), applies status threshold + cooldown, sends through
        the low-level primitive -- never send_trading_notification."""
        observation = await self._latest_observation(contract, chain, since)
        if observation is None:
            return False
        presentation = build_presentation(observation)
        status = classify(presentation)
        if status not in SENDABLE_STATUSES:
            return False
        if await self._within_cooldown(contract, chain):
            return False
        forward_rows = await momentum_signal_observation.forward_performance_for(observation["id"])
        text = format_signal(
            symbol=observation.get("symbol"), contract=contract, chain=chain,
            presentation=presentation, status=status,
            decision_ts=observation["decision_ts"], forward_rows=forward_rows,
        )
        from aria_core.gateway.telegram_bot import send_message

        sent = await send_message(text, chat_id=_signal_chat_id(), disable_preview=True)
        if sent:
            await self._record_notification(contract, chain, observation["id"], status)
        return bool(sent)


live_signal_observer = LiveSignalObserver()
