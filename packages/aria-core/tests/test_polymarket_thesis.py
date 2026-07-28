"""Tests for ARIA's own Polymarket probability judgment (26/07, Item #108) --
no real network/LLM calls, everything monkeypatched."""
from __future__ import annotations

import pytest

from aria_core.services.polymarket import PolymarketCandidateMarket
from aria_core.skills import polymarket_thesis as pt


def _market(*, yes_price=0.5, question="Will X happen?", days_left=None) -> PolymarketCandidateMarket:
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
        days_left=days_left,
    )


@pytest.fixture(autouse=True)
def _reset_judgment_cache():
    """26/07 -- ``_judgment_cache`` is a module-level dict keyed by
    yes_token_id -- almost every test in this file uses the SAME fixed
    ``"yes-token"`` id via ``_market()``'s default. Without this reset,
    whichever test runs first would populate the cache and every later test
    would silently read its stale result instead of exercising its own mock,
    same trap already fixed once for momentum_entry.py's holders cache."""
    pt._judgment_cache.clear()
    yield
    pt._judgment_cache.clear()


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


# ── vote lens diversity + delayed price display (#146, 28/07) ──────────────────────

@pytest.mark.asyncio
async def test_estimate_never_shows_market_price_in_the_vote_prompt(monkeypatch):
    """The market's own price must never leak into the prompt the 3 votes see
    -- it is used only afterward (in code) to compute `edge`. A price value
    distinctive enough (0.63) to show up as a false positive if leaked."""
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    captured = []

    async def fake_chat_with_context(user_message, *args, **kwargs):
        captured.append(user_message)
        return _vote_json(0.9)

    monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

    await pt.estimate_market_probability(_market(yes_price=0.63))

    assert len(captured) == pt.VOTE_COUNT
    for message in captured:
        assert "63" not in message
        assert "Prix de marché" not in message


@pytest.mark.asyncio
async def test_estimate_fires_each_vote_with_a_distinct_lens(monkeypatch):
    """Real fix for the monoculture bug: VOTE_COUNT identical prompts at low
    temperature converge almost by construction. Each vote must now argue
    from a genuinely different system prompt (`_VOTE_LENSES`)."""
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    captured_system_prompts = []

    async def fake_chat_with_context(user_message, system_prompt, *args, **kwargs):
        captured_system_prompts.append(system_prompt)
        return _vote_json(0.9)

    monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

    await pt.estimate_market_probability(_market(yes_price=0.5))

    assert len(captured_system_prompts) == pt.VOTE_COUNT == len(pt._VOTE_LENSES)
    assert captured_system_prompts == list(pt._VOTE_LENSES)
    assert len(set(captured_system_prompts)) == pt.VOTE_COUNT  # genuinely distinct, not repeated


def test_single_probability_vote_defaults_to_bare_system_prompt():
    """Direct callers (unit tests, any future ad-hoc use) that don't pass a
    lens still get a sane, unbiased default rather than an error."""
    import inspect

    sig = inspect.signature(pt._single_probability_vote)
    assert sig.parameters["system_prompt"].default == pt._SYSTEM_PROMPT


# ── horizon-differentiated thresholds (#148, 28/07) ─────────────────────────────────

def test_horizon_thresholds_baseline_when_days_left_unknown():
    assert pt._horizon_thresholds(_market(days_left=None)) == (pt.MAX_VOTE_SPREAD, pt.MIN_EDGE_PROBABILITY)


def test_horizon_thresholds_baseline_for_long_horizon():
    assert pt._horizon_thresholds(_market(days_left=29.0)) == (pt.MAX_VOTE_SPREAD, pt.MIN_EDGE_PROBABILITY)


def test_horizon_thresholds_stricter_for_short_horizon():
    days_left = 7.0 / 24.0  # 7 hours
    assert pt._horizon_thresholds(_market(days_left=days_left)) == (
        pt.MAX_VOTE_SPREAD_SHORT_HORIZON, pt.MIN_EDGE_PROBABILITY_SHORT_HORIZON,
    )


def test_horizon_thresholds_boundary_is_exclusive():
    """Exactly SHORT_HORIZON_DAYS is NOT short-horizon (`<`, not `<=`) --
    matches the boundary style of the rest of this module's numeric gates."""
    assert pt._horizon_thresholds(_market(days_left=pt.SHORT_HORIZON_DAYS)) == (
        pt.MAX_VOTE_SPREAD, pt.MIN_EDGE_PROBABILITY,
    )


@pytest.mark.asyncio
async def test_estimate_rejects_on_short_horizon_spread_that_would_pass_long_horizon(monkeypatch):
    """A vote spread of 0.12 clears the baseline MAX_VOTE_SPREAD (0.15) but
    must be rejected on a short-horizon (7h) market, whose stricter spread
    cap (0.10) it fails."""
    _patch_tavily(monkeypatch, _TavilyUnavailable())
    # spread = 0.12 (between the short-horizon 0.10 cap and baseline 0.15);
    # mean = 0.8533, clears MIN_WIN_PROBABILITY=0.85 so the win-probability
    # gate doesn't interfere with isolating the spread check.
    _patch_votes(monkeypatch, [_vote_json(0.90), _vote_json(0.88), _vote_json(0.78)])

    long_horizon_result = await pt.estimate_market_probability(_market(yes_price=0.5, days_left=29.0))
    assert long_horizon_result.action == "BET"

    pt._judgment_cache.clear()
    _patch_votes(monkeypatch, [_vote_json(0.90), _vote_json(0.88), _vote_json(0.78)])  # re-arm: 1 call = 3 votes
    short_horizon_result = await pt.estimate_market_probability(_market(yes_price=0.5, days_left=7.0 / 24.0))
    assert short_horizon_result.action == "SKIP"
    assert short_horizon_result.skip_reason == "no_consensus"


@pytest.mark.asyncio
async def test_estimate_rejects_on_short_horizon_edge_that_would_pass_long_horizon(monkeypatch):
    """Converged votes at 0.90 (win_probability clears MIN_WIN_PROBABILITY=
    0.85) against a 0.75 market -- edge 0.15 clears the baseline
    MIN_EDGE_PROBABILITY (0.12) but not the short-horizon one (0.20)."""
    _patch_tavily(monkeypatch, _TavilyUnavailable())

    _patch_votes(monkeypatch, [_vote_json(0.90), _vote_json(0.91), _vote_json(0.89)])
    long_horizon_result = await pt.estimate_market_probability(_market(yes_price=0.75, days_left=29.0))
    assert long_horizon_result.action == "BET"

    pt._judgment_cache.clear()
    _patch_votes(monkeypatch, [_vote_json(0.90), _vote_json(0.91), _vote_json(0.89)])  # re-arm: 1 call = 3 votes
    short_horizon_result = await pt.estimate_market_probability(_market(yes_price=0.75, days_left=7.0 / 24.0))
    assert short_horizon_result.action == "SKIP"
    assert short_horizon_result.skip_reason == "no_edge"  # same 0.15 edge fails the stricter 0.20 short-horizon floor


# ── cache TTL (26/07, full-pipeline audit -- gaspillage Tavily/LLM réel) ────────────

class TestJudgmentCache:
    @pytest.mark.asyncio
    async def test_second_call_within_ttl_reuses_cache_no_new_llm_call(self, monkeypatch):
        _patch_tavily(monkeypatch, _TavilyUnavailable())
        calls = {"n": 0}

        async def fake_chat_with_context(*args, **kwargs):
            calls["n"] += 1
            return _vote_json(0.9)

        monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

        market = _market(yes_price=0.5)
        first = await pt.estimate_market_probability(market)
        second = await pt.estimate_market_probability(market)

        assert calls["n"] == pt.VOTE_COUNT  # only the first call actually votes
        assert second is first  # same cached PolymarketJudgment object

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self, monkeypatch):
        _patch_tavily(monkeypatch, _TavilyUnavailable())
        _patch_votes(monkeypatch, [_vote_json(0.9), _vote_json(0.9), _vote_json(0.9)])

        fake_now = {"t": 1000.0}
        monkeypatch.setattr(pt.time, "monotonic", lambda: fake_now["t"])

        market = _market(yes_price=0.5)
        await pt.estimate_market_probability(market)

        calls = {"n": 0}

        async def fake_chat_with_context(*args, **kwargs):
            calls["n"] += 1
            return _vote_json(0.9)

        monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)
        fake_now["t"] += pt._JUDGMENT_CACHE_TTL_SECONDS + 1.0
        await pt.estimate_market_probability(market)

        assert calls["n"] == pt.VOTE_COUNT  # re-judged for real after expiry

    @pytest.mark.asyncio
    async def test_different_markets_never_share_a_cache_entry(self, monkeypatch):
        _patch_tavily(monkeypatch, _TavilyUnavailable())
        calls = {"n": 0}

        async def fake_chat_with_context(*args, **kwargs):
            calls["n"] += 1
            return _vote_json(0.9)

        monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

        other = _market(yes_price=0.5, question="Will Y happen?")
        other.yes_token_id = "a-different-yes-token"
        await pt.estimate_market_probability(_market(yes_price=0.5))
        await pt.estimate_market_probability(other)

        assert calls["n"] == pt.VOTE_COUNT * 2

    @pytest.mark.asyncio
    async def test_falls_back_to_question_text_when_token_id_missing(self, monkeypatch):
        """A market with no yes_token_id (e.g. velocity signal structurally
        unavailable for it too) must still be cached, keyed by its question
        text -- never silently skip caching just because the preferred key is
        absent."""
        _patch_tavily(monkeypatch, _TavilyUnavailable())
        calls = {"n": 0}

        async def fake_chat_with_context(*args, **kwargs):
            calls["n"] += 1
            return _vote_json(0.9)

        monkeypatch.setattr(pt, "chat_with_context", fake_chat_with_context)

        market = _market(yes_price=0.5)
        market.yes_token_id = None
        await pt.estimate_market_probability(market)
        await pt.estimate_market_probability(market)

        assert calls["n"] == pt.VOTE_COUNT
