"""Drawdown circuit breaker for the Polymarket paper portfolio (Item #109,
26/07) -- same test shape as test_risk_guard.py's own portfolio circuit
breaker section, adapted to polymarket_paper_trader.py's separate pocket."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import pytest

from aria_core import outgoing_pause, polymarket_paper_trader as ppt, polymarket_risk_guard as prg
from aria_core.paths import configure_data_dir


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    monkeypatch.setattr(ppt, "DB_PATH", str(tmp_path / "polymarket_paper.db"))
    return tmp_path


async def _insert_closed_position(*, size_usd: float, pnl_usd: float, question: str) -> None:
    """Direct SQL insert of an already-resolved position -- simulating a real
    win/loss via ppt.open_bet/check_resolutions would require mocking the
    order book AND the resolution client per position; a circuit-breaker
    test only cares about the resulting equity/pnl history, not how a
    position got there."""
    await ppt._ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(ppt.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO polymarket_paper_position (
                event_slug, event_title, question, side, yes_token_id, no_token_id,
                entry_price, size_usd, shares, opened_at, status, resolution_price,
                closed_at, pnl_usd
            ) VALUES (?, ?, ?, 'YES', 'yes-tok', 'no-tok', 0.5, ?, ?, ?, 'closed', 0.0, ?, ?)
            """,
            (question, question, question, size_usd, size_usd / 0.5, now, now, pnl_usd),
        )
        await db.commit()


# ── new_bet_block_status / block_new_bets / resume_new_bets ────────────────────────

class TestNewBetBlockState:
    def test_default_not_blocked(self, tmp_db):
        blocked, reason = prg.blocks_new_bets()
        assert blocked is False
        assert reason is None

    def test_block_then_resume(self, tmp_db):
        prg.block_new_bets("drawdown 22%", by=999)
        blocked, reason = prg.blocks_new_bets()
        assert blocked is True
        assert "drawdown 22%" in reason

        prg.resume_new_bets(by=999)
        blocked, reason = prg.blocks_new_bets()
        assert blocked is False
        assert reason is None

    def test_state_persists_on_disk_separate_file_from_risk_guard_and_outgoing_pause(self, tmp_db):
        prg.block_new_bets("test")
        state_file = tmp_db / "polymarket_risk_guard_state.json"
        assert state_file.exists()
        assert not (tmp_db / "risk_guard_state.json").exists()
        assert not (tmp_db / "pause_state.json").exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["blocked"] is True

    def test_corrupt_file_fails_closed(self, tmp_db):
        (tmp_db / "polymarket_risk_guard_state.json").write_text("{ not valid json", encoding="utf-8")
        blocked, reason = prg.blocks_new_bets()
        assert blocked is True
        assert "illisible" in reason.lower() or "corrompu" in reason.lower()

    def test_never_confused_with_outgoing_pause(self, tmp_db):
        outgoing_pause.pause(by=1, reason="stop opérateur")
        blocked, reason = prg.blocks_new_bets()
        assert blocked is True
        assert "pause globale" in reason.lower()

        outgoing_pause.resume(by=1)
        assert prg.blocks_new_bets() == (False, None)

        pause_before = (tmp_db / "pause_state.json").read_text(encoding="utf-8")
        prg.block_new_bets("drawdown")
        assert (tmp_db / "pause_state.json").read_text(encoding="utf-8") == pause_before
        assert outgoing_pause.is_paused() is False


# ── evaluate_portfolio_risk (intégration polymarket_paper_trader) ──────────────────

class TestEvaluatePortfolioRisk:
    @pytest.mark.asyncio
    async def test_no_drawdown_normal_state(self, tmp_db):
        state = await prg.evaluate_portfolio_risk()
        assert state.equity == ppt.STARTING_CAPITAL_USD
        assert state.high_water_mark == ppt.STARTING_CAPITAL_USD
        assert state.drawdown_pct == 0.0
        assert state.alloc_multiplier == 1.0
        assert state.blocked is False
        assert state.newly_triggered_soft is False
        assert state.newly_triggered_hard is False

    @pytest.mark.asyncio
    async def test_soft_drawdown_halves_new_bet_alloc(self, tmp_db):
        # 12% de perte depuis le capital de départ (100k) -> palier souple (>=10%, <20%).
        await _insert_closed_position(size_usd=12_000.0, pnl_usd=-12_000.0, question="q1")

        state = await prg.evaluate_portfolio_risk()
        assert round(state.drawdown_pct, 2) == 0.12
        assert state.alloc_multiplier == prg.SOFT_ALLOC_MULTIPLIER
        assert state.blocked is False
        assert state.newly_triggered_soft is True

        state2 = await prg.evaluate_portfolio_risk()
        assert state2.newly_triggered_soft is False
        assert state2.alloc_multiplier == prg.SOFT_ALLOC_MULTIPLIER

    @pytest.mark.asyncio
    async def test_hard_drawdown_blocks_new_bets_until_manual_resume(self, tmp_db):
        # 25% de perte depuis le capital de départ (100k) -> palier dur (>=20%).
        await _insert_closed_position(size_usd=25_000.0, pnl_usd=-25_000.0, question="q1")

        state = await prg.evaluate_portfolio_risk()
        assert state.drawdown_pct >= prg.HARD_DRAWDOWN_PCT
        assert state.blocked is True
        assert state.newly_triggered_hard is True

        state2 = await prg.evaluate_portfolio_risk()
        assert state2.blocked is True
        assert state2.newly_triggered_hard is False

        # Reprise JAMAIS automatique, même si l'équité (hypothétiquement) remontait.
        prg.resume_new_bets(by=1)
        blocked, _ = prg.blocks_new_bets()
        assert blocked is False

    @pytest.mark.asyncio
    async def test_five_consecutive_losses_blocks_regardless_of_drawdown_pct(self, tmp_db):
        # Petites pertes (1% du capital chacune) -> drawdown cumulé faible, mais 5 pertes
        # d'affilée doivent quand même armer le palier dur.
        for i in range(5):
            await _insert_closed_position(size_usd=1_000.0, pnl_usd=-500.0, question=f"q{i}")

        state = await prg.evaluate_portfolio_risk()
        assert state.consecutive_losses == 5
        assert state.blocked is True

    @pytest.mark.asyncio
    async def test_high_water_mark_persists_across_calls(self, tmp_db):
        # Un gain fait monter le plus-haut d'équité -- persisté sur polymarket_paper_state,
        # jamais sur paper_state (le portefeuille momentum).
        await _insert_closed_position(size_usd=10_000.0, pnl_usd=20_000.0, question="q1")

        state = await prg.evaluate_portfolio_risk()
        assert state.equity == ppt.STARTING_CAPITAL_USD + 20_000.0
        assert state.high_water_mark == state.equity
        assert await ppt.get_equity_high_water_mark() == state.equity


# ── câblage réel dans run_polymarket_paper_cycle ────────────────────────────────────

class TestCycleWiring:
    @pytest.mark.asyncio
    async def test_cycle_never_opens_a_bet_when_hard_blocked(self, tmp_db, monkeypatch):
        monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
        await _insert_closed_position(size_usd=25_000.0, pnl_usd=-25_000.0, question="q1")

        from aria_core.services.polymarket import PolymarketCandidateMarket

        async def fake_list_liquid_events(self, **kwargs):
            return [
                PolymarketCandidateMarket(
                    event_title="e", event_slug="new-evt", question="Will Y happen?",
                    yes_token_id="y", no_token_id="n", yes_price=0.5, volume_usd=100_000.0,
                    liquidity_usd=50_000.0, end_date="2026-08-15T00:00:00Z",
                )
            ]

        # Patched on the CLASS, never the singleton instance -- monkeypatch
        # on an instance leaves a permanent residual instance attribute
        # after teardown (getattr on the instance resolves to the class's
        # bound method BEFORE the patch, so monkeypatch's teardown restores
        # it as an instance attribute that then masks the class for every
        # OTHER test in the session -- same trap already documented in
        # test_momentum_entry.py's _stub_polymarket_unavailable fixture).
        monkeypatch.setattr(
            "aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events,
        )

        called = {"judged": False}

        async def fake_estimate(market):
            called["judged"] = True
            raise AssertionError("must never be reached -- circuit breaker should stop the cycle first")

        monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

        result = await ppt.run_polymarket_paper_cycle()

        assert result["opened"] == []
        assert called["judged"] is False

    @pytest.mark.asyncio
    async def test_cycle_notifies_on_newly_triggered_hard_block(self, tmp_db, monkeypatch):
        monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
        await _insert_closed_position(size_usd=25_000.0, pnl_usd=-25_000.0, question="q1")

        notified = []

        async def notifier(msg):
            notified.append(msg)

        await ppt.run_polymarket_paper_cycle(notifier=notifier)

        assert any("DUR" in m for m in notified)

    @pytest.mark.asyncio
    async def test_cycle_halves_bet_size_on_soft_drawdown(self, tmp_db, monkeypatch):
        monkeypatch.setenv("ARIA_POLYMARKET_PAPER_ENABLED", "true")
        await _insert_closed_position(size_usd=12_000.0, pnl_usd=-12_000.0, question="q1")

        from aria_core.services.polymarket import PolymarketCandidateMarket, PolymarketOrderBook
        from aria_core.skills.polymarket_thesis import PolymarketJudgment

        market = PolymarketCandidateMarket(
            event_title="e", event_slug="new-evt", question="Will Y happen?",
            yes_token_id="y", no_token_id="n", yes_price=0.5, volume_usd=100_000.0,
            liquidity_usd=50_000.0, end_date="2026-08-15T00:00:00Z",
        )

        async def fake_list_liquid_events(self, **kwargs):
            return [market]

        # Patched on the CLASS -- see the comment in
        # test_cycle_never_opens_a_bet_when_hard_blocked above.
        monkeypatch.setattr(
            "aria_core.services.polymarket.PolymarketClient.list_liquid_events", fake_list_liquid_events,
        )

        async def fake_estimate(m):
            return PolymarketJudgment(
                market_question=m.question, market_probability=0.5, aria_probability=0.9,
                vote_spread=0.05, edge=0.4, side="YES", win_probability=0.9,
                reasoning="r", action="BET",
            )

        monkeypatch.setattr(ppt, "estimate_market_probability", fake_estimate)

        async def fake_get_order_book(self, token_id):
            return PolymarketOrderBook(available=True, best_bid=0.48, best_ask=0.5, spread=0.02)

        monkeypatch.setattr(
            "aria_core.services.polymarket.PolymarketClient.get_order_book", fake_get_order_book,
        )

        # Sizing sans coupe-circuit (multiplicateur 1.0), pour comparaison directe.
        equity = await ppt.cash_available()
        judgment = await fake_estimate(market)
        full_size = ppt.compute_bet_size(judgment, 0.5, equity, alloc_multiplier=1.0)

        result = await ppt.run_polymarket_paper_cycle()

        assert len(result["opened"]) == 1
        assert result["opened"][0]["size_usd"] == pytest.approx(full_size * prg.SOFT_ALLOC_MULTIPLIER, rel=0.01)


class TestPortfolioReportSurfacesTheCircuitBreaker:
    @pytest.mark.asyncio
    async def test_report_shows_nothing_when_not_blocked(self, tmp_db):
        report = await ppt.format_portfolio_report()
        assert "Coupe-circuit" not in report

    @pytest.mark.asyncio
    async def test_report_shows_the_block_reason_when_armed(self, tmp_db):
        prg.block_new_bets("drawdown 22% (Polymarket)")
        report = await ppt.format_portfolio_report()
        assert "Coupe-circuit armé" in report
        assert "drawdown 22%" in report
