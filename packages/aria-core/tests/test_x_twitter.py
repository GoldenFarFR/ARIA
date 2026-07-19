import pytest

from aria_core.gateway.x_twitter import (
    fetch_user_recent_tweets,
    is_x_configured,
    is_x_post_configured,
    is_x_read_configured,
    is_x_reading_active,
    search_recent_tweets,
    x_status,
)


def test_x_status_defaults():
    st = x_status()
    assert st["handle"] == "@Aria_ZHC"
    assert "read" in st
    assert "post" in st
    assert is_x_configured() == (is_x_read_configured() or is_x_post_configured())


def test_reading_active_false_without_bearer(test_settings):
    test_settings.x_bearer_token = ""
    test_settings.x_curiosity_enabled = True
    assert is_x_read_configured() is False
    assert is_x_reading_active() is False


def test_reading_active_false_when_bearer_configured_but_all_gates_off(test_settings):
    """#123 — bearer présent mais aucune tâche consommatrice active = pas de vraie lecture
    (cf. CLAUDE.md 11/07 : lecture X coupée délibérément pour maîtriser le coût)."""
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = False
    test_settings.x_allow_replies = False
    test_settings.x_mentions_learn_enabled = False
    assert is_x_read_configured() is True
    assert is_x_reading_active() is False
    assert x_status()["reading_active"] is False


def test_reading_active_true_when_curiosity_gate_on(test_settings):
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = True
    assert is_x_reading_active() is True
    assert x_status()["reading_active"] is True


def test_reading_active_true_when_replies_gate_on(test_settings):
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = False
    test_settings.x_allow_replies = True
    assert is_x_reading_active() is True


def test_reading_active_true_when_mentions_learn_gate_on(test_settings):
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = False
    test_settings.x_allow_replies = False
    test_settings.x_mentions_learn_enabled = True
    assert is_x_reading_active() is True


def test_reading_active_true_when_conviction_research_gate_on(test_settings):
    """19/07 -- la diligence de conviction est un consommateur de lecture X au même
    titre que la curiosité/mentions -- doit se refléter dans is_x_reading_active()."""
    test_settings.x_bearer_token = "b"
    test_settings.x_curiosity_enabled = False
    test_settings.x_allow_replies = False
    test_settings.x_mentions_learn_enabled = False
    test_settings.aria_conviction_research_enabled = True
    assert is_x_reading_active() is True


# ── search_recent_tweets / fetch_user_recent_tweets (19/07, conviction_research.py) ──

class _FakeGetResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeGetOnlyClient:
    """Fake httpx.AsyncClient supportant uniquement get() -- suffisant pour
    search_recent_tweets/fetch_user_recent_tweets (aucun POST)."""

    def __init__(self, responses_by_url_substring: dict[str, object]):
        self._responses = responses_by_url_substring

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None, params=None):
        for substring, resp in self._responses.items():
            if substring in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"URL inattendue dans le fake client : {url}")


@pytest.mark.asyncio
async def test_search_recent_tweets_empty_query_returns_empty(test_settings):
    test_settings.x_bearer_token = "b"
    assert await search_recent_tweets("") == []


@pytest.mark.asyncio
async def test_search_recent_tweets_no_bearer_returns_empty(test_settings):
    test_settings.x_bearer_token = ""
    assert await search_recent_tweets("COBOT") == []


@pytest.mark.asyncio
async def test_search_recent_tweets_success(test_settings, monkeypatch):
    test_settings.x_bearer_token = "b"
    payload = {
        "data": [
            {"id": "1", "text": "COBOT is pumping!", "created_at": "2026-07-19T10:00:00.000Z", "author_id": "42"},
            {"id": "2", "text": "", "created_at": "2026-07-19T10:05:00.000Z"},  # texte vide -- filtré
        ]
    }
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.httpx.AsyncClient",
        lambda **kw: _FakeGetOnlyClient({"/tweets/search/recent": _FakeGetResponse(200, payload)}),
    )
    results = await search_recent_tweets("COBOT", max_results=10)
    assert len(results) == 1
    assert results[0]["text"] == "COBOT is pumping!"
    assert results[0]["tweet_id"] == "1"


@pytest.mark.asyncio
async def test_search_recent_tweets_http_failure_returns_empty(test_settings, monkeypatch):
    test_settings.x_bearer_token = "b"
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.httpx.AsyncClient",
        lambda **kw: _FakeGetOnlyClient({"/tweets/search/recent": _FakeGetResponse(429, {})}),
    )
    assert await search_recent_tweets("COBOT") == []


@pytest.mark.asyncio
async def test_search_recent_tweets_exception_returns_empty(test_settings, monkeypatch):
    test_settings.x_bearer_token = "b"
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.httpx.AsyncClient",
        lambda **kw: _FakeGetOnlyClient({"/tweets/search/recent": ConnectionError("boom")}),
    )
    assert await search_recent_tweets("COBOT") == []


@pytest.mark.asyncio
async def test_fetch_user_recent_tweets_empty_username_returns_empty(test_settings):
    test_settings.x_bearer_token = "b"
    assert await fetch_user_recent_tweets("") == []


@pytest.mark.asyncio
async def test_fetch_user_recent_tweets_no_bearer_returns_empty(test_settings):
    test_settings.x_bearer_token = ""
    assert await fetch_user_recent_tweets("cobot_official") == []


@pytest.mark.asyncio
async def test_fetch_user_recent_tweets_success(test_settings, monkeypatch):
    test_settings.x_bearer_token = "b"
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.httpx.AsyncClient",
        lambda **kw: _FakeGetOnlyClient({
            "/users/by/username/cobot_official": _FakeGetResponse(200, {"data": {"id": "999"}}),
            "/users/999/tweets": _FakeGetResponse(200, {"data": [
                {"id": "1", "text": "gm", "created_at": "2026-07-19T10:00:00.000Z"},
            ]}),
        }),
    )
    results = await fetch_user_recent_tweets("@cobot_official")
    assert len(results) == 1
    assert results[0]["text"] == "gm"


@pytest.mark.asyncio
async def test_fetch_user_recent_tweets_user_lookup_fails_returns_empty(test_settings, monkeypatch):
    test_settings.x_bearer_token = "b"
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.httpx.AsyncClient",
        lambda **kw: _FakeGetOnlyClient({
            "/users/by/username/ghost": _FakeGetResponse(404, {}),
        }),
    )
    assert await fetch_user_recent_tweets("ghost") == []
