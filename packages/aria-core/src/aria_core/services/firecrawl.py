"""Firecrawl web-crawl client -- REPLACES Tavily as the crawl provider for
website_substance.py (09/08, explicit operator directive: "construis le
remplacement Tavily par Firecrawl"). Motivation: the cadence needed to scan
the full watchlist within 7 days (~77 web-column evaluations/hour, computed
live against the real backlog) requires ~13,000 crawls/week -- ~2.8x beyond
Tavily's highest priced tier (Growth, 100k credits/month, Enterprise
unpriced). Firecrawl's Standard plan ($83/month, 100,000 credits/month, 1
credit/page for markdown) covers this volume in a single paid tier -- full
cost comparison in docs/HANDOFF_SIGNAL_CASCADE.md.

Unlike Tavily's crawl() (a single synchronous POST), Firecrawl's crawl is
ASYNCHRONOUS: POST /v2/crawl returns a job id immediately, the real content
only appears once GET /v2/crawl/{id} reports status == "completed" -- this
client implements the full start -> poll -> aggregate cycle itself (never
the official firecrawl-py SDK, whose crawl() blocks the calling thread for
the whole crawl duration -- incompatible with aria-core's async-everywhere
pattern, cf. services/geckoterminal.py).

Sourced (docs.firecrawl.dev, verified live via WebFetch, 09/08):
- POST https://api.firecrawl.dev/v2/crawl -- starts a job. Body:
  {"url", "limit", "maxDiscoveryDepth", "scrapeOptions": {"formats": [...]}}
  -> {"success": true, "id": "<uuid>", "url": "..."}.
- GET https://api.firecrawl.dev/v2/crawl/{id} -- poll. "status" in
  ("scraping", "completed", "failed"); once "completed", "data" is a list of
  {"markdown", "html", "links", "metadata": {"title", "url", "sourceURL",
  "statusCode"}}.
- Auth: `Authorization: Bearer fc-<key>` (same header shape as Tavily's
  extract/crawl -- different key value, own env var).
- Rate limits (Standard plan, docs.firecrawl.dev/rate-limits): /crawl 100
  req/min, /scrape 500 req/min -- HTTP 429 on excess, `Retry-After` header
  documented. CLAUDE.md "90% of real capacity" doctrine: throttle at 90
  req/min (0.667s between calls) -- applied to BOTH the start POST and each
  poll GET (the poll endpoint's own rate-limit family is not documented
  separately -- fail-safe assumption: shares the same /crawl bucket until
  proven otherwise).
- Errors: 400 invalid URL, 408 timeout (retryable once), 429 rate limit,
  401/403 refused key.

This module only ever gets CALLED once website_substance.py's crawl swap
point is wired to it -- kept as an inert, independently-tested addition
until a real FIRECRAWL_API_KEY is available for a live smoke test (process
norm: "every new external API client tested against a REAL live call before
considered done")."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

CRAWL_START_URL = "https://api.firecrawl.dev/v2/crawl"
CRAWL_STATUS_URL = "https://api.firecrawl.dev/v2/crawl/{job_id}"

UNAVAILABLE = "donnée Firecrawl indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class FirecrawlPage:
    """A crawled page -- real markdown content, never a synthetic summary.
    Same shape as services.tavily.TavilyPage (both consumed identically by
    website_substance.gather_website_substance_facts)."""

    url: str
    title: str = ""
    raw_content: str = ""


@dataclass
class FirecrawlCrawlResult:
    root_url: str = ""
    pages: list[FirecrawlPage] = field(default_factory=list)
    available: bool = False
    error: str | None = None


def firecrawl_api_key() -> str:
    """Firecrawl key from the env ONLY (never hardcoded, never logged)."""
    return os.environ.get("FIRECRAWL_API_KEY", "").strip()


def is_firecrawl_configured() -> bool:
    return bool(firecrawl_api_key())


class FirecrawlClient:
    """Async HTTP client, read-only, moderate throttle. Implements the
    start -> poll -> aggregate cycle itself (see module docstring)."""

    # Sourced (09/08, docs.firecrawl.dev/rate-limits, Standard plan, live
    # WebFetch): 100 req/min on /crawl. CLAUDE.md "90% of real capacity"
    # doctrine: 90 req/min = 0.667s between calls.
    #
    # poll_interval/max_wait_s: no documented figure for a realistic crawl
    # duration at limit=15 -- kept conservative (90s ceiling, ~36 polls) to
    # avoid hogging a heartbeat cycle indefinitely. RECALIBRATE against real
    # observed durations once live in prod.
    def __init__(
        self, *, min_interval: float = 0.667, poll_interval: float = 2.5, max_wait_s: float = 90.0,
    ) -> None:
        self._min_interval = min_interval
        self._poll_interval = poll_interval
        self._max_wait_s = max_wait_s
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
                "firecrawl: %s consecutive failures (last: %s) -- no blocking",
                self._consecutive_failures, detail,
            )
        else:
            logger.info(
                "firecrawl: call failed (%s/%s) -- %s",
                self._consecutive_failures, _FAIL_STREAK_WARN_THRESHOLD, detail,
            )

    async def _request(
        self, method: str, url: str, *, json_body: dict | None = None,
        headers: dict | None = None, timeout: float = 15.0,
    ) -> tuple[object | None, str | None]:
        """Generic request with the guardrail's error policy (mirrors
        services/tavily.py's _post -- same 429/5xx/401/403 handling, plus
        Firecrawl's own documented 400/408). Never logs the key/payload."""
        attempt_429 = 0
        retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method == "POST":
                        response = await client.post(url, json=json_body, headers=headers)
                    else:
                        response = await client.get(url, headers=headers)
            except httpx.TransportError as exc:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} (timeout)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    self._record_failure(f"{url} -> HTTP 429 after {attempt_429} attempts")
                    return None, f"{UNAVAILABLE} (rate limit)"
                retry_after = response.headers.get("Retry-After") if hasattr(response, "headers") else None
                try:
                    wait_s = float(retry_after) if retry_after else 0.5 * (2**attempt_429)
                except (TypeError, ValueError):
                    wait_s = 0.5 * (2**attempt_429)
                await asyncio.sleep(wait_s)
                continue

            if response.status_code >= 500:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> HTTP {response.status_code}")
                return None, f"{UNAVAILABLE} (erreur serveur)"

            if response.status_code in (401, 403):
                # Missing/invalid key: soft degradation, the key is never logged.
                self._record_failure(f"{url} -> HTTP {response.status_code} (clé ?)")
                return None, f"{UNAVAILABLE} (clé refusée ou absente)"

            if response.status_code == 400:
                self._record_failure(f"{url} -> HTTP 400")
                return None, f"{UNAVAILABLE} (URL invalide)"

            if response.status_code == 408:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> HTTP 408")
                return None, f"{UNAVAILABLE} (timeout distant)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> HTTP {exc.response.status_code}")
                return None, f"{UNAVAILABLE} (HTTP {exc.response.status_code})"

            self._record_success()
            return response.json(), None

    async def crawl(
        self, root_url: str, *, limit: int = 15, max_discovery_depth: int = 2, caller: str = "unknown",
    ) -> FirecrawlCrawlResult:
        """Crawls a site starting from ``root_url`` -- drop-in replacement
        for services.tavily.TavilyClient.crawl in website_substance.py
        (same ``.available``/``.pages``/``.error`` shape). Asynchronous
        cycle: start job (POST) -> poll (GET) until status is "completed"/
        "failed" or ``max_wait_s`` elapsed.

        Budget: monthly credit cap checked BEFORE starting the job (worst
        case = ``limit`` pages, 1 credit/page for markdown-only), real spend
        recorded from the job's own ``creditsUsed`` field once known
        (falls back to the page count actually returned if that field is
        ever missing -- never silently unrecorded)."""
        url = (root_url or "").strip()
        if not url:
            return FirecrawlCrawlResult(available=False, error="URL racine vide")

        api_key = firecrawl_api_key()
        if not api_key:
            return FirecrawlCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (FIRECRAWL_API_KEY absente)")

        page_limit = max(1, min(int(limit), 100))
        depth = max(0, min(int(max_discovery_depth), 5))

        from aria_core.services import firecrawl_budget

        worst_case = firecrawl_budget.estimate_crawl_worst_case(page_limit)
        if not await firecrawl_budget.can_spend(worst_case):
            return FirecrawlCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (budget mensuel épuisé)")

        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "url": url,
            "limit": page_limit,
            "maxDiscoveryDepth": depth,
            "scrapeOptions": {"formats": ["markdown"]},
        }
        data, error = await self._request("POST", CRAWL_START_URL, json_body=payload, headers=headers, timeout=20.0)
        if error is not None:
            return FirecrawlCrawlResult(root_url=url, available=False, error=error)
        if not isinstance(data, dict) or not data.get("id"):
            return FirecrawlCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (réponse de démarrage illisible)")

        job_id = str(data["id"])
        status_url = CRAWL_STATUS_URL.format(job_id=job_id)

        elapsed = 0.0
        final: dict | None = None
        while elapsed < self._max_wait_s:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
            poll_data, poll_error = await self._request("GET", status_url, headers=headers, timeout=15.0)
            if poll_error is not None:
                return FirecrawlCrawlResult(root_url=url, available=False, error=poll_error)
            if not isinstance(poll_data, dict):
                continue
            status = poll_data.get("status")
            if status == "completed":
                final = poll_data
                break
            if status == "failed":
                return FirecrawlCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (job échoué)")

        if final is None:
            return FirecrawlCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (délai d'attente dépassé)")

        pages: list[FirecrawlPage] = []
        for item in final.get("data") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("markdown") or "").strip()
            if not content:
                continue
            metadata = item.get("metadata") or {}
            pages.append(
                FirecrawlPage(
                    url=str(metadata.get("url") or metadata.get("sourceURL") or ""),
                    title=str(metadata.get("title") or ""),
                    raw_content=content,
                )
            )

        real_credits = final.get("creditsUsed")
        credits = int(real_credits) if isinstance(real_credits, int) else len(pages)
        await firecrawl_budget.record_spend(caller=caller, query=f"crawl:{url}", credits=max(0, credits))

        if not pages:
            return FirecrawlCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (aucune page exploitable)")
        return FirecrawlCrawlResult(root_url=url, pages=pages, available=True, error=None)


firecrawl_client = FirecrawlClient()
