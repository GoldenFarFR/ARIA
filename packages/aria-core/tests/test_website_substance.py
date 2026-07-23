"""Signal 'substance Website' -- crawl Tavily multi-page (23/07, demande
opérateur : "elle doit pouvoir extraire tout pour noter")."""
from __future__ import annotations

import pytest

from aria_core.services.tavily import TavilyCrawlResult, TavilyPage
from aria_core.skills.website_substance import (
    WebsiteSubstanceFacts,
    gather_website_substance_facts,
    judge_website_substance,
)


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_website_substance(WebsiteSubstanceFacts(available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_generic_placeholder_is_weak():
    v = judge_website_substance(
        WebsiteSubstanceFacts(
            available=True, pages_found=1, total_words=500, https=True,
            key_sections_found=0, has_generic_placeholder=True,
        )
    )
    assert v.signal == "weak"
    assert any("generic" in p for p in v.points)


def test_rich_site_is_positive():
    v = judge_website_substance(
        WebsiteSubstanceFacts(
            available=True, pages_found=12, total_words=9000, https=True,
            key_sections_found=4, has_generic_placeholder=False,
        )
    )
    assert v.signal == "positive"
    assert v.score is not None and v.score >= 70


def test_thin_site_is_weak():
    v = judge_website_substance(
        WebsiteSubstanceFacts(
            available=True, pages_found=1, total_words=200, https=False,
            key_sections_found=0, has_generic_placeholder=False,
        )
    )
    assert v.signal == "weak"


# ── Récolte (crawl factice) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gather_invalid_url_unavailable():
    facts = await gather_website_substance_facts("not-a-url")
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_no_url_unavailable():
    facts = await gather_website_substance_facts(None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_crawl_unavailable_degrades():
    async def crawl_fn(url):
        return TavilyCrawlResult(root_url=url, available=False, error="budget mensuel épuisé")

    facts = await gather_website_substance_facts("https://example.com", crawl_fn=crawl_fn)
    assert facts.available is False
    assert "budget" in (facts.error or "")


@pytest.mark.asyncio
async def test_gather_below_min_words_unavailable():
    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True,
            pages=[TavilyPage(url=url, raw_content="quelques mots seulement pas assez pour juger")],
        )

    facts = await gather_website_substance_facts("https://example.com", crawl_fn=crawl_fn)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_detects_key_sections_and_https():
    long_text = "roadmap tokenomics team docs " + ("mot réel " * 200)

    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True,
            pages=[
                TavilyPage(url="https://example.com/", raw_content=long_text),
                TavilyPage(url="https://example.com/about", raw_content=long_text),
            ],
        )

    facts = await gather_website_substance_facts("https://example.com", crawl_fn=crawl_fn)
    assert facts.available is True
    assert facts.pages_found == 2
    assert facts.https is True
    assert facts.key_sections_found >= 3


@pytest.mark.asyncio
async def test_gather_fetch_exception_degrades():
    async def crawl_fn(url):
        raise RuntimeError("panne réseau")

    facts = await gather_website_substance_facts("https://example.com", crawl_fn=crawl_fn)
    assert facts.available is False
