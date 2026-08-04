"""Codex.io client (GraphQL, real-time enriched blockchain data) -- new OHLCV
cascade tier (29/07, Item #185), inserted between DexPaprika and the degraded
DexScreener synthesis in ``momentum_entry.fetch_candles``.

Context: triggered by a real production incident (wallet_scan_queue live-lock,
29/07) -- ``smart_money.py``'s wallet-scoring path called GeckoTerminal
directly with zero fallback, unlike the momentum pipeline's own 6-stage
cascade. While fixing that (Item #186, reusing the existing cascade), the
operator asked for one more real provider in the cascade itself, verified live
before use (never assumed from marketing docs, same doctrine as dexpaprika.py):

- Legitimacy: real company, powers Defined.fi (well-known DEX charting tool
  used broadly in the crypto community -- a genuine signal, not just a
  landing page). 76M+ tokens across 80+ chains including Base. Real X
  presence, real pricing page (docs.codex.io / codex.io/pricing).
- API shape: GraphQL (not REST) at ``https://graph.codex.io/graphql``, POST
  with an ``Authorization: <API_KEY>`` header (raw key, no "Bearer " prefix
  needed for a standard secret key -- the prefix is only for short-lived JWT
  tokens, a different auth mode this client doesn't use). ``getBars(symbol,
  from, to, resolution)`` returns PARALLEL arrays (``o``/``h``/``l``/``c``/
  ``t``/``volume``, not a list of row objects). Base networkId=8453,
  Ethereum networkId=1. All of the above directly confirmed live against 3
  official doc pages (docs.codex.io/concepts/authentication,
  docs.codex.io/networks, docs.codex.io/reference/getbars) on 29/07, not
  just search-engine summaries. Max 1500 datapoints/request.
- ``symbol`` accepts ``"<address>:<networkId>"`` where address can be a pool
  OR a token (token auto-resolves to its top pair) -- this client always
  passes a POOL address for consistency with every other tier of the cascade
  (all receive a known ``pool_address``, never a bare token).
- Free tier: 10,000 requests/month, 5 req/s (docs.codex.io/pricing) -- by far
  the SCARCEST budget of any provider in this cascade (DexPaprika measured
  ~53 req/min sustained, GeckoTerminal/CoinMarketCap/Mobula are per-second
  throttled, not monthly-capped). Deliberately positioned as a LAST real-
  candle tier (after DexPaprika, before the DexScreener degraded synthesis)
  -- "increasing cost" ordering, and this IS the most costly/rare budget of
  them all. A dedicated monthly counter (``codex_request_log``, this module)
  enforces an internal cap of 9,500/month (95% of the real 10,000 -- 5%
  safety margin, never runs the real account into an overage/lockout) --
  fails CLOSED on this provider only (skip Codex, the cascade degrades to
  the next tier) once the cap is reached, never blocking.

LIVE-TESTED on 04/08 (real key in prod since then -- the "NOT YET
LIVE-TESTED" caveat that stood here from 29/07 is resolved): real calls from
the prod container returned 241 candles on the reference Base pool AND, the
decisive part, real candles on two low-volume pools invisible to DexPaprika
(ROBO $21k/24h: 159 candles standard + 101 real 15m candles fresh to ~3min;
Anon $4.7k/24h: 241 candles) -- this provider has the best small-cap
coverage of the whole cascade, which is exactly why the scalping ladder
below exists despite the scarce budget.

Scalping ladder (04/08, operator "go"): ``mode="scalping"`` requests real
15m -> 30m bars, wired as the LAST tier of the scalping cascade (after
GeckoTerminal/Mobula/DexPaprika) in ``momentum_entry``. Guarded by its own
monthly sub-budget (``_MONTHLY_SCALPING_CAP``, a slice INSIDE the global
cap, never in addition to it): once the slice is spent, scalping calls fail
closed (skip, cascade degrades) while standard-mode calls continue up to
the global cap -- the standard tier keeps priority on this scarce budget.

"Dome" doctrine (identical to dexpaprika.py/mobula.py):
- 429: exponential backoff, 3 attempts max, then give up without blocking.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation.
- A GraphQL-level ``errors`` field (HTTP 200 but the query itself failed) is
  treated the same as a network failure -- never silently ignored.
- Missing data is never replaced by a guess.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import aiosqlite
import httpx

from aria_core.paths import aria_db_path
from aria_core.services.geckoterminal import OHLCVResult
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnee Codex.io indisponible"

GRAPHQL_URL = "https://graph.codex.io/graphql"
DB_PATH = str(aria_db_path())

# Base=8453/Ethereum=1 are each chain's own EVM chainId (Codex reuses it
# directly as its networkId, confirmed via docs.codex.io) -- explicit mapping
# rather than assuming every chain this project touches maps 1:1 (e.g. a
# future Solana/non-EVM addition would need its own entry here, never a
# guessed number).
_NETWORK_IDS: dict[str, int] = {"base": 8453, "ethereum": 1}

# 29/07 -- calibrated to 90% of the documented 5 req/s (CLAUDE.md doctrine
# "rate calibrated to 90% of the real capacity"): 4.5 req/s = 0.222s.
_MIN_INTERVAL = 0.222
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()

# 29/07 -- 95% of the real documented 10,000/month cap (5% safety margin --
# never runs the real account into an overage/lockout from this process
# alone). Deliberately the SCARCEST budget in the whole OHLCV cascade, hence
# this provider's last-resort position (see module docstring).
_MONTHLY_REQUEST_CAP = 9_500

# 04/08 -- scalping sub-budget: a SLICE of the global cap above (never in
# addition to it) reserved for the scalping ladder. Sized so a fully-spent
# slice still leaves 5,500+ requests/month to the standard tier (which was
# consuming <1,000/month when this was added). Scalping calls fail closed
# (skip) once the slice is spent; standard calls only stop at the global cap.
_MONTHLY_SCALPING_CAP = 4_000

_STANDARD_LADDER: tuple[str, ...] = ("1D", "240", "60")  # day -> 4h -> 1h
# 15m first (matches the scalping candle width used by every variant), 30m
# as the explicit degradation -- mirrors the Mobula/DexPaprika scalping
# ladders in this same cascade.
_SCALPING_LADDER: tuple[str, ...] = ("15", "30")
_MIN_USEFUL_CANDLES = 20
_CANDLES_TO_REQUEST = 120
_WINDOW_SAFETY_FACTOR = 2.0
_RESOLUTION_SECONDS: dict[str, int] = {
    "1D": 86400, "240": 14400, "60": 3600, "30": 1800, "15": 900,
}


def codex_configured() -> bool:
    return bool(os.environ.get("CODEX_IO_API_KEY", "").strip())


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS codex_request_log (requested_at TEXT NOT NULL)"
        )
        # 04/08 -- soft migration for the scalping sub-budget: rows written
        # before this column existed default to 'standard', which is exactly
        # what they all were (the scalping ladder didn't exist yet).
        try:
            await db.execute(
                "ALTER TABLE codex_request_log ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard'"
            )
        except aiosqlite.OperationalError:
            pass  # column already exists
        await db.commit()


def _month_start(now: datetime | None = None) -> datetime:
    n = now or datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _requests_this_month(now: datetime | None = None, *, mode: str | None = None) -> int:
    """Total requests this month; ``mode`` restricts the count to one
    ladder's slice (``"scalping"``), ``None`` counts everything (the global
    cap's view)."""
    await _ensure_table()
    start = _month_start(now)
    async with aiosqlite.connect(DB_PATH) as db:
        if mode is None:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM codex_request_log WHERE requested_at >= ?",
                (start.isoformat(),),
            )
        else:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM codex_request_log WHERE requested_at >= ? AND mode = ?",
                (start.isoformat(), mode),
            )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def _record_request(now: datetime | None = None, *, mode: str = "standard") -> None:
    await _ensure_table()
    ts = (now or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO codex_request_log (requested_at, mode) VALUES (?, ?)", (ts, mode)
        )
        await db.commit()


async def _monthly_cap_reached(*, mode: str = "standard") -> bool:
    """Global cap applies to every call; scalping additionally fails closed
    on its own sub-budget slice (see ``_MONTHLY_SCALPING_CAP``)."""
    if await _requests_this_month() >= _MONTHLY_REQUEST_CAP:
        return True
    if mode == "scalping":
        return await _requests_this_month(mode="scalping") >= _MONTHLY_SCALPING_CAP
    return False


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        elapsed = time.monotonic() - _last_call_at
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_call_at = time.monotonic()


async def _graphql_query(
    query: str, variables: dict, *, mode: str = "standard"
) -> tuple[dict | None, str | None]:
    """POST with the same dome retry policy as the rest of the OHLCV
    cascade. Returns (data, error) -- ``data`` is the ``data`` field of the
    GraphQL response, never the raw envelope."""
    api_key = os.environ.get("CODEX_IO_API_KEY", "").strip()
    if not api_key:
        return None, f"{UNAVAILABLE} (cle CODEX_IO_API_KEY absente)"

    attempt_429 = 0
    timeout_retried = False
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    while True:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    GRAPHQL_URL, json={"query": query, "variables": variables}, headers=headers,
                )
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("codex: timeout on getBars -> %s", exc)
            return None, f"{UNAVAILABLE} (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("codex: HTTP 429 after %s attempts", attempt_429)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("codex: HTTP %s", response.status_code)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("codex: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        await _record_request(mode=mode)
        payload = response.json()
        if not isinstance(payload, dict):
            return None, f"{UNAVAILABLE} (reponse illisible)"
        errors = payload.get("errors")
        if errors:
            logger.warning("codex: GraphQL errors -> %s", errors)
            return None, f"{UNAVAILABLE} (erreur GraphQL: {errors})"
        data = payload.get("data")
        if not isinstance(data, dict):
            return None, f"{UNAVAILABLE} (champ data absent)"
        return data, None


def _compute_window(resolution: str, limit: int) -> tuple[int, int]:
    seconds = _RESOLUTION_SECONDS.get(resolution, 3600) * limit * _WINDOW_SAFETY_FACTOR
    now = int(time.time())
    return now - int(seconds), now


def _parse_bars(data: object) -> list[Candle]:
    """``getBars`` returns PARALLEL arrays (o/h/l/c/t/volume), never a list
    of row objects -- confirmed via docs.codex.io/reference/getbars. A
    missing/null entry at any index drops that ONE candle (never a guess),
    a length mismatch across arrays is treated as fully unusable (a
    malformed response, not a partial one worth salvaging)."""
    if not isinstance(data, dict):
        return []
    bars = data.get("getBars")
    if not isinstance(bars, dict):
        return []
    o, h, low, c, t, v = (bars.get(k) for k in ("o", "h", "l", "c", "t", "volume"))
    lists = [o, h, low, c, t]
    if not all(isinstance(x, list) for x in lists):
        return []
    n = len(t)
    if any(len(x) != n for x in lists):
        return []
    volumes = v if isinstance(v, list) and len(v) == n else [0.0] * n

    candles: list[Candle] = []
    for i in range(n):
        try:
            if any(x[i] is None for x in (o, h, low, c, t)):
                continue
            candles.append(
                Candle(
                    ts=int(t[i]), open=float(o[i]), high=float(h[i]),
                    low=float(low[i]), close=float(c[i]),
                    volume=float(volumes[i]) if volumes[i] is not None else 0.0,
                )
            )
        except (TypeError, ValueError):
            continue
    candles.sort(key=lambda x: x.ts)
    return candles


_GET_BARS_QUERY = """
query GetBars($symbol: String!, $from: Int!, $to: Int!, $resolution: String!) {
  getBars(symbol: $symbol, from: $from, to: $to, resolution: $resolution) {
    o
    h
    l
    c
    volume
    t
  }
}
"""


async def _fetch_one_resolution(
    pool_address: str, network_id: int, resolution: str, *, mode: str = "standard"
) -> list[Candle]:
    start, end = _compute_window(resolution, _CANDLES_TO_REQUEST)
    data, error = await _graphql_query(
        _GET_BARS_QUERY,
        {"symbol": f"{pool_address}:{network_id}", "from": start, "to": end, "resolution": resolution},
        mode=mode,
    )
    if error is not None:
        logger.info("codex: %s:%s (%s) failed -- %s", pool_address[:10], network_id, resolution, error)
        return []
    return _parse_bars(data)


async def get_ohlcv(pool_address: str, *, network: str = "base", mode: str = "standard") -> OHLCVResult:
    """Real OHLCV candles for ``pool_address`` on ``network`` -- last real-
    candle tier of the cascade (never primary, see module docstring).
    ``mode="standard"`` walks the day/4h/1h ladder under the global monthly
    cap; ``mode="scalping"`` (04/08) walks the real 15m/30m ladder and
    additionally fails closed on its own sub-budget slice
    (``_MONTHLY_SCALPING_CAP``) so the scalping call volume can never starve
    the standard tier of this scarce budget."""
    if not codex_configured():
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (non configure)")

    network_id = _NETWORK_IDS.get(network)
    if network_id is None:
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (chaine {network} non mappee)")

    if await _monthly_cap_reached(mode=mode):
        logger.info(
            "codex: monthly request cap reached (global %s, scalping slice %s, mode=%s) -- skipping this call",
            _MONTHLY_REQUEST_CAP, _MONTHLY_SCALPING_CAP, mode,
        )
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (plafond mensuel atteint)")

    ladder = _SCALPING_LADDER if mode == "scalping" else _STANDARD_LADDER
    best: list[Candle] = []
    for resolution in ladder:
        candles = await _fetch_one_resolution(pool_address, network_id, resolution, mode=mode)
        if not candles:
            continue
        if len(candles) >= _MIN_USEFUL_CANDLES:
            return OHLCVResult(candles=candles, available=True, error=None)
        if len(candles) > len(best):
            best = candles

    if best:
        return OHLCVResult(candles=best, available=True, error=None)
    return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (aucune bougie)")
