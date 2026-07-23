"""Docs substance signal -- REAL depth of a project's documentation/whitepaper,
via ``services.tavily.TavilyClient.crawl`` on the "Docs" URL DECLARED by the
project (23/07, direct operator request: "the doc must also be read in full...
https://docs.crynux.io/" -- a crawl rooted on the Website link does NOT
guarantee coverage of the docs if they live on an unrelated domain, unlike the
CNX case where docs.crynux.io is a subdomain discovered incidentally).

No documentation extraction existed before this signal: the only site text
already fetched elsewhere (``site_snapshot.py``) is capped at 600 characters --
verified under real conditions that docs.crynux.io alone runs to 535,526
characters of raw HTML, across several pages (architecture, tokenomics,
guides). Same doctrine as ``website_substance.py``: MEASURABLE FACTS on the
actually fetched text, never a judgment fabricated on absent data."""
from __future__ import annotations

from dataclasses import dataclass, field

# TECHNICAL depth keywords (textual proxy -- actual presence of content on
# these topics, never a truthfulness check of the content itself, which
# remains declarative like any project_links). English-only (the vast
# majority of project docs are published in English regardless of the
# team's own language).
_TECHNICAL_KEYWORDS = (
    "architecture", "protocol", "algorithm", "consensus", "smart contract",
    "api", "sdk", "node",
)
_ROADMAP_KEYWORDS = ("roadmap", "milestone")
_TOKENOMICS_KEYWORDS = ("tokenomics", "allocation", "vesting", "supply", "emission")
_RISK_KEYWORDS = ("risk", "disclaimer", "limitation")

# Below this number of cumulated REAL words, the sample is too weak to judge
# honestly (spirit of the "< 800 words -> capped" from the external proposal,
# but ``unknown`` rather than a forced score -- same doctrine as the rest of
# the project: never a fabricated figure on a signal that's too weak).
_MIN_WORDS_FOR_SIGNAL = 300
_WORD_COUNT_FULL_SCORE = 5000
_TECHNICAL_KEYWORDS_FULL_SCORE = 4


@dataclass
class DocsSubstanceFacts:
    available: bool = False
    error: str | None = None
    pages_found: int = 0
    total_words: int = 0
    technical_keywords_found: int = 0
    has_roadmap: bool = False
    has_tokenomics: bool = False
    has_risk_disclosure: bool = False


@dataclass
class DocsSubstanceVerdict:
    signal: str  # "positive" | "neutral" | "weak" | "unknown"
    score: float | None
    points: list[str] = field(default_factory=list)


async def _default_crawl(url: str):
    from aria_core.services.tavily import tavily_client

    return await tavily_client.crawl(url, caller="docs_substance")


async def gather_docs_substance_facts(docs_url: str | None, *, crawl_fn=None) -> DocsSubstanceFacts:
    """Best-effort collection, never blocking. ``crawl_fn`` injectable for tests."""
    url = (docs_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return DocsSubstanceFacts(available=False, error="invalid URL")

    crawl_fn = crawl_fn or _default_crawl
    try:
        result = await crawl_fn(url)
    except Exception as exc:  # noqa: BLE001 -- never blocking
        return DocsSubstanceFacts(available=False, error=str(exc))

    if not result.available or not result.pages:
        return DocsSubstanceFacts(available=False, error=result.error or "no usable page")

    combined = " ".join(p.raw_content for p in result.pages)
    total_words = len(combined.split())
    if total_words < _MIN_WORDS_FOR_SIGNAL:
        return DocsSubstanceFacts(available=False, error="real content too thin to judge")

    lower = combined.lower()
    technical = sum(1 for kw in _TECHNICAL_KEYWORDS if kw in lower)

    return DocsSubstanceFacts(
        available=True,
        pages_found=len(result.pages),
        total_words=total_words,
        technical_keywords_found=technical,
        has_roadmap=any(kw in lower for kw in _ROADMAP_KEYWORDS),
        has_tokenomics=any(kw in lower for kw in _TOKENOMICS_KEYWORDS),
        has_risk_disclosure=any(kw in lower for kw in _RISK_KEYWORDS),
    )


def judge_docs_substance(facts: DocsSubstanceFacts) -> DocsSubstanceVerdict:
    """Pure judgment, no network call. 4 weighted criteria."""
    if not facts.available:
        return DocsSubstanceVerdict(signal="unknown", score=None, points=[facts.error or "unavailable"])

    depth_score = min(1.0, facts.total_words / _WORD_COUNT_FULL_SCORE) * 100
    technical_score = min(1.0, facts.technical_keywords_found / _TECHNICAL_KEYWORDS_FULL_SCORE) * 100
    roadmap_score = 100.0 if facts.has_roadmap else 0.0
    transparency_score = 100.0 if (facts.has_tokenomics and facts.has_risk_disclosure) else (
        50.0 if (facts.has_tokenomics or facts.has_risk_disclosure) else 0.0
    )

    score = 0.35 * depth_score + 0.25 * technical_score + 0.20 * roadmap_score + 0.20 * transparency_score

    points = [
        f"substance {score:.1f}/100 -- {facts.total_words} real words across {facts.pages_found} page(s), "
        f"{facts.technical_keywords_found}/{len(_TECHNICAL_KEYWORDS)} technical term(s) found, "
        f"roadmap {'present' if facts.has_roadmap else 'absent'}, "
        f"tokenomics {'present' if facts.has_tokenomics else 'absent'}, "
        f"risks {'disclosed' if facts.has_risk_disclosure else 'not disclosed'}",
    ]

    if score >= 70:
        signal = "positive"
    elif score >= 40:
        signal = "neutral"
    else:
        signal = "weak"

    return DocsSubstanceVerdict(signal=signal, score=round(score, 1), points=points)
