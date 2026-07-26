"""Read-only Polymarket client (Gamma API) — macro signal via prediction
market (#59).

Exposes the IMPLIED probability (market price, 0-1) of real macro events
(e.g. Fed rate decisions) — a signal complementary to `btc_cycles` (which
reads the halving cycle, not monetary-policy expectations). No writes, no API
key required (public Gamma API). Same error policy as
`services/coingecko.py` (cf. AGENTS.md):
- Timeout / endpoint unavailable: 1 retry after 5s, then explicit fallback.
- Missing data is never replaced by a guess — the `error` field (and
  `available=False`) carry the absence of data.

Wired into the `/vc` LLM context (`vc_analysis._fetch_polymarket_signals`)
since 10/07 -- the "dormant seam" note above is stale, fixed on 19/07 (doc/code
drift found while auditing the /vc<->momentum unification). Since 19/07,
`momentum_entry.py` reuses the SAME client + the SAME formatter
(`format_polymarket_prompt_lines`) for the same depth of analysis.

26/07 -- extended for Item #108 (ARIA places paper bets on Polymarket, a new
asset class distinct from crypto momentum/VC-thesis -- explicit operator
decision, paper-only for now). Two additions, still read-only, no key
required: ``list_liquid_events`` (real listing/filtering of tradeable markets
-- the old ``fetch_top_event_by_tag`` only ever returns ONE event per tag, not
enough to source real candidates) and ``get_order_book`` (real bid/ask depth
from the CLOB, `clob.polymarket.com` -- a DIFFERENT domain/infrastructure than
Gamma, confirmed via a live call requiring no auth for market-data reads).
The CLOB market-data endpoints (``/book``/``/price``/``/midprice``) are rate
limited at 1,500 requests/10s per Polymarket's official docs
(docs.polymarket.com/api-reference/rate-limits, cross-checked via aggregator
guides since the docs page itself is JS-rendered and not scrapable with a
plain HTTP call) -- our real usage (a handful of book lookups per Polymarket
paper cycle, not a tight loop) sits nowhere near that ceiling, so the CLOB
throttle below is deliberately conservative rather than tuned to 90% of
capacity (the "90% of real capacity" doctrine exists to avoid a NEEDLESS
speed loss when there IS a real throughput need -- there isn't one here).
Gamma and CLOB get SEPARATE throttles (own lock, own `_min_interval`) since
they're genuinely different servers with different limits, never a
duplicated throttle for the SAME provider.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
UNAVAILABLE = "signal Polymarket indisponible"

# Polymarket tags to query for macro context (#59). Only ``fed-rates`` for
# now: tested live on 10/07, gives the most liquid prediction market on Fed
# rate decisions -- a signal complementary to ``btc_cycles`` (halving cycle)
# and ``market_sentiment`` (short/medium-term technical). Extending to other
# tags = operator decision.
DEFAULT_TAGS: list[str] = ["fed-rates"]


@dataclass
class PolymarketOutcome:
    label: str
    probability: float  # 0.0-1.0, market price = implied probability


@dataclass
class PolymarketEventSummary:
    available: bool
    title: str | None = None
    slug: str | None = None
    outcomes: list[PolymarketOutcome] = field(default_factory=list)
    volume_usd: float | None = None
    error: str | None = None


@dataclass
class PolymarketCandidateMarket:
    """One tradeable YES/NO market (26/07, Item #108) -- unlike
    ``PolymarketEventSummary`` (event-level, used for the existing macro LLM
    context), this is MARKET-level: an event can hold several distinct
    markets (e.g. "Fed Decision in July?" holds a separate market per bps
    step) and only the market level carries the ``clob_token_ids`` needed to
    look up a real order book."""

    event_title: str
    event_slug: str
    question: str
    yes_token_id: str | None
    no_token_id: str | None
    yes_price: float | None  # Gamma's own outcomePrices[0] -- a reference point, not the fill price.
    volume_usd: float | None
    liquidity_usd: float | None
    end_date: str | None  # ISO 8601, e.g. "2026-07-29T00:00:00Z"
    tags: list[str] = field(default_factory=list)


@dataclass
class PolymarketOrderBook:
    """Real bid/ask depth for one outcome token (26/07, Item #108) -- a
    market's ``yes_price`` from Gamma is a reference point, not what a real
    fill would cost; the order book is the closer-to-truth number used to
    simulate slippage (same doctrine as the momentum pipeline's
    ``simulated_fill_price``, never assume the spot/reference price is the
    real execution price)."""

    available: bool
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    error: str | None = None


@dataclass
class PolymarketPricePoint:
    timestamp: int  # unix epoch seconds
    probability: float


class PolymarketClient:
    """Async HTTP client, read-only, cautious throttle (public API, no key)."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        min_interval: float = 2.0,
        clob_base_url: str = CLOB_BASE_URL,
        clob_min_interval: float = 0.5,
    ) -> None:
        self.clob_base_url = clob_base_url.rstrip("/")
        self._clob_min_interval = clob_min_interval
        self._clob_lock = asyncio.Lock()
        self._clob_last_request = 0.0
        self.base_url = base_url.rstrip("/")
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

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        logger.info("polymarket: call failed -- %s", detail)

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    async def fetch_top_event_by_tag(self, tag_slug: str) -> PolymarketEventSummary:
        """Most liquid macro event for a given tag (e.g. `fed-rates`).

        Never a fabricated probability: event/market not found or malformed
        data -> `available=False`.
        """
        url = (
            f"{self.base_url}/events?limit=1&active=true&closed=false"
            f"&tag_slug={tag_slug}&order=volume&ascending=false"
        )
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url)
        except httpx.TransportError as exc:
            await asyncio.sleep(5.0)
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(url)
            except httpx.TransportError as exc2:
                self._record_failure(f"{url} -> {exc2}")
                return PolymarketEventSummary(available=False, error=f"{UNAVAILABLE} (timeout)")
        except Exception as exc:  # noqa: BLE001 -- a network outage must never propagate
            self._record_failure(f"{url} -> {exc}")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        if response.status_code >= 400:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return PolymarketEventSummary(available=False, error=f"{UNAVAILABLE} (HTTP {response.status_code})")

        try:
            events = response.json()
        except Exception:  # noqa: BLE001
            self._record_failure(f"{url} -> unreadable response")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        if not isinstance(events, list) or not events:
            self._record_failure(f"{url} -> no event for this tag")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        event = events[0]
        markets = event.get("markets") or []
        outcomes: list[PolymarketOutcome] = []
        for m in markets:
            question = m.get("question")
            raw_prices = m.get("outcomePrices")
            if not question or not raw_prices:
                continue
            try:
                # outcomePrices is a JSON STRING (not a real list) on this
                # endpoint -- verified live on 10/07, never assume the type.
                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                prob = float(prices[0])  # price of "Yes" -> implied probability of the question.
            except (ValueError, TypeError, IndexError, json.JSONDecodeError):
                continue
            outcomes.append(PolymarketOutcome(label=question, probability=prob))

        if not outcomes:
            self._record_failure(f"{url} -> markets with no usable price")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        self._record_success()
        return PolymarketEventSummary(
            available=True,
            title=event.get("title"),
            slug=event.get("slug"),
            outcomes=outcomes,
            volume_usd=float(event["volume"]) if event.get("volume") is not None else None,
        )

    async def _get_json_clob(self, url: str) -> tuple[object | None, str | None]:
        """CLOB-specific fetch: own throttle/lock (`_clob_*`, separate server
        from Gamma), same retry-once-after-5s policy as the rest of this
        client. Returns ``(data, error)`` -- never raises."""
        async with self._clob_lock:
            now = asyncio.get_event_loop().time()
            wait = self._clob_min_interval - (now - self._clob_last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._clob_last_request = asyncio.get_event_loop().time()

        async def _attempt() -> httpx.Response:
            async with httpx.AsyncClient(timeout=20.0) as client:
                return await client.get(url)

        try:
            response = await _attempt()
        except httpx.TransportError:
            await asyncio.sleep(5.0)
            try:
                response = await _attempt()
            except httpx.TransportError as exc2:
                self._record_failure(f"{url} -> {exc2}")
                return None, f"{UNAVAILABLE} (timeout)"
        except Exception as exc:  # noqa: BLE001 -- a network outage must never propagate
            self._record_failure(f"{url} -> {exc}")
            return None, UNAVAILABLE

        if response.status_code >= 400:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return None, f"{UNAVAILABLE} (HTTP {response.status_code})"
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            self._record_failure(f"{url} -> unreadable response")
            return None, UNAVAILABLE
        self._record_success()
        return data, None

    async def get_order_book(self, token_id: str) -> PolymarketOrderBook:
        """Real bid/ask depth for one CLOB outcome token (26/07, Item #108).

        Public endpoint, no auth required (verified live). Empty book (no
        bids or no asks -- e.g. a market that just opened, or one already
        resolved) is a legitimate state, never an error: ``available=True``
        with ``best_bid``/``best_ask`` left ``None``."""
        if not token_id:
            return PolymarketOrderBook(available=False, error="token_id manquant")
        url = f"{self.clob_base_url}/book?token_id={token_id}"
        data, error = await self._get_json_clob(url)
        if error is not None:
            return PolymarketOrderBook(available=False, error=error)
        if not isinstance(data, dict):
            return PolymarketOrderBook(available=False, error=UNAVAILABLE)

        def _best_price(levels: object) -> float | None:
            if not isinstance(levels, list) or not levels:
                return None
            try:
                return float(levels[0]["price"])
            except (KeyError, TypeError, ValueError, IndexError):
                return None

        best_bid = _best_price(data.get("bids"))
        best_ask = _best_price(data.get("asks"))
        spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
        return PolymarketOrderBook(available=True, best_bid=best_bid, best_ask=best_ask, spread=spread)

    async def list_liquid_events(
        self,
        *,
        min_volume_usd: float = 50_000.0,
        min_liquidity_usd: float = 20_000.0,
        max_days_to_resolution: int | None = 30,
        min_days_to_resolution: float = 0.25,
        limit: int = 100,
    ) -> list[PolymarketCandidateMarket]:
        """Real listing of tradeable markets (26/07, Item #108) -- unlike
        ``fetch_top_event_by_tag`` (ONE event, hardcoded to a single tag,
        built for the macro LLM-context use case), this lists many events
        across ALL active tags and flattens each into its individual
        markets, filtered on volume/liquidity/time-to-resolution.

        ``limit`` (26/07, verified live): Gamma's own ``/events`` endpoint
        caps its real response well below any requested value on this
        query shape (100 returned when 500 was requested, tested live) --
        never assume the requested limit is honored, filtering always
        happens on whatever comes back.

        ``min_days_to_resolution`` (default 6 hours): a market resolving in
        the next few minutes leaves no real time for a paper position to
        exist -- excluded here rather than in the judgment layer, since it's
        a structural property of the market itself, not a probability
        estimate.

        Never a fabricated market: network failure or empty response ->
        empty list, same fail-open posture as `momentum_entry.discover_
        momentum_candidates` tolerating a source outage."""
        url = f"{self.base_url}/events?limit={int(limit)}&active=true&closed=false&order=volume&ascending=false"
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url)
        except Exception as exc:  # noqa: BLE001 -- a network outage must never propagate
            self._record_failure(f"{url} -> {exc}")
            return []

        if response.status_code >= 400:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return []
        try:
            events = response.json()
        except Exception:  # noqa: BLE001
            self._record_failure(f"{url} -> unreadable response")
            return []
        if not isinstance(events, list):
            return []

        now = datetime.now(timezone.utc)
        candidates: list[PolymarketCandidateMarket] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            volume = event.get("volume")
            liquidity = event.get("liquidity")
            end_date_raw = event.get("endDate")
            try:
                volume_f = float(volume) if volume is not None else None
                liquidity_f = float(liquidity) if liquidity is not None else None
            except (TypeError, ValueError):
                continue
            if volume_f is None or volume_f < min_volume_usd:
                continue
            if liquidity_f is None or liquidity_f < min_liquidity_usd:
                continue
            days_left: float | None = None
            if end_date_raw:
                try:
                    end_dt = datetime.fromisoformat(str(end_date_raw).replace("Z", "+00:00"))
                    days_left = (end_dt - now).total_seconds() / 86400.0
                except (ValueError, TypeError):
                    days_left = None
            if days_left is None or days_left < min_days_to_resolution:
                continue
            if max_days_to_resolution is not None and days_left > max_days_to_resolution:
                continue

            tags = [t.get("slug") for t in (event.get("tags") or []) if isinstance(t, dict) and t.get("slug")]
            for m in event.get("markets") or []:
                if not isinstance(m, dict):
                    continue
                question = m.get("question")
                token_ids_raw = m.get("clobTokenIds")
                prices_raw = m.get("outcomePrices")
                if not question or not token_ids_raw:
                    continue
                try:
                    token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
                except (json.JSONDecodeError, TypeError):
                    token_ids = None
                yes_token = token_ids[0] if isinstance(token_ids, list) and len(token_ids) > 0 else None
                no_token = token_ids[1] if isinstance(token_ids, list) and len(token_ids) > 1 else None
                yes_price: float | None = None
                try:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    yes_price = float(prices[0]) if isinstance(prices, list) and prices else None
                except (json.JSONDecodeError, TypeError, ValueError, IndexError):
                    yes_price = None

                m_volume = m.get("volume")
                m_liquidity = m.get("liquidity")
                try:
                    m_volume_f = float(m_volume) if m_volume is not None else volume_f
                except (TypeError, ValueError):
                    m_volume_f = volume_f
                try:
                    m_liquidity_f = float(m_liquidity) if m_liquidity is not None else liquidity_f
                except (TypeError, ValueError):
                    m_liquidity_f = liquidity_f

                candidates.append(
                    PolymarketCandidateMarket(
                        event_title=str(event.get("title") or ""),
                        event_slug=str(event.get("slug") or ""),
                        question=str(question),
                        yes_token_id=str(yes_token) if yes_token else None,
                        no_token_id=str(no_token) if no_token else None,
                        yes_price=yes_price,
                        volume_usd=m_volume_f,
                        liquidity_usd=m_liquidity_f,
                        end_date=str(end_date_raw) if end_date_raw else None,
                        tags=tags,
                    )
                )

        self._record_success()
        return candidates

    async def get_market_resolution(self, event_slug: str, yes_token_id: str) -> tuple[bool, float | None]:
        """Checks whether a specific market has resolved (26/07, Item #108) --
        ``(is_resolved, yes_final_price)``. ``yes_final_price`` is ``1.0``/``0.0``
        once resolved, ``None`` while still open.

        Uses ``/events/slug/{slug}`` (verified live: returns a single event
        object, not a list) rather than re-listing/filtering all events --
        the position already knows exactly which event it came from. Matches
        the specific market within that event by ``yes_token_id`` (never by
        ``question`` text, which could theoretically collide or drift) --
        falls back to unresolved/``(False, None)`` on any lookup failure,
        never a fabricated resolution."""
        if not event_slug or not yes_token_id:
            return False, None
        url = f"{self.base_url}/events/slug/{event_slug}"
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url)
        except Exception as exc:  # noqa: BLE001 -- a network outage must never propagate
            self._record_failure(f"{url} -> {exc}")
            return False, None
        if response.status_code >= 400:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return False, None
        try:
            event = response.json()
        except Exception:  # noqa: BLE001
            self._record_failure(f"{url} -> unreadable response")
            return False, None
        if not isinstance(event, dict):
            return False, None

        for m in event.get("markets") or []:
            if not isinstance(m, dict):
                continue
            token_ids_raw = m.get("clobTokenIds")
            try:
                token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(token_ids, list) or not token_ids or str(token_ids[0]) != yes_token_id:
                continue
            if not m.get("closed"):
                return False, None
            prices_raw = m.get("outcomePrices")
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                yes_final = float(prices[0]) if isinstance(prices, list) and prices else None
            except (json.JSONDecodeError, TypeError, ValueError, IndexError):
                return False, None
            if yes_final is None:
                return False, None
            self._record_success()
            return True, yes_final

        return False, None

    async def get_price_history(
        self, token_id: str, *, interval: str = "1w", fidelity: int = 1440,
    ) -> list[PolymarketPricePoint]:
        """Real historical probability series via the CLOB's own
        ``/prices-history`` endpoint (26/07, Item #108 follow-up -- operator
        insight: "si la chance passe de 20% a 70% en 1 semaine c'est que
        quelque chose se passe" -- a fast-moving market price is itself a
        signal worth reacting to, distinct from ARIA's own static research
        snapshot). ``interval``/``fidelity`` use Polymarket's own vocabulary
        (verified live: "1d"/"1w"/"1m"/"max", ``fidelity`` = minutes between
        points, 1440 = daily). Empty list on any failure -- never a
        fabricated point, same fail-open posture as the rest of this
        client."""
        if not token_id:
            return []
        url = f"{self.clob_base_url}/prices-history?market={token_id}&interval={interval}&fidelity={fidelity}"
        data, error = await self._get_json_clob(url)
        if error is not None or not isinstance(data, dict):
            return []
        points: list[PolymarketPricePoint] = []
        for row in data.get("history") or []:
            if not isinstance(row, dict):
                continue
            try:
                points.append(PolymarketPricePoint(timestamp=int(row["t"]), probability=float(row["p"])))
            except (KeyError, TypeError, ValueError):
                continue
        return points


def compute_probability_velocity(history: list[PolymarketPricePoint]) -> float | None:
    """Probability-point delta between the FIRST and LAST point of a history
    window (the caller controls the window via ``get_price_history``'s
    ``interval``/``fidelity``) -- ``None`` if fewer than 2 points (nothing to
    compare). A large delta signals a real shift in how the market reads the
    underlying event -- NEVER a trade trigger by itself (same "the signal
    wakes up attention, real judgment still arbitrates" doctrine as
    ``radar_x.py``'s social signal): consumed as extra CONTEXT for
    ``skills.polymarket_thesis.estimate_market_probability``, prioritizing a
    fresh, targeted research pass rather than deciding anything on its own."""
    if len(history) < 2:
        return None
    return history[-1].probability - history[0].probability


def format_polymarket_prompt_lines(events: list[dict]) -> list[str]:
    """Compact lines for injection into an LLM prompt (19/07) -- extracted from
    ``vc_analysis.py``'s inline logic (until then duplicated in substance by
    every caller) so that ``momentum_entry.py`` benefits from the SAME macro
    diligence as `/vc` without reimplementing filtering/truncation/sanitization.

    Input: the shape produced by looping over ``fetch_top_event_by_tag`` --
    ``[{"title": str, "outcomes": [{"label": str, "probability": float}, ...]}]``.
    3 outcomes max per event (same cap as ``vc_analysis.py``), never a
    fabricated probability -- a malformed entry is simply skipped, never an
    exception propagating to the caller."""
    from aria_core.sanitize import sanitize_untrusted_text

    lines: list[str] = []
    for event in events:
        title = sanitize_untrusted_text(event.get("title") or "", 120)
        for outcome in (event.get("outcomes") or [])[:3]:
            label = sanitize_untrusted_text(outcome.get("label") or "", 160)
            prob = outcome.get("probability")
            if label and prob is not None:
                try:
                    lines.append(f"- [{title}] {label} : {float(prob):.0%}")
                except (TypeError, ValueError):
                    pass
    return lines


polymarket_client = PolymarketClient()
