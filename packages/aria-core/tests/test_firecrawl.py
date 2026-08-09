"""Client Firecrawl (crawl asynchrone start->poll, patron dôme) -- 09/08,
remplacement de Tavily pour website_substance.py.

Aucun réseau réel : httpx.AsyncClient est monkeypatché. La clé API n'est jamais
écrite en dur -- les tests posent FIRECRAWL_API_KEY via monkeypatch.setenv.
"""
from __future__ import annotations

import httpx
import pytest

from aria_core.services import firecrawl, firecrawl_budget


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=_FakeHttpResp(self.status_code))


class _FakeHttpResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    """Remplace httpx.AsyncClient : POST programmable une fois, GET dépile une
    file de réponses successives (simule le cycle start -> poll -> completed)."""

    _post_response = None
    _get_responses: list = []
    _captured_post_payload = None
    _captured_headers = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        type(self)._captured_post_payload = json
        type(self)._captured_headers = headers
        return type(self)._post_response

    async def get(self, url, headers=None):
        type(self)._captured_headers = headers
        if type(self)._get_responses:
            return type(self)._get_responses.pop(0)
        return _FakeResponse(200, {"status": "completed", "data": []})


@pytest.fixture
def _fresh_client(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    client = firecrawl.FirecrawlClient(min_interval=0.0, poll_interval=0.0, max_wait_s=5.0)
    _FakeAsyncClient._post_response = None
    _FakeAsyncClient._get_responses = []
    _FakeAsyncClient._captured_post_payload = None
    _FakeAsyncClient._captured_headers = None
    monkeypatch.setattr(firecrawl.httpx, "AsyncClient", _FakeAsyncClient)
    return client


def test_is_firecrawl_configured(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert firecrawl.is_firecrawl_configured() is False
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-x")
    assert firecrawl.is_firecrawl_configured() is True


@pytest.mark.asyncio
async def test_crawl_without_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    client = firecrawl.FirecrawlClient(min_interval=0.0, poll_interval=0.0)
    result = await client.crawl("https://example.com")
    assert result.available is False
    assert "FIRECRAWL_API_KEY" in (result.error or "")


@pytest.mark.asyncio
async def test_crawl_empty_root_url_never_calls_network(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("ne doit jamais être appelé, URL racine vide")

    monkeypatch.setattr(firecrawl.httpx, "AsyncClient", _fail_if_called)
    client = firecrawl.FirecrawlClient(min_interval=0.0)
    result = await client.crawl("   ")
    assert result.available is False


@pytest.mark.asyncio
async def test_crawl_success_starts_then_polls_until_completed(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(200, {"success": True, "id": "job-123", "url": "https://crynux.io"})
    _FakeAsyncClient._get_responses = [
        _FakeResponse(200, {"status": "scraping", "total": 2, "completed": 0}),
        _FakeResponse(
            200,
            {
                "status": "completed",
                "creditsUsed": 2,
                "data": [
                    {
                        "markdown": "# Crynux\nContenu homepage réel.",
                        "metadata": {"title": "Crynux", "url": "https://crynux.io/", "statusCode": 200},
                    },
                    {
                        "markdown": "Contenu docs réel.",
                        "metadata": {"title": "Docs", "url": "https://docs.crynux.io/", "statusCode": 200},
                    },
                    {"markdown": "", "metadata": {"url": "https://blog.crynux.io/"}},  # page vide -- éliminée
                ],
            },
        ),
    ]
    result = await _fresh_client.crawl("https://crynux.io", limit=15, caller="website_substance")
    assert result.available is True
    assert len(result.pages) == 2
    assert {p.url for p in result.pages} == {"https://crynux.io/", "https://docs.crynux.io/"}
    # Payload de démarrage réel envoyé -- markdown only, pas de format superflu.
    assert _FakeAsyncClient._captured_post_payload["scrapeOptions"]["formats"] == ["markdown"]


@pytest.mark.asyncio
async def test_crawl_job_failed_status_is_unavailable(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(200, {"success": True, "id": "job-123"})
    _FakeAsyncClient._get_responses = [_FakeResponse(200, {"status": "failed"})]
    result = await _fresh_client.crawl("https://example.com")
    assert result.available is False
    assert "job échoué" in (result.error or "")


@pytest.mark.asyncio
async def test_crawl_start_response_without_job_id_is_unavailable(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(200, {"success": True})  # pas d'"id"
    result = await _fresh_client.crawl("https://example.com")
    assert result.available is False


@pytest.mark.asyncio
async def test_crawl_never_exceeding_max_wait_gives_up(_fresh_client, monkeypatch):
    client = firecrawl.FirecrawlClient(min_interval=0.0, poll_interval=0.01, max_wait_s=0.02)
    _FakeAsyncClient._post_response = _FakeResponse(200, {"success": True, "id": "job-123"})

    async def _always_scraping(self, url, headers=None):
        return _FakeResponse(200, {"status": "scraping"})

    # monkeypatch.setattr (jamais une assignation directe sur la classe) --
    # restauré automatiquement en fin de test, sinon la classe reste cassée
    # pour tous les tests suivants du même run (pollution inter-tests).
    monkeypatch.setattr(_FakeAsyncClient, "get", _always_scraping)
    result = await client.crawl("https://example.com")
    assert result.available is False
    assert "délai d'attente dépassé" in (result.error or "")


@pytest.mark.asyncio
async def test_crawl_empty_pages_is_unavailable(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(200, {"success": True, "id": "job-123"})
    _FakeAsyncClient._get_responses = [_FakeResponse(200, {"status": "completed", "data": []})]
    result = await _fresh_client.crawl("https://example.com")
    assert result.available is False


@pytest.mark.asyncio
async def test_crawl_budget_worst_case_checked_before_any_network_call(monkeypatch, _fresh_client):
    checked = {}

    async def _no_budget(credits):
        checked["credits"] = credits
        return False

    monkeypatch.setattr(firecrawl_budget, "can_spend", _no_budget)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("budget refusé -- aucun appel réseau ne doit partir")

    monkeypatch.setattr(firecrawl.httpx, "AsyncClient", _fail_if_called)

    result = await _fresh_client.crawl("https://example.com", limit=15)
    assert result.available is False
    assert "budget mensuel épuisé" in (result.error or "")
    # Vérifié sur le PIRE CAS (15 pages x 1 crédit), jamais un chiffre optimiste.
    assert checked["credits"] == 15


@pytest.mark.asyncio
async def test_crawl_records_real_credits_used_not_worst_case(_fresh_client, monkeypatch):
    _FakeAsyncClient._post_response = _FakeResponse(200, {"success": True, "id": "job-123"})
    _FakeAsyncClient._get_responses = [
        _FakeResponse(
            200,
            {
                "status": "completed",
                "creditsUsed": 3,  # réellement retourné par Firecrawl, distinct du pire cas (15)
                "data": [{"markdown": "contenu réel", "metadata": {"url": "https://example.com/"}}],
            },
        ),
    ]

    recorded = {}

    async def _record(*, caller, query, credits):
        recorded["credits"] = credits

    monkeypatch.setattr(firecrawl_budget, "record_spend", _record)
    await _fresh_client.crawl("https://example.com", limit=15, caller="website_substance")
    assert recorded["credits"] == 3


@pytest.mark.asyncio
async def test_crawl_429_on_start_retries_then_gives_up(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(429, {}, headers={"Retry-After": "0"})
    result = await _fresh_client.crawl("https://example.com")
    assert result.available is False
    assert "rate limit" in (result.error or "")


@pytest.mark.asyncio
async def test_crawl_refused_key_is_unavailable(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(401, {})
    result = await _fresh_client.crawl("https://example.com")
    assert result.available is False
    assert "clé refusée" in (result.error or "")


@pytest.mark.asyncio
async def test_crawl_invalid_url_400_is_unavailable(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(400, {})
    result = await _fresh_client.crawl("not-a-real-url")
    assert result.available is False
    assert "URL invalide" in (result.error or "")


@pytest.mark.asyncio
async def test_crawl_sends_bearer_auth_header(_fresh_client):
    _FakeAsyncClient._post_response = _FakeResponse(200, {"success": True, "id": "job-123"})
    _FakeAsyncClient._get_responses = [_FakeResponse(200, {"status": "completed", "data": []})]
    await _fresh_client.crawl("https://example.com")
    assert _FakeAsyncClient._captured_headers == {"Authorization": "Bearer fc-test-key"}
