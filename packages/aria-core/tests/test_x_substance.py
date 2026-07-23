"""Signal 'substance X' -- TwitterAPI.io en premier (profil PUIS derniers
tweets pour activité/engagement, 23/07), repli Tavily extract (âge seul) si
absent. La régularité de publication via TAVILY a été ÉVALUÉE puis ÉCARTÉE
(page profil extraite = tweets "highlights" non chronologiques) -- mais
réintégrée via TwitterAPI.io/last_tweets, qui donne un vrai fil daté (voir
docstring de x_substance.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.services.tavily import TavilyExtractResult, TavilyPage
from aria_core.services.twitterapi_io import TwitterApiIoProfile, TwitterApiIoTweet
from aria_core.skills.x_substance import (
    XSubstanceFacts,
    gather_x_substance_facts,
    judge_x_substance,
)


async def _no_tweets(handle):
    return None

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_x_substance(XSubstanceFacts(available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_old_account_good_ratio_is_positive():
    v = judge_x_substance(
        XSubstanceFacts(available=True, account_age_days=700, followers=3676, following=242)
    )
    assert v.signal == "positive"
    assert v.score == 100.0  # âge >=18 mois (100) + ratio <=1.0 (100)


def test_old_account_bad_ratio_is_neutral():
    """Compte ancien mais ratio following/followers dégradé (following > followers)."""
    v = judge_x_substance(
        XSubstanceFacts(available=True, account_age_days=700, followers=100, following=400)
    )
    # Poids redistribués (âge 0.30 + ratio 0.25, régularité/engagement absents) :
    # (0.30*100 + 0.25*0) / 0.55 = 54.5
    assert v.score == 54.5
    assert v.signal == "neutral"


def test_young_account_is_weak():
    v = judge_x_substance(XSubstanceFacts(available=True, account_age_days=30))
    assert v.signal == "weak"
    assert v.score == 0.0


def test_age_only_fallback_when_followers_missing():
    """Repli Tavily : ni followers ni following -- score = âge seul, honnête."""
    v = judge_x_substance(XSubstanceFacts(available=True, account_age_days=700))
    assert v.score == 100.0
    assert "indisponibles" in v.points[0]


def test_zero_followers_never_divides_by_zero():
    v = judge_x_substance(
        XSubstanceFacts(available=True, account_age_days=700, followers=0, following=50)
    )
    # ratio_score=0 si followers<=0, jamais une exception -- (0.30*100+0.25*0)/0.55 = 54.5
    assert v.score == 54.5


def test_all_four_axes_present_is_positive():
    v = judge_x_substance(
        XSubstanceFacts(
            available=True, account_age_days=700, followers=3676, following=242,
            active_weeks_recent=10, tweets_analyzed=20, avg_engagement_rate=0.015,
        )
    )
    # 0.30*100 (âge) + 0.25*100 (ratio) + 0.25*100 (régularité 10/8 plafonné) + 0.20*100 (engagement >=1%)
    assert v.score == 100.0
    assert v.signal == "positive"
    assert "engagement moyen" in v.points[0]
    assert "actif 10/12 semaines" in v.points[0]


def test_low_activity_and_weak_engagement_drags_score_down():
    v = judge_x_substance(
        XSubstanceFacts(
            available=True, account_age_days=700, followers=3676, following=242,
            active_weeks_recent=1, tweets_analyzed=5, avg_engagement_rate=0.0002,
        )
    )
    # 0.30*100 + 0.25*100 + 0.25*(1/8*100=12.5) + 0.20*0 (engagement < 0,05%)
    assert v.score == pytest.approx(0.30 * 100 + 0.25 * 100 + 0.25 * 12.5 + 0.20 * 0, abs=0.1)
    assert v.signal != "positive"


def test_tweets_analyzed_but_engagement_none_when_followers_zero():
    """Si followers<=0 au moment du calcul d'activité, avg_engagement_rate reste
    None (jamais une division par zéro) -- seule la régularité est retenue."""
    v = judge_x_substance(
        XSubstanceFacts(
            available=True, account_age_days=700, followers=0, following=0,
            active_weeks_recent=5, tweets_analyzed=10, avg_engagement_rate=None,
        )
    )
    # âge(0.30)+ratio(0.25, followers<=0 -> 0)+régularité(0.25, 5/8*100=62.5) ; engagement absent
    expected = (0.30 * 100 + 0.25 * 0 + 0.25 * 62.5) / 0.80
    assert v.score == pytest.approx(expected, abs=0.1)


# ── Récolte (TwitterAPI.io en premier, repli Tavily) ────────────────────────


@pytest.mark.asyncio
async def test_gather_no_handle_unavailable():
    facts = await gather_x_substance_facts(None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_uses_twitterapi_io_when_available():
    async def twitterapi_fn(handle):
        assert handle == "crynuxio"
        return TwitterApiIoProfile(
            followers=3676, following=242, created_at=datetime(2023, 10, 27, tzinfo=timezone.utc),
        )

    async def extract_fn(handle):
        raise AssertionError("ne doit jamais appeler Tavily si TwitterAPI.io a répondu")

    facts = await gather_x_substance_facts(
        "crynuxio", twitterapi_fn=twitterapi_fn, tweets_fn=_no_tweets, extract_fn=extract_fn, now=NOW,
    )
    assert facts.available is True
    assert facts.source == "twitterapi_io"
    assert facts.followers == 3676
    assert facts.following == 242
    assert facts.account_age_days == (NOW - datetime(2023, 10, 27, tzinfo=timezone.utc)).days
    assert facts.active_weeks_recent is None
    assert facts.avg_engagement_rate is None


@pytest.mark.asyncio
async def test_gather_computes_activity_and_engagement_from_tweets():
    async def twitterapi_fn(handle):
        return TwitterApiIoProfile(
            followers=1000, following=100, created_at=datetime(2023, 10, 27, tzinfo=timezone.utc),
        )

    async def tweets_fn(handle):
        return [
            TwitterApiIoTweet(
                created_at=NOW - timedelta(days=d),
                like_count=10, reply_count=2, retweet_count=1, quote_count=0,
            )
            for d in (1, 8, 15, 40)  # 4 semaines distinctes récentes
        ]

    facts = await gather_x_substance_facts(
        "crynuxio", twitterapi_fn=twitterapi_fn, tweets_fn=tweets_fn, now=NOW,
    )
    assert facts.tweets_analyzed == 4
    assert facts.active_weeks_recent == 4
    # (10+2+1+0)=13 par tweet / 1000 followers = 0.013
    assert facts.avg_engagement_rate == pytest.approx(0.013)


@pytest.mark.asyncio
async def test_gather_tweets_failure_degrades_only_activity_axes():
    """Une panne sur last_tweets ne doit jamais faire tomber tout le signal --
    le profil (âge/ratio) reste disponible, seuls activité/engagement manquent."""
    async def twitterapi_fn(handle):
        return TwitterApiIoProfile(
            followers=1000, following=100, created_at=datetime(2023, 10, 27, tzinfo=timezone.utc),
        )

    async def tweets_fn(handle):
        raise RuntimeError("panne réseau sur last_tweets")

    facts = await gather_x_substance_facts(
        "crynuxio", twitterapi_fn=twitterapi_fn, tweets_fn=tweets_fn, now=NOW,
    )
    assert facts.available is True
    assert facts.followers == 1000
    assert facts.active_weeks_recent is None
    assert facts.avg_engagement_rate is None


@pytest.mark.asyncio
async def test_gather_falls_back_to_tavily_when_twitterapi_io_returns_none():
    async def twitterapi_fn(handle):
        return None  # clé absente ou panne

    async def extract_fn(handle):
        return TavilyExtractResult(
            available=True,
            pages=[TavilyPage(url=f"https://x.com/{handle}", raw_content="Bio\n\nJoined October 2023\n\n")],
        )

    facts = await gather_x_substance_facts(
        "crynuxio", twitterapi_fn=twitterapi_fn, extract_fn=extract_fn, now=NOW,
    )
    assert facts.available is True
    assert facts.source == "tavily_fallback"
    assert facts.followers is None
    assert facts.following is None
    assert facts.account_age_days == (NOW - datetime(2023, 10, 1, tzinfo=timezone.utc)).days


@pytest.mark.asyncio
async def test_gather_falls_back_to_tavily_when_twitterapi_io_raises():
    async def twitterapi_fn(handle):
        raise RuntimeError("panne réseau")

    async def extract_fn(handle):
        return TavilyExtractResult(
            available=True,
            pages=[TavilyPage(url="https://x.com/crynuxio", raw_content="Joined October 2023")],
        )

    facts = await gather_x_substance_facts(
        "crynuxio", twitterapi_fn=twitterapi_fn, extract_fn=extract_fn, now=NOW,
    )
    assert facts.available is True
    assert facts.source == "tavily_fallback"


@pytest.mark.asyncio
async def test_gather_both_sources_fail_unavailable():
    async def twitterapi_fn(handle):
        return None

    async def extract_fn(handle):
        return TavilyExtractResult(available=False, error="budget mensuel épuisé")

    facts = await gather_x_substance_facts(
        "crynuxio", twitterapi_fn=twitterapi_fn, extract_fn=extract_fn,
    )
    assert facts.available is False
    assert "budget" in (facts.error or "")


@pytest.mark.asyncio
async def test_gather_handle_strips_at_sign():
    calls = []

    async def twitterapi_fn(handle):
        calls.append(handle)
        return None

    async def extract_fn(handle):
        return TavilyExtractResult(available=False, error="test")

    await gather_x_substance_facts("@crynuxio", twitterapi_fn=twitterapi_fn, extract_fn=extract_fn)
    assert calls == ["crynuxio"]
