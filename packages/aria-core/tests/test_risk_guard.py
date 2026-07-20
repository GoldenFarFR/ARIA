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
#          MEILLEURS setups, pas plus gros partout ; redesign 3 paliers 19/07, feedback
#          opérateur direct : "l'achat maxi doit etre de 5% et mini de 2%") ────────────

class TestConvictionSizeMultiplier:
    def test_strong_setup_gets_max_tier(self):
        mult = risk_guard.conviction_size_multiplier(2.5, 3)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_above_threshold_still_max_tier(self):
        """Un R/R énorme (ex. 20+) ne dépasse jamais le plafond dur à 5 % -- le palier
        FORT est un plafond, pas une échelle sans fin proportionnelle au R/R brut."""
        mult = risk_guard.conviction_size_multiplier(20.0, 3)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_moderate_tier_between_direct_buy_floor_and_strong_threshold(self):
        """R/R >= 2.0 (plancher d'achat direct) mais sous 2.5 (palier fort), ou
        alignement insuffisant pour le palier fort -- palier MODÉRÉ (3.5 %), jamais le
        palier fort (5 %) ni le plancher faible (2 %)."""
        assert risk_guard.conviction_size_multiplier(2.0, 3) == risk_guard.MODERATE_ALLOC_MULTIPLIER
        # 19/07 -- seuil d'alignement abaissé à 2 (décision opérateur) : align_score=1
        # ne qualifie plus pour le palier fort même à R/R élevé -- retombe en modéré.
        assert risk_guard.conviction_size_multiplier(2.5, 1) == risk_guard.MODERATE_ALLOC_MULTIPLIER

    def test_weak_tier_below_direct_buy_floor(self):
        """R/R sous le plancher d'achat direct (2.0, typiquement un achat confirmé par
        LLM sur un R/R plus faible) -- palier FAIBLE (2 %), le plancher dur."""
        assert risk_guard.conviction_size_multiplier(1.5, 3) == risk_guard.MIN_ALLOC_MULTIPLIER
        assert risk_guard.conviction_size_multiplier(0.1, 0) == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_two_of_three_alignment_now_qualifies_for_strong_tier(self):
        """19/07 -- seuil abaissé de 3 à 2 (décision opérateur, via AskUserQuestion) :
        align_score=2 (MACD + pattern de bougie, sans EMA -- le cas réel observé sur les
        5 premiers trades momentum) qualifie désormais pour le palier fort."""
        assert risk_guard.conviction_size_multiplier(2.5, 2) == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_missing_data_defaults_to_max_tier(self):
        """Comportement INCHANGÉ pour tout appelant qui ne fournit pas rr/align_score
        (ex. l'ancien pilote VC-thesis, dormant) -- jamais réduit sous ce qu'il avait
        avant ce chantier. Seul le pipeline momentum (qui fournit toujours ces deux
        champs sur un BUY) est concerné par le nouveau plafond/plancher."""
        assert risk_guard.conviction_size_multiplier(None, 3) == risk_guard.MAX_ALLOC_MULTIPLIER
        assert risk_guard.conviction_size_multiplier(2.5, None) == risk_guard.MAX_ALLOC_MULTIPLIER
        assert risk_guard.conviction_size_multiplier(None, None) == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_never_goes_below_min_tier(self):
        """Le plancher (2 %) est un vrai plancher -- aucune combinaison de R/R/alignement
        mesurés (même un R/R négatif/nul, défensif) ne descend en dessous."""
        assert risk_guard.conviction_size_multiplier(0.1, 0) == risk_guard.MIN_ALLOC_MULTIPLIER
        assert risk_guard.conviction_size_multiplier(-1.0, 0) == risk_guard.MIN_ALLOC_MULTIPLIER
        assert risk_guard.conviction_size_multiplier(0.0, 0) == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_never_exceeds_max_tier(self):
        """Le plafond (5 %) est un vrai plafond -- aucune combinaison ne le dépasse,
        c'est précisément le point du feedback opérateur ("maxi doit etre de 5%")."""
        for rr in (2.5, 5.0, 20.0, 100.0):
            assert risk_guard.conviction_size_multiplier(rr, 3) <= risk_guard.MAX_ALLOC_MULTIPLIER


# ── 1ter. fundamental_score (19/07, décision opérateur "s'ajoute en ET") ────────────

class TestConvictionSizeMultiplierFundamental:
    def test_backward_compatible_no_fundamental_arg(self):
        """Aucun appelant existant ne passe fundamental_score -- comportement
        identique à avant ce chantier."""
        assert risk_guard.conviction_size_multiplier(2.5, 3) == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_unknown_fundamental_never_blocks_technical_bonus(self):
        """Fail-open sur inconnu (None) : recherche non menée/indisponible -- jamais
        réduit sous ce que le setup technique seul aurait eu."""
        mult = risk_guard.conviction_size_multiplier(2.5, 3, fundamental_score=None)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_strong_fundamental_keeps_technical_bonus(self):
        mult = risk_guard.conviction_size_multiplier(2.5, 3, fundamental_score=8.0)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_confirmed_weak_fundamental_downgrades_to_moderate(self):
        """Fail-closed sur une donnée CONFIRMÉE mauvaise (pas juste inconnue) : le
        potentiel fondamental contredit activement la conviction technique -- rétrograde
        au palier MODÉRÉ (jamais directement au plancher FAIBLE, la conviction technique
        reste réelle, seul le bonus maximal est refusé)."""
        mult = risk_guard.conviction_size_multiplier(2.5, 3, fundamental_score=2.0)
        assert mult == risk_guard.MODERATE_ALLOC_MULTIPLIER

    def test_fundamental_exactly_at_threshold_still_downgrades(self):
        below = risk_guard.FUNDAMENTAL_WEAK_THRESHOLD - 0.01
        mult = risk_guard.conviction_size_multiplier(2.5, 3, fundamental_score=below)
        assert mult == risk_guard.MODERATE_ALLOC_MULTIPLIER

    def test_weak_fundamental_never_creates_a_bonus_on_mediocre_technical(self):
        """Le fondamental ne peut JAMAIS déclencher un meilleur palier seul -- il ne
        s'applique QUE dans le garde du palier fort, jamais pour un setup qui n'a même
        pas atteint ce palier techniquement (retombe simplement en palier faible,
        indépendamment du fondamental)."""
        mult = risk_guard.conviction_size_multiplier(1.0, 1, fundamental_score=10.0)
        assert mult == risk_guard.MIN_ALLOC_MULTIPLIER


# ── 1quinquies. conviction_size_multiplier + volume_confirmed (19/07, revue croisée
#                Gemini -- malus de conviction sur RVOL indisponible) ──────────────────


class TestConvictionSizeMultiplierVolume:
    def test_backward_compatible_no_volume_arg(self):
        assert risk_guard.conviction_size_multiplier(2.5, 3) == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_none_never_downgrades(self):
        """Comportement historique (avant ce chantier) pour tout appelant qui ne
        fournit pas ce signal."""
        mult = risk_guard.conviction_size_multiplier(2.5, 3, volume_confirmed=None)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_true_never_downgrades(self):
        mult = risk_guard.conviction_size_multiplier(2.5, 3, volume_confirmed=True)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_false_downgrades_strong_tier_to_moderate(self):
        """Malus de conviction demandé par Gemini : RVOL non vérifiable (donnée
        absente) -> jamais le palier fort, même si R/R + alignement le mériteraient."""
        mult = risk_guard.conviction_size_multiplier(2.5, 3, volume_confirmed=False)
        assert mult == risk_guard.MODERATE_ALLOC_MULTIPLIER

    def test_false_never_creates_a_bonus_or_further_penalty_below_strong_tier(self):
        """Le malus ne s'applique QUE dans le garde du palier fort -- un setup déjà
        modéré ou faible n'est jamais rétrogradé davantage."""
        mult_moderate = risk_guard.conviction_size_multiplier(2.0, 3, volume_confirmed=False)
        assert mult_moderate == risk_guard.MODERATE_ALLOC_MULTIPLIER
        mult_weak = risk_guard.conviction_size_multiplier(1.0, 1, volume_confirmed=False)
        assert mult_weak == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_composes_with_fundamental_veto_stacks_to_weak_tier(self):
        """Les deux vétos EN MÊME TEMPS (fondamental faible ET volume non confirmé)
        cumulent jusqu'au palier FAIBLE (19/07, revue croisée Gemini round 5 -- deux
        drapeaux rouges indépendants = risque cumulé, jamais traité comme un seul)."""
        mult = risk_guard.conviction_size_multiplier(
            2.5, 3, fundamental_score=1.0, volume_confirmed=False,
        )
        assert mult == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_single_veto_still_only_reaches_moderate(self):
        """Un seul drapeau (fondamental faible SEUL, ou volume non confirmé SEUL) ->
        palier MODÉRÉ, jamais directement FAIBLE -- le cumul ne se déclenche que si
        les DEUX signaux d'alerte sont présents simultanément."""
        mult_fundamental_only = risk_guard.conviction_size_multiplier(
            2.5, 3, fundamental_score=1.0, volume_confirmed=True,
        )
        assert mult_fundamental_only == risk_guard.MODERATE_ALLOC_MULTIPLIER
        mult_volume_only = risk_guard.conviction_size_multiplier(
            2.5, 3, fundamental_score=10.0, volume_confirmed=False,
        )
        assert mult_volume_only == risk_guard.MODERATE_ALLOC_MULTIPLIER


# ── 1quater. sizing HYBRIDE risque-cible/ATR (20/07, revue croisée Gemini round 7,
#             go explicite opérateur) -- même tiering/cumul de vétos que
#             conviction_size_multiplier ci-dessus, mais en BUDGET DE RISQUE %,
#             divisé par la largeur ATR pour obtenir l'allocation $ -────────────────────


class TestConvictionRiskBudgetPct:
    def test_none_signal_returns_none(self):
        """Signale à l'appelant de retomber sur conviction_size_multiplier -- jamais
        un budget inventé faute de signal."""
        assert risk_guard.conviction_risk_budget_pct(None, None) is None
        assert risk_guard.conviction_risk_budget_pct(2.5, None) is None
        assert risk_guard.conviction_risk_budget_pct(None, 3) is None

    def test_strong_setup_gets_strong_budget(self):
        budget = risk_guard.conviction_risk_budget_pct(2.5, 3)
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_STRONG_PCT

    def test_moderate_setup_gets_moderate_budget(self):
        budget = risk_guard.conviction_risk_budget_pct(2.0, 3)
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_MODERATE_PCT

    def test_weak_setup_gets_weak_budget(self):
        budget = risk_guard.conviction_risk_budget_pct(1.0, 1)
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_WEAK_PCT

    def test_single_veto_downgrades_strong_to_moderate_budget(self):
        budget = risk_guard.conviction_risk_budget_pct(2.5, 3, fundamental_score=1.0)
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_MODERATE_PCT

    def test_both_vetoes_stack_to_weak_budget(self):
        """Même cumul que conviction_size_multiplier -- les DEUX drapeaux en même
        temps chutent au palier faible, jamais plafonnés à modéré."""
        budget = risk_guard.conviction_risk_budget_pct(
            2.5, 3, fundamental_score=1.0, volume_confirmed=False,
        )
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_WEAK_PCT


class TestSizeByRiskBudget:
    def test_wide_stop_reduces_allocation_vs_tight_stop(self):
        """Le coeur du correctif Gemini round 7 : à budget de risque IDENTIQUE, un
        stop plus large (token nerveux) doit réduire l'allocation par rapport à un
        stop plus serré (token calme) -- jamais la même allocation quelle que soit
        la volatilité."""
        wide = risk_guard.size_by_risk_budget(0.01, 0.35, 1_000_000.0)   # stop 35%
        tight = risk_guard.size_by_risk_budget(0.01, 0.08, 1_000_000.0)  # stop 8%
        assert wide < tight
        assert wide == pytest.approx(1_000_000.0 * 0.01 / 0.35)
        assert tight == pytest.approx(1_000_000.0 * 0.01 / 0.08)

    def test_ceiling_caps_the_allocation_never_grows_beyond_it(self):
        """Un budget de risque élevé sur un stop très serré peut donner une allocation
        brute énorme (0.015 / 0.05 = 30 % du capital) -- le plafond absolu doit
        toujours l'emporter, ce mécanisme ne fait jamais grossir une position au-delà
        du maximum historique."""
        raw = risk_guard.size_by_risk_budget(0.015, 0.05, 1_000_000.0)
        assert raw == pytest.approx(300_000.0)  # sans plafond, bien au-delà de l'ancien max
        capped = risk_guard.size_by_risk_budget(0.015, 0.05, 1_000_000.0, ceiling_usd=50_000.0)
        assert capped == pytest.approx(50_000.0)

    def test_ceiling_never_raises_an_already_smaller_allocation(self):
        """Le plafond ne relève JAMAIS une allocation déjà sous ce plafond -- un
        plafond, jamais un bonus."""
        result = risk_guard.size_by_risk_budget(0.005, 0.40, 1_000_000.0, ceiling_usd=50_000.0)
        assert result == pytest.approx(1_000_000.0 * 0.005 / 0.40)
        assert result < 50_000.0

    def test_zero_or_negative_trail_pct_or_capital_returns_zero(self):
        assert risk_guard.size_by_risk_budget(0.01, 0.0, 1_000_000.0) == 0.0
        assert risk_guard.size_by_risk_budget(0.01, -0.1, 1_000_000.0) == 0.0
        assert risk_guard.size_by_risk_budget(0.01, 0.15, 0.0) == 0.0


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
        """19/07 -- redesign 3 paliers (feedback opérateur : "maxi 5%, mini 2%") : le
        frein à main hebdo compose avec CHAQUE palier, jamais un cas isolé. Palier fort
        (5 %) -> 2.5 % ; palier modéré (3.5 %) -> 1.75 % ; palier faible (2 %) -> 1 %."""
        pacing = risk_guard.weekly_pacing_size_multiplier(
            {"equity": 1_100_000.0, "target_equity": 1_100_000.0}
        )
        strong = risk_guard.conviction_size_multiplier(3.0, 3)  # setup fort
        moderate = risk_guard.conviction_size_multiplier(2.0, 3)  # setup modéré
        weak = risk_guard.conviction_size_multiplier(1.0, 1)  # setup faible
        assert round(0.05 * strong * pacing, 4) == 0.025
        assert round(0.05 * moderate * pacing, 4) == 0.0175
        assert round(0.05 * weak * pacing, 4) == 0.01


# ── 1quater. regime_size_multiplier (20/07, Regime Switch, feu vert opérateur
#             explicite "200k mais à garder à l'œil") ──────────────────────────────

class TestRegimeSizeMultiplier:
    def test_fear_halves_the_allocation(self):
        assert risk_guard.regime_size_multiplier("peur") == risk_guard.REGIME_FEAR_SIZE_MULTIPLIER
        assert risk_guard.regime_size_multiplier("peur") == 0.5

    def test_neutral_and_euphoria_stay_at_baseline(self):
        assert risk_guard.regime_size_multiplier("neutre") == 1.0
        assert risk_guard.regime_size_multiplier("euphorie") == 1.0

    def test_missing_or_unknown_regime_defaults_to_baseline(self):
        assert risk_guard.regime_size_multiplier(None) == 1.0
        assert risk_guard.regime_size_multiplier("regime_inconnu") == 1.0

    def test_composes_with_conviction_and_pacing_multipliers(self):
        """Même patron que test_composes_with_conviction_multiplier_as_expected --
        les 3 multiplicateurs (conviction/pacing/régime) sont indépendants et
        composés multiplicativement, jamais l'un à la place de l'autre."""
        strong = risk_guard.conviction_size_multiplier(3.0, 3)
        pacing = risk_guard.weekly_pacing_size_multiplier(None)  # objectif pas encore atteint -> 1.0
        regime = risk_guard.regime_size_multiplier("peur")
        assert round(0.05 * strong * pacing * regime, 4) == 0.025


# ── 1quater. cap_alloc_to_price_impact (19/07, revue croisée Gemini) ────────────────


class TestCapAllocToPriceImpact:
    def test_negligible_impact_on_deep_pool_unchanged(self):
        """Un pool profond (100M$) face à une allocation modeste (20k$) -- impact
        estimé (0,04 %) bien trop faible pour jamais faire tomber le R/R dégradé
        sous le plancher -- alloc renvoyée inchangée."""
        alloc = risk_guard.cap_alloc_to_price_impact(20_000.0, 1.0, 1.5, 0.9, 100_000_000.0)
        assert alloc == 20_000.0

    def test_shrinks_on_thin_pool_matches_hand_computed_breakeven(self):
        """entry=1.0, target=1.5, invalidation=0.9 (R/R brut 5.0), pool=100k$,
        alloc demandée 50k$ (la moitié du pool -- absurde). Solution fermée
        attendue : 10 000$ (vérifié à la main -- à cette taille, impact 20 %,
        prix dégradé 1.2, R/R dégradé = (1.5-1.2)/(1.2-0.9) = 1.0, exactement
        PRICE_IMPACT_MIN_RR)."""
        alloc = risk_guard.cap_alloc_to_price_impact(50_000.0, 1.0, 1.5, 0.9, 100_000.0)
        assert alloc == pytest.approx(10_000.0, rel=1e-6)

    def test_stronger_raw_rr_tolerates_more_size_not_less(self):
        """Non-régression du piège identifié en concevant cette fonction : un R/R
        brut TRÈS élevé (25, entry=1.0/invalidation=0.96/target=2.0) doit tolérer
        PLUS de taille avant de heurter le plancher, jamais moins -- confirmé :
        24 000$ ici contre 10 000$ pour le cas R/R=5.0 ci-dessus, sur le même pool
        100k$."""
        alloc = risk_guard.cap_alloc_to_price_impact(50_000.0, 1.0, 2.0, 0.96, 100_000.0)
        assert alloc == pytest.approx(24_000.0, rel=1e-6)

    def test_returns_zero_when_raw_rr_already_below_floor(self):
        """Garde défensive : un R/R brut déjà sous PRICE_IMPACT_MIN_RR (1.0) avant
        même tout impact ne devrait jamais arriver via le pipeline momentum réel
        (qui garantit rr >= _RR_AMBIGUOUS_FLOOR == 1.0 pour tout BUY), mais la
        fonction doit rester sûre pour tout appelant futur : 0.0, jamais une
        exception ni un alloc laissé tel quel."""
        alloc = risk_guard.cap_alloc_to_price_impact(10_000.0, 1.0, 1.05, 0.9, 100_000.0)
        assert alloc == 0.0

    def test_never_raises_above_entry_value(self):
        """Un plafond, jamais un bonus -- même doctrine que size_position_by_risk."""
        alloc = risk_guard.cap_alloc_to_price_impact(1_000.0, 1.0, 1.5, 0.9, 50_000.0)
        assert alloc <= 1_000.0

    @pytest.mark.parametrize(
        "alloc,entry,target,invalidation,liquidity",
        [
            (0.0, 1.0, 1.5, 0.9, 100_000.0),      # alloc nulle
            (-100.0, 1.0, 1.5, 0.9, 100_000.0),   # alloc négative
            (10_000.0, 0.0, 1.5, 0.9, 100_000.0), # prix d'entrée invalide
            (10_000.0, 1.0, None, 0.9, 100_000.0),   # cible absente
            (10_000.0, 1.0, 1.5, None, 100_000.0),   # invalidation absente
            (10_000.0, 1.0, 1.5, 0.9, None),         # liquidité du pool inconnue
            (10_000.0, 1.0, 1.5, 0.9, 0.0),          # liquidité du pool nulle
            (10_000.0, 1.0, 0.8, 0.9, 100_000.0),    # cible sous le prix d'entrée
            (10_000.0, 1.0, 1.5, 1.1, 100_000.0),    # invalidation au-dessus du prix d'entrée
        ],
    )
    def test_fail_open_on_missing_or_incoherent_data(
        self, alloc, entry, target, invalidation, liquidity,
    ):
        """Donnée manquante/incohérente -> alloc inchangée (fail-open) -- le
        garde-fou dur sur la liquidité vit dans momentum_entry._MIN_LIQUIDITY_USD,
        pas ici."""
        result = risk_guard.cap_alloc_to_price_impact(alloc, entry, target, invalidation, liquidity)
        assert result == alloc


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
