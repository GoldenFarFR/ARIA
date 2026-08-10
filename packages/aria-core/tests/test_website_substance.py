"""Signal 'substance Website' -- crawl Tavily multi-page (23/07, demande
opérateur : "elle doit pouvoir extraire tout pour noter")."""
from __future__ import annotations

import pytest

from aria_core.services.firecrawl import FirecrawlCrawlResult, FirecrawlPage
from aria_core.services.tavily import TavilyCrawlResult, TavilyPage
from aria_core.services.website_scraper import WebsiteScraperResult
from aria_core.skills.website_substance import (
    WebsiteSubstanceFacts,
    _default_crawl,
    gather_website_substance_facts,
    judge_website_substance,
)


@pytest.fixture(autouse=True)
def _isolated_crawl_failure_log_db(tmp_path, monkeypatch):
    """10/08 -- _default_crawl now logs to website_crawl_failure_log when
    every layer fails, same isolation need as the other DB-backed
    mechanisms this session."""
    from aria_core import website_crawl_failure_log

    monkeypatch.setattr(website_crawl_failure_log, "DB_PATH", str(tmp_path / "crawl_failure_log_test.db"))


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


# ── contract_confirmed -- gate anti-usurpation (09/08) ──────────────────────


@pytest.mark.asyncio
async def test_gather_no_contract_leaves_confirmed_none():
    long_text = "roadmap tokenomics team docs " + ("mot réel " * 200)

    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True, pages=[TavilyPage(url=url, raw_content=long_text)],
        )

    facts = await gather_website_substance_facts("https://example.com", crawl_fn=crawl_fn)
    assert facts.contract_confirmed is None


@pytest.mark.asyncio
async def test_gather_detects_contract_present_case_insensitive():
    contract = "0xAbCdEf0000000000000000000000000000dEaD"
    long_text = f"Buy $TOK now, contract: {contract.lower()} " + ("mot réel " * 200)

    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True, pages=[TavilyPage(url=url, raw_content=long_text)],
        )

    facts = await gather_website_substance_facts(
        "https://example.com", crawl_fn=crawl_fn, contract=contract,
    )
    assert facts.contract_confirmed is True


@pytest.mark.asyncio
async def test_gather_detects_contract_absent():
    long_text = "roadmap tokenomics team docs " + ("mot réel " * 200)

    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True, pages=[TavilyPage(url=url, raw_content=long_text)],
        )

    facts = await gather_website_substance_facts(
        "https://example.com", crawl_fn=crawl_fn, contract="0x" + "a" * 40,
    )
    assert facts.contract_confirmed is False


@pytest.mark.asyncio
async def test_gather_checks_contract_even_on_thin_content():
    """Un contrat peut légitimement apparaître sur une page minimale (cas
    réel : messyvirgo.com, lien de swap direct) -- ne doit jamais dépendre
    du seuil de mots minimum utilisé pour le score de substance."""
    contract = "0x" + "a" * 40
    thin_text = f"buy now {contract}"

    async def crawl_fn(url):
        return TavilyCrawlResult(
            root_url=url, available=True, pages=[TavilyPage(url=url, raw_content=thin_text)],
        )

    facts = await gather_website_substance_facts(
        "https://example.com", crawl_fn=crawl_fn, contract=contract,
    )
    assert facts.available is False  # toujours "trop mince" pour le score de substance
    assert facts.contract_confirmed is True  # mais la vérification du contrat, elle, a tourné


# ── _default_crawl : scraper maison en premier, Firecrawl puis Tavily en
# repli ─────────────────────────────────────────────────────────────────
# 10/08, backlog #43 -- le scraper maison (aucun quota tiers) est tenté
# EN PREMIER ; Firecrawl (gratuit, 09/08) puis Tavily (budget partagé)
# restent des replis réels si le scraper échoue (WAF, site JS-only sans
# SSR, etc.) -- jamais une dépendance dure sur aucun des trois.


def _unavailable_scraper(monkeypatch, *, error: str = "homepage inaccessible"):
    import aria_core.services.website_scraper as wsc

    async def fake_scraper_crawl(url, *, caller="unknown", **kwargs):
        return WebsiteScraperResult(root_url=url, available=False, error=error)

    monkeypatch.setattr(wsc, "crawl", fake_scraper_crawl)


@pytest.mark.asyncio
async def test_default_crawl_uses_homemade_scraper_when_available(monkeypatch):
    import aria_core.services.firecrawl as fc
    import aria_core.services.tavily as tv
    import aria_core.services.website_scraper as wsc
    from aria_core.services.website_scraper import WebsiteScraperPage

    async def fake_scraper_crawl(url, *, caller="unknown", **kwargs):
        return WebsiteScraperResult(
            root_url=url, available=True,
            pages=[WebsiteScraperPage(url=url, raw_content="contenu scraper maison")],
        )

    async def fail_if_called(url, *, caller="unknown", **kwargs):
        raise AssertionError("Firecrawl/Tavily ne doivent jamais être appelés si le scraper maison a répondu")

    monkeypatch.setattr(wsc, "crawl", fake_scraper_crawl)
    monkeypatch.setattr(fc.firecrawl_client, "crawl", fail_if_called)
    monkeypatch.setattr(tv.tavily_client, "crawl", fail_if_called)

    result = await _default_crawl("https://example.com")
    assert result.available is True
    assert result.pages[0].raw_content == "contenu scraper maison"


@pytest.mark.asyncio
async def test_default_crawl_uses_firecrawl_when_scraper_unavailable_and_firecrawl_configured(monkeypatch):
    import aria_core.services.firecrawl as fc
    import aria_core.services.tavily as tv

    _unavailable_scraper(monkeypatch)
    monkeypatch.setattr(fc, "firecrawl_api_key", lambda: "fc-test-key")

    async def fake_firecrawl_crawl(url, *, caller="unknown", **kwargs):
        return FirecrawlCrawlResult(
            root_url=url, available=True, pages=[FirecrawlPage(url=url, raw_content="contenu firecrawl")],
        )

    async def fail_if_called_tavily(url, *, caller="unknown", **kwargs):
        raise AssertionError("Tavily ne doit jamais être appelé si Firecrawl a répondu")

    monkeypatch.setattr(fc.firecrawl_client, "crawl", fake_firecrawl_crawl)
    monkeypatch.setattr(tv.tavily_client, "crawl", fail_if_called_tavily)

    result = await _default_crawl("https://example.com")
    assert result.available is True
    assert result.pages[0].raw_content == "contenu firecrawl"


@pytest.mark.asyncio
async def test_default_crawl_falls_back_to_tavily_when_scraper_and_firecrawl_unconfigured(monkeypatch):
    import aria_core.services.firecrawl as fc
    import aria_core.services.tavily as tv

    _unavailable_scraper(monkeypatch)
    monkeypatch.setattr(fc, "firecrawl_api_key", lambda: "")  # pas de clé -- jamais tenté

    async def fail_if_called_firecrawl(url, *, caller="unknown", **kwargs):
        raise AssertionError("Firecrawl ne doit jamais être appelé sans clé configurée")

    async def fake_tavily_crawl(url, *, caller="unknown", **kwargs):
        return TavilyCrawlResult(
            root_url=url, available=True, pages=[TavilyPage(url=url, raw_content="contenu tavily")],
        )

    monkeypatch.setattr(fc.firecrawl_client, "crawl", fail_if_called_firecrawl)
    monkeypatch.setattr(tv.tavily_client, "crawl", fake_tavily_crawl)

    result = await _default_crawl("https://example.com")
    assert result.available is True
    assert result.pages[0].raw_content == "contenu tavily"


@pytest.mark.asyncio
async def test_default_crawl_logs_when_every_layer_fails(monkeypatch):
    """10/08 -- le compteur de résilience (website_crawl_failure_log)
    doit s'armer exactement quand les 3 étages échouent tous, jamais avant
    (les tests de repli ci-dessus n'y touchent jamais, un des 3 répond)."""
    import aria_core.services.firecrawl as fc
    import aria_core.services.tavily as tv
    from aria_core import website_crawl_failure_log as wcfl

    _unavailable_scraper(monkeypatch, error="homepage WAF-bloquée")
    monkeypatch.setattr(fc, "firecrawl_api_key", lambda: "")  # non configuré

    async def fake_tavily_crawl(url, *, caller="unknown", **kwargs):
        return TavilyCrawlResult(root_url=url, available=False, error="budget mensuel épuisé")

    monkeypatch.setattr(tv.tavily_client, "crawl", fake_tavily_crawl)

    result = await _default_crawl("https://example.com")
    assert result.available is False

    assert await wcfl.failure_count_since(days=1) == 1
    failures = await wcfl.recent_failures()
    assert failures[0]["url"] == "https://example.com"
    assert failures[0]["layer_errors"]["scraper_maison"] == "homepage WAF-bloquée"
    assert failures[0]["layer_errors"]["firecrawl"] == "clé non configurée"
    assert failures[0]["layer_errors"]["tavily"] == "budget mensuel épuisé"


@pytest.mark.asyncio
async def test_default_crawl_falls_back_to_tavily_when_scraper_and_firecrawl_unavailable(monkeypatch):
    """Scraper maison ET Firecrawl échouent tous les deux (WAF, budget
    épuisé, etc.) -- jamais bloquant, retombe sur Tavily comme si aucun des
    deux n'existait."""
    import aria_core.services.firecrawl as fc
    import aria_core.services.tavily as tv

    _unavailable_scraper(monkeypatch)
    monkeypatch.setattr(fc, "firecrawl_api_key", lambda: "fc-test-key")

    async def fake_firecrawl_crawl(url, *, caller="unknown", **kwargs):
        return FirecrawlCrawlResult(root_url=url, available=False, error="budget mensuel épuisé")

    async def fake_tavily_crawl(url, *, caller="unknown", **kwargs):
        return TavilyCrawlResult(
            root_url=url, available=True, pages=[TavilyPage(url=url, raw_content="contenu tavily")],
        )

    monkeypatch.setattr(fc.firecrawl_client, "crawl", fake_firecrawl_crawl)
    monkeypatch.setattr(tv.tavily_client, "crawl", fake_tavily_crawl)

    result = await _default_crawl("https://example.com")
    assert result.available is True
    assert result.pages[0].raw_content == "contenu tavily"
