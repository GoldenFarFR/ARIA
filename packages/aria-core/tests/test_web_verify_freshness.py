"""#126 — tri/filtre par fraîcheur de publication pour les questions d'actu live.

Confirmé en test réel le 11/07 : une question "dernières heures" citait des sources
périmées/hors-sujet temporellement (sans fabrication grâce au filet #113, mais pas
pertinent). Ce fichier couvre :
- l'extraction best-effort de date depuis un extrait DDG (âge relatif, ISO, "Jul 10, 2026").
- le parsing de `published_date` Tavily (ISO 8601).
- le tri _rank_by_freshness : sources fraîches d'abord, non datées ensuite, périmées en
  dernier (jamais supprimées).
- l'intégration dans fetch_web_snippets : pour une question is_live_info_question, le
  provider (ddg ou tavily) est interrogé pour plus de candidats que max_snippets, puis
  trié par fraîcheur avant troncature.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.knowledge import web_verify
from aria_core.knowledge.web_verify import (
    WebSource,
    _parse_iso_datetime,
    _parse_leading_date,
    _rank_by_freshness,
)

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


# ── extraction de date ──────────────────────────────────────────────────────────────────


def test_parse_leading_date_relative_hours_ago():
    dt = _parse_leading_date("3 hours ago - Bitcoin price update.", now=NOW)
    assert dt == NOW - timedelta(hours=3)


def test_parse_leading_date_relative_french():
    dt = _parse_leading_date("il y a 2 jours - match rejoué.", now=NOW)
    assert dt == NOW - timedelta(days=2)


def test_parse_leading_date_iso():
    dt = _parse_leading_date("2026-07-10 - some snippet text here.", now=NOW)
    assert dt == datetime(2026, 7, 10, tzinfo=timezone.utc)


def test_parse_leading_date_month_name():
    dt = _parse_leading_date("Jul 10, 2026 - some snippet text here.", now=NOW)
    assert dt == datetime(2026, 7, 10, tzinfo=timezone.utc)


def test_parse_leading_date_none_when_absent():
    assert _parse_leading_date("No date prefix on this snippet at all.", now=NOW) is None


def test_parse_iso_datetime_tavily_format():
    assert _parse_iso_datetime("2026-07-11T09:00:00Z") == datetime(
        2026, 7, 11, 9, 0, 0, tzinfo=timezone.utc
    )


def test_parse_iso_datetime_none_or_empty():
    assert _parse_iso_datetime(None) is None
    assert _parse_iso_datetime("") is None


# ── tri par fraîcheur ────────────────────────────────────────────────────────────────────


def test_rank_by_freshness_orders_dated_desc():
    old = WebSource(text="old", published=NOW - timedelta(hours=20))
    older = WebSource(text="older", published=NOW - timedelta(hours=40))
    fresh = WebSource(text="fresh", published=NOW - timedelta(minutes=30))
    ranked = _rank_by_freshness([old, older, fresh], now=NOW)
    assert [s.text for s in ranked] == ["fresh", "old", "older"]


def test_rank_by_freshness_keeps_undated_after_fresh_before_stale():
    fresh = WebSource(text="fresh", published=NOW - timedelta(hours=1))
    undated = WebSource(text="undated")
    stale = WebSource(text="stale", published=NOW - timedelta(days=10))
    ranked = _rank_by_freshness([stale, undated, fresh], now=NOW)
    assert [s.text for s in ranked] == ["fresh", "undated", "stale"]


def test_rank_by_freshness_never_drops_sources():
    only_stale = [WebSource(text="stale", published=NOW - timedelta(days=30))]
    ranked = _rank_by_freshness(only_stale, now=NOW)
    assert len(ranked) == 1
    assert ranked[0].text == "stale"


def test_rank_by_freshness_no_dates_preserves_order():
    a = WebSource(text="a")
    b = WebSource(text="b")
    ranked = _rank_by_freshness([a, b], now=NOW)
    assert [s.text for s in ranked] == ["a", "b"]


# ── intégration fetch_web_snippets ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_web_snippets_reorders_ddg_results_by_freshness(monkeypatch):
    monkeypatch.setattr(web_verify, "_web_search_provider", lambda: "ddg")
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.get_cached", lambda q: None)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.set_cached", lambda q, s: None)

    stale = WebSource(text="Stale bitcoin snippet from days ago.", published=NOW - timedelta(days=10))
    fresh = WebSource(text="Fresh bitcoin snippet from minutes ago.", published=NOW - timedelta(minutes=5))

    async def _fake_ddg_once(client, q):
        # Le fournisseur réseau renvoie le résultat périmé en premier -- sans tri, il
        # gagnerait juste par ordre d'arrivée.
        return [stale, fresh]

    monkeypatch.setattr(web_verify, "_fetch_ddg_once", _fake_ddg_once)

    sources = await web_verify.fetch_web_snippets("le prix du bitcoin monte ou descend ?", max_snippets=1)
    assert len(sources) == 1
    assert "Fresh" in sources[0].text


@pytest.mark.asyncio
async def test_fetch_web_snippets_non_live_query_skips_freshness_ranking(monkeypatch):
    """Une question sans signal d'actu live garde l'ordre d'arrivée réseau (comportement
    inchangé) -- le tri par fraîcheur ne s'applique qu'aux is_live_info_question."""
    monkeypatch.setattr(web_verify, "_web_search_provider", lambda: "ddg")
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.get_cached", lambda q: None)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.set_cached", lambda q, s: None)

    older_but_first = WebSource(text="First arrival, older date.", published=NOW - timedelta(days=5))
    newer_but_second = WebSource(text="Second arrival, newer date.", published=NOW - timedelta(days=1))

    async def _fake_ddg_once(client, q):
        return [older_but_first, newer_but_second]

    monkeypatch.setattr(web_verify, "_fetch_ddg_once", _fake_ddg_once)

    assert not web_verify.is_live_info_question("explique-moi le fonctionnement de la preuve d'enjeu")
    sources = await web_verify.fetch_web_snippets(
        "explique-moi le fonctionnement de la preuve d'enjeu", max_snippets=1
    )
    assert len(sources) == 1
    assert "First arrival" in sources[0].text


@pytest.mark.asyncio
async def test_fetch_web_snippets_tavily_reorders_by_published_date(monkeypatch):
    from aria_core.services.tavily import TavilyResult

    monkeypatch.setattr(web_verify, "_web_search_provider", lambda: "tavily")
    monkeypatch.setattr("aria_core.services.tavily.is_tavily_configured", lambda: True)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.get_cached", lambda q: None)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.set_cached", lambda q, s: None)

    fresh_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _fake_search(query, *, max_results=4, **kw):
        return TavilyResult(
            query=query,
            snippets=[
                ("Stale Tavily result.", "https://old.example", "2020-01-01T00:00:00Z"),
                ("Fresh Tavily result.", "https://new.example", fresh_iso),
            ],
            available=True,
        )

    monkeypatch.setattr("aria_core.services.tavily.tavily_client.search", _fake_search)

    sources = await web_verify.fetch_web_snippets("le prix du bitcoin monte ou descend ?", max_snippets=1)
    assert len(sources) == 1
    assert "Fresh Tavily" in sources[0].text
