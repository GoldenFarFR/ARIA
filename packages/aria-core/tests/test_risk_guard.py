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


# ── 1ter-bis. mode="scalping" (08/04, diligence #9 -- seuils R/R dédiés) ────────────

class TestConvictionSizeMultiplierScalpingMode:
    def test_swing_rr_never_reaches_scalping_conviction_tier_without_mode(self):
        """Un R/R typique scalping (ex. 1.8, sous le seuil swing 2.5 ET sous le
        seuil swing modéré 2.0) retombe au palier FAIBLE sans ``mode`` --
        exactement le bug diagnostiqué (88-91% des ordres v6/v7 au plancher 2%)."""
        assert risk_guard.conviction_size_multiplier(1.8, 3) == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_same_rr_reaches_moderate_tier_with_scalping_mode(self):
        """Le MÊME R/R (1.8), avec ``mode="scalping"``, dépasse le seuil modéré
        scalping (1.4) sans atteindre le seuil fort scalping (2.2) -- palier
        MODÉRÉ, la variable redevient discriminante."""
        assert risk_guard.conviction_size_multiplier(1.8, 3, mode="scalping") == risk_guard.MODERATE_ALLOC_MULTIPLIER

    def test_scalping_conviction_threshold_reaches_strong_tier(self):
        assert risk_guard.conviction_size_multiplier(2.2, 3, mode="scalping") == risk_guard.MAX_ALLOC_MULTIPLIER
        assert risk_guard.conviction_size_multiplier(2.5, 3, mode="scalping") == risk_guard.MAX_ALLOC_MULTIPLIER  # au-delà aussi, jamais un 4e palier

    def test_scalping_below_moderate_threshold_stays_weak(self):
        assert risk_guard.conviction_size_multiplier(1.0, 3, mode="scalping") == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_unknown_mode_falls_back_to_swing_thresholds(self):
        """Toute valeur autre que ``"scalping"`` (``None``, ``"standard"``,
        ``"vc"``...) garde le comportement swing d'origine, inchangé -- seul
        le pipeline scalping passe explicitement ``mode="scalping"``."""
        for mode in (None, "standard", "vc"):
            assert risk_guard.conviction_size_multiplier(2.0, 3, mode=mode) == risk_guard.MODERATE_ALLOC_MULTIPLIER
            assert risk_guard.conviction_size_multiplier(1.8, 3, mode=mode) == risk_guard.MIN_ALLOC_MULTIPLIER


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


# ── 1sexies. dex_security_score (28/07, Item #179 -- signal additif
#             dex_composite_score.py, 3e drapeau, même doctrine que fundamental_score/
#             volume_confirmed ci-dessus) ──────────────────────────────────────────────


class TestConvictionSizeMultiplierDexSecurity:
    def test_backward_compatible_no_dex_security_arg(self):
        assert risk_guard.conviction_size_multiplier(2.5, 3) == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_unknown_dex_security_never_downgrades(self):
        mult = risk_guard.conviction_size_multiplier(2.5, 3, dex_security_score=None)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_strong_dex_security_keeps_technical_bonus(self):
        mult = risk_guard.conviction_size_multiplier(2.5, 3, dex_security_score=90.0)
        assert mult == risk_guard.MAX_ALLOC_MULTIPLIER

    def test_confirmed_weak_dex_security_downgrades_to_moderate(self):
        mult = risk_guard.conviction_size_multiplier(2.5, 3, dex_security_score=10.0)
        assert mult == risk_guard.MODERATE_ALLOC_MULTIPLIER

    def test_dex_security_exactly_at_threshold_still_downgrades(self):
        below = risk_guard.DEX_SECURITY_WEAK_THRESHOLD - 0.01
        mult = risk_guard.conviction_size_multiplier(2.5, 3, dex_security_score=below)
        assert mult == risk_guard.MODERATE_ALLOC_MULTIPLIER

    def test_weak_dex_security_never_creates_a_bonus_on_mediocre_technical(self):
        mult = risk_guard.conviction_size_multiplier(1.0, 1, dex_security_score=5.0)
        assert mult == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_composes_with_fundamental_veto_stacks_to_weak_tier(self):
        """Deux drapeaux (fondamental faible ET dex_security faible) cumulent au
        palier FAIBLE -- même doctrine de cumul que fundamental+volume."""
        mult = risk_guard.conviction_size_multiplier(
            2.5, 3, fundamental_score=1.0, dex_security_score=10.0,
        )
        assert mult == risk_guard.MIN_ALLOC_MULTIPLIER

    def test_all_three_vetoes_still_only_reach_weak_tier_never_below(self):
        """Les TROIS drapeaux en même temps ne créent jamais un 4e palier sous le
        plancher FAIBLE déjà atteint par deux drapeaux -- MIN_ALLOC_MULTIPLIER reste
        le vrai plancher quel que soit le nombre de vétos."""
        mult = risk_guard.conviction_size_multiplier(
            2.5, 3, fundamental_score=1.0, volume_confirmed=False, dex_security_score=5.0,
        )
        assert mult == risk_guard.MIN_ALLOC_MULTIPLIER


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

    def test_dex_security_score_alone_downgrades_to_moderate_budget(self):
        budget = risk_guard.conviction_risk_budget_pct(2.5, 3, dex_security_score=10.0)
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_MODERATE_PCT

    def test_dex_security_score_unknown_never_downgrades_budget(self):
        budget = risk_guard.conviction_risk_budget_pct(2.5, 3, dex_security_score=None)
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_STRONG_PCT

    def test_dex_security_stacks_with_fundamental_to_weak_budget(self):
        budget = risk_guard.conviction_risk_budget_pct(
            2.5, 3, fundamental_score=1.0, dex_security_score=10.0,
        )
        assert budget == risk_guard.CONVICTION_RISK_BUDGET_WEAK_PCT

    def test_scalping_mode_uses_scalping_thresholds(self):
        """R/R 1.8 : palier faible sans mode, modéré avec mode="scalping" --
        même bascule que conviction_size_multiplier, propagée jusqu'ici."""
        assert risk_guard.conviction_risk_budget_pct(1.8, 3) == risk_guard.CONVICTION_RISK_BUDGET_WEAK_PCT
        assert risk_guard.conviction_risk_budget_pct(1.8, 3, mode="scalping") == risk_guard.CONVICTION_RISK_BUDGET_MODERATE_PCT


class TestConvictionTierLabel:
    def test_none_signal_returns_none(self):
        assert risk_guard.conviction_tier_label(None, None) is None

    def test_strong_setup_labeled_strong(self):
        assert risk_guard.conviction_tier_label(2.5, 3) == "strong"

    def test_moderate_setup_labeled_moderate(self):
        assert risk_guard.conviction_tier_label(2.0, 3) == "moderate"

    def test_weak_setup_labeled_weak(self):
        assert risk_guard.conviction_tier_label(1.0, 1) == "weak"

    def test_confirmed_weak_dex_security_downgrades_label_to_moderate(self):
        label = risk_guard.conviction_tier_label(2.5, 3, dex_security_score=10.0)
        assert label == "moderate"

    def test_two_vetoes_stack_label_to_weak(self):
        label = risk_guard.conviction_tier_label(
            2.5, 3, fundamental_score=1.0, dex_security_score=10.0,
        )
        assert label == "weak"

    def test_scalping_mode_uses_scalping_thresholds(self):
        assert risk_guard.conviction_tier_label(1.8, 3) == "weak"
        assert risk_guard.conviction_tier_label(1.8, 3, mode="scalping") == "moderate"
        assert risk_guard.conviction_tier_label(2.2, 3, mode="scalping") == "strong"


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


# ── 1ter. vc_thesis_alloc_usd (20/07, #174 -- Formule B, sizing câblé) ──────────────


class TestVcThesisAllocUsd:
    def test_none_taille_pct_signals_fallback(self):
        assert risk_guard.vc_thesis_alloc_usd(None, 1_000_000.0) is None

    def test_zero_taille_pct_signals_fallback(self):
        """0 est la valeur que le LLM pose explicitement quand recommandation != BUY
        (cf. vc_analysis.py) -- jamais une allocation nulle, un signal de repli."""
        assert risk_guard.vc_thesis_alloc_usd(0.0, 1_000_000.0) is None

    def test_negative_taille_pct_signals_fallback(self):
        """Défensif -- ne devrait jamais arriver (déjà clampé >=0 par vc_analysis.py),
        mais une valeur aberrante ne doit jamais produire une allocation négative."""
        assert risk_guard.vc_thesis_alloc_usd(-5.0, 1_000_000.0) is None

    def test_mid_range_taille_pct_computes_exact_fraction(self):
        assert risk_guard.vc_thesis_alloc_usd(5.0, 1_000_000.0) == pytest.approx(50_000.0)
        assert risk_guard.vc_thesis_alloc_usd(2.5, 1_000_000.0) == pytest.approx(25_000.0)

    def test_max_taille_pct_ten_percent(self):
        assert risk_guard.vc_thesis_alloc_usd(10.0, 1_000_000.0) == pytest.approx(100_000.0)

    def test_taille_pct_above_ten_is_clamped_not_rejected(self):
        """Ne devrait jamais arriver (déjà clampé à la source par MAX_POSITION_SIZE_PCT
        dans vc_analysis.py), mais une valeur aberrante > 10 doit être bornée, jamais
        traitée comme un signal de repli (contrairement à 0/négatif ci-dessus) --
        un LLM confiant à 25% ne doit jamais tripler le plafond produit."""
        assert risk_guard.vc_thesis_alloc_usd(25.0, 1_000_000.0) == pytest.approx(100_000.0)

    def test_scales_with_capital_total_not_a_fixed_constant(self):
        assert risk_guard.vc_thesis_alloc_usd(5.0, 500_000.0) == pytest.approx(25_000.0)


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

    def test_apply_swap_fee_default_false_unchanged(self):
        """apply_swap_fee non fourni -- comportement historique inchangé, même
        résultat que sans ce paramètre du tout."""
        alloc = risk_guard.cap_alloc_to_price_impact(50_000.0, 1.0, 1.5, 0.9, 100_000.0)
        alloc_explicit_false = risk_guard.cap_alloc_to_price_impact(
            50_000.0, 1.0, 1.5, 0.9, 100_000.0, apply_swap_fee=False,
        )
        assert alloc == alloc_explicit_false == pytest.approx(10_000.0, rel=1e-6)

    def test_apply_swap_fee_true_caps_tighter_than_false(self):
        """08/01 -- real bug fix: le frais de swap (1%, mode scalping) doit
        réduire la marge disponible pour l'impact de taille, jamais l'ignorer.
        Même setup (entry=1.0, target=1.06, invalidation=0.97, pool 50k$),
        valeurs vérifiées à la main : sans frais 375$, avec frais ~123,76$."""
        alloc_no_fee = risk_guard.cap_alloc_to_price_impact(
            1_000.0, 1.0, 1.06, 0.97, 50_000.0, apply_swap_fee=False,
        )
        alloc_with_fee = risk_guard.cap_alloc_to_price_impact(
            1_000.0, 1.0, 1.06, 0.97, 50_000.0, apply_swap_fee=True,
        )
        assert alloc_no_fee == pytest.approx(375.0, rel=1e-6)
        assert alloc_with_fee == pytest.approx(299.102692, rel=1e-5)  # fee 0.3% (08/05)
        assert alloc_with_fee < alloc_no_fee

    def test_apply_swap_fee_integration_matches_final_fill_rr_floor(self):
        """Le vrai test de non-régression du bug PLAY (01/08) : l'alloc plafonnée
        AVEC apply_swap_fee=True, une fois passée dans simulated_fill_price
        (même apply_swap_fee=True, même ordre), doit produire un R/R final >=
        PRICE_IMPACT_MIN_RR -- jamais un effondrement comme le 0.067 observé
        en prod. Avant ce correctif, capper SANS le frais puis remplir AVEC
        le frais aurait donné un R/R final de ~0.63 sur cet exact setup
        (vérifié à la main), bien sous le plancher visé."""
        entry, target, invalidation, liquidity = 1.0, 1.06, 0.97, 50_000.0
        alloc = risk_guard.cap_alloc_to_price_impact(
            1_000.0, entry, target, invalidation, liquidity, apply_swap_fee=True,
        )
        fill_price = risk_guard.simulated_fill_price(entry, alloc, liquidity, apply_swap_fee=True)
        final_rr = (target - fill_price) / (fill_price - invalidation)
        assert final_rr == pytest.approx(1.0, abs=1e-6)

    def test_apply_swap_fee_alone_breaches_floor_returns_zero(self):
        """Un setup où le seul frais de swap (avant même tout impact de taille)
        suffit à casser le plancher R/R -- doit rejeter (0.0), jamais laisser
        passer une taille infinitésimale qui filerait quand même sous le
        plancher une fois le frais appliqué au fill."""
        # target_degraded_entry = (1.005 + 1.0*0.97)/2 = 0.9875 < fee_adjusted_entry (1.01)
        alloc = risk_guard.cap_alloc_to_price_impact(
            1_000.0, 1.0, 1.005, 0.97, 50_000.0, apply_swap_fee=True,
        )
        assert alloc == 0.0

    def test_min_rr_defaults_to_module_constant_unchanged_behavior(self):
        """08/02 -- min_rr added, defaulting to PRICE_IMPACT_MIN_RR: swing/vc
        callers that don't pass it explicitly must see byte-for-byte the same
        result as before this parameter existed."""
        alloc_explicit_default = risk_guard.cap_alloc_to_price_impact(
            1_000.0, 1.0, 1.06, 0.97, 50_000.0, apply_swap_fee=True, min_rr=risk_guard.PRICE_IMPACT_MIN_RR,
        )
        alloc_implicit_default = risk_guard.cap_alloc_to_price_impact(
            1_000.0, 1.0, 1.06, 0.97, 50_000.0, apply_swap_fee=True,
        )
        assert alloc_implicit_default == alloc_explicit_default == pytest.approx(299.102692, rel=1e-5)  # fee 0.3% (08/05)

    def test_scalping_min_rr_gives_more_room_than_default_on_a_tight_setup(self):
        """08/02 -- real problem found live (audit + adversarial verify
        workflow): scalping's tight ATR stops leave so little margin above
        PRICE_IMPACT_MIN_RR (1.0) that the mandatory 1% swap fee alone
        crushed most signals to a few $ instead of the conviction tier's
        intended size (scalping_v2: 0/4 real signals ever opened). A lower
        floor for scalping specifically must produce a STRICTLY LARGER
        allocation on the same setup -- verified against a hand-computed
        value, not just "larger"."""
        alloc_default = risk_guard.cap_alloc_to_price_impact(
            1_000.0, 1.0, 1.06, 0.97, 50_000.0, apply_swap_fee=True, min_rr=risk_guard.PRICE_IMPACT_MIN_RR,
        )
        alloc_scalping = risk_guard.cap_alloc_to_price_impact(
            1_000.0, 1.0, 1.06, 0.97, 50_000.0, apply_swap_fee=True,
            min_rr=risk_guard.PRICE_IMPACT_MIN_RR_SCALPING,
        )
        assert alloc_default == pytest.approx(299.102692, rel=1e-5)  # fee 0.3% (08/05)
        assert alloc_scalping == pytest.approx(672.981057, rel=1e-5)
        assert alloc_scalping > alloc_default


# ── 1quinquies. simulated_fill_price (20/07, #175 -- prix d'exécution dégradé) ──────


class TestSimulatedFillPrice:
    def test_matches_cap_alloc_internal_degraded_entry(self):
        """Même modèle d'impact que cap_alloc_to_price_impact -- vérifié sur le cas
        déjà validé à la main dans TestCapAllocToPriceImpact (entry=1.0, alloc
        capée à 10 000$ sur un pool de 100k$ -> impact 20%, prix dégradé 1.2)."""
        price = risk_guard.simulated_fill_price(1.0, 10_000.0, 100_000.0)
        assert price == pytest.approx(1.2, rel=1e-6)

    def test_negligible_impact_on_deep_pool_barely_moves_price(self):
        price = risk_guard.simulated_fill_price(1.0, 20_000.0, 100_000_000.0)
        assert price == pytest.approx(1.0, rel=1e-3)
        assert price > 1.0  # jamais exactement égal -- toujours un impact, même infime

    def test_always_at_or_above_entry_price_never_below(self):
        """Un achat pousse le prix vers le haut, jamais vers le bas -- quelle que
        soit la taille."""
        for alloc in (100.0, 10_000.0, 100_000.0):
            price = risk_guard.simulated_fill_price(1.0, alloc, 50_000.0)
            assert price >= 1.0

    def test_scales_with_alloc_bigger_order_worse_fill(self):
        small = risk_guard.simulated_fill_price(1.0, 5_000.0, 100_000.0)
        big = risk_guard.simulated_fill_price(1.0, 50_000.0, 100_000.0)
        assert big > small

    @pytest.mark.parametrize(
        "entry,alloc,liquidity",
        [
            (0.0, 10_000.0, 100_000.0),   # prix d'entrée invalide
            (1.0, 0.0, 100_000.0),        # alloc nulle
            (1.0, -100.0, 100_000.0),     # alloc négative
            (1.0, 10_000.0, None),        # liquidité du pool inconnue
            (1.0, 10_000.0, 0.0),         # liquidité du pool nulle
        ],
    )
    def test_fail_open_returns_entry_price_unchanged(self, entry, alloc, liquidity):
        result = risk_guard.simulated_fill_price(entry, alloc, liquidity)
        assert result == entry


class TestSimulatedExitPrice:
    """22/07 -- item #18 (stress-test) : symétrique de simulated_fill_price, mais
    une VENTE pousse le prix vers le BAS, jamais vers le haut."""

    def test_matches_same_impact_model_as_fill_price(self):
        # Même formule (_price_impact_pct), donc même magnitude -- ici une vente
        # de 10 000$ sur un pool de 100k$ -> impact 20%, prix dégradé vers 0.8.
        price = risk_guard.simulated_exit_price(1.0, 10_000.0, 100_000.0)
        assert price == pytest.approx(0.8, rel=1e-6)

    def test_negligible_impact_on_deep_pool_barely_moves_price(self):
        price = risk_guard.simulated_exit_price(1.0, 20_000.0, 100_000_000.0)
        assert price == pytest.approx(1.0, rel=1e-3)
        assert price < 1.0  # jamais exactement égal -- toujours un impact, même infime

    def test_always_at_or_below_current_price_never_above(self):
        for value in (100.0, 10_000.0, 100_000.0):
            price = risk_guard.simulated_exit_price(1.0, value, 50_000.0)
            assert price <= 1.0

    def test_scales_with_position_value_bigger_position_worse_exit(self):
        small = risk_guard.simulated_exit_price(1.0, 5_000.0, 100_000.0)
        big = risk_guard.simulated_exit_price(1.0, 50_000.0, 100_000.0)
        assert big < small

    def test_never_goes_negative_on_extreme_impact(self):
        price = risk_guard.simulated_exit_price(1.0, 1_000_000.0, 10_000.0)  # impact >100%
        assert price >= 0.0

    @pytest.mark.parametrize(
        "current,value,liquidity",
        [
            (0.0, 10_000.0, 100_000.0),
            (1.0, 0.0, 100_000.0),
            (1.0, -100.0, 100_000.0),
            (1.0, 10_000.0, None),
            (1.0, 10_000.0, 0.0),
        ],
    )
    def test_fail_open_returns_current_price_unchanged(self, current, value, liquidity):
        result = risk_guard.simulated_exit_price(current, value, liquidity)
        assert result == current


# ── 1sexies. apply_swap_fee (Item #101, 26/07 -- frais de swap DEX reel) ────────

class TestApplySwapFee:
    """Frais de swap DEX (DEX_SWAP_FEE_PCT=1%, tier Uniswap v3 standard pour
    paire volatile) -- distinct de l'impact de prix, jamais applique par
    defaut (apply_swap_fee=False), scope au mode scalping par les appelants."""

    def test_fill_price_applies_fee_even_without_known_liquidity(self):
        """Un frais de protocole reel est preleve meme si l'impact de prix
        n'est pas calculable (liquidite inconnue) -- jamais un fail-open total."""
        price = risk_guard.simulated_fill_price(1.0, 10_000.0, None, apply_swap_fee=True)
        assert price == pytest.approx(1.0 * (1.0 + risk_guard.DEX_SWAP_FEE_PCT))

    def test_fill_price_combines_fee_and_impact(self):
        # Frais 1% (-> 1.01) puis impact 20% sur ce prix deja majore.
        price = risk_guard.simulated_fill_price(1.0, 10_000.0, 100_000.0, apply_swap_fee=True)
        expected = 1.0 * (1.0 + risk_guard.DEX_SWAP_FEE_PCT) * 1.2
        assert price == pytest.approx(expected, rel=1e-6)

    def test_fill_price_default_never_applies_fee(self):
        """Non-regression : sans apply_swap_fee explicite, comportement historique
        inchange (deja couvert par les tests ci-dessus, verifie ici explicitement)."""
        price = risk_guard.simulated_fill_price(1.0, 10_000.0, 100_000.0)
        assert price == pytest.approx(1.2, rel=1e-6)  # pas de frais melange dedans

    def test_exit_price_applies_fee_even_without_known_liquidity(self):
        price = risk_guard.simulated_exit_price(1.0, 10_000.0, None, apply_swap_fee=True)
        assert price == pytest.approx(1.0 * (1.0 - risk_guard.DEX_SWAP_FEE_PCT))

    def test_exit_price_combines_fee_and_impact(self):
        price = risk_guard.simulated_exit_price(1.0, 10_000.0, 100_000.0, apply_swap_fee=True)
        expected = 1.0 * (1.0 - risk_guard.DEX_SWAP_FEE_PCT) * 0.8
        assert price == pytest.approx(expected, rel=1e-6)

    def test_exit_price_default_never_applies_fee(self):
        price = risk_guard.simulated_exit_price(1.0, 10_000.0, 100_000.0)
        assert price == pytest.approx(0.8, rel=1e-6)


# ── 2. Coupe-circuit dédié : persistance, robustesse, distinction avec outgoing_pause ──


class TestNewEntryBlockState:
    def test_default_not_blocked(self, tmp_db):
        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is False
        assert reason is None

    def test_block_then_resume(self, tmp_db):
        risk_guard.block_new_entries("swing", "drawdown 22%", by=999)
        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is True
        assert "drawdown 22%" in reason

        risk_guard.resume_new_entries("swing", by=999)
        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is False
        assert reason is None

    def test_state_persists_on_disk_separate_file_from_outgoing_pause(self, tmp_db):
        risk_guard.block_new_entries("swing", "test")
        state_file = tmp_db / "risk_guard_state_swing.json"
        assert state_file.exists()
        assert not (tmp_db / "pause_state.json").exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["blocked"] is True

    def test_each_pocket_has_its_own_dedicated_state_file(self, tmp_db):
        """27/07, Phase 3: arming ONE pocket's breaker must never touch the
        other two pockets' own files -- the real bug this chantier fixes."""
        risk_guard.block_new_entries("scalping", "test scalping only")
        assert (tmp_db / "risk_guard_state_scalping.json").exists()
        assert not (tmp_db / "risk_guard_state_swing.json").exists()
        assert not (tmp_db / "risk_guard_state_vc.json").exists()
        assert risk_guard.blocks_new_entries("scalping") == (True, "test scalping only")
        assert risk_guard.blocks_new_entries("swing") == (False, None)
        assert risk_guard.blocks_new_entries("vc") == (False, None)

    def test_corrupt_file_fails_closed(self, tmp_db):
        """Doctrine argent : contrairement à outgoing_pause (fail-open jobs), ce coupe-circuit
        est TOUJOURS money-adjacent -> fail-closed sur corruption, jamais fail-open."""
        (tmp_db / "risk_guard_state_swing.json").write_text("{ not valid json", encoding="utf-8")
        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is True
        assert "illisible" in reason.lower() or "corrompu" in reason.lower()

    def test_never_confused_with_outgoing_pause(self, tmp_db):
        """outgoing_pause actif bloque aussi les nouvelles entrées paper (respecté), mais la
        raison rapportée distingue clairement les deux mécanismes -- jamais confondus."""
        outgoing_pause.pause(by=1, reason="stop opérateur")
        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is True
        assert "pause globale" in reason.lower()

        outgoing_pause.resume(by=1)
        assert risk_guard.blocks_new_entries("swing") == (False, None)

        # Le coupe-circuit dédié, lui, reste indépendant : l'armer ne touche jamais
        # l'état d'outgoing_pause (fichier séparé, jamais modifié par risk_guard).
        pause_before = (tmp_db / "pause_state.json").read_text(encoding="utf-8")
        risk_guard.block_new_entries("swing", "drawdown")
        assert (tmp_db / "pause_state.json").read_text(encoding="utf-8") == pause_before
        assert outgoing_pause.is_paused() is False


# ── 2bis. Rappel horaire tant qu'un coupe-circuit reste armé (31/07) ────────
# Demande opérateur explicite : "je veut une notification dans telegram
# toute les heures tant que j'ai pas traité le problème avec toi".


class TestPocketReminder:
    def test_no_reminder_when_not_blocked(self, tmp_db):
        assert risk_guard.should_send_pocket_reminder("swing") is False

    def test_first_reminder_right_after_arming(self, tmp_db):
        """Aucun rappel encore envoyé -> True dès que le coupe-circuit est armé,
        pas besoin d'attendre une première heure complète."""
        risk_guard.block_new_entries("swing", "drawdown 22%")
        assert risk_guard.should_send_pocket_reminder("swing") is True

    def test_no_second_reminder_before_interval_elapsed(self, tmp_db):
        risk_guard.block_new_entries("swing", "drawdown 22%")
        risk_guard.record_pocket_reminder_sent("swing")
        assert risk_guard.should_send_pocket_reminder("swing") is False

    def test_reminder_again_once_interval_elapsed(self, tmp_db):
        import json as _json
        from datetime import datetime, timedelta, timezone

        risk_guard.block_new_entries("swing", "drawdown 22%")
        stale = (datetime.now(timezone.utc) - timedelta(seconds=risk_guard.REMINDER_INTERVAL_SECONDS + 5)).isoformat()
        state_file = tmp_db / "risk_guard_state_swing.json"
        data = _json.loads(state_file.read_text(encoding="utf-8"))
        data["last_reminder_at"] = stale
        state_file.write_text(_json.dumps(data), encoding="utf-8")
        assert risk_guard.should_send_pocket_reminder("swing") is True

    def test_record_reminder_preserves_other_fields(self, tmp_db):
        risk_guard.block_new_entries("swing", "drawdown 22%", by=999)
        risk_guard.record_pocket_reminder_sent("swing")
        status = risk_guard.new_entry_block_status("swing")
        assert status["blocked"] is True
        assert status["reason"] == "drawdown 22%"
        assert status["by"] == 999
        assert status["last_reminder_at"] is not None

    def test_resuming_stops_the_reminder(self, tmp_db):
        risk_guard.block_new_entries("swing", "drawdown 22%")
        risk_guard.resume_new_entries("swing")
        assert risk_guard.should_send_pocket_reminder("swing") is False

    def test_soft_tier_alone_never_reminds(self, tmp_db):
        """Le palier SOUPLE ne bloque aucune entrée -- pas la situation "ARIA
        arrête de trader" que ce rappel cible, jamais de rappel pour lui seul."""
        assert risk_guard.should_send_pocket_reminder("swing") is False

    def test_format_reminder_mentions_since_and_reason(self, tmp_db):
        risk_guard.block_new_entries("swing", "drawdown 22%")
        status = risk_guard.new_entry_block_status("swing")
        text = risk_guard.format_pocket_blocked_reminder_alert(status, "swing")
        assert "drawdown 22%" in text
        assert "RAPPEL" in text
        assert "/riskresume" in text

    def test_each_pocket_reminder_independent(self, tmp_db):
        risk_guard.block_new_entries("scalping", "drawdown scalping")
        assert risk_guard.should_send_pocket_reminder("scalping") is True
        assert risk_guard.should_send_pocket_reminder("swing") is False
        assert risk_guard.should_send_pocket_reminder("vc") is False


class TestMacroReminder:
    def test_no_reminder_when_not_paused(self, tmp_db):
        assert risk_guard.should_send_macro_reminder() is False

    def test_no_reminder_when_paused_for_an_unrelated_reason(self, tmp_db):
        """Un /stop opérateur volontaire (sans rapport avec un drawdown) ne doit
        jamais produire un rappel "coupe-circuit MACRO" trompeur."""
        outgoing_pause.pause(by=1, reason="pause opérateur volontaire")
        assert risk_guard.should_send_macro_reminder() is False

    @pytest.mark.asyncio
    async def test_reminder_after_macro_trigger(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0, wallet="scalping")
        await pt.reset_portfolio(1_000_000.0, wallet="swing")
        await pt.reset_portfolio(1_000_000.0, wallet="vc")

        async def price_lookup(contract):
            return 1.0

        # Force un HWM élevé puis un effondrement -16% (> MACRO_CIRCUIT_BREAKER_LOSS_PCT).
        state = await risk_guard.evaluate_macro_risk(price_lookup=price_lookup)
        assert state.newly_triggered is False  # pas encore de drawdown

        async def crashed_lookup(contract):
            return 1.0

        import json as _json

        macro_file = tmp_db / "risk_guard_state_macro.json"
        data = _json.loads(macro_file.read_text(encoding="utf-8"))
        data["high_water_mark"] = 10_000_000.0  # simule un HWM bien plus haut que l'équité réelle
        macro_file.write_text(_json.dumps(data), encoding="utf-8")

        state2 = await risk_guard.evaluate_macro_risk(price_lookup=crashed_lookup)
        assert state2.newly_triggered is True
        assert risk_guard.should_send_macro_reminder() is True

        risk_guard.record_macro_reminder_sent()
        assert risk_guard.should_send_macro_reminder() is False

        text = risk_guard.format_macro_blocked_reminder_alert(state2)
        assert "RAPPEL" in text
        assert "/resume" in text


# ── 3. evaluate_portfolio_risk (intégration paper_trader) ──────────────────


class TestEvaluatePortfolioRisk:
    @pytest.mark.asyncio
    async def test_no_drawdown_normal_state(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        state = await risk_guard.evaluate_portfolio_risk("swing")
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
        await pt.open_position(A, "AAA", 1.0, alloc_usd=100_000, wallet="swing")

        async def price_lookup(contract):
            return 2.0  # +100k de valeur latente -> équité 1.1M, nouveau plus haut

        state = await risk_guard.evaluate_portfolio_risk("swing", price_lookup=price_lookup)
        assert round(state.equity) == 1_100_000
        assert state.high_water_mark == state.equity
        assert await pt.get_equity_high_water_mark() == state.equity

    @pytest.mark.asyncio
    async def test_soft_drawdown_halves_new_entry_alloc(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=100_000, wallet="swing")
        await pt.close_position(A, 1.0)  # HWM = 1M, équité toujours 1M

        # Nouvelle perte qui creuse un drawdown de 12% depuis le plus haut (1M).
        await pt.open_position(B, "BBB", 1.0, alloc_usd=120_000, wallet="swing")
        await pt.close_position(B, 0.001)  # quasi-perte totale des 120k -> équité ~880k, DD ~12%

        state = await risk_guard.evaluate_portfolio_risk("swing")
        assert round(state.drawdown_pct, 2) == 0.12
        assert state.alloc_multiplier == risk_guard.SOFT_ALLOC_MULTIPLIER
        assert state.blocked is False
        assert state.newly_triggered_soft is True

        # Un second appel dans la même bande ne redéclenche pas la notif (évite le bruit).
        state2 = await risk_guard.evaluate_portfolio_risk("swing")
        assert state2.newly_triggered_soft is False
        assert state2.alloc_multiplier == risk_guard.SOFT_ALLOC_MULTIPLIER

    @pytest.mark.asyncio
    async def test_hard_drawdown_blocks_new_entries_until_manual_resume(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000, wallet="swing")
        await pt.close_position(A, 0.2)  # perte de 200k sur 250k -> équité 800k, DD 20%

        state = await risk_guard.evaluate_portfolio_risk("swing")
        assert state.drawdown_pct >= risk_guard.HARD_DRAWDOWN_PCT
        assert state.blocked is True
        assert state.newly_triggered_hard is True

        # Persisté : un nouvel appel confirme toujours bloqué, sans re-déclencher la notif.
        state2 = await risk_guard.evaluate_portfolio_risk("swing")
        assert state2.blocked is True
        assert state2.newly_triggered_hard is False

        # Reprise JAMAIS automatique : même si l'équité remonte, ça reste bloqué tant que
        # resume_new_entries n'a pas été appelé explicitement.
        async def recovered_price(contract):
            return 10.0  # équité largement remontée

        state3 = await risk_guard.evaluate_portfolio_risk("swing", price_lookup=recovered_price)
        assert state3.blocked is True

        risk_guard.resume_new_entries("swing", by=1)
        blocked, _ = risk_guard.blocks_new_entries("swing")
        assert blocked is False

    @pytest.mark.asyncio
    async def test_five_consecutive_losses_blocks_regardless_of_drawdown_pct(self, tmp_db):
        await pt.reset_portfolio(10_000_000.0)  # capital large : le drawdown % reste faible
        for i, contract in enumerate([A, B, C, "0x" + "d" * 40, "0x" + "e" * 40]):
            await pt.open_position(contract, f"T{i}", 1.0, alloc_usd=1_000, wallet="swing")
            await pt.close_position(contract, 0.5, reason="perte")  # petite perte à chaque fois

        state = await risk_guard.evaluate_portfolio_risk("swing")
        assert state.consecutive_losses == 5
        assert state.blocked is True
        assert "pertes consécutives" in (state.blocked_reason or "")

    @pytest.mark.asyncio
    async def test_win_breaks_consecutive_loss_streak(self, tmp_db):
        await pt.reset_portfolio(10_000_000.0)
        for i, contract in enumerate([A, B, C]):
            await pt.open_position(contract, f"T{i}", 1.0, alloc_usd=1_000, wallet="swing")
            await pt.close_position(contract, 0.5, reason="perte")
        # Un gain interrompt la série -- plus récent en premier (ORDER BY closed_at DESC).
        win_contract = "0x" + "f" * 40
        await pt.open_position(win_contract, "WIN", 1.0, alloc_usd=1_000, wallet="swing")
        await pt.close_position(win_contract, 2.0, reason="gain")

        state = await risk_guard.evaluate_portfolio_risk("swing")
        assert state.consecutive_losses == 0
        assert state.blocked is False


# ── 4. Câblage open_position/run_paper_cycle ────────────────────────────────


class TestWiredIntoPaperTrader:
    @pytest.mark.asyncio
    async def test_open_position_refuses_when_blocked(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        risk_guard.block_new_entries("swing", "test hard block")
        pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=10_000, wallet="swing")
        assert pos is None

    @pytest.mark.asyncio
    async def test_open_position_applies_risk_cap(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        # invalidation à 50% de l'entrée -> alloc 50k plafonnée à 40k (cf. TestSizePositionByRisk).
        pos = await pt.open_position(A, "AAA", 1.0, invalidation_price=0.5, alloc_usd=50_000, wallet="swing")
        assert pos is not None
        assert round(pos["cost_usd"]) == 40_000

    @pytest.mark.asyncio
    async def test_run_paper_cycle_skips_new_entries_when_hard_blocked(self, tmp_db):
        await pt.reset_portfolio(1_000_000.0)
        risk_guard.block_new_entries("swing", "test")

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
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000, wallet="swing")
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
        await pt.open_position(B, "BBB", 1.0, invalidation_price=0.5, alloc_usd=10_000, wallet="swing")
        risk_guard.block_new_entries("swing", "test")

        async def price_lookup(contract):
            return 0.4  # sous l'invalidation -> doit se fermer normalement

        act = await pt.run_paper_cycle(candidates=[], price_lookup=price_lookup)
        assert len(act["closed"]) == 1
        assert not await pt.has_open(B)


# ── 5. Isolation par poche (27/07, Phase 3) -- le vrai bug corrigé par ce chantier :
#      avant, un seul état de coupe-circuit partagé faisait qu'un drawdown/une série de
#      pertes sur UNE SEULE poche bloquait/contaminait les 3. ─────────────────────────


class TestPerPocketDrawdownIsolation:
    @pytest.mark.asyncio
    async def test_drawdown_on_scalping_alone_never_blocks_swing_or_vc(self, tmp_db):
        """LE bug corrigé par ce chantier : avant, un seul fichier d'état partagé
        faisait qu'un drawdown dur sur UNE poche bloquait le sourcing des 3 à la
        fois. Chaque poche a maintenant son propre fichier, entièrement indépendant."""
        for wallet in ("scalping", "swing", "vc"):
            await pt.reset_portfolio(1_000_000.0, wallet=wallet)

        # Creuse un drawdown DUR (-20%) sur "scalping" SEULE.
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000, wallet="scalping")
        await pt.close_position(A, 0.2, reason="perte")  # perte de 200k sur 250k -> DD 20%

        scalping_state = await risk_guard.evaluate_portfolio_risk("scalping")
        assert scalping_state.blocked is True
        assert scalping_state.newly_triggered_hard is True

        blocked_scalping, _ = risk_guard.blocks_new_entries("scalping")
        blocked_swing, _ = risk_guard.blocks_new_entries("swing")
        blocked_vc, _ = risk_guard.blocks_new_entries("vc")
        assert blocked_scalping is True
        assert blocked_swing is False
        assert blocked_vc is False

        # Confirmé aussi via un vrai evaluate_portfolio_risk sur les 2 autres poches :
        # équité intacte, jamais un drawdown hérité de scalping.
        swing_state = await risk_guard.evaluate_portfolio_risk("swing")
        vc_state = await risk_guard.evaluate_portfolio_risk("vc")
        assert swing_state.blocked is False
        assert swing_state.drawdown_pct == 0.0
        assert vc_state.blocked is False
        assert vc_state.drawdown_pct == 0.0

    @pytest.mark.asyncio
    async def test_consecutive_losses_scoped_per_pocket(self, tmp_db):
        """Le compteur de pertes consécutives ne doit JAMAIS mélanger les poches --
        bug d'origine : ``get_closed_positions()`` sans ``wallet=`` comptait toutes
        les poches ensemble."""
        for wallet in ("scalping", "swing", "vc"):
            await pt.reset_portfolio(10_000_000.0, wallet=wallet)  # capital large : DD% reste faible

        contracts = [A, B, C, "0x" + "d" * 40, "0x" + "e" * 40]
        for i, contract in enumerate(contracts):
            await pt.open_position(contract, f"T{i}", 1.0, alloc_usd=1_000, wallet="scalping")
            await pt.close_position(contract, 0.5, reason="perte")

        scalping_state = await risk_guard.evaluate_portfolio_risk("scalping")
        assert scalping_state.consecutive_losses == 5
        assert scalping_state.blocked is True

        # "swing" et "vc" n'ont AUCUNE position clôturée -- leur propre compteur
        # doit rester à 0, jamais contaminé par les 5 pertes de scalping.
        swing_state = await risk_guard.evaluate_portfolio_risk("swing")
        vc_state = await risk_guard.evaluate_portfolio_risk("vc")
        assert swing_state.consecutive_losses == 0
        assert swing_state.blocked is False
        assert vc_state.consecutive_losses == 0
        assert vc_state.blocked is False


# ── 5bis. paper_risk_circuit_breakers_disabled (08/02) -- operator explicit
#      call, live incident (a hard breaker just armed on scalping_v3 while
#      the operator was watching): "les coupe circuit ne servent à rien à
#      paper test ... tu peux les supprimer". Scoped to the automated risk
#      circuit breakers only -- never outgoing_pause (manual kill-switch),
#      never any fraud-detection gate (honeypot/blacklist/concentration). ──


class TestPaperRiskCircuitBreakersDisabled:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", raising=False)
        assert risk_guard.paper_risk_circuit_breakers_disabled() is False

    @pytest.mark.asyncio
    async def test_gate_on_never_arms_the_hard_breaker(self, tmp_db, monkeypatch):
        """Same setup as test_hard_drawdown_blocks_new_entries_until_manual_
        resume above (-20% drawdown) -- with the gate on, must never arm at
        all (not just "ignored", never even written to the state file)."""
        monkeypatch.setenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", "true")
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000, wallet="swing")
        await pt.close_position(A, 0.2)  # perte de 200k sur 250k -> équité 800k, DD 20%

        state = await risk_guard.evaluate_portfolio_risk("swing")
        assert state.drawdown_pct >= risk_guard.HARD_DRAWDOWN_PCT
        assert state.blocked is False
        assert state.newly_triggered_hard is False

        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is False
        assert reason is None

    @pytest.mark.asyncio
    async def test_gate_on_ignores_a_breaker_already_armed_before_the_gate(self, tmp_db, monkeypatch):
        """Real scenario: the breaker armed BEFORE the operator turned the gate
        on (exactly the scalping_v3 incident) -- the already-persisted armed
        state must be ignored too, not just prevented from arming going
        forward."""
        monkeypatch.delenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", raising=False)
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000, wallet="swing")
        await pt.close_position(A, 0.2)
        state = await risk_guard.evaluate_portfolio_risk("swing")
        assert state.blocked is True  # armed for real, gate still off here

        monkeypatch.setenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", "true")
        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is False
        assert reason is None

    def test_gate_on_never_touches_the_manual_kill_switch(self, monkeypatch):
        """outgoing_pause (/stop) is a human decision, never an automated
        one -- must stay fully authoritative regardless of this flag."""
        from aria_core import outgoing_pause

        monkeypatch.setenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", "true")
        monkeypatch.setattr(outgoing_pause, "is_paused", lambda: True)
        blocked, reason = risk_guard.blocks_new_entries("swing")
        assert blocked is True
        assert reason is not None and "pause globale" in reason

    @pytest.mark.asyncio
    async def test_gate_on_silences_the_hourly_reminder(self, tmp_db, monkeypatch):
        monkeypatch.delenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", raising=False)
        await pt.reset_portfolio(1_000_000.0)
        await pt.open_position(A, "AAA", 1.0, alloc_usd=250_000, wallet="swing")
        await pt.close_position(A, 0.2)
        await risk_guard.evaluate_portfolio_risk("swing")
        assert risk_guard.should_send_pocket_reminder("swing") is True  # gate off: real reminder

        monkeypatch.setenv("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", "true")
        assert risk_guard.should_send_pocket_reminder("swing") is False


# ── 6. Coupe-circuit MACRO (27/07, Phase 3) -- agrège l'équité des 3 poches,
#      backstop pour un krach CORRÉLÉ où chaque poche reste individuellement sous
#      son propre seuil HARD_DRAWDOWN_PCT (20%). ────────────────────────────────


class TestMacroCircuitBreaker:
    @pytest.mark.asyncio
    async def test_not_triggered_at_rest_no_false_positive(self, tmp_db):
        """Comportement au repos, aucune perte -- jamais un faux positif basique
        écriture/lecture du fichier d'état macro."""
        for wallet in ("scalping", "swing", "vc"):
            await pt.reset_portfolio(1_000_000.0, wallet=wallet)

        state = await risk_guard.evaluate_macro_risk()
        assert state.blocked is False
        assert state.newly_triggered is False
        assert round(state.total_equity) == 3_000_000
        assert round(state.total_high_water_mark) == 3_000_000
        assert state.drawdown_pct == 0.0
        assert outgoing_pause.is_paused() is False

        # Un second appel, toujours au repos, ne déclenche rien non plus.
        state2 = await risk_guard.evaluate_macro_risk()
        assert state2.blocked is False
        assert state2.newly_triggered is False
        assert outgoing_pause.is_paused() is False

    @pytest.mark.asyncio
    async def test_triggers_on_aggregated_loss_below_each_pockets_own_hard_threshold(self, tmp_db):
        """Chaque poche perd individuellement ~16% (SOUS son propre seuil HARD de
        20%), mais la perte AGRÉGÉE des 3 poches dépasse le seuil MACRO de 15% --
        le vrai angle mort que ce coupe-circuit comble."""
        for wallet in ("scalping", "swing", "vc"):
            await pt.reset_portfolio(1_000_000.0, wallet=wallet)

        # Établit le plus haut MACRO (3M) AVANT toute perte.
        baseline = await risk_guard.evaluate_macro_risk()
        assert baseline.newly_triggered is False
        assert round(baseline.total_high_water_mark) == 3_000_000

        # Perte de 160k (16%) sur CHACUNE des 3 poches -- individuellement sous
        # HARD_DRAWDOWN_PCT (20%), jamais un déclenchement par poche.
        for wallet in ("scalping", "swing", "vc"):
            await pt.open_position(A, "AAA", 1.0, alloc_usd=200_000, wallet=wallet)
            await pt.close_position(A, 0.2, reason="perte")  # -160k sur 200k -> DD 16%
            pocket_state = await risk_guard.evaluate_portfolio_risk(wallet)
            assert pocket_state.drawdown_pct < risk_guard.HARD_DRAWDOWN_PCT
            assert pocket_state.blocked is False

        assert outgoing_pause.is_paused() is False  # pas encore déclenché côté macro

        macro_state = await risk_guard.evaluate_macro_risk()
        assert macro_state.drawdown_pct >= risk_guard.MACRO_CIRCUIT_BREAKER_LOSS_PCT
        assert macro_state.newly_triggered is True
        assert macro_state.blocked is True
        assert outgoing_pause.is_paused() is True

        # Un second appel, déjà déclenché : ne redéclenche pas (déjà armé), mais
        # reste "blocked" tant que /resume n'a pas été appelé.
        macro_state2 = await risk_guard.evaluate_macro_risk()
        assert macro_state2.newly_triggered is False
        assert macro_state2.blocked is True

    @pytest.mark.asyncio
    async def test_resume_allows_a_later_independent_retrigger(self, tmp_db):
        """Un /resume opérateur explicite (jamais automatique) doit permettre au
        coupe-circuit MACRO de se réarmer sur un krach LATER, indépendant --
        jamais "consommé" une seule fois pour toujours."""
        for wallet in ("scalping", "swing", "vc"):
            await pt.reset_portfolio(1_000_000.0, wallet=wallet)
        await risk_guard.evaluate_macro_risk()  # établit le plus haut

        for wallet in ("scalping", "swing", "vc"):
            await pt.open_position(A, "AAA", 1.0, alloc_usd=200_000, wallet=wallet)
            await pt.close_position(A, 0.2, reason="perte")

        first = await risk_guard.evaluate_macro_risk()
        assert first.newly_triggered is True
        assert outgoing_pause.is_paused() is True

        outgoing_pause.resume(by=1)
        assert outgoing_pause.is_paused() is False

        # Nouvelle perte corrélée, plus tard : doit re-déclencher, pas rester
        # silencieux à cause d'un flag "triggered" resté vrai dans le fichier macro.
        for wallet in ("scalping", "swing", "vc"):
            await pt.open_position(B, "BBB", 1.0, alloc_usd=200_000, wallet=wallet)
            await pt.close_position(B, 0.2, reason="perte")

        second = await risk_guard.evaluate_macro_risk()
        assert second.newly_triggered is True
        assert outgoing_pause.is_paused() is True


class TestResetPortfolioLiftsCircuitBreaker:
    @pytest.mark.asyncio
    async def test_manual_reset_lifts_a_stale_hard_block(self, tmp_db):
        """08/01 -- real bug found live (operator: "cest vraiment etrange quil
        se passe rien" -- a pocket stayed silent for over an hour after a
        full manual reset, capital fresh at 1M$, zero errors anywhere).
        run_weekly_reset() always lifts the pocket's own circuit breaker as
        part of its reset (resume_new_entries) -- reset_portfolio() (the
        MANUAL reset) never did, silently leaving a pre-existing hard block
        (e.g. 5 consecutive losses) armed on an otherwise completely fresh
        portfolio."""
        risk_guard.block_new_entries("scalping", "5 pertes consécutives", by="test")
        blocked_before, _ = risk_guard.blocks_new_entries("scalping")
        assert blocked_before is True

        await pt.reset_portfolio(1_000_000.0, wallet="scalping")

        blocked_after, reason_after = risk_guard.blocks_new_entries("scalping")
        assert blocked_after is False
        assert reason_after is None

    @pytest.mark.asyncio
    async def test_manual_reset_scoped_to_its_own_wallet_only(self, tmp_db):
        """Un reset sur UNE poche ne doit jamais lever le coupe-circuit d'une
        AUTRE poche -- même isolation stricte que le reste de l'architecture
        3+ poches (chaque wallet a son propre fichier d'état)."""
        risk_guard.block_new_entries("scalping", "test", by="test")
        risk_guard.block_new_entries("swing", "test", by="test")

        await pt.reset_portfolio(1_000_000.0, wallet="scalping")

        blocked_scalping, _ = risk_guard.blocks_new_entries("scalping")
        blocked_swing, _ = risk_guard.blocks_new_entries("swing")
        assert blocked_scalping is False
        assert blocked_swing is True  # jamais touché


class TestMigrateWalletState:
    """08/01, one-off migration (legacy "scalping" pocket folded into
    "scalping_v6") -- a pocket's circuit-breaker block/resume history is
    real data, never silently dropped on a rename."""

    def test_moves_state_file_to_new_wallet(self, tmp_db):
        risk_guard.block_new_entries("scalping", "5 pertes consécutives", by="test")

        moved = risk_guard.migrate_wallet_state("scalping", "scalping_v6")

        assert moved is True
        blocked_old, _ = risk_guard.blocks_new_entries("scalping")
        blocked_new, reason_new = risk_guard.blocks_new_entries("scalping_v6")
        assert blocked_old is False  # nothing left under the old name
        assert blocked_new is True
        assert reason_new == "5 pertes consécutives"
        assert not risk_guard._state_path("scalping").exists()

    def test_nothing_to_migrate_is_a_safe_noop(self, tmp_db):
        moved = risk_guard.migrate_wallet_state("scalping", "scalping_v6")
        assert moved is False
        assert not risk_guard._state_path("scalping_v6").exists()

    def test_never_overwrites_an_existing_destination(self, tmp_db):
        """scalping_v6 already has its OWN real history (e.g. this migration
        already ran once) -- a second call must never clobber it."""
        risk_guard.block_new_entries("scalping", "old reason", by="test")
        risk_guard.block_new_entries("scalping_v6", "scalping_v6's own real block", by="test")

        moved = risk_guard.migrate_wallet_state("scalping", "scalping_v6")

        assert moved is False
        blocked_old, reason_old = risk_guard.blocks_new_entries("scalping")
        blocked_new, reason_new = risk_guard.blocks_new_entries("scalping_v6")
        assert blocked_old is True and reason_old == "old reason"  # untouched, left in place
        assert blocked_new is True and reason_new == "scalping_v6's own real block"  # untouched
