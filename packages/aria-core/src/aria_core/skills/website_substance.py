"""Website Substance signal -- REAL multi-page content of a project site,
via ``services.tavily.TavilyClient.crawl`` (23/07, direct operator request
from a real screenshot of project links: "elle doit pouvoir extraire tout pour
noter").

Distinct from ``services/site_snapshot.py`` (600 characters, a single page,
designed to enrich an LLM prompt with a preview -- never to audit a
site). Verified under real conditions (23/07) on crynux.io: the homepage alone
is 110,798 characters of raw HTML, the existing snapshot only extracts
600 of them -- the Tavily crawl renders the JS and follows internal links
(including subdomains, e.g. docs.<site>), 15 real pages retrieved in a single call.

Same spirit as ``github_substance.py``: measurable FACTS, never a
judgment the current infrastructure has no means to make -- visual
coherence/design ("generic memecoin template" vs. own
identity) would require a screenshot + a vision model, absent from
ARIA's auto pipeline. Documented as an honest limitation, never simulated or
fabricated (CLAUDE.md absolute rule)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Structuring section keywords -- TEXTUAL proxy for a site's transparency/
# structure (real presence of content on these topics), never a
# visual navigation check. English-only (the vast majority of project
# sites publish in English regardless of the team's own language).
_KEY_SECTION_KEYWORDS = (
    "team", "roadmap", "tokenomics",
    "whitepaper", "docs", "documentation", "audit",
)
_GENERIC_PLACEHOLDER_MARKERS = ("lorem ipsum",)

# Below this number of cumulated REAL words (across all pages), the sample
# is too small to judge honestly -- never a score fabricated on top of it
# (same doctrine as ``_MIN_RAW_COMMITS_BEFORE_DETAIL`` in github_substance.py).
_MIN_WORDS_FOR_SIGNAL = 150

# Full depth score at this number of cumulated words -- calibrated on the
# real CNX case (~4,500 useful words on the crawl's main pages), never an
# arbitrary unanchored figure.
_WORD_COUNT_FULL_SCORE = 3000
_KEY_SECTIONS_FULL_SCORE = 4
_PAGES_FOUND_FULL_SCORE = 5


@dataclass
class WebsiteSubstanceFacts:
    available: bool = False
    error: str | None = None
    pages_found: int = 0
    total_words: int = 0
    https: bool = False
    key_sections_found: int = 0
    has_generic_placeholder: bool = False
    # 09/08, operator design ("je vois pas sur X et sur le site web l'équipe
    # écrire ce contrat" -- a real impersonation risk: a token can reference a
    # legitimate project's site/name without that project ever having
    # launched or acknowledged it): whether the crawled RAW content itself
    # contains the candidate's own contract address. None = not checked (no
    # contract passed in, or every existing caller before this field
    # existed) -- never conflated with the substance SCORE above, a site can
    # be substantial AND not confirm this specific contract.
    contract_confirmed: bool | None = None


@dataclass
class WebsiteSubstanceVerdict:
    signal: str  # "positive" | "neutral" | "weak" | "unknown"
    score: float | None
    points: list[str] = field(default_factory=list)


@dataclass
class _EmptyCrawlResult:
    """Synthetic fallback -- only returned if EVERY layer in _CRAWL_LAYERS
    raised (none of the current 3 ever does, per their own "never
    blocking" doctrine; kept as a safety net for a future layer that
    might not honor it)."""

    root_url: str = ""
    pages: list = field(default_factory=list)
    available: bool = False
    error: str | None = None


async def _crawl_via_scraper(url: str):
    from aria_core.services.website_scraper import crawl as scraper_crawl

    return await scraper_crawl(url, caller="website_substance")


async def _crawl_via_firecrawl(url: str):
    from aria_core.services.firecrawl import firecrawl_client, is_firecrawl_configured
    from aria_core.services.firecrawl import FirecrawlCrawlResult

    if not is_firecrawl_configured():
        return FirecrawlCrawlResult(root_url=url, available=False, error="clé non configurée")
    return await firecrawl_client.crawl(url, caller="website_substance")


async def _crawl_via_tavily(url: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.crawl(url, caller="website_substance")


# 10/08 -- seam left deliberately open (CLAUDE.md "ANTICIPATION" doctrine):
# a future 4th/5th crawl candidate is added here as ONE more (name, fn)
# entry, never a structural rewrite of _default_crawl. Order matters (first
# match wins) -- scraper maison (zero third-party quota) always first,
# shared-budget providers last. Deliberately NOT expanded speculatively:
# website_crawl_failure_log.py exists precisely to gather real evidence
# before that decision is made (operator question 10/08: "firecrawl + tavily
# ça va suffire ?" -- answer: yes at the current real volume, revisit only
# if failure_count_since() shows otherwise).
_CRAWL_LAYERS = [
    ("scraper_maison", _crawl_via_scraper),
    ("firecrawl", _crawl_via_firecrawl),
    ("tavily", _crawl_via_tavily),
]


def _pages_confirm_contract(pages, contract: str) -> bool:
    combined = " ".join(p.raw_content for p in pages).lower()
    return contract.strip().lower() in combined


# 12/08, real incident (signal-cascade triage, wJUNO diligence): a declared
# "official website" (seamlessprotocol.com) turned out to 301-redirect to
# its own X profile (x.com/SeamlessFi). Firecrawl followed that redirect
# server-side and then discovered/scraped several profile sub-pages through
# its dedicated (expensive, ~30 credits/page) X engine -- 156 credits for a
# single crawl (39% of the monthly free-plan budget) for content ARIA
# already gets far cheaper via services.twitterapi_io. A domain check on
# the INPUT url alone would have missed this (seamlessprotocol.com itself
# isn't an X domain) -- the check below resolves redirects FIRST via a
# cheap HEAD request (no crawl-provider credits spent either way) so a
# redirect chain landing on X is caught before any paid layer runs.
_SOCIAL_PLATFORM_HOSTS = ("x.com", "twitter.com")


def _is_social_platform_host(host: str) -> bool:
    host = (host or "").lower().removeprefix("www.")
    return host in _SOCIAL_PLATFORM_HOSTS


async def _resolves_to_social_platform(url: str) -> bool:
    """Fail-open by design: a HEAD failure/timeout here must never block a
    legitimate crawl -- worst case is falling back to the pre-12/08
    behavior for that one call, never worse. Only a CONFIRMED resolution to
    x.com/twitter.com (following real redirects) returns True."""
    try:
        host = urlparse(url).netloc
    except ValueError:
        return False
    if _is_social_platform_host(host):
        return True

    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.head(url)
    except Exception as exc:  # noqa: BLE001 -- fail-open, never blocking
        logger.info("website_substance: redirect pre-check failed for %s (%s)", url, exc)
        return False
    return _is_social_platform_host(urlparse(str(response.url)).netloc)


async def _default_crawl(url: str, *, contract: str | None = None):
    """Tries every layer in ``_CRAWL_LAYERS`` in order, first
    ``available=True`` result wins -- never a hard dependency on any single
    one: a layer returning unavailable (homepage unreachable/WAF-blocked/
    JS-only-SPA for the scraper; no key/budget exhausted/failure for
    Firecrawl/Tavily) falls straight through to the next, exactly as if it
    didn't exist. If EVERY layer fails, logs the event (with each layer's
    own error) via ``website_crawl_failure_log`` before returning the last
    result -- the real signal to consult before deciding a new layer is
    worth adding, never a guess.

    12/08 -- real false negative found live (HAYLORD, signal-cascade
    triage): the scraper maison (plain HTTP fetch, no JS rendering) returned
    ``available=True`` with plenty of real content, but missed a
    dynamically-rendered element carrying the token's own contract address --
    the old "first available wins" rule stopped right there and never tried
    Firecrawl (which DOES render JS, and did find the address on manual
    inspection). When ``contract`` is given and the first available layer's
    combined content doesn't confirm it, the REMAINING layers are now tried
    IN ADDITION, purely to look for the contract -- their pages are merged
    into the first layer's own pages so the caller's own text-search
    (``gather_website_substance_facts``) picks up the contract from
    whichever layer actually rendered it. The first layer's content stays
    the basis for the substance score either way -- this never replaces it,
    only extends the search for the one specific security-relevant string.
    Never re-tries a layer already exhausted; ``contract=None`` (the
    default) keeps the exact old one-shot behavior for every other
    caller.

    12/08 -- real incident (see ``_resolves_to_social_platform``'s own
    docstring): checked BEFORE any layer (including the free scraper --
    X/Twitter content is never the right shape for this pipeline regardless
    of cost, ARIA already has a dedicated cheaper path via
    ``services.twitterapi_io``)."""
    from aria_core import website_crawl_failure_log

    if await _resolves_to_social_platform(url):
        return _EmptyCrawlResult(root_url=url, error="X/Twitter domain -- use services.twitterapi_io instead")

    errors: dict[str, str] = {}
    last_result = None
    primary_result = None
    for name, layer_fn in _CRAWL_LAYERS:
        try:
            result = await layer_fn(url)
        except Exception as exc:  # noqa: BLE001 -- a future layer might not guarantee never-raises
            errors[name] = str(exc)
            continue
        last_result = result
        if not result.available:
            errors[name] = result.error or "unavailable"
            continue

        if primary_result is None:
            primary_result = result
            if not contract or _pages_confirm_contract(result.pages, contract):
                return primary_result
            continue  # contract given but not confirmed here -- keep looking

        # A later layer, tried only because `contract` wasn't confirmed yet.
        if _pages_confirm_contract(result.pages, contract):
            from dataclasses import replace

            return replace(primary_result, pages=[*primary_result.pages, *result.pages])

    if primary_result is not None:
        return primary_result  # contract genuinely not found in any layer tried

    await website_crawl_failure_log.record_all_layers_failed(url, errors)
    if last_result is None:
        return _EmptyCrawlResult(root_url=url, error="; ".join(f"{k}: {v}" for k, v in errors.items()))
    return last_result


async def gather_website_substance_facts(
    website_url: str | None, *, crawl_fn=None, contract: str | None = None,
) -> WebsiteSubstanceFacts:
    """Best-effort collection, never blocking. ``crawl_fn`` injectable for
    tests (same pattern as ``fetch=`` in ``github_substance.py``)."""
    url = (website_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return WebsiteSubstanceFacts(available=False, error="invalid URL")

    try:
        if crawl_fn is None:
            # Only the real default crawler knows how to try extra layers
            # when a contract isn't confirmed yet (see _default_crawl's own
            # 12/08 docstring entry) -- injected test doubles keep their
            # existing 1-argument `(url)` signature unchanged.
            result = await _default_crawl(url, contract=contract)
        else:
            result = await crawl_fn(url)
    except Exception as exc:  # noqa: BLE001 -- never blocking
        return WebsiteSubstanceFacts(available=False, error=str(exc))

    if not result.available or not result.pages:
        return WebsiteSubstanceFacts(available=False, error=result.error or "no usable page")

    combined = " ".join(p.raw_content for p in result.pages)
    lower = combined.lower()
    # Computed BEFORE the thin-content early return below -- orthogonal to
    # the substance score (a minimal page can legitimately just contain a
    # contract/"buy" link, real case observed live: messyvirgo.com).
    contract_confirmed = contract.strip().lower() in lower if contract else None

    total_words = len(combined.split())
    if total_words < _MIN_WORDS_FOR_SIGNAL:
        return WebsiteSubstanceFacts(
            available=False, error="real content too thin to judge", contract_confirmed=contract_confirmed,
        )

    key_sections = sum(1 for kw in _KEY_SECTION_KEYWORDS if kw in lower)
    generic = any(marker in lower for marker in _GENERIC_PLACEHOLDER_MARKERS)

    return WebsiteSubstanceFacts(
        available=True,
        pages_found=len(result.pages),
        total_words=total_words,
        https=url.lower().startswith("https://"),
        key_sections_found=key_sections,
        has_generic_placeholder=generic,
        contract_confirmed=contract_confirmed,
    )


def judge_website_substance(facts: WebsiteSubstanceFacts) -> WebsiteSubstanceVerdict:
    """Pure judgment, no network call. 4 weighted criteria, DELIBERATELY
    reduced compared to a broader external proposal (axes "visual
    coherence"/"rigorous mobile-friendliness"/"exhaustive broken links" removed --
    not honestly measurable with the current text infrastructure, never
    approximated silently)."""
    if not facts.available:
        return WebsiteSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "unavailable"])

    if facts.has_generic_placeholder:
        return WebsiteSubstanceVerdict(
            signal="weak", score=10.0,
            points=["generic placeholder text detected (lorem ipsum) -- site likely unfinished"],
        )

    depth_score = min(1.0, facts.total_words / _WORD_COUNT_FULL_SCORE) * 100
    structure_score = min(1.0, facts.key_sections_found / _KEY_SECTIONS_FULL_SCORE) * 100
    reach_score = min(1.0, facts.pages_found / _PAGES_FOUND_FULL_SCORE) * 100
    https_score = 100.0 if facts.https else 0.0

    score = 0.40 * depth_score + 0.25 * structure_score + 0.20 * reach_score + 0.15 * https_score

    points = [
        f"substance {score:.1f}/100 -- {facts.total_words} real words across {facts.pages_found} page(s), "
        f"{facts.key_sections_found}/{len(_KEY_SECTION_KEYWORDS)} key section(s) detected"
        f"{', HTTPS' if facts.https else ', NO HTTPS'}",
    ]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return WebsiteSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
