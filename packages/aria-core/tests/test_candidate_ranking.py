"""Classement des candidats (tri de l'analyse de masse) — pur et déterministe, hors-ligne."""
from __future__ import annotations

import pytest

from aria_core.skills.candidate_ranking import (
    _concentration_points,
    _liquidity_points,
    draw_top,
    rank_candidates,
    score_candidate,
    top_candidates,
)


def _row(**kw) -> dict:
    base = {
        "contract": "0x" + "a" * 40,
        "symbol": "AAA",
        "security_score": 75,
        "liquidity_usd": 50_000.0,
        "top_holder_pct": 15.0,
        "verdict": "SAFE",
    }
    base.update(kw)
    return base


def test_liquidity_points_bounds():
    assert _liquidity_points(20_000) == 0.0
    assert _liquidity_points(30_000) == 0.0
    assert _liquidity_points(100_000) > 0
    assert _liquidity_points(1_000_000) <= 25.0
    assert _liquidity_points(10 ** 9) == 25.0  # plafonné


def test_concentration_points():
    assert _concentration_points(None) == 0.0
    assert _concentration_points(5) == 10.0
    assert _concentration_points(40) == -10.0
    assert -10.0 <= _concentration_points(20) <= 10.0


def test_score_higher_for_better_token():
    good = score_candidate(
        _row(security_score=90, liquidity_usd=500_000, top_holder_pct=8, verdict="SAFE")
    )
    bad = score_candidate(
        _row(security_score=60, liquidity_usd=31_000, top_holder_pct=28, verdict="CAUTION")
    )
    assert good.rank_score > bad.rank_score


def test_danger_verdict_penalised():
    safe = score_candidate(_row(verdict="SAFE"))
    danger = score_candidate(_row(verdict="DANGER"))
    assert danger.rank_score < safe.rank_score


def test_rank_sorts_descending_and_deterministic():
    rows = [
        _row(contract="0x" + "1" * 40, security_score=60),
        _row(contract="0x" + "2" * 40, security_score=90),
        _row(contract="0x" + "3" * 40, security_score=75),
    ]
    ranked = rank_candidates(rows)
    scores = [c.rank_score for c in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ranked[0].security_score == 90
    # déterministe : même entrée -> même ordre
    assert [c.contract for c in rank_candidates(rows)] == [c.contract for c in ranked]


def test_score_handles_missing_fields():
    c = score_candidate({"contract": "0xabc"})
    assert isinstance(c.rank_score, float)
    assert c.symbol == ""
    assert c.top_holder_pct is None


@pytest.mark.asyncio
async def test_top_candidates_injected_lister():
    rows = [
        _row(contract="0x" + "1" * 40, security_score=60),
        _row(contract="0x" + "2" * 40, security_score=95),
    ]

    async def lister():
        return rows

    tops = await top_candidates(1, lister=lister)
    assert len(tops) == 1
    assert tops[0].security_score == 95


@pytest.mark.asyncio
async def test_draw_top_returns_pool_dicts():
    rows = [_row(contract="0x" + "2" * 40, security_score=95)]

    async def lister():
        return rows

    out = await draw_top(5, lister=lister)
    assert isinstance(out, list) and out
    assert out[0]["contract"] == "0x" + "2" * 40
    assert "rank_score" in out[0]
