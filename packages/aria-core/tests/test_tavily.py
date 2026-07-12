"""Client Tavily (recherche web, patron dôme) + provider-switch dans fetch_web_snippets.

Aucun réseau réel : httpx.AsyncClient est monkeypatché. La clé API n'est jamais écrite en
dur — les tests posent TAVILY_API_KEY via monkeypatch.setenv.
"""
from __future__ import annotations

import pytest

from aria_core.services import tavily


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "err", request=None, response=_FakeHttpResp(self.status_code)
            )


class _FakeHttpResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    """Remplace httpx.AsyncClient : renvoie une réponse programmée, capture le payload."""

    _response = None
    _captured_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        type(self)._captured_payload = json
        return type(self)._response


@pytest.fixture
def _fresh_client(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    client = tavily.TavilyClient(min_interval=0.0)
    _FakeAsyncClient._response = None
    _FakeAsyncClient._captured_payload = None
    monkeypatch.setattr(tavily.httpx, "AsyncClient", _FakeAsyncClient)
    return client


def test_is_tavily_configured(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert tavily.is_tavily_configured() is False
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    assert tavily.is_tavily_configured() is True


@pytest.mark.asyncio
async def test_search_without_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    client = tavily.TavilyClient(min_interval=0.0)
    result = await client.search("bitcoin price today")
    assert result.available is False
    assert "TAVILY_API_KEY" in (result.error or "")


@pytest.mark.asyncio
async def test_search_empty_query(_fresh_client):
    result = await _fresh_client.search("   ")
    assert result.available is False
    assert "vide" in (result.error or "")


@pytest.mark.asyncio
async def test_search_success_parses_results_and_answer(_fresh_client):
    _FakeAsyncClient._response = _FakeResponse(
        200,
        {
            "answer": "Bitcoin is around $X today.",
            "results": [
                {"content": "BTC trades at $X on major exchanges.", "url": "https://ex.com/btc"},
                {"title": "Fallback title only", "url": "https://ex.com/2"},
            ],
        },
    )
    result = await _fresh_client.search("bitcoin price", max_results=4)
    assert result.available is True
    assert result.answer == "Bitcoin is around $X today."
    assert any("BTC trades" in t for t, _, _ in result.snippets)
    # La clé est bien envoyée dans le payload (mais jamais loguée par le code).
    assert _FakeAsyncClient._captured_payload["api_key"] == "tvly-test-key"


@pytest.mark.asyncio
async def test_search_401_degrades_softly(_fresh_client):
    _FakeAsyncClient._response = _FakeResponse(401, {})
    result = await _fresh_client.search("anything")
    assert result.available is False
    assert "refusée ou absente" in (result.error or "")


@pytest.mark.asyncio
async def test_search_empty_results_is_unavailable(_fresh_client):
    _FakeAsyncClient._response = _FakeResponse(200, {"results": [], "answer": None})
    result = await _fresh_client.search("obscure query")
    assert result.available is False
    assert "aucun résultat" in (result.error or "")


# ── provider-switch dans web_verify.fetch_web_snippets ────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_web_snippets_uses_tavily_when_provider_set(monkeypatch):
    from aria_core.knowledge import web_verify
    from aria_core.services.tavily import TavilyResult

    monkeypatch.setattr(
        "aria_core.knowledge.web_verify._web_search_provider", lambda: "tavily"
    )
    monkeypatch.setattr("aria_core.services.tavily.is_tavily_configured", lambda: True)

    async def _fake_search(query, *, max_results=4, **kw):
        return TavilyResult(
            query=query,
            snippets=[("Real fact about the query from Tavily.", "https://src.com", None)],
            answer="Direct Tavily answer.",
            available=True,
        )

    monkeypatch.setattr("aria_core.services.tavily.tavily_client.search", _fake_search)
    # Neutralise le cache pour un test déterministe.
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.get_cached", lambda q: None)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.set_cached", lambda q, s: None)

    sources = await web_verify.fetch_web_snippets("bitcoin price today")
    assert sources
    assert any("Direct Tavily answer" in s.text or "Real fact" in s.text for s in sources)


@pytest.mark.asyncio
async def test_fetch_web_snippets_falls_back_to_ddg_when_tavily_empty(monkeypatch):
    from aria_core.knowledge import web_verify

    monkeypatch.setattr(
        "aria_core.knowledge.web_verify._web_search_provider", lambda: "tavily"
    )

    async def _empty_tavily(query, max_snippets):
        return []

    monkeypatch.setattr(web_verify, "_fetch_tavily_snippets", _empty_tavily)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.get_cached", lambda q: None)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.set_cached", lambda q, s: None)

    ddg_called = {"n": 0}

    async def _fake_ddg_once(client, q):
        ddg_called["n"] += 1
        return [web_verify.WebSource(text="DDG fallback snippet here.", url="https://d.com")]

    monkeypatch.setattr(web_verify, "_fetch_ddg_once", _fake_ddg_once)

    sources = await web_verify.fetch_web_snippets("bitcoin price today")
    assert ddg_called["n"] >= 1  # DDG a bien pris le relais
    assert any("DDG fallback" in s.text for s in sources)


@pytest.mark.asyncio
async def test_fetch_web_snippets_default_provider_is_ddg(monkeypatch):
    from aria_core.knowledge import web_verify

    # Provider par défaut = ddg : le chemin Tavily ne doit jamais être appelé.
    monkeypatch.setattr(
        "aria_core.knowledge.web_verify._web_search_provider", lambda: "ddg"
    )
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.get_cached", lambda q: None)
    monkeypatch.setattr("aria_core.knowledge.ddg_cache.set_cached", lambda q, s: None)

    async def _boom_tavily(query, max_snippets):
        raise AssertionError("Tavily ne doit pas être appelé quand provider=ddg")

    monkeypatch.setattr(web_verify, "_fetch_tavily_snippets", _boom_tavily)

    async def _fake_ddg_once(client, q):
        return [web_verify.WebSource(text="DDG default snippet.", url="")]

    monkeypatch.setattr(web_verify, "_fetch_ddg_once", _fake_ddg_once)

    sources = await web_verify.fetch_web_snippets("bitcoin price today")
    assert any("DDG default" in s.text for s in sources)
