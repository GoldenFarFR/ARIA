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
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://gamma-api.polymarket.com"
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


class PolymarketClient:
    """Async HTTP client, read-only, cautious throttle (public API, no key)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 2.0) -> None:
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
