"""Signal 'engagement récent sur le token' -- distinct de x_substance.py
(activité générale du compte) et github_substance.py (activité du produit) :
demande spécifiquement quand le compte a mentionné pour la dernière fois LE
SYMBOLE de ce token précis (03/08, cas C-MEM)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.skills.token_cashtag_engagement import (
    TokenCashtagEngagementFacts,
    gather_token_cashtag_engagement_facts,
    judge_token_cashtag_engagement,
)

NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_token_cashtag_engagement(TokenCashtagEngagementFacts(available=False, error="handle manquant"))
    assert v.signal == "unknown"


def test_no_tweets_scanned_is_unknown():
    v = judge_token_cashtag_engagement(TokenCashtagEngagementFacts(tweets_scanned=0, available=True))
    assert v.signal == "unknown"


def test_no_mention_found_is_concern():
    v = judge_token_cashtag_engagement(
        TokenCashtagEngagementFacts(tweets_scanned=20, days_since_last_mention=None, available=True)
    )
    assert v.signal == "concern"
    assert any("aucune mention" in p for p in v.points)


def test_stale_mention_is_concern():
    v = judge_token_cashtag_engagement(
        TokenCashtagEngagementFacts(tweets_scanned=20, days_since_last_mention=75, available=True)
    )
    assert v.signal == "concern"
    assert any("75 jours" in p for p in v.points)


def test_weak_mention_is_neutral():
    v = judge_token_cashtag_engagement(
        TokenCashtagEngagementFacts(tweets_scanned=20, days_since_last_mention=20, available=True)
    )
    assert v.signal == "neutral"


def test_recent_mention_is_positive():
    v = judge_token_cashtag_engagement(
        TokenCashtagEngagementFacts(tweets_scanned=20, days_since_last_mention=2, available=True)
    )
    assert v.signal == "positive"


# ── Récolte X (TwitterAPI.io factice) ────────────────────────────────────────


class _Tweet:
    def __init__(self, created_at, text):
        self.created_at, self.text = created_at, text


@pytest.mark.asyncio
async def test_gather_missing_handle_or_symbol_is_unavailable():
    facts = await gather_token_cashtag_engagement_facts(None, "CMEM")
    assert facts.available is False
    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_tweets_fn_failure_is_unavailable():
    async def failing_fn(handle):
        raise RuntimeError("network down")

    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", "CMEM", tweets_fn=failing_fn)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_no_tweets_is_zero_scanned():
    async def empty_fn(handle):
        return None

    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", "CMEM", tweets_fn=empty_fn, now=NOW)
    assert facts.available is True
    assert facts.tweets_scanned == 0


@pytest.mark.asyncio
async def test_gather_finds_cashtag_mention_with_dollar_sign():
    async def fn(handle):
        return [_Tweet(NOW - timedelta(days=5), "Big news for $CMEM today!")]

    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", "CMEM", tweets_fn=fn, now=NOW)
    assert facts.tweets_scanned == 1
    assert facts.days_since_last_mention == 5


@pytest.mark.asyncio
async def test_gather_finds_bare_symbol_without_dollar_sign():
    async def fn(handle):
        return [_Tweet(NOW - timedelta(days=3), "CMEM is shipping fast")]

    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", "CMEM", tweets_fn=fn, now=NOW)
    assert facts.days_since_last_mention == 3


@pytest.mark.asyncio
async def test_gather_no_mention_in_scanned_tweets_is_none():
    async def fn(handle):
        return [_Tweet(NOW - timedelta(days=1), "unrelated product update")]

    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", "CMEM", tweets_fn=fn, now=NOW)
    assert facts.tweets_scanned == 1
    assert facts.days_since_last_mention is None


@pytest.mark.asyncio
async def test_gather_uses_most_recent_mention_among_several():
    async def fn(handle):
        return [
            _Tweet(NOW - timedelta(days=40), "$CMEM launch"),
            _Tweet(NOW - timedelta(days=10), "$CMEM update"),
            _Tweet(NOW - timedelta(days=25), "$CMEM again"),
        ]

    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", "CMEM", tweets_fn=fn, now=NOW)
    assert facts.days_since_last_mention == 10


@pytest.mark.asyncio
async def test_gather_does_not_false_positive_on_substring():
    """'CMEM' ne doit pas matcher dans un mot plus long comme 'ACMEMORY'."""

    async def fn(handle):
        return [_Tweet(NOW - timedelta(days=1), "ACMEMORY is unrelated")]

    facts = await gather_token_cashtag_engagement_facts("Claude_Memory", "CMEM", tweets_fn=fn, now=NOW)
    assert facts.days_since_last_mention is None
