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

from dataclasses import dataclass, field

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


async def _default_crawl(url: str):
    """Homemade scraper (services/website_scraper.py, 10/08, backlog #43)
    FIRST -- no third-party quota at all, real evidence it covers most
    real project sites (11/12 on a live sample, see that module's
    docstring). Firecrawl (free plan) second, Tavily (shared monthly
    budget) last -- never a hard dependency on any of the three: each
    returning unavailable (homepage unreachable/WAF-blocked/JS-only-SPA
    for the scraper; no key/budget exhausted/failure for Firecrawl) falls
    straight through to the next, exactly as if the failed one didn't
    exist."""
    from aria_core.services.website_scraper import crawl as scraper_crawl

    result = await scraper_crawl(url, caller="website_substance")
    if result.available:
        return result

    from aria_core.services.firecrawl import firecrawl_client, is_firecrawl_configured

    if is_firecrawl_configured():
        result = await firecrawl_client.crawl(url, caller="website_substance")
        if result.available:
            return result

    from aria_core.services.tavily import tavily_client

    return await tavily_client.crawl(url, caller="website_substance")


async def gather_website_substance_facts(
    website_url: str | None, *, crawl_fn=None, contract: str | None = None,
) -> WebsiteSubstanceFacts:
    """Best-effort collection, never blocking. ``crawl_fn`` injectable for
    tests (same pattern as ``fetch=`` in ``github_substance.py``)."""
    url = (website_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return WebsiteSubstanceFacts(available=False, error="invalid URL")

    crawl_fn = crawl_fn or _default_crawl
    try:
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
