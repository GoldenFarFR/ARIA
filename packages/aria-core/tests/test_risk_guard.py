"""Gestion du risque portefeuille (#186) -- sizing ajusté au risque (fonction pure) +
coupe-circuit de drawdown (état persisté, fichier dédié, distinct d'outgoing_pause)."""
from __future__ import annotations

import json

import pytest

from aria_core import outgoing_pause, paper_trader as pt, risk_guard
from aria_core.paths import configure_data_dir

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    return tmp_path


# ── 1. size_position_by_risk (fonction pure) ────────────────────────────────


class TestSizePositionByRisk:
    def test_within_cap_unchanged(self):
        # entrée=2.0, invalidation=1.5 -> risque 25 % ; alloc 50k * 25 % = 12.5k <= cap
        # (2 % * 1M = 20k) -> inchangé.
        alloc = risk_guard.size_position_by_risk(50_000.0, 2.0, 1.5, 1_000_000.0)
        assert alloc == 50_000.0

    def test_wide_stop_reduced_to_cap(self):
        # entrée=1.0, invalidation=0.5 -> risque 50 % ; alloc 50k * 50 % = 25k > cap 20k
        # -> réduit pour que risked_usd retombe exactement à cap_usd (20k / 0.5 = 40k).
        alloc = risk_guard.size_position_by_risk(50_000.0, 1.0, 0.5, 1_000_000.0)
        assert round(alloc) == 40_000

    def test_never_increases_above_original(self):
        """Un stop très serré ne doit JAMAIS gonfler alloc_usd au-delà de sa valeur
        d'entrée -- c'est un plafond, jamais un bonus."""
        alloc = risk_guard.size_position_by_risk(10_000.0, 2.0, 1.99, 1_000_000.0)
        assert alloc == 10_000.0

    def test_no_invalidation_unchanged(self):
        assert risk_guard.size_position_by_risk(50_000.0, 1.0, None, 1_000_000.0) == 50_000.0

    def test_invalidation_at_or_above_entry_unchanged(self):
        """Donnée incohérente (invalidation >= entrée, risque non mesurable) -- pas de cap."""
        assert risk_guard.size_position_by_risk(50_000.0, 1.0, 1.0, 1_000_000.0) == 50_000.0
        assert risk_guard.size_position_by_risk(50_000.0, 1.0, 1.5, 1_000_000.0) == 50_000.0

    def test_zero_or_negative_inputs_unchanged(self):
        assert risk_guard.size_position_by_risk(0.0, 1.0, 0.5, 1_000_000.0) == 0.0
        assert risk_guard.size_position_by_risk(50_000.0, 0.0, 0.5, 1_000_000.0) == 50_000.0
        assert risk_guard.size_position_by_risk(50_000.0, 1.0, 0.5, 0.0) == 50_000.0

    def test_exactly_at_cap_boundary_unchanged(self):
        # risque 20 % -> risked = 50k*0.2 = 10k == cap(1M*0.02=20k)? non, 10k < 20k -> inchangé.
        alloc = risk_guard.size_position_by_risk(50_000.0, 1.0, 0.8, 1_000_000.0)
        assert alloc == 50_000.0


# ── 1bis. conviction_size_multiplier (18/07, "plus agressive" = plus gros sur les
#          MEILLEURS setups, pas plus gros partout) ─────────────────────────────────

class TestConvictionSizeMultiplier:
    def test_exceptional_setup_gets_boosted(self):
        mult = risk_guard.conviction_size_multiplier(2.5, 3)
        assert mult == risk_guard.CONVICTION_SIZE_MULTIPLIER

    def test_above_threshold_still_boosted(self):
        mult = risk_guard.conviction_size_multiplier(4.0, 3)
        assert mult == risk_guard.CONVICTION_SIZE_MULTIPLIER

    def test_correct_but_not_exceptional_stays_default(self):
        """R/R correct mais pas exceptionnel -- jamais un bonus sans les DEUX conditions."""
        assert risk_guard.conviction_size_multiplier(2.0, 3) == 1.0
        assert risk_guard.conviction_size_multiplier(2.5, 2) == 1.0
        assert risk_guard.conviction_size_multiplier(1.5, 3) == 1.0

    def test_missing_data_defaults_to_baseline(self):
        """Jamais un bonus sans preuve du signal -- absence de donnée -> 1.0, pas un
        multiplicateur inventé."""
        assert risk_guard.conviction_size_multiplier(None, 3) == 1.0
        assert risk_guard.conviction_size_multiplier(2.5, None) == 1.0
        assert risk_guard.conviction_size_multiplier(None, None) == 1.0

    def test_never_reduces_below_baseline(self):
        assert risk_guard.conviction_size_multiplier(0.1, 0) == 1.0


# ── 1ter. weekly_pacing_size_multiplier (18/07, "frein à main" déterministe validé
#          après revue croisée -- jamais un LLM, jamais 0 %) ───────────────────────────

class TestWeeklyPacingSizeMultiplier:
    def test_objective_already_reached_dampens_by_half(self):
        ctx = {"equity": 1_100_000.0, "target_equity": 1_100_000.0}
        assert risk_guard.weekly_pacing_size_multiplier(ctx) == risk_guard.WEEKLY_PACING_DAMPENING_MULTIPLIER
        assert risk_guard.weekly_pacing_size_multiplier(ctx) == 0.5

    def test_objective_exceeded_still_dampens_never_zero(self):
        ctx = {"equity": 1_200_000.0, "target_equity": 1_100_000.0}
        mult = risk_guard.weekly_pacing_size_multiplier(ctx)
        assert mult == 0.5
        assert mult > 0.0  # jamais 0 % -- le marché ne sait pas qu'on "a fait sa semaine"

    def test_objective_not_yet_reached_stays_default(self):
        ctx = {"equity": 1_050_000.0, "target_equity": 1_100_000.0}
        assert risk_guard.weekly_pacing_size_multiplier(ctx) == 1.0

    def test_missing_context_defaults_to_baseline(self):
        assert risk_guard.weekly_pacing_size_multiplier(None) == 1.0
        assert risk_guard.weekly_pacing_size_multiplier({}) == 1.0
        assert risk_guard.weekly_pacing_size_multiplier({"equity": 1_100_000.0}) == 1.0
        assert risk_guard.weekly_pacing_size_multiplier({"target_equity": 1_100_000.0}) == 1.0

    def test_composes_with_conviction_multiplier_as_expected(self):
        """Le cas décrit par la revue : 8 % (conviction) -> 4 %, 5 % (défaut) -> 2.5 %."""
        conviction = risk_guard.conviction_size_multiplier(3.0, 3)  # setup exceptionnel
        pacing = risk_guard.weekly_pacing_size_multiplier(
            {"equity": 1_100_000.0, "target_equity": 1_100_000.0}
        )
        assert round(0.05 * conviction * pacing, 4) == 0.04
        assert round(0.05 * 1.0 * pacing, 4) == 0.025


# ── 2. Coupe-circuit dédié : persistance, robustesse, distinction avec outgoing_pause ──


class TestNewEntryBlockState:
    def test_default_not_blocked(self, tmp_db):
        blocked, reason = risk_guard.blocks_new_entries()
        assert blocked is False
        assert reason is None

    def test_block_then_resume(self, tmp_db):
        risk_guard.block_new_entries("drawdown 22%", by=999)
        blocked, reason = risk_guard.blocks_new_entries()
        assert blocked is True
        assert "drawdown 22%" in reason

        risk_guard.resume_new_entries(by=999)
        blocked, reason = risk_guard.blocks_new_entries()
        assert blocked is False
        assert reason is None

    def test_state_persists_on_disk_separate_file_from_outgoing_pause(self, tmp_db):
        risk_guard.block_new_entries("test")
        state_file = tmp_db / "risk_guard_state.json"
        assert state_file.exists()
        assert not (tmp_db / "pause_state.json").exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["blocked"] is True

    def test_corrupt_file_fails_closed(self, tmp_db):
        """Doctrine argent : contrairement à outgoing_pause (fail-open jobs), ce coupe-circuit
        est TOUJOURS money-adjacent -> fail-closed sur corruption, jamais fail-open."""
        (tmp_db / "risk_guard_state.json").write_text("{ not valid json", encoding="utf-8")
        blocked, reason = risk_guard.blocks_new_entries()
        assert blocked is True
        assert "illisible" in reason.lower() or "corrompu" in reason.lower()

    def test_never_confused_with_outgoing_pause(self, tmp_db):
        """outgoing_pause actif bloque aussi les nouvelles entrées paper (respecté), mais la
        raison rapportée distingue clairement les deux mécanismes -- jamais confondus."""
        outgoing_pause.pause(by=1, reason="stop opérateur")
        blocked, reason = risk_guard.blocks_new_entries()
        assert blocked is True
        assert "pause globale" in reason.lower()

        outgoing_pause.resume(by=1)
        assert risk_guard.blocks_new_entries() == (False, None)

        # Le coupe-circuit dédié, lui, reste indépendant : l'armer ne touche jamais
        # l'état d'outgoing_pause (fichier séparé, jamais modifié par risk_guard).
        pause_before = (tmp_db / "pause_state.json").read_text(encoding="utf-8")
        risk_guard.block_new_entries("drawdown")
        assert (tmp_db / "pause_state.json").read_text(encoding="utf-8") == pause_before
        assert outgoing_pause.is_paused() is False


# ── 3. evaluate_portfolio_risk (intégration paper_trader) ──────────────────


class TestEvaluatePortfolioRisk:
    @pytest.mark.asyncio
    async def test_no_drawdown_normal_state(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        state = await risk_guard.evaluate_portfolio_risk()
        assert state.equity == 1_000_000.0
        assert state.high_water_mark == 1_000_000.0
        assert state.drawdown_pct == 0.0
        assert state.alloc_multiplier == 1.0
        assert state.blocked is False
        assert state.newly_triggered_soft is False
        assert state.newly_triggered_hard is False

    @pytest.mark.asyncio
    async def test_high_water_mark_tracks_new_peak(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=100_000)

        async def price_lookup(contract):
            return 2.0  # +100k de valeur latente -> équité 1.1M, nouveau plus haut

        state = await risk_guard.evaluate_portfolio_risk(price_lookup=price_lookup)
        assert round(state.equity) == 1_100_000
        assert state.high_water_mark == state.equity
        assert await pt.get_equity_high_water_mark() == state.equity

    @pytest.mark.asyncio
    async def test_soft_drawdown_halves_new_entry_alloc(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=100_000)
        await pt.close_position(A, 1.0)  # HWM = 1M, équité toujours 1M

        # Nouvelle perte qui creuse un drawdown de 12% depuis le plus haut (1M).
        await pt.open_position(B, "BBB", 1.0, alloc_usd=120_000)
        await pt.close_position(B, 0.001)  # quasi-perte totale des 120k -> équité ~880k, DD ~12%

        state = await risk_guard.evaluate_portfolio_risk()
        assert round(state.drawdown_pct, 2) == 0.12
        assert state.alloc_multiplier == risk_guard.SOFT_ALLOC_MULTIPLIER
        assert state.blocked is False
        assert state.newly_triggered_soft is True

        # Un second appel dans la même bande ne redéclenche pas la notif (évite le bruit).
        state2 = await risk_guard.evaluate_portfolio_risk()
        assert state2.newly_triggered_soft is False
        assert state2.alloc_multiplier == risk_guard.SOFT_ALLOC_MULTIPLIER

    @pytest.mark.asyncio
    async def test_hard_drawdown_blocks_new_entries_until_manual_resume(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000)
        await pt.close_position(A, 0.2)  # perte de 200k sur 250k -> équité 800k, DD 20%

        state = await risk_guard.evaluate_portfolio_risk()
        assert state.drawdown_pct >= risk_guard.HARD_DRAWDOWN_PCT
        assert state.blocked is True
        assert state.newly_triggered_hard is True

        # Persisté : un nouvel appel confirme toujours bloqué, sans re-déclencher la notif.
        state2 = await risk_guard.evaluate_portfolio_risk()
        assert state2.blocked is True
        assert state2.newly_triggered_hard is False

        # Reprise JAMAIS automatique : même si l'équité remonte, ça reste bloqué tant que
        # resume_new_entries n'a pas été appelé explicitement.
        async def recovered_price(contract):
            return 10.0  # équité largement remontée

        state3 = await risk_guard.evaluate_portfolio_risk(price_lookup=recovered_price)
        assert state3.blocked is True

        risk_guard.resume_new_entries(by=1)
        blocked, _ = risk_guard.blocks_new_entries()
        assert blocked is False

    @pytest.mark.asyncio
    async def test_five_consecutive_losses_blocks_regardless_of_drawdown_pct(self, tmp_db):
        await pt.reset_portfolio(10_000_000.0)  # capital large : le drawdown % reste faible
        for i, contract in enumerate([A, B, C, "0x" + "d" * 40, "0x" + "e" * 40]):
            await pt.open_position(contract, f"T{i}", 1.0, alloc_usd=1_000)
            await pt.close_position(contract, 0.5, reason="perte")  # petite perte à chaque fois

        state = await risk_guard.evaluate_portfolio_risk()
        assert state.consecutive_losses == 5
        assert state.blocked is True
        assert "pertes consécutives" in (state.blocked_reason or "")

    @pytest.mark.asyncio
    async def test_win_breaks_consecutive_loss_streak(self, tmp_db):
        await pt.reset_portfolio(10_000_000.0)
        for i, contract in enumerate([A, B, C]):
            await pt.open_position(contract, f"T{i}", 1.0, alloc_usd=1_000)
            await pt.close_position(contract, 0.5, reason="perte")
        # Un gain interrompt la série -- plus récent en premier (ORDER BY closed_at DESC).
        win_contract = "0x" + "f" * 40
        await pt.open_position(win_contract, "WIN", 1.0, alloc_usd=1_000)
        await pt.close_position(win_contract, 2.0, reason="gain")

        state = await risk_guard.evaluate_portfolio_risk()
        assert state.consecutive_losses == 0
        assert state.blocked is False


# ── 4. Câblage open_position/run_paper_cycle ────────────────────────────────


class TestWiredIntoPaperTrader:
    @pytest.mark.asyncio
    async def test_open_position_refuses_when_blocked(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        risk_guard.block_new_entries("test hard block")
        pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000)
        assert pos is None

    @pytest.mark.asyncio
    async def test_open_position_applies_risk_cap(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        # invalidation à 50% de l'entrée -> alloc 50k plafonnée à 40k (cf. TestSizePositionByRisk).
        pos = await pt.open_position(A, "AAA", 1.0, invalidation_price=0.5, alloc_usd=50_000)
        assert pos is not None
        assert round(pos["cost_usd"]) == 40_000

    @pytest.mark.asyncio
    async def test_run_paper_cycle_skips_new_entries_when_hard_blocked(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        risk_guard.block_new_entries("test")

        async def analyzer(contract):
            return {"action": "BUY", "symbol": "X", "price": 1.0, "target": 2.0, "invalidation": 0.5}

        async def price_lookup(contract):
            return 1.0

        act = await pt.run_paper_cycle(candidates=[A], analyzer=analyzer, price_lookup=price_lookup)
        assert act["opened"] == []
        assert act["risk_state"].blocked is True

    @pytest.mark.asyncio
    async def test_run_paper_cycle_notifies_on_hard_trigger(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000)
        await pt.close_position(A, 0.2, reason="perte")  # DD 20% -> déclenche le palier dur

        alerts: list[str] = []

        async def notifier(msg):
            alerts.append(msg)

        async def price_lookup(contract):
            return 1.0

        act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup, notifier=notifier)
        assert act["risk_state"].newly_triggered_hard is True
        assert any("palier DUR" in a for a in alerts)

    @pytest.mark.asyncio
    async def test_run_paper_cycle_still_manages_open_positions_when_blocked(self, tmp_db):
        """Coupe-circuit dur armé -> aucune NOUVELLE entrée, mais les positions déjà
        ouvertes continuent d'être gérées par leur propre stop/take-profit."""
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(B, "BBB", 1.0, invalidation_price=0.5, alloc_usd=10_000)
        risk_guard.block_new_entries("test")

        async def price_lookup(contract):
            return 0.4  # sous l'invalidation -> doit se fermer normalement

        act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
        assert len(act["closed"]) == 1
        assert not await pt.has_open(B)
