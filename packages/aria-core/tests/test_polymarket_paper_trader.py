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


def _judgment(
    *, side="YES", win_probability=0.9, aria_probability=0.9, market_probability=0.5, edge=0.4,
    action="BET", market_question="Will X happen?",
):
    return PolymarketJudgment(
        market_question=market_question,
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
    # Item #244 (30/07), operator request ("je veut aussi la date de fin du
    # paris") -- market.end_date was already fetched at candidate-build time
    # but never carried onto the position before this.
    assert pos["end_date"] == "2026-08-15T00:00:00Z"


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
async def test_portfolio_summary_marks_open_position_to_market(tmp_db, monkeypatch):
    """Item #229 (30/07, real bug found live): "equity" used to equal "cash"
    exactly (never valued an open position at its current price) -- a bet
    bought at 0.5 whose market has since moved to 0.7 must raise equity above
    cash by the position's unrealized gain, not leave it identical."""
    _patch_order_book(monkeypatch, best_ask=0.5)
    pos = await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="YES"))

    async def fake_order_book_moved(self, token_id):
        from aria_core.services.polymarket import PolymarketOrderBook

        return PolymarketOrderBook(available=True, best_bid=0.68, best_ask=0.7, spread=0.02)

    monkeypatch.setattr(
        "aria_core.services.polymarket.PolymarketClient.get_order_book", fake_order_book_moved,
    )

    summary = await ppt.portfolio_summary()
    assert summary["cash"] == pytest.approx(ppt.STARTING_CAPITAL_USD - pos["size_usd"])
    expected_open_value = pos["shares"] * 0.68
    assert summary["equity"] == pytest.approx(summary["cash"] + expected_open_value)
    assert summary["equity"] > summary["cash"]  # market moved in ARIA's favor


@pytest.mark.asyncio
async def test_portfolio_summary_degrades_to_entry_cost_when_book_unavailable(tmp_db, monkeypatch):
    """Same bug fix, degradation path: an unavailable/empty order book on the
    held side must never fabricate a value -- falls back to the position's
    own entry cost (size_usd), never blocking the summary."""
    _patch_order_book(monkeypatch, best_ask=0.5)
    pos = await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="YES"))

    async def fake_order_book_unavailable(self, token_id):
        from aria_core.services.polymarket import PolymarketOrderBook

        return PolymarketOrderBook(available=False, error="indisponible")

    monkeypatch.setattr(
        "aria_core.services.polymarket.PolymarketClient.get_order_book", fake_order_book_unavailable,
    )

    summary = await ppt.portfolio_summary()
    assert summary["equity"] == pytest.approx(summary["cash"] + pos["size_usd"])


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
    # Item #244 (30/07): _market()'s own default end_date, real doctrine
    # (persisted at open time, no live fetch inside this read-only report).
    assert "fin : 15/08/2026" in report


@pytest.mark.asyncio
async def test_format_portfolio_report_empty_portfolio(tmp_db):
    report = await ppt.format_portfolio_report()
    assert "Portefeuille Polymarket" in report
    assert "Positions ouvertes : 0" in report


# -- compute_calibration_buckets / format_calibration_report (Item #147, 28/07) --

@pytest.mark.asyncio
async def test_compute_calibration_buckets_empty_when_nothing_resolved(tmp_db):
    calibration = await ppt.compute_calibration_buckets()
    assert calibration == {"n": 0, "overall_brier": None, "buckets": []}


@pytest.mark.asyncio
async def test_compute_calibration_buckets_groups_by_decile_and_scores_brier(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)

    async def resolve_yes_wins(self, event_slug, yes_token_id):
        return True, 1.0

    async def resolve_yes_loses(self, event_slug, yes_token_id):
        return True, 0.0

    # Bet A: win_probability=0.90, wins -- decile 9 ([0.9, 1.0]).
    await ppt.open_bet(_market(event_slug="a", yes_token="yes-a"), _judgment(side="YES", win_probability=0.90))
    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", resolve_yes_wins)
    await ppt.check_resolutions()

    # Bet B: win_probability=0.90, loses -- same decile 9.
    await ppt.open_bet(_market(event_slug="b", yes_token="yes-b"), _judgment(side="YES", win_probability=0.90))
    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", resolve_yes_loses)
    await ppt.check_resolutions()

    # Bet C: win_probability=0.88, wins -- decile 8 ([0.8, 0.9)).
    await ppt.open_bet(_market(event_slug="c", yes_token="yes-c"), _judgment(side="YES", win_probability=0.88))
    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", resolve_yes_wins)
    await ppt.check_resolutions()

    calibration = await ppt.compute_calibration_buckets()

    assert calibration["n"] == 3
    assert calibration["overall_brier"] == pytest.approx(((0.90 - 1.0) ** 2 + (0.90 - 0.0) ** 2 + (0.88 - 1.0) ** 2) / 3)

    by_decile = {b["decile_low"]: b for b in calibration["buckets"]}
    assert set(by_decile) == {0.8, 0.9}

    bucket_9 = by_decile[0.9]
    assert bucket_9["n"] == 2
    assert bucket_9["avg_predicted_probability"] == pytest.approx(0.90)
    assert bucket_9["actual_win_rate"] == pytest.approx(0.5)  # 1 win, 1 loss, both predicted 90%
    assert bucket_9["brier_score"] == pytest.approx(((0.90 - 1.0) ** 2 + (0.90 - 0.0) ** 2) / 2)

    bucket_8 = by_decile[0.8]
    assert bucket_8["n"] == 1
    assert bucket_8["actual_win_rate"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_compute_calibration_buckets_ignores_positions_missing_win_probability(tmp_db, monkeypatch):
    """A legacy closed position from before ``win_probability_at_entry``
    existed (NULL after the additive schema migration) must never crash the
    calibration report -- excluded, not a fabricated 0.0 that would corrupt
    the Brier score. ``open_bet`` itself always sets this field for a real
    bet, so a direct SQL update simulates the legacy-row case."""
    import aiosqlite

    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(), _judgment())

    async def fake_resolution(self, event_slug, yes_token_id):
        return True, 1.0

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)
    await ppt.check_resolutions()

    async with aiosqlite.connect(ppt.DB_PATH) as db:
        await db.execute("UPDATE polymarket_paper_position SET win_probability_at_entry = NULL")
        await db.commit()

    calibration = await ppt.compute_calibration_buckets()
    assert calibration == {"n": 0, "overall_brier": None, "buckets": []}


def test_format_calibration_report_empty():
    report = ppt.format_calibration_report({"n": 0, "overall_brier": None, "buckets": []})
    assert "indisponible" in report


def test_format_calibration_report_with_data():
    calibration = {
        "n": 2,
        "overall_brier": 0.41,
        "buckets": [
            {
                "decile_low": 0.9, "decile_high": 1.0, "n": 2,
                "avg_predicted_probability": 0.9, "actual_win_rate": 0.5, "brier_score": 0.41,
            }
        ],
    }
    report = ppt.format_calibration_report(calibration)
    assert "0.410" in report
    assert "2 pari" in report
    assert "90%-100%" in report
    assert "50% réel" in report


@pytest.mark.asyncio
async def test_format_portfolio_report_includes_calibration_after_a_resolution(tmp_db, monkeypatch):
    _patch_order_book(monkeypatch, best_ask=0.5)
    await ppt.open_bet(_market(yes_token="yes-tok"), _judgment(side="YES", win_probability=0.9))

    async def fake_resolution(self, event_slug, yes_token_id):
        return True, 1.0

    monkeypatch.setattr("aria_core.services.polymarket.PolymarketClient.get_market_resolution", fake_resolution)
    await ppt.check_resolutions()

    report = await ppt.format_portfolio_report()
    assert "Calibrage" in report


@pytest.mark.asyncio
async def test_format_portfolio_report_omits_calibration_when_nothing_resolved(tmp_db):
    report = await ppt.format_portfolio_report()
    assert "Calibrage" not in report


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
    assert await ppt.recently_judged("fed-decision", market.question) is True


@pytest.mark.asyncio
async def test_recently_judged_false_for_unknown_market(tmp_db):
    assert await ppt.recently_judged("never-seen", "Will X happen?") is False


@pytest.mark.asyncio
async def test_recently_judged_false_after_cooldown_expires(tmp_db):
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    market = _market(event_slug="fed-decision")
    await ppt.save_judgment_log(market, _judgment(action="SKIP"))
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    async with aiosqlite.connect(ppt.DB_PATH) as db:
        await db.execute(
            "UPDATE polymarket_judgment_log SET judged_at = ? WHERE event_slug = ? AND question = ?",
            (stale, "fed-decision", market.question),
        )
        await db.commit()
    assert await ppt.recently_judged("fed-decision", market.question, cooldown_hours=24) is False


@pytest.mark.asyncio
async def test_judgment_log_hot_migration_preserves_rows_and_new_key(tmp_db):
    """Item #195 (29/07): a table already deployed under the OLD single-
    column PK (event_slug only) must be migrated in place (SQLite can't
    ALTER a PK) -- existing rows preserved, new composite key (event_slug,
    question) enforced afterward."""
    import aiosqlite

    async with aiosqlite.connect(ppt.DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE polymarket_judgment_log (
                event_slug TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                judged_at TEXT NOT NULL,
                market_probability REAL,
                aria_probability REAL,
                vote_spread REAL,
                edge REAL,
                side TEXT,
                win_probability REAL,
                action TEXT NOT NULL,
                skip_reason TEXT
            )
            """
        )
        await db.execute(
            "INSERT INTO polymarket_judgment_log "
            "(event_slug, question, judged_at, action) VALUES (?, ?, ?, ?)",
            ("fed-decision", "Old question?", "2026-07-28T17:42:41+00:00", "SKIP"),
        )
        await db.commit()

    # Triggers _ensure_tables() -- must detect the old shape and migrate.
    await ppt.recently_judged("unrelated", "unrelated?")

    async with aiosqlite.connect(ppt.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM polymarket_judgment_log")
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["event_slug"] == "fed-decision"
    assert rows[0]["question"] == "Old question?"

    # New composite key now allows the same event_slug with a DIFFERENT question.
    market2 = _market(event_slug="fed-decision", question="New question?")
    await ppt.save_judgment_log(market2, _judgment(action="SKIP", market_question="New question?"))
    async with aiosqlite.connect(ppt.DB_PATH) as db:
        cur2 = await db.execute("SELECT COUNT(*) FROM polymarket_judgment_log WHERE event_slug = ?", ("fed-decision",))
        count = (await cur2.fetchone())[0]
    assert count == 2


@pytest.mark.asyncio
async def test_recently_judged_scoped_per_question_not_just_event_slug(tmp_db):
    """Item #195 (29/07), the real bug: an event can hold MANY distinct
    markets sharing the same event_slug (e.g. "what-price-will-ethereum-
    hit" held 33 price-threshold questions) -- judging ONE must never cool
    down the OTHERS. has_open_position already got this right; this locks
    the same invariant into recently_judged/polymarket_judgment_log."""
    eth_2000 = _market(event_slug="eth-price-targets", question="Will ETH hit $2000?")
    eth_3000 = _market(event_slug="eth-price-targets", question="Will ETH hit $3000?")
    await ppt.save_judgment_log(eth_2000, _judgment(action="SKIP", market_question=eth_2000.question))

    assert await ppt.recently_judged("eth-price-targets", "Will ETH hit $2000?") is True
    assert await ppt.recently_judged("eth-price-targets", "Will ETH hit $3000?") is False


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

    assert await ppt.recently_judged("fed-decision", market.question) is True


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


# ── format_bet_alert (Items #225/#226, 30/07) ───────────────────────────────

def _fake_position(**overrides) -> dict:
    pos = {
        "side": "NO",
        "question": "Will Company D be the largest company in the world by market cap on July 31?",
        "entry_price": 0.5,
        "size_usd": 4512.5,
        "aria_probability_at_entry": 0.07,
        "market_probability_at_entry": 0.5,
        "win_probability_at_entry": 0.93,
        "reasoning": "Over the last 15 years, only Apple/Microsoft/Nvidia held the top spot.",
        "market_slug": "will-company-d-be-the-largest-company-in-the-world-by-market-cap-on-july-31-20260624192329852",
    }
    pos.update(overrides)
    return pos


def test_format_bet_alert_includes_polymarket_link():
    """Item #225 (30/07), operator request ("il me faut le lien sur
    polymarket"): verified empirically (curl HEAD) that /market/{market_slug}
    -- not /event/{market_slug} -- is the real, working link to the EXACT
    market a bet was placed on."""
    msg = ppt.format_bet_alert(_fake_position())
    assert (
        "https://polymarket.com/market/"
        "will-company-d-be-the-largest-company-in-the-world-by-market-cap-on-july-31-20260624192329852" in msg
    )


def test_format_bet_alert_omits_link_when_market_slug_missing():
    """A position booked before the market_slug column existed degrades
    honestly -- no link, never a broken/fabricated URL."""
    msg = ppt.format_bet_alert(_fake_position(market_slug=None))
    assert "polymarket.com/market/" not in msg


def test_format_bet_alert_includes_win_probability():
    """Item #226 (30/07), operator request ("je veux la probabilité de
    réussite du paris aussi"): win_probability_at_entry is ARIA's own
    estimated P(the SIDE SHE BET ON wins) -- already computed and persisted,
    but never shown on this alert before this fix."""
    msg = ppt.format_bet_alert(_fake_position())
    assert "Probabilité de réussite du pari : 93.0%" in msg


def test_format_bet_alert_omits_win_probability_when_unresolved():
    msg = ppt.format_bet_alert(_fake_position(win_probability_at_entry=None))
    assert "Probabilité de réussite" not in msg


# Item #244 (30/07), operator request ("je veut aussi la date de fin du
# paris") -- verified live against the real Polymarket API on the exact
# event these paper positions are booked against
# ("largest-company-end-of-july-...", endDate "2026-07-31T23:59:00Z").


def test_format_end_date_formats_iso_string_to_french_date():
    assert ppt.format_end_date("2026-07-31T23:59:00Z") == "31/07/2026"


def test_format_end_date_none_degrades_honestly():
    assert ppt.format_end_date(None) == "date inconnue"


def test_format_end_date_unparseable_degrades_honestly():
    assert ppt.format_end_date("not-a-real-date") == "date inconnue"


def test_format_bet_alert_includes_end_date():
    msg = ppt.format_bet_alert(_fake_position(end_date="2026-07-31T23:59:00Z"))
    assert "Date de fin : 31/07/2026" in msg


def test_format_bet_alert_end_date_unknown_when_missing():
    msg = ppt.format_bet_alert(_fake_position())
    assert "Date de fin : date inconnue" in msg
