"""Signal 'substance X' -- TwitterAPI.io en premier (followers/following/âge
du compte en un appel, 23/07), repli Tavily extract (âge seul) si absent.
La régularité de publication a été ÉVALUÉE puis ÉCARTÉE (testée en conditions
réelles : la page profil extraite par Tavily renvoie des tweets "highlights"
non chronologiques, pas le fil récent réel -- voir docstring de x_substance.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core.services.tavily import TavilyExtractResult, TavilyPage
from aria_core.services.twitterapi_io import TwitterApiIoProfile
from aria_core.skills.x_substance import (
    XSubstanceFacts,
    gather_x_substance_facts,
    judge_x_substance,
)

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
    assert v.score == 50.0  # 0.5*100 (âge) + 0.5*0 (ratio > 3.0)
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
    assert v.score == 50.0  # ratio_score=0 si followers<=0, jamais une exception


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
        "crynuxio", twitterapi_fn=twitterapi_fn, extract_fn=extract_fn, now=NOW,
    )
    assert facts.available is True
    assert facts.source == "twitterapi_io"
    assert facts.followers == 3676
    assert facts.following == 242
    assert facts.account_age_days == (NOW - datetime(2023, 10, 27, tzinfo=timezone.utc)).days


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
