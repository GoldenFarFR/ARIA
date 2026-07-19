"""Client twit.sh (x402) -- aucun appel réseau réel, x402_executor.fetch_paid_resource
mocké directement (même patron que test_ottoai.py/test_cybercentry.py). Schéma de
réponse réel capturé en conditions réelles (19/07, 2 paiements de vérification)."""
from __future__ import annotations

import pytest

from aria_core.services import twitsh


class _FakeResult:
    def __init__(self, *, status, body=b"", reason="", amount_usd=0.0):
        self.status = status
        self.body = body
        self.reason = reason
        self.amount_usd = amount_usd


# Corps réel observé (19/07, x402.twit.sh/tweets/search?words=base) -- tronqué,
# created_at au format Twitter v1.1 legacy confirmé en conditions réelles.
_REAL_SEARCH_BODY = (
    b'{"data":[{"id":"2078869474107605386","text":"Real tweet about base tokens",'
    b'"created_at":"Sun Jul 19 15:48:00 +0000 2026","author_id":"1767549728575594497",'
    b'"public_metrics":{"retweet_count":0,"reply_count":0,"like_count":3}}]}'
)


@pytest.mark.asyncio
async def test_search_tweets_parses_real_response_shape(monkeypatch):
    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn):
        assert url == "https://x402.twit.sh/tweets/search?words=base&maxResults=10"
        assert resource == "tweets-search"
        assert provider == "twitsh"
        return _FakeResult(status="ok", body=_REAL_SEARCH_BODY, amount_usd=0.006)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    tweets = await twitsh.search_tweets("base", max_results=10)

    assert len(tweets) == 1
    assert tweets[0]["text"] == "Real tweet about base tokens"
    assert tweets[0]["tweet_id"] == "2078869474107605386"
    assert tweets[0]["author_id"] == "1767549728575594497"
    assert tweets[0]["public_metrics"]["like_count"] == 3


@pytest.mark.asyncio
async def test_search_tweets_normalizes_legacy_date_to_iso(monkeypatch):
    """"Sun Jul 19 15:48:00 +0000 2026" (Twitter v1.1 legacy, observé en conditions
    réelles) -> ISO 8601 -- sinon _posting_cadence_from_tweets échoue silencieusement
    à parser CHAQUE tweet twit.sh (bug réel évité, cf. docstring du module)."""
    async def fake_fetch(url, **kwargs):
        return _FakeResult(status="ok", body=_REAL_SEARCH_BODY, amount_usd=0.006)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    tweets = await twitsh.search_tweets("base")

    from datetime import datetime

    # Doit être parsable par fromisoformat (le même parseur que x_twitter.py) --
    # sinon la normalisation n'a servi à rien.
    parsed = datetime.fromisoformat(tweets[0]["created_at"])
    assert parsed.year == 2026 and parsed.month == 7 and parsed.day == 19


@pytest.mark.asyncio
async def test_search_tweets_empty_query_no_call(monkeypatch):
    async def _fail_if_called(*a, **k):
        raise AssertionError("ne doit jamais être appelé, requête vide")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", _fail_if_called)

    assert await twitsh.search_tweets("") == []
    assert await twitsh.search_tweets("   ") == []


@pytest.mark.asyncio
async def test_search_tweets_blocked_returns_empty(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return _FakeResult(status="blocked", reason="plafond hebdomadaire x402 dépassé")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    assert await twitsh.search_tweets("base") == []


@pytest.mark.asyncio
async def test_search_tweets_exception_never_raises(monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("réseau down")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", _raise)

    assert await twitsh.search_tweets("base") == []


@pytest.mark.asyncio
async def test_search_tweets_unreadable_body_returns_empty(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return _FakeResult(status="ok", body=b"not json at all")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    assert await twitsh.search_tweets("base") == []


@pytest.mark.asyncio
async def test_search_tweets_tweets_without_text_are_dropped(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return _FakeResult(status="ok", body=b'{"data":[{"id":"1","text":""},{"id":"2"}]}')

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    assert await twitsh.search_tweets("base") == []


# ── fetch_user_tweets (/tweets/user, param `username` -- confirmé après un premier
#    essai avec `from` en 400 "Missing required query parameter: username") ────────

_REAL_USER_BODY = (
    b'{"data":[{"id":"2078743878933393909","text":"Timeline tweet",'
    b'"created_at":"Sun Jul 19 07:28:56 +0000 2026","author_id":"44196397"}]}'
)


@pytest.mark.asyncio
async def test_fetch_user_tweets_uses_username_param(monkeypatch):
    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn):
        assert url == "https://x402.twit.sh/tweets/user?username=cobot_official&maxResults=20"
        assert resource == "tweets-user"
        assert provider == "twitsh"
        return _FakeResult(status="ok", body=_REAL_USER_BODY, amount_usd=0.01)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    tweets = await twitsh.fetch_user_tweets("cobot_official", max_results=20)

    assert len(tweets) == 1
    assert tweets[0]["text"] == "Timeline tweet"


@pytest.mark.asyncio
async def test_fetch_user_tweets_strips_at_prefix(monkeypatch):
    async def fake_fetch(url, **kwargs):
        assert "username=cobot_official" in url
        return _FakeResult(status="ok", body=_REAL_USER_BODY)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    await twitsh.fetch_user_tweets("@cobot_official")


@pytest.mark.asyncio
async def test_fetch_user_tweets_empty_username_no_call(monkeypatch):
    async def _fail_if_called(*a, **k):
        raise AssertionError("ne doit jamais être appelé, username vide")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", _fail_if_called)

    assert await twitsh.fetch_user_tweets("") == []


@pytest.mark.asyncio
async def test_fetch_user_tweets_exception_never_raises(monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("réseau down")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", _raise)

    assert await twitsh.fetch_user_tweets("cobot_official") == []


def test_normalize_created_at_malformed_passes_through():
    assert twitsh._normalize_created_at("not a date") == "not a date"
    assert twitsh._normalize_created_at(None) is None
    assert twitsh._normalize_created_at(123) is None
