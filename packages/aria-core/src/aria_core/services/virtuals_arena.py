"""Read-only client — Virtuals Arena public leaderboard (Hyperliquid perps).

Phase 0 of the Arena pilot (backlog #60): observe the public leaderboard,
**zero wallet, zero key, zero execution**. No call other than GET on the
confirmed public endpoint (no key required):

    GET https://degen.virtuals.io/api/leaderboard?limit=&offset=

Same dome as ``services/virtuals.py``: throttle, 429 backoff, timeout/5xx
retry, graceful degradation (``fetch_leaderboard`` never raises, returns
``None`` on failure). Every external string (agent name, token symbol)
goes through the same anti-prompt-injection sanitization (angle-bracket
neutralization) as the rest of the ``services/`` folder.

Deliberately minimal: a single anchor point (this class), so that if
Virtuals evolves the Arena (mechanics, UI, program), only one file needs
touching — never business logic that assumes their current program details
(template, mirror pot).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

LEADERBOARD_ENDPOINT = "https://degen.virtuals.io/api/leaderboard"

UNAVAILABLE = "donnée Arena Virtuals indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3

# Dome: same defense as virtuals.py (single choke point).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FIELD_MAX = 200


def _sanitize(text: object, max_len: int = _FIELD_MAX) -> str | None:
    """Neutralizes an external string. ``None`` stays ``None`` (facts-only)."""
    if text is None:
        return None
    s = _CONTROL_CHARS_RE.sub("", str(text))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    if value is None or isinstance(value, bool) or isinstance(value, (list, dict)):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass
class ArenaAgentEntry:
    """One Arena leaderboard agent. All strings are already sanitized."""

    id: str | None = None
    name: str | None = None
    token_symbol: str | None = None
    token_address: str | None = None
    agent_address: str | None = None
    total_realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    holdings_value_usd: float | None = None
    total_trade_count: int | None = None
    win_count: int | None = None
    loss_count: int | None = None
    win_rate: float | None = None
    return_pct: float | None = None
    total_trade_volume: float | None = None
    last_trade_at: str | None = None


@dataclass
class ArenaLeaderboard:
    """Leaderboard page. ``entries`` sorted as returned by the API (by rank)."""

    time_range: str | None = None
    total: int | None = None
    entries: list[ArenaAgentEntry] = field(default_factory=list)


def _parse_entry(raw: dict) -> ArenaAgentEntry | None:
    """Parses a leaderboard object. Never an exception, never a guess."""
    if not isinstance(raw, dict):
        return None
    perf = raw.get("performance") if isinstance(raw.get("performance"), dict) else {}
    return ArenaAgentEntry(
        id=_sanitize(raw.get("id"), 40),
        name=_sanitize(raw.get("name"), 120),
        token_symbol=_sanitize(raw.get("tokenSymbol"), 20),
        token_address=_sanitize(raw.get("tokenAddress"), 80),
        agent_address=_sanitize(raw.get("agentAddress"), 80),
        total_realized_pnl=_safe_float(perf.get("totalRealizedPnl")),
        unrealized_pnl=_safe_float(perf.get("unrealizedPnl")),
        holdings_value_usd=_safe_float(perf.get("holdingsValueUsd")),
        total_trade_count=_safe_int(perf.get("totalTradeCount")),
        win_count=_safe_int(perf.get("winCount")),
        loss_count=_safe_int(perf.get("lossCount")),
        win_rate=_safe_float(perf.get("winRate")),
        return_pct=_safe_float(perf.get("returnPct")),
        total_trade_volume=_safe_float(perf.get("totalTradeVolume")),
        last_trade_at=_sanitize(perf.get("lastTradeAt"), 40),
    )


class ArenaClient:
    """Async HTTP client, read-only, cautious throttle (public API, no key)."""

    def __init__(self, endpoint: str = LEADERBOARD_ENDPOINT, *, min_interval: float = 0.5) -> None:
        self.endpoint = endpoint
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "virtuals_arena: %s consecutive failures (last: %s) — not blocking",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "virtuals_arena: call failure (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, url: str) -> tuple[object | None, str | None]:
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers={"accept": "application/json"})
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout Arena)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 after {attempt_429} attempts"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Arena)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Arena)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def fetch_leaderboard(self, limit: int = 20, offset: int = 0) -> ArenaLeaderboard | None:
        """Public leaderboard page. ``None`` on error — never an exception."""
        try:
            safe_limit = max(1, min(_safe_int(limit) or 20, 100))
            safe_offset = max(0, _safe_int(offset) or 0)
            params = urlencode({"limit": safe_limit, "offset": safe_offset})
            url = f"{self.endpoint}?{params}"
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict) or not data.get("success"):
                return None
            items = data.get("data")
            if not isinstance(items, list):
                return None
            entries = [entry for raw in items if (entry := _parse_entry(raw)) is not None]
            pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
            return ArenaLeaderboard(
                time_range=_sanitize(data.get("timeRange"), 40),
                total=_safe_int(pagination.get("total")),
                entries=entries,
            )
        except Exception as exc:  # ultimate degradation: never an exception escapes
            logger.info("virtuals_arena: fetch_leaderboard unexpected failure — %s", exc)
            return None


arena_client = ArenaClient()
