"""Signal 'substance Docs' -- crawl Tavily sur l'URL Docs DÉCLARÉE (23/07,
demande opérateur : la doc doit être lue en entier depuis son propre lien,
jamais seulement découverte incidemment via le crawl du site)."""
from __future__ import annotations

import pytest

from aria_core.services.tavily import TavilyCrawlResult, TavilyPage
from aria_core.skills.docs_substance import (
    DocsSubstanceFacts,
    gather_docs_substance_facts,
    judge_docs_substance,
)


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_docs_substance(DocsSubstanceFacts(available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_rich_docs_with_roadmap_and_tokenomics_is_positive():
    v = judge_docs_substance(
        DocsSubstanceFacts(
            available=True, pages_found=15, total_words=13000, technical_keywords_found=8,
            has_roadmap=True, has_tokenomics=True, has_risk_disclosure=True,
        )
    )
    assert v.signal == "positive"
    assert v.score is not None and v.score >= 70


def test_thin_docs_no_roadmap_no_tokenomics_is_weak():
    v = judge_docs_substance(
        DocsSubstanceFacts(
            available=True, pages_found=1, total_words=400, technical_keywords_found=0,
            has_roadmap=False, has_tokenomics=False, has_risk_disclosure=False,
        )
    )
    assert v.signal == "weak"


# ── Récolte (crawl factice) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gather_invalid_url_unavailable():
    facts = await gather_docs_substance_facts("not-a-url")
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_below_min_words_unavailable():
    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True,
            pages=[TavilyPage(url=url, raw_content="trop court pour juger honnêtement")],
        )

    facts = await gather_docs_substance_facts("https://docs.example.com", crawl_fn=crawl_fn)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_detects_technical_and_tokenomics_keywords():
    text = "architecture protocol consensus smart contract tokenomics vesting risk " + ("word " * 400)

    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True,
            pages=[TavilyPage(url=url, raw_content=text)],
        )

    facts = await gather_docs_substance_facts("https://docs.example.com", crawl_fn=crawl_fn)
    assert facts.available is True
    assert facts.technical_keywords_found >= 3
    assert facts.has_tokenomics is True
    assert facts.has_risk_disclosure is True
    assert facts.has_roadmap is False


@pytest.mark.asyncio
async def test_gather_crawl_unavailable_degrades():
    async def crawl_fn(url):
        return TavilyCrawlResult(root_url=url, available=False, error="indisponible")

    facts = await gather_docs_substance_facts("https://docs.example.com", crawl_fn=crawl_fn)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_fetch_exception_degrades():
    async def crawl_fn(url):
        raise RuntimeError("panne réseau")

    facts = await gather_docs_substance_facts("https://docs.example.com", crawl_fn=crawl_fn)
    assert facts.available is False
