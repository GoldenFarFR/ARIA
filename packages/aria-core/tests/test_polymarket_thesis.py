"""Tests for ARIA's own Polymarket probability judgment (26/07, Item #108) --
no real network/LLM calls, everything monkeypatched."""
from __future__ import annotations

import pytest

from aria_core.services.polymarket import PolymarketCandidateMarket
from aria_core.skills import polymarket_thesis as pt


def _market(*, yes_price=0.5, question="Will X happen?") -> PolymarketCandidateMarket:
    return PolymarketCandidateMarket(
        event_title="Some Event",
        event_slug="some-event",
        question=question,
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_price=yes_price,
        volume_usd=100_000.0,
        liquidity_usd=50_000.0,
        end_date="2026-08-15T00:00:00Z",
        tags=["macro"],
    )


@pytest.fixture(autouse=True)
def _no_real_price_history_calls(monkeypatch):
    """`estimate_market_probability` now also fetches recent price-history
    for the velocity signal -- default to "no history" (velocity None) so no
    test in this file makes a real network call by accident. Tests dedicated
    to the velocity signal override this explicitly via `_patch_velocity`."""

    async def fake_get_price_history(self, token_id, **kwargs):
        return []

    monkeypatch.setattr(
        "aria_core.services.polymarket.PolymarketClient.get_price_history", fake_get_price_history
    )


def _patch_velocity(monkeypatch, points):
    """``points``: list of (timestamp, probability) tuples, oldest first."""
    from aria_core.services.polymarket import PolymarketPricePoint

    async def fake_get_price_history(self, token_id, **kwargs):
        return [PolymarketPricePoint(timestamp=t, probability=p) for t, p in points]

    monkeypatch.setattr(
        "aria_core.services.polymarket.PolymarketClient.get_price_history", fake_get_price_history
    )


class _TavilyUnavailable:
    available = False
    answer = None
    snippets: list = []


class _TavilyResult:
    def __init__(self, answer, snippets):
        self.available = True
        self.answer = answer
        self.snippets = snippets


def _patch_tavily(monkeypatch, result):
    """Patches the CLASS, never the singleton instance -- patching the
    instance leaves a permanent residual instance attribute after teardown
    (getattr on the instance resolves to the class's bound method BEFORE the
    patch, so monkeypatch's teardown restores it as an instance attribute
    instead of deleting one -- silently shadowing any later class-level patch
    from other test files for the rest of the pytest session). Same fix
    already applied earlier in this codebase for goplus_client/virtuals_client/
    coingecko_client."""
    from aria_core.services import tavily as tavily_module

    async def fake_search(*args, **kwargs):
        return result

    monkeypatch.setattr(type(tavily_module.tavily_client), "search", staticmethod(fake_search))


def _patch_votes(monkeypatch, raw_responses: list[str | None]):
    """Feeds ``raw_responses`` to successive ``chat_with_context`` calls, in
    call order (deterministic here: no real awaited I/O between mock calls,
    ``asyncio.gather`` schedules them in creation order)."""
    calls = {"i": 0}

    async def fake_chat_with_context(*args, **kwargs):
        i = calls["i"]
        calls["i"] += 1
        if i < len(raw_responses):
            return raw_responses[i]
        return raw_responses[-1] if raw_responses else None

    monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)


def _vote_json(probability: float, reasoning: str = "raisonnement court") -> str:
    return f'{{"probability": {probability}, "reasoning": "{reasoning}"}}'


# ── _extreme_price_skip ──────────────────────────────────────────────────────────

def test_extreme_price_skip_none_price():
    result = pt._extreme_price_skip(_market(yes_price=None))
    assert result is not None
    assert result.action == "SKIP"
    assert result.skip_reason == "market_price_unavailable"


def test_extreme_price_skip_near_zero():
    result = pt._extreme_price_skip(_market(yes_price=0.02))
    assert result is not None
    assert result.skip_reason == "market_price_already_extreme"


def test_extreme_price_skip_near_one():
    result = pt._extreme_price_skip(_market(yes_price=0.98))
    assert result is not None
    assert result.skip_reason == "market_price_already_extreme"


def test_extreme_price_skip_normal_price_passes():
    assert pt._extreme_price_skip(_market(yes_price=0.5)) is None


# ── _single_probability_vote ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_vote_parses_valid_response(monkeypatch):
    _patch_votes(monkeypatch, [_vote_json(0.7, "car X est probable")])
    probability, reasoning = await pt._single_probability_vote("some prompt")
    assert probability == 0.7
    assert reasoning == "car X est probable"


@pytest.mark.asyncio
async def test_single_vote_clamps_extreme_probability(monkeypatch):
    _patch_votes(monkeypatch, ['{"probability": 1.5, "reasoning": "trop sur"}'])
    probability, _ = await pt._single_probability_vote("some prompt")
    assert probability == 0.99


@pytest.mark.asyncio
async def test_single_vote_llm_exception_returns_none(monkeypatch):
    async def raising(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pt, "chat_with_context", raising)
    probability, reasoning = await pt._single_probability_vote("some prompt")
    assert probability is None
    assert reasoning is None


@pytest.mark.asyncio
async def test_single_vote_empty_response_returns_none(monkeypatch):
    _patch_votes(monkeypatch, [None])
    probability, reasoning = await pt._single_probability_vote("some prompt")
    assert probability is None
    assert reasoning is None


@pytest.mark.asyncio
async def test_single_vote_unparseable_json_returns_none(monkeypatch):
    _patch_votes(monkeypatch, ["not json at all"])
    probability, reasoning = await pt._single_probability_vote("some prompt")
    assert probability is None
    assert reasoning is None


@pytest.mark.asyncio
async def test_single_vote_missing_probability_returns_none(monkeypatch):
    _patch_votes(monkeypatch, ['{"reasoning": "no probability field"}'])
    probability, reasoning = await pt._single_probability_vote("some prompt")
    assert probability is None
    assert reasoning is None


# ── estimate_market_probability -- full pipeline ────────────────────────────────

@pytest.mark.asyncio
async def test_estimate_skips_extreme_price_without_any_llm_call(monkeypatch):
    calls = {"n": 0}

    async def fake_chat_with_context(*args, **kwargs):
        calls["n"] += 1
        return _vote_json(0.9)

    monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

    result = await pt.estimate_market_probability(_market(yes_price=0.03))

    assert result.action == "SKIP"
    assert result.skip_reason == "market_price_already_extreme"
    assert calls["n"] == 0  # no wasted LLM call on a structurally-excluded market


@pytest.mark.asyncio
async def test_estimate_insufficient_votes_skips(monkeypatch):
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    # Only 1 of 3 votes succeeds -- below the "majority" threshold (>= 2/3).
    _patch_votes(monkeypatch, [_vote_json(0.9), None, None])

    result = await pt.estimate_market_probability(_market(yes_price=0.5))

    assert result.action == "SKIP"
    assert result.skip_reason == "insufficient_votes"


@pytest.mark.asyncio
async def test_estimate_no_consensus_skips(monkeypatch):
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    # 3 valid votes but wildly spread (0.90 - 0.30 = 0.60 > MAX_VOTE_SPREAD) --
    # no real convergence, the "quality" signal fails even though every call
    # individually succeeded.
    _patch_votes(monkeypatch, [_vote_json(0.90), _vote_json(0.60), _vote_json(0.30)])

    result = await pt.estimate_market_probability(_market(yes_price=0.5))

    assert result.action == "SKIP"
    assert result.skip_reason == "no_consensus"
    assert result.vote_spread == pytest.approx(0.60)


@pytest.mark.asyncio
async def test_estimate_win_probability_too_low_skips(monkeypatch):
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    # Converged votes around 0.65 -- a real edge vs a 0.50 market, but 0.65
    # never clears the operator's explicit 85% floor.
    _patch_votes(monkeypatch, [_vote_json(0.65), _vote_json(0.66), _vote_json(0.64)])

    result = await pt.estimate_market_probability(_market(yes_price=0.5))

    assert result.action == "SKIP"
    assert result.skip_reason == "win_probability_too_low"
    assert result.win_probability == pytest.approx(0.65, abs=0.01)


@pytest.mark.asyncio
async def test_estimate_no_edge_skips_even_above_win_floor(monkeypatch):
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    # Converged votes at 0.90 clear the 85% floor, but the market is ALREADY
    # at 0.87 -- barely any edge left (secondary guard, MIN_EDGE_PROBABILITY).
    _patch_votes(monkeypatch, [_vote_json(0.90), _vote_json(0.90), _vote_json(0.90)])

    result = await pt.estimate_market_probability(_market(yes_price=0.87))

    assert result.action == "SKIP"
    assert result.skip_reason == "no_edge"


@pytest.mark.asyncio
async def test_estimate_bets_yes_on_a_real_qualifying_edge(monkeypatch):
    _patch_tavily(monkeypatch, _TavilyResult("resume factuel", [("un extrait", "https://example.com", "2026-07-20")]))
    _patch_votes(monkeypatch, [_vote_json(0.92), _vote_json(0.90), _vote_json(0.91)])

    result = await pt.estimate_market_probability(_market(yes_price=0.5))

    assert result.action == "BET"
    assert result.side == "YES"
    assert result.win_probability == pytest.approx(0.91, abs=0.01)
    assert result.reasoning == "raisonnement court"


@pytest.mark.asyncio
async def test_estimate_bets_no_when_aria_probability_is_low(monkeypatch):
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    # ARIA thinks "Yes" is very unlikely (~0.06) while the market prices it at
    # 0.5 -- betting NO wins with probability (1 - 0.06) = 0.94.
    _patch_votes(monkeypatch, [_vote_json(0.06), _vote_json(0.05), _vote_json(0.07)])

    result = await pt.estimate_market_probability(_market(yes_price=0.5))

    assert result.action == "BET"
    assert result.side == "NO"
    assert result.win_probability == pytest.approx(0.94, abs=0.01)


@pytest.mark.asyncio
async def test_estimate_research_failure_still_proceeds_to_llm(monkeypatch):
    """Tavily being down must never block the judgment -- the LLM call still
    happens, just without research context."""
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    _patch_votes(monkeypatch, [_vote_json(0.92), _vote_json(0.90), _vote_json(0.91)])

    result = await pt.estimate_market_probability(_market(yes_price=0.5))

    assert result.action == "BET"


# ── probability velocity (26/07, operator insight on trading/prediction correlation) ─

@pytest.mark.asyncio
async def test_recent_velocity_none_without_token_id():
    from aria_core.services.polymarket import PolymarketCandidateMarket

    market = PolymarketCandidateMarket(
        event_title="e", event_slug="e", question="q", yes_token_id=None, no_token_id=None,
        yes_price=0.5, volume_usd=1.0, liquidity_usd=1.0, end_date="2026-08-01T00:00:00Z",
    )
    assert await pt._recent_velocity(market) is None


@pytest.mark.asyncio
async def test_recent_velocity_computes_delta(monkeypatch):
    _patch_velocity(monkeypatch, [(1000, 0.20), (2000, 0.70)])
    velocity = await pt._recent_velocity(_market())
    assert velocity == pytest.approx(0.50)


@pytest.mark.asyncio
async def test_recent_velocity_none_on_fetch_failure(monkeypatch):
    async def raising(self, token_id, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_price_history", raising)
    assert await pt._recent_velocity(_market()) is None


@pytest.mark.asyncio
async def test_estimate_carries_velocity_even_on_skip(monkeypatch):
    _patch_velocity(monkeypatch, [(1000, 0.20), (2000, 0.70)])
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    _patch_votes(monkeypatch, [_vote_json(0.55), _vote_json(0.56), _vote_json(0.54)])

    result = await pt.estimate_market_probability(_market(yes_price=0.5))

    assert result.action == "SKIP"
    assert result.probability_velocity_7d == pytest.approx(0.50)


@pytest.mark.asyncio
async def test_estimate_highlights_significant_velocity_in_the_llm_prompt(monkeypatch):
    _patch_velocity(monkeypatch, [(1000, 0.20), (2000, 0.70)])
    _patch_tavily(monkeypatch, _TavilyUnavailable())

    captured = {}

    async def fake_chat_with_context(user_message, *args, **kwargs):
        captured["message"] = user_message
        return _vote_json(0.9)

    monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

    await pt.estimate_market_probability(_market(yes_price=0.5))

    assert "Signal de marché" in captured["message"]
    assert "augmenté" in captured["message"]


@pytest.mark.asyncio
async def test_estimate_does_not_highlight_small_velocity(monkeypatch):
    _patch_velocity(monkeypatch, [(1000, 0.50), (2000, 0.55)])  # 5 points, below the 15-point threshold
    _patch_tavily(monkeypatch, _TavilyUnavailable())

    captured = {}

    async def fake_chat_with_context(user_message, *args, **kwargs):
        captured["message"] = user_message
        return _vote_json(0.9)

    monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

    await pt.estimate_market_probability(_market(yes_price=0.5))

    assert "Signal de marché" not in captured["message"]
