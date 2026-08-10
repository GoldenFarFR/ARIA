"""Homemade multi-page website scraper -- FIRST-CHOICE crawl provider for
website_substance.py (10/08, backlog item #43, "à valider avec opérateur"
-- validated same day, explicit "oui go les deux"). Reduces dependence on
Firecrawl (free plan, 2 req/min, very low ceiling) and Tavily (shared
monthly credit budget across many other callers) for the common case: a
plain HTTP fetch + regex extraction, no third-party quota at all.

Real evidence this is viable BEFORE being built (never assumed): a live
test against 12 real watchlist project sites (09/08, see
docs/HANDOFF_SIGNAL_CASCADE.md) found 11/12 answer HTTP 200 with usable
content (13 to 11,583 words), the one failure a WAF (403) -- JS-only SPA
shells were NOT the dominant failure mode on this sample. Reuses the exact
extraction logic already proven in ``site_snapshot.py`` (title/meta-
description/visible-text regex, hidden-element stripping against prompt-
injection) rather than a new parser -- the only real addition here is
following internal links across ~15 pages instead of a single page, and
removing that module's 600-character cap (built for an LLM-prompt preview,
not for a substance audit).

Same CrawlResult SHAPE as ``services/firecrawl.py``/``services/tavily.py``
(``.available``/``.pages[].raw_content``/``.error``) so
``website_substance._default_crawl`` can try this FIRST and fall through to
Firecrawl/Tavily unchanged on a thin/failed result -- never a hard
replacement, both external providers stay as real fallbacks (a WAF or a
JS-only SPA will keep failing here no matter how many times it's retried).

No robots.txt/ToS check (documented honestly as a limitation, not silently
ignored) -- consistent with the existing ``site_snapshot.py``, which
already does the same plain GET against the same class of sites without
one. Self-imposed pacing only (``_MIN_INTERVAL_S`` between requests) since
there is no third-party quota to respect; a real, identifying User-Agent
(reused from ``site_snapshot.py``) rather than pretending to be a
browser."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée scraper maison indisponible"

_TIMEOUT_SECONDS = 8.0
_MAX_RAW_HTML_CHARS = 200_000  # per page -- caps parsing work, not network size
_USER_AGENT = "Mozilla/5.0 (compatible; AriaVanguardBot/1.0; +https://ariavanguardzhc.com)"

# Same shape as website_substance._PAGES_FOUND_FULL_SCORE=5, some margin
# above it (Tavily's own crawl targeted "15 real pages" per that module's
# docstring) -- never unbounded, a self-hosted crawl has no external quota
# to naturally cap it.
_MAX_PAGES = 15

# Self-imposed pacing between OUR OWN requests to the SAME target site --
# no third-party quota to respect, this exists purely as politeness (never
# hammer a single site with 15 near-simultaneous requests).
_MIN_INTERVAL_S = 0.5

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE
)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Same technical hidden-element signal as site_snapshot.py (never a class-
# name heuristic) -- defense against a page hiding prompt-injection text
# from a human visitor while still exposing it to this extractor.
_HIDDEN_ELEMENT_RE = re.compile(
    r"<([a-zA-Z][a-zA-Z0-9]*)\b(?=[^>]*"
    r"(?:style\s*=\s*[\"'][^\"']*(?:display\s*:\s*none|visibility\s*:\s*hidden)[^\"']*[\"']"
    r"|\bhidden\b"
    r"|aria-hidden\s*=\s*[\"']true[\"'])"
    r")[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HREF_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\'#]+)["\']', re.IGNORECASE)


def _clean_text(raw: str) -> str:
    return _WS_RE.sub(" ", raw).strip()


def _extract_page_text(html: str) -> tuple[str, str]:
    """Same extraction as site_snapshot.py's _extract_snapshot_text, minus
    its 600-character cap (built for an LLM-prompt preview, not a substance
    audit that needs real word counts)."""
    title_match = _TITLE_RE.search(html)
    title = _clean_text(title_match.group(1)) if title_match else ""
    desc_match = _META_DESC_RE.search(html)
    description = _clean_text(desc_match.group(1)) if desc_match else ""

    body = _SCRIPT_STYLE_RE.sub(" ", html)
    body = _HIDDEN_ELEMENT_RE.sub(" ", body)
    body = _TAG_RE.sub(" ", body)
    visible_text = _clean_text(body)

    parts = [p for p in (description, visible_text) if p]
    return title, " — ".join(parts)


def _registrable_domain(host: str) -> str:
    """Last two labels (``example.com`` from ``docs.example.com``) -- good
    enough to decide "same project" for internal-link discovery without a
    public-suffix-list dependency; a real limitation on multi-part TLDs
    (``example.co.uk`` would compare on ``co.uk``) documented here rather
    than silently mishandled -- acceptable since a false-negative here only
    means fewer pages crawled, never a wrong/unsafe result."""
    labels = host.lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.lower()


def _extract_internal_links(html: str, base_url: str, root_domain: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if _registrable_domain(parsed.netloc) != root_domain:
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


@dataclass
class WebsiteScraperPage:
    """Same shape as FirecrawlPage/TavilyPage -- consumed identically by
    website_substance.gather_website_substance_facts."""

    url: str
    title: str = ""
    raw_content: str = ""


@dataclass
class WebsiteScraperResult:
    root_url: str = ""
    pages: list[WebsiteScraperPage] = field(default_factory=list)
    available: bool = False
    error: str | None = None


async def _fetch_one(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """Returns (html, title) or None on any failure/non-HTML content --
    never raises, never blocking."""
    try:
        r = await client.get(url, headers={"User-Agent": _USER_AGENT})
    except Exception as exc:  # noqa: BLE001 -- never blocking
        logger.info("website_scraper: fetch %s failed (%s)", url, exc)
        return None
    if r.status_code != 200:
        return None
    content_type = r.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return None
    return r.text[:_MAX_RAW_HTML_CHARS], ""


async def crawl(url: str, *, caller: str = "website_substance") -> WebsiteScraperResult:
    """Fetches the homepage, follows internal links (same registrable
    domain, including subdomains like ``docs.<site>``) up to ``_MAX_PAGES``
    total. Self-paced (``_MIN_INTERVAL_S`` between requests, no third-party
    quota to respect). Returns ``available=False`` (never raises) on the
    homepage itself failing -- a real crawl needs at least a working
    homepage, a failed internal link is just skipped."""
    parsed_root = urlparse(url)
    if parsed_root.scheme not in ("http", "https") or not parsed_root.netloc:
        return WebsiteScraperResult(root_url=url, available=False, error=f"{UNAVAILABLE} (URL invalide)")
    root_domain = _registrable_domain(parsed_root.netloc)

    pages: list[WebsiteScraperPage] = []
    visited: set[str] = set()
    to_visit = [url]

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        while to_visit and len(pages) < _MAX_PAGES:
            next_url = to_visit.pop(0)
            if next_url in visited:
                continue
            visited.add(next_url)

            if pages:  # never throttle the very first (homepage) request
                await asyncio.sleep(_MIN_INTERVAL_S)
            fetched = await _fetch_one(client, next_url)
            if fetched is None:
                if not pages:
                    # Homepage itself failed -- no point trying internal
                    # links discovered from a page we never fetched.
                    return WebsiteScraperResult(
                        root_url=url, available=False,
                        error=f"{UNAVAILABLE} (homepage inaccessible: {next_url})",
                    )
                continue

            html, _ = fetched
            title, text = _extract_page_text(html)
            pages.append(WebsiteScraperPage(url=next_url, title=title, raw_content=text))

            if len(pages) < _MAX_PAGES:
                for link in _extract_internal_links(html, next_url, root_domain):
                    if link not in visited and link not in to_visit:
                        to_visit.append(link)

    if not pages:
        return WebsiteScraperResult(root_url=url, available=False, error=UNAVAILABLE)
    return WebsiteScraperResult(root_url=url, pages=pages, available=True)
