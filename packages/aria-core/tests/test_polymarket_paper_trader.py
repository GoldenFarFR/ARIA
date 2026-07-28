"""Polymarket paper portfolio (26/07, Item #108) -- deterministic engine,
isolated temp DB, no real network/LLM calls."""
from __future__ import annotations

import pytest

from aria_core import polymarket_paper_trader as ppt
from aria_core.services.polymarket import PolymarketCandidateMarket, PolymarketOrderBook
from aria_core.skills.polymarket_thesis import PolymarketJudgment


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ppt, "DB_PATH", str(tmp_path / "polymarket_paper.db"))
    return tmp_path


def _market(*, event_slug="evt", question="Will X happen?", yes_price=0.5, yes_token="yes-tok", no_token="no-tok"):
    return PolymarketCandidateMarket(
        event_title="Some Event",
        event_slug=event_slug,
        question=question,
        yes_token_id=yes_token,
        no_token_id=no_token,
        yes_price=yes_price,
        volume_usd=100_000.0,
        liquidity_usd=50_000.0,
        end_date="2026-08-15T00:00:00Z",
        tags=["macro"],
    )


def _judgment(*, side="YES", win_probability=0.9, aria_probability=0.9, market_probability=0.5, edge=0.4, action="BET"):
    return PolymarketJudgment(
        market_question="Will X happen?",
        market_probability=market_probability,
        aria_probability=aria_probability,
        vote_spread=0.05,
        edge=edge,
        side=side,
        win_probability=win_probability,
        reasoning="raisonnement",
        action=action,
    )


def _patch_order_book(monkeypatch, best_ask):
    async def fake_get_order_book(self, token_id):
        return PolymarketOrderBook(available=True, best_bid=best_ask - 0.02, best_ask=best_ask, spread=0.02)

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_order_book", fake_get_order_book)


# ── compute_bet_size ─────────────────────────────────────────────────────────────

def test_compute_bet_size_kelly_formula():
    j = _judgment(win_probability=0.7)
    # b = (1-0.5)/0.5 = 1.0 ; kelly_f = 0.7 - 0.3/1.0 = 0.4 ; *0.25 = 0.10 -> under MAX_BET_PCT(0.05)? no, 0.10 > 0.05 -> capped
    size = ppt.compute_bet_size(j, entry_price=0.5, equity_usd=100_000.0)
    assert size == pytest.approx(0.05 * 100_000.0)  # capped at MAX_BET_PCT


def test_compute_bet_size_below_cap_uses_kelly():
    # win_probability just above 0.5 at entry_price 0.5 gives a small Kelly fraction,
    # low enough to stay under MAX_BET_PCT uncapped.
    j = _judgment(win_probability=0.55)
    size = ppt.compute_bet_size(j, entry_price=0.5, equity_usd=100_000.0)
    # b=1.0, kelly_f = 0.55 - 0.45/1.0 = 0.10, *0.25 = 0.025 (< 0.05 cap)
    assert size == pytest.approx(0.025 * 100_000.0)


def test_compute_bet_size_zero_on_missing_win_probability():
    j = _judgment()
    j.win_probability = None
    assert ppt.compute_bet_size(j, entry_price=0.5, equity_usd=100_000.0) == 0.0


def test_compute_bet_size_zero_on_invalid_entry_price():
    j = _judgment()
    assert ppt.compute_bet_size(j, entry_price=0.0, equity_usd=100_000.0) == 0.0
    assert ppt.compute_bet_size(j, entry_price=1.0, equity_usd=100_000.0) == 0.0


# ── polymarket_paper_enabled ─────────────────────────────────────────────────────

def test_polymarket_paper_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("ARIA_POLYMARKET_PAPER_ENABLED", raising=False)
    assert ppt.polymarket_paper_enabled() is False
    monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
    assert ppt.polymarket_paper_enabled() is True


# ── open_bet ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_bet_persists_all_entry_fields(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.55)
    market = _market()
    j = _judgment()

    pos = await ppt.open_bet(market, j)

    assert pos is not None
    assert pos["side"] == "YES"
    assert pos["entry_price"] == 0.55  # real order-book ask, not the Gamma reference price
    assert pos["status"] == "open"
    assert pos["market_probability_at_entry"] == 0.5
    assert pos["aria_probability_at_entry"] == 0.9
    assert pos["win_probability_at_entry"] == 0.9
    assert pos["shares"] == pytest.approx(pos["size_usd"] / 0.55)


@pytest.mark.asyncio
async def test_open_bet_refuses_non_bet_action(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.55)
    j = _judgment(action="SKIP")
    pos = await ppt.open_bet(_market(), j)
    assert pos is None


@pytest.mark.asyncio
async def test_open_bet_refuses_duplicate_market(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.55)
    market = _market()
    j = _judgment()
    first = await ppt.open_bet(market, j)
    assert first is not None
    second = await ppt.open_bet(market, j)
    assert second is None


@pytest.mark.asyncio
async def test_open_bet_refuses_at_position_cap(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.55)
    for i in range(ppt.MAX_OPEN_POSITIONS):
        pos = await ppt.open_bet(_market(event_slug=f"evt-{i}", question=f"Q{i}?"), _judgment())
        assert pos is not None
    overflow = await ppt.open_bet(_market(event_slug="evt-overflow", question="Overflow?"), _judgment())
    assert overflow is None


@pytest.mark.asyncio
async def test_open_bet_falls_back_to_gamma_price_when_book_unavailable(tmp_db, monkeypatch):
    async def fake_get_order_book(self, token_id):
        return PolymarketOrderBook(available=False, error="unavailable")

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_order_book", fake_get_order_book)

    pos = await ppt.open_bet(_market(yes_price=0.4), _judgment())
    assert pos is not None
    assert pos["entry_price"] == 0.4  # Gamma reference price, degraded gracefully


@pytest.mark.asyncio
async def test_open_bet_no_side_uses_inverse_gamma_price(tmp_db, monkeypatch):
    async def fake_get_order_book(self, token_id):
        return PolymarketOrderBook(available=False, error="unavailable")

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_order_book", fake_get_order_book)

    j = _judgment(side="NO", win_probability=0.9)
    pos = await ppt.open_bet(_market(yes_price=0.3), j)
    assert pos is not None
    assert pos["side"] == "NO"
    assert pos["entry_price"] == pytest.approx(0.7)  # 1 - yes_price


@pytest.mark.asyncio
async def test_open_bet_refuses_negligible_size(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    # Defense-in-depth: `estimate_market_probability` should never produce a
    # BET this close to the entry-implied breakeven (MIN_WIN_PROBABILITY=0.85
    # is enforced upstream) -- this constructs the edge case directly to
    # verify `open_bet` itself refuses a near-zero Kelly fraction rather than
    # booking a symbolic position.
    j = _judgment(win_probability=0.500001, edge=0.000001)
    pos = await ppt.open_bet(_market(), j)
    assert pos is None


# ── check_resolutions ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_resolutions_closes_a_winning_yes_bet(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    pos = await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="YES"))
    assert pos is not None

    async def fake_resolution(self, event_slug, yes_token_id):
        return True, 1.0  # "Yes" won

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)

    closed = await ppt.check_resolutions()
    assert len(closed) == 1
    assert closed[0]["resolution_price"] == 1.0
    assert closed[0]["pnl_usd"] > 0  # shares * 1.0 - size_usd, a win pays out more than staked


@pytest.mark.asyncio
async def test_check_resolutions_closes_a_losing_yes_bet(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="YES"))

    async def fake_resolution(self, event_slug, yes_token_id):
        return True, 0.0  # "Yes" lost

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)

    closed = await ppt.check_resolutions()
    assert len(closed) == 1
    assert closed[0]["pnl_usd"] == pytest.approx(-closed[0]["size_usd"])  # total loss of the stake


@pytest.mark.asyncio
async def test_check_resolutions_no_bet_wins_when_yes_resolves_false(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="NO", win_probability=0.9))

    async def fake_resolution(self, event_slug, yes_token_id):
        return True, 0.0  # "Yes" lost -> the NO bet wins

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)

    closed = await ppt.check_resolutions()
    assert len(closed) == 1
    assert closed[0]["pnl_usd"] > 0


@pytest.mark.asyncio
async def test_check_resolutions_leaves_unresolved_market_open(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(), _judgment())

    async def fake_resolution(self, event_slug, yes_token_id):
        return False, None

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)

    closed = await ppt.check_resolutions()
    assert closed == []
    assert len(await ppt.get_open_positions()) == 1


# ── portfolio_summary / cash_available ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_summary_reflects_realized_pnl(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="YES"))

    async def fake_resolution(self, event_slug, yes_token_id):
        return True, 1.0

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)
    await ppt.check_resolutions()

    summary = await ppt.portfolio_summary()
    assert summary["closed_count"] == 1
    assert summary["win_rate"] == 1.0
    assert summary["equity"] == pytest.approx(ppt.STARTING_CAPITAL_USD + summary["realized_pnl"])


@pytest.mark.asyncio
async def test_cash_available_reduced_by_open_position_cost(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    start = await ppt.cash_available()
    pos = await ppt.open_bet(_market(), _judgment())
    after = await ppt.cash_available()
    assert after == pytest.approx(start - pos["size_usd"])


# ── run_polymarket_paper_cycle ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_never_opens_new_bets_when_gate_off(tmp_db, monkeypatch):
    monkeypatch.delenv("ARIA_POLYMARKET_PAPER_ENABLED", raising=False)

    async def fake_list_liquid_events(self, **kwargs):
        return [_market()]

    async def fake_estimate(market):
        return _judgment()

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events)
    monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

    result = await ppt.run_polymarket_paper_cycle()
    assert result["opened"] == []


@pytest.mark.asyncio
async def test_cycle_opens_bets_when_gate_on_and_notifies(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
    _patch_order_book(monkeypatch, best_ask=0.5)

    async def fake_list_liquid_events(self, **kwargs):
        return [_market()]

    async def fake_estimate(market):
        return _judgment()

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events)
    monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

    notified = []

    async def notifier(text):
        notified.append(text)

    result = await ppt.run_polymarket_paper_cycle(notifier=notifier)
    assert len(result["opened"]) == 1
    assert len(notified) == 1
    assert "Pari Polymarket" in notified[0]


@pytest.mark.asyncio
async def test_cycle_skips_candidates_already_positioned(tmp_db, monkeypatch):
    monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
    _patch_order_book(monkeypatch, best_ask=0.5)
    market = _market()
    await ppt.open_bet(market, _judgment())

    calls = {"n": 0}

    async def fake_list_liquid_events(self, **kwargs):
        return [market]

    async def fake_estimate(m):
        calls["n"] += 1
        return _judgment()

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events)
    monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

    result = await ppt.run_polymarket_paper_cycle()
    assert result["opened"] == []
    assert calls["n"] == 0  # never even judged -- already positioned


@pytest.mark.asyncio
async def test_cycle_notifies_resolutions_regardless_of_gate(tmp_db, monkeypatch):
    monkeypatch.delenv("ARIA_POLYMARKET_PAPER_ENABLED", raising=False)
    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="YES"))

    async def fake_resolution(self, event_slug, yes_token_id):
        return True, 1.0

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)

    notified = []

    async def notifier(text):
        notified.append(text)

    result = await ppt.run_polymarket_paper_cycle(notifier=notifier)
    assert len(result["resolved"]) == 1
    assert any("Résolution Polymarket" in n for n in notified)


# ── format_portfolio_report (26/07 -- the operator's own access point) ─────────

@pytest.mark.asyncio
async def test_format_portfolio_report_reflects_open_and_closed_positions(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(event_slug="evt-open", question="Still open?"), _judgment())
    await ppt.open_bet(_market(event_slug="evt-closed", yes_token="yes-tok-2", question="Already closed?"), _judgment())

    async def fake_resolution(self, event_slug, yes_token_id):
        return (True, 1.0) if event_slug == "evt-closed" else (False, None)

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)
    await ppt.check_resolutions()

    report = await ppt.format_portfolio_report()

    assert "Still open?" in report
    assert "Already closed?" in report
    assert "GAGNÉ" in report
    assert "Positions ouvertes : 1" in report
    assert "Résolues : 1" in report


@pytest.mark.asyncio
async def test_format_portfolio_report_empty_portfolio(tmp_db):
    report = await ppt.format_portfolio_report()
    assert "Portefeuille Polymarket" in report
    assert "Positions ouvertes : 0" in report


@pytest.mark.asyncio
async def test_cycle_free_skip_never_starves_the_next_candidate(tmp_db, monkeypatch):
    """27/07 -- Item #133, real bug found live: a market ALWAYS first in the
    volume-sorted list (a live prod case: a Fed-decision market pinned at
    yes_price=0.0015) permanently starved every other candidate every single
    cycle with CANDIDATES_PER_CYCLE=1 -- confirmed live via 0 Tavily calls in
    ~25h since the gate's activation. A free skip (extreme price/missing
    price, decided before any research/LLM call) must never consume the
    CANDIDATES_PER_CYCLE budget."""
    monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
    monkeypatch.setattr(ppt, "CANDIDATES_PER_CYCLE", 1)
    _patch_order_book(monkeypatch, best_ask=0.5)

    always_extreme = _market(event_slug="fed-decision", yes_price=0.0015)
    real_candidate = _market(event_slug="gta-vi", yes_price=0.5)

    async def fake_list_liquid_events(self, **kwargs):
        return [always_extreme, real_candidate]

    judged = []

    async def fake_estimate(market):
        judged.append(market.event_slug)
        if market.event_slug == "fed-decision":
            return PolymarketJudgment(
                market_question=market.question,
                market_probability=market.yes_price,
                action="SKIP",
                skip_reason="market_price_already_extreme",
            )
        return _judgment()

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events)
    monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

    result = await ppt.run_polymarket_paper_cycle()

    # Both markets got judged (the free skip didn't stop the loop early)...
    assert judged == ["fed-decision", "gta-vi"]
    # ...and the real candidate behind it actually got booked.
    assert len(result["opened"]) == 1


@pytest.mark.asyncio
async def test_cycle_paid_skip_does_consume_the_budget(tmp_db, monkeypatch):
    """Counterpart to the test above: a skip that only happens AFTER research/
    LLM votes already ran (e.g. no_consensus) must still count against
    CANDIDATES_PER_CYCLE -- only the free, pre-research skips are exempt."""
    monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
    monkeypatch.setattr(ppt, "CANDIDATES_PER_CYCLE", 1)
    _patch_order_book(monkeypatch, best_ask=0.5)

    first = _market(event_slug="first", yes_price=0.5)
    second = _market(event_slug="second", yes_price=0.5)

    async def fake_list_liquid_events(self, **kwargs):
        return [first, second]

    judged = []

    async def fake_estimate(market):
        judged.append(market.event_slug)
        return PolymarketJudgment(
            market_question=market.question,
            market_probability=market.yes_price,
            action="SKIP",
            skip_reason="no_consensus",
        )

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events)
    monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

    result = await ppt.run_polymarket_paper_cycle()

    # Budget of 1 -- the second candidate is never even reached.
    assert judged == ["first"]
    assert result["opened"] == []


# ── #151 -- judgment log + anti-monoculture rotation ────────────────────────


@pytest.mark.asyncio
async def test_save_judgment_log_then_recently_judged_true(tmp_db):
    market = _market(event_slug="fed-decision")
    judgment = _judgment(action="SKIP")
    await ppt.save_judgment_log(market, judgment)
    assert await ppt.recently_judged("fed-decision") is True


@pytest.mark.asyncio
async def test_recently_judged_false_for_unknown_market(tmp_db):
    assert await ppt.recently_judged("never-seen") is False


@pytest.mark.asyncio
async def test_recently_judged_false_after_cooldown_expires(tmp_db):
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    market = _market(event_slug="fed-decision")
    await ppt.save_judgment_log(market, _judgment(action="SKIP"))
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    async with aiosqlite.connect(ppt.DB_PATH) as db:
        await db.execute(
            "UPDATE polymarket_judgment_log SET judged_at = ? WHERE event_slug = ?", (stale, "fed-decision"),
        )
        await db.commit()
    assert await ppt.recently_judged("fed-decision", cooldown_hours=24) is False


@pytest.mark.asyncio
async def test_save_judgment_log_upserts_latest_verdict_only(tmp_db):
    """One row per market, not a history -- a market's real probability keeps
    moving, and answering "why didn't ARIA bet" only ever needs the latest
    verdict."""
    import aiosqlite

    market = _market(event_slug="fed-decision")
    await ppt.save_judgment_log(market, _judgment(action="SKIP", aria_probability=0.5))
    await ppt.save_judgment_log(market, _judgment(action="SKIP", aria_probability=0.84))
    async with aiosqlite.connect(ppt.DB_PATH) as db:
        async with db.execute("SELECT COUNT(*), aria_probability FROM polymarket_judgment_log") as cur:
            count, aria_probability = await cur.fetchone()
    assert count == 1
    assert aria_probability == pytest.approx(0.84)


@pytest.mark.asyncio
async def test_cycle_persists_a_judgment_log_row_even_on_skip(tmp_db, monkeypatch):
    """The real gap this item closes: before #151, only a booked bet left any
    trace -- a rejected candidate vanished with no way to know it was ever
    evaluated, short of forcing a manual cycle."""
    monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
    monkeypatch.setattr(ppt, "CANDIDATES_PER_CYCLE", 1)
    _patch_order_book(monkeypatch, best_ask=0.5)

    market = _market(event_slug="fed-decision")

    async def fake_list_liquid_events(self, **kwargs):
        return [market]

    async def fake_estimate(m):
        return PolymarketJudgment(
            market_question=m.question, market_probability=0.75, aria_probability=0.84,
            vote_spread=0.06, edge=0.09, side="YES", win_probability=0.84,
            action="SKIP", skip_reason="win_probability_too_low",
        )

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events)
    monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

    await ppt.run_polymarket_paper_cycle()

    assert await ppt.recently_judged("fed-decision") is True


@pytest.mark.asyncio
async def test_cycle_skips_recently_judged_market_letting_next_candidate_through(tmp_db, monkeypatch):
    """The anti-monoculture fix itself: the highest-volume market, already
    judged last cycle, must not monopolize this one too -- the next candidate
    (a different topic) gets its turn instead, for free (no estimate_
    market_probability call, no budget consumed)."""
    monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
    monkeypatch.setattr(ppt, "CANDIDATES_PER_CYCLE", 1)
    _patch_order_book(monkeypatch, best_ask=0.5)

    fed = _market(event_slug="fed-decision", yes_price=0.75)
    sports = _market(event_slug="world-cup-final", yes_price=0.5)
    await ppt.save_judgment_log(fed, _judgment(action="SKIP"))

    async def fake_list_liquid_events(self, **kwargs):
        return [fed, sports]  # fed still sorts first by volume

    judged = []

    async def fake_estimate(m):
        judged.append(m.event_slug)
        return _judgment()

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events)
    monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

    result = await ppt.run_polymarket_paper_cycle()

    # fed-decision was skipped for free (cooldown) -- never reached estimate_market_probability.
    assert judged == ["world-cup-final"]
    assert len(result["opened"]) == 1
