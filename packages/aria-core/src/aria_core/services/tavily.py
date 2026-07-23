"""Tavily web search client — a reliable provider for fact verification.

An alternative to DuckDuckGo (which returns systematic 403s with no throttle/backoff) for
the single entry point `web_verify.fetch_web_snippets`. Tavily is LLM-oriented: it
returns short excerpts already tailored for fact verification.

Guardrail doctrine (identical to goplus.py / blockscout.py):
- 429: exponential backoff, 3 attempts max, then give up without blocking the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation (empty list, never
  invented data).
- The API key lives ONLY in the environment (`TAVILY_API_KEY`) — never hardcoded, never
  logged. Without a key, the client is simply `available=False` and makes no call.

Read-only. Gated upstream by `settings.aria_web_search_provider == "tavily"`: until
the operator flips the flag AND supplies a key, DuckDuckGo remains the provider.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
# 07/23 -- added to route X reading to Tavily (cheaper than the x402
# twit.sh fallback) and for full site extraction (Website/Docs Substance,
# never possible with site_snapshot.py's 600-character snapshot, designed
# to enrich an LLM prompt, not for a site audit). Authentication
# verified under real conditions (07/23): ``Authorization: Bearer <key>``
# works on ALL THREE endpoints (search/extract/crawl) -- used here
# for these two new endpoints; ``search()`` keeps its historical
# authentication (key in the body) unchanged, never touched without reason.
EXTRACT_URL = "https://api.tavily.com/extract"
CRAWL_URL = "https://api.tavily.com/crawl"

UNAVAILABLE = "donnée Tavily indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class TavilyResult:
    """Web excerpts returned by Tavily. `available=False` + `error` if unavailable;
    never invented data."""

    query: str
    # Each snippet: (text, url, raw published_date or None -- Tavily doesn't always
    # provide it, especially outside "news" search; cf. #126).
    snippets: list[tuple[str, str, str | None]] = field(default_factory=list)
    # Optional synthetic answer from Tavily (include_answer).
    answer: str | None = None
    available: bool = False
    error: str | None = None


@dataclass
class TavilyPage:
    """An extracted page (via ``extract`` or ``crawl``) -- real text content,
    never a synthetic summary."""

    url: str
    title: str = ""
    raw_content: str = ""


@dataclass
class TavilyExtractResult:
    urls: list[str] = field(default_factory=list)
    pages: list[TavilyPage] = field(default_factory=list)
    available: bool = False
    error: str | None = None


@dataclass
class TavilyCrawlResult:
    root_url: str = ""
    pages: list[TavilyPage] = field(default_factory=list)
    available: bool = False
    error: str | None = None


def tavily_api_key() -> str:
    """Tavily key from the env ONLY (never hardcoded, never logged)."""
    return os.environ.get("TAVILY_API_KEY", "").strip()


def is_tavily_configured() -> bool:
    return bool(tavily_api_key())


class TavilyClient:
    """Async HTTP client, read-only, moderate throttle."""

    # 07/21 -- calibrated to 90% of a confirmed 100 req/min (Development tier,
    # confirmed on the real dashboard for the "ARIA" key -- "dev" type, prefix
    # "tvly-dev-" -- not the Production tier at 1000/min). CLAUDE.md doctrine
    # "Throughput calibrated to 90%": 90/min = 0.667s. Replaces 0.5s (120/min), which
    # already exceeded the real Dev cap.
    def __init__(self, *, min_interval: float = 0.667) -> None:
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
                "tavily: %s consecutive failures (last: %s) -- no blocking",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "tavily: call failed (%s/%s) -- %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _post(
        self, url: str, payload: dict, *, headers: dict | None = None, timeout: float = 15.0,
    ) -> tuple[object | None, str | None]:
        """Generic POST with the guardrail's error policy. Returns (data, error).

        NB: the API key (body OR ``Authorization`` header) is never logged --
        only the URL and the error code are logged, never the payload/header."""
        attempt_429 = 0
        retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
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
                await asyncio.sleep(0.5 * (2**attempt_429))
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

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> HTTP {exc.response.status_code}")
                return None, f"{UNAVAILABLE} (HTTP {exc.response.status_code})"

            self._record_success()
            return response.json(), None

    async def _post_json(self, payload: dict) -> tuple[object | None, str | None]:
        """Historical fallback for ``search()`` -- key in the body, never touched."""
        return await self._post(TAVILY_URL, payload)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 4,
        search_depth: str = "basic",
        include_answer: bool = True,
        include_domains: list[str] | None = None,
        caller: str = "unknown",
    ) -> TavilyResult:
        """Tavily search for a query. Best-effort, never blocking.

        ``include_domains`` (07/22): restricts results to specific domains --
        verified live that ``["twitter.com", "x.com"]`` returns real relevant
        results (already-indexed public posts/profiles), without going through the
        official X API nor the x402 fallback (twit.sh). Honest scope: classic
        web indexing, not a real-time feed -- suited for monitoring, not an
        urgent decision.

        ``caller`` (07/22): identifies who's spending (e.g. ``web_verify``,
        ``tavily_learning``) -- serves traceability (``tavily_budget.recent_searches``),
        not just the budget. Shared MONTHLY budget (``tavily_budget.py``,
        900/1000 credits) checked PROACTIVELY here, before any real HTTP call --
        same doctrine as ``blockscout.py`` for its Pro budget."""
        q = (query or "").strip()
        if not q:
            return TavilyResult(query=q, available=False, error="requête vide")

        api_key = tavily_api_key()
        if not api_key:
            return TavilyResult(query=q, available=False, error=f"{UNAVAILABLE} (TAVILY_API_KEY absente)")

        depth = search_depth if search_depth in ("basic", "advanced") else "basic"

        from aria_core.services import tavily_budget

        credit_cost = tavily_budget.cost_for_search(depth)
        if not await tavily_budget.can_spend(credit_cost):
            return TavilyResult(query=q, available=False, error=f"{UNAVAILABLE} (budget mensuel épuisé)")

        payload = {
            "api_key": api_key,
            "query": q[:400],
            "search_depth": depth,
            "max_results": max(1, min(int(max_results), 10)),
            "include_answer": bool(include_answer),
        }
        if include_domains:
            payload["include_domains"] = list(include_domains)[:10]
        data, error = await self._post_json(payload)
        if error is not None:
            return TavilyResult(query=q, available=False, error=error)
        await tavily_budget.record_spend(caller=caller, query=q, credits=credit_cost)
        if not isinstance(data, dict):
            return TavilyResult(query=q, available=False, error=UNAVAILABLE)

        snippets: list[tuple[str, str, str | None]] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("content") or item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            published = item.get("published_date")
            published = str(published).strip() if published else None
            if len(text) >= 15:
                snippets.append((text[:280], url, published))

        answer = data.get("answer")
        answer = str(answer).strip() if answer else None

        if not snippets and not answer:
            return TavilyResult(query=q, available=False, error=f"{UNAVAILABLE} (aucun résultat)")

        return TavilyResult(query=q, snippets=snippets, answer=answer, available=True, error=None)

    async def extract(
        self, urls: list[str], *, extract_depth: str = "basic", caller: str = "unknown",
    ) -> TavilyExtractResult:
        """REAL text content of one or several pages -- unlike ``search``
        (THIRD-PARTY excerpts about a page), this is the content of the page
        itself, rendered by Tavily's infrastructure (handles JS server-side --
        verified under real conditions on 07/23: works on an X/Twitter
        SPA page, which ``site_snapshot.py`` -- a plain httpx GET -- couldn't render).

        07/23, #routing X reads to Tavily + Website/Docs Substance -- REPLACES
        ``twit.sh`` (x402, paid per call) for X profiles when Tavily is
        configured, and replaces ``site_snapshot.py``'s 600-character snapshot
        for substance signals (which stays unchanged for its
        historical use -- enriching the LLM prompt, not an audit)."""
        clean_urls = [u.strip() for u in (urls or []) if u and u.strip()][:20]
        if not clean_urls:
            return TavilyExtractResult(available=False, error="aucune URL fournie")

        api_key = tavily_api_key()
        if not api_key:
            return TavilyExtractResult(urls=clean_urls, available=False, error=f"{UNAVAILABLE} (TAVILY_API_KEY absente)")

        depth = extract_depth if extract_depth in ("basic", "advanced") else "basic"

        from aria_core.services import tavily_budget

        credit_cost = tavily_budget.cost_for_extract(depth, len(clean_urls))
        if not await tavily_budget.can_spend(credit_cost):
            return TavilyExtractResult(urls=clean_urls, available=False, error=f"{UNAVAILABLE} (budget mensuel épuisé)")

        payload = {"urls": clean_urls, "extract_depth": depth}
        headers = {"Authorization": f"Bearer {api_key}"}
        data, error = await self._post(EXTRACT_URL, payload, headers=headers, timeout=25.0)
        if error is not None:
            return TavilyExtractResult(urls=clean_urls, available=False, error=error)
        await tavily_budget.record_spend(caller=caller, query=f"extract:{clean_urls[0]}", credits=credit_cost)
        if not isinstance(data, dict):
            return TavilyExtractResult(urls=clean_urls, available=False, error=UNAVAILABLE)

        pages: list[TavilyPage] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("raw_content") or "").strip()
            if not content:
                continue
            pages.append(
                TavilyPage(url=str(item.get("url") or ""), title=str(item.get("title") or ""), raw_content=content)
            )

        if not pages:
            return TavilyExtractResult(urls=clean_urls, available=False, error=f"{UNAVAILABLE} (aucune page exploitable)")
        return TavilyExtractResult(urls=clean_urls, pages=pages, available=True, error=None)

    async def crawl(
        self, root_url: str, *, max_depth: int = 2, limit: int = 15,
        extract_depth: str = "basic", caller: str = "unknown",
    ) -> TavilyCrawlResult:
        """Crawls a site starting from ``root_url`` (follows internal links,
        including subdomains like ``docs.<site>``) and returns the real
        text content of every page found -- the only way to "extract everything to
        score" a multi-page site (explicit operator request, 07/23); the
        homepage-only 600-character snapshot from ``site_snapshot.py`` never covers
        subpages (Docs/Team/Tokenomics...).

        Variable cost (depends on the REAL number of pages returned, known only
        after the call) -- budget check BEFORE the call on the WORST CASE
        (``limit`` pages, Tavily never returns more), REAL spend
        recorded afterward based on the pages actually received."""
        url = (root_url or "").strip()
        if not url:
            return TavilyCrawlResult(available=False, error="URL racine vide")

        api_key = tavily_api_key()
        if not api_key:
            return TavilyCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (TAVILY_API_KEY absente)")

        depth_param = max(1, min(int(max_depth), 3))
        page_limit = max(1, min(int(limit), 30))
        extract_d = extract_depth if extract_depth in ("basic", "advanced") else "basic"

        from aria_core.services import tavily_budget

        worst_case = tavily_budget.estimate_crawl_worst_case(extract_d, page_limit)
        if not await tavily_budget.can_spend(worst_case):
            return TavilyCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (budget mensuel épuisé)")

        payload = {
            "url": url, "max_depth": depth_param, "limit": page_limit, "extract_depth": extract_d,
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        data, error = await self._post(CRAWL_URL, payload, headers=headers, timeout=60.0)
        if error is not None:
            return TavilyCrawlResult(root_url=url, available=False, error=error)
        if not isinstance(data, dict):
            await tavily_budget.record_spend(caller=caller, query=f"crawl:{url}", credits=0)
            return TavilyCrawlResult(root_url=url, available=False, error=UNAVAILABLE)

        pages: list[TavilyPage] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("raw_content") or "").strip()
            if not content:
                continue
            pages.append(
                TavilyPage(url=str(item.get("url") or ""), title=str(item.get("title") or ""), raw_content=content)
            )

        real_cost = tavily_budget.cost_for_crawl(extract_d, len(pages))
        await tavily_budget.record_spend(caller=caller, query=f"crawl:{url}", credits=real_cost)

        if not pages:
            return TavilyCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (aucune page exploitable)")
        return TavilyCrawlResult(root_url=url, pages=pages, available=True, error=None)


tavily_client = TavilyClient()
