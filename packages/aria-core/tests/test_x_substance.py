"""Signal 'substance X' -- réduit à l'âge du compte (23/07). La régularité de
publication a été ÉVALUÉE puis ÉCARTÉE (testée en conditions réelles : la page
profil extraite par Tavily renvoie des tweets "highlights" non chronologiques,
pas le fil récent réel -- voir docstring de x_substance.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core.services.tavily import TavilyExtractResult, TavilyPage
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


def test_old_account_is_positive():
    v = judge_x_substance(XSubstanceFacts(available=True, account_age_days=700))  # ~23 mois
    assert v.signal == "positive"
    assert v.score == 100.0


def test_mid_age_account_is_neutral():
    v = judge_x_substance(XSubstanceFacts(available=True, account_age_days=200))  # ~6.6 mois
    assert v.signal == "neutral"
    assert v.score == 40.0


def test_young_account_is_weak():
    v = judge_x_substance(XSubstanceFacts(available=True, account_age_days=30))
    assert v.signal == "weak"
    assert v.score == 0.0


# ── Récolte (extract factice) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gather_no_handle_unavailable():
    facts = await gather_x_substance_facts(None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_extract_unavailable_degrades():
    async def extract_fn(handle):
        return TavilyExtractResult(available=False, error="budget mensuel épuisé")

    facts = await gather_x_substance_facts("crynuxio", extract_fn=extract_fn)
    assert facts.available is False
    assert "budget" in (facts.error or "")


@pytest.mark.asyncio
async def test_gather_parses_join_date_real_format():
    async def extract_fn(handle):
        return TavilyExtractResult(
            available=True,
            pages=[TavilyPage(url=f"https://x.com/{handle}", raw_content="Crynux\n\nJoined October 2023\n\n")],
        )

    facts = await gather_x_substance_facts("crynuxio", extract_fn=extract_fn, now=NOW)
    assert facts.available is True
    assert facts.account_age_days == (NOW - datetime(2023, 10, 1, tzinfo=timezone.utc)).days


@pytest.mark.asyncio
async def test_gather_no_join_date_found_unavailable():
    async def extract_fn(handle):
        return TavilyExtractResult(
            available=True, pages=[TavilyPage(url="https://x.com/foo", raw_content="Bio sans date d'inscription.")],
        )

    facts = await gather_x_substance_facts("foo", extract_fn=extract_fn)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_handle_strips_at_sign():
    calls = []

    async def extract_fn(handle):
        calls.append(handle)
        return TavilyExtractResult(available=False, error="test")

    await gather_x_substance_facts("@crynuxio", extract_fn=extract_fn)
    assert calls == ["crynuxio"]


@pytest.mark.asyncio
async def test_gather_fetch_exception_degrades():
    async def extract_fn(handle):
        raise RuntimeError("panne réseau")

    facts = await gather_x_substance_facts("crynuxio", extract_fn=extract_fn)
    assert facts.available is False
