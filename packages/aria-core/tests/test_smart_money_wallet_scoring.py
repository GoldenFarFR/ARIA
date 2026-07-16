"""#157 — évaluateur "smart wallet" wallet-centrique multi-token (extension de
smart_money.py). Aucun appel réseau réel — Blockscout/GeckoTerminal/GoPlus/LLM
mockés.

Couvre : FIFO PnL, Sortino, drawdown wallet, sélection/plafond de tokens
(couche 2), disqualifiants durs (couche 1, dont le correctif anti-faux-positif
wash-trading multi-token du 14/07 et le remplacement du registre malveillant
par GoPlus AML), drapeau suspect positif séparé (couche 3), et le point
d'entrée bout-en-bout `score_wallets`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.services.blockscout import (
    AddressInfo,
    BoundedTransactionsResult,
    Transaction,
    TokenTransfer,
    TokenTransfersResult,
)
from aria_core.services.geckoterminal import OHLCVResult, PoolMetadata
from aria_core.services.goplus import AddressSecurity
from aria_core.skills.ta_levels import Candle
from aria_core.services import smart_money as sm
from aria_core.services import wallet_scan_state


@pytest.fixture(autouse=True)
def _isolated_wallet_scan_state_db(tmp_path, monkeypatch):
    """15/07 (#157 suite) : wallet_scan_state.py a son PROPRE DB_PATH (module
    séparé de smart_money.py, cf. import différé anti-cycle) -- sans cette
    isolation automatique, un `score_wallets` de test écrirait dans la vraie
    base par défaut et polluerait les tests suivants (un wallet/token de test
    "déjà scanné" resterait marqué comme tel entre deux tests sans rapport)."""
    monkeypatch.setattr(wallet_scan_state, "DB_PATH", str(tmp_path / "wallet_scan_state.db"))


WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40
WALLET_C = "0x" + "c" * 40
TOKEN_X = "0x" + "1" * 40
TOKEN_Y = "0x" + "2" * 40
POOL_X = "0x" + "3" * 40
POOL_Y = "0x" + "4" * 40
FUNDER = "0x" + "f" * 40
ROUTER = "0x" + "9" * 40  # infra DEX partagée entre plusieurs tokens


def _dt(offset_days: float = 0.0, base: datetime | None = None) -> datetime:
    base = base or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(days=offset_days)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _transfer(
    *, from_addr: str, to_addr: str, token: str, ts: datetime, amount: float = 100.0, tx_hash: str = "0x1",
) -> TokenTransfer:
    return TokenTransfer(
        tx_hash=tx_hash,
        from_address=from_addr,
        to_address=to_addr,
        token_address=token,
        token_symbol="TOK",
        token_name="Token",
        amount=amount,
        timestamp=_iso(ts),
    )


# ---------------------------------------------------------------------------
# FIFO / Sortino / drawdown — fonctions pures, séries synthétiques connues
# ---------------------------------------------------------------------------

class TestFifoMatch:
    """``buys``/``sells`` sont des triplets ``(ts, amount, tx_hash)`` (14/07,
    prix par hash exact) -- le hash n'est pas exercé par ces tests (fonctions
    pures sur des séries synthétiques, ``price_lookup`` ignore son 2e argument
    via un lambda ``ts, _h``), seul le comportement FIFO/prix est couvert ici."""

    def test_simple_winning_trade(self):
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 10.0, "0xsell")]
        prices = {_dt(0): 1.0, _dt(1): 2.0}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts, _h: prices.get(ts))

        assert len(result.closed_trades) == 1
        trade = result.closed_trades[0]
        assert trade.pnl_usd == pytest.approx(10.0)  # 10 * (2-1)
        assert trade.return_pct == pytest.approx(1.0)
        assert result.unpriced_legs == 0
        assert result.open_position_amount == 0.0

    def test_simple_losing_trade(self):
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 10.0, "0xsell")]
        prices = {_dt(0): 2.0, _dt(1): 1.0}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts, _h: prices.get(ts))

        assert result.closed_trades[0].pnl_usd == pytest.approx(-10.0)

    def test_missing_price_counted_unpriced_never_zeroed(self):
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 10.0, "0xsell")]
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts, _h: None)

        assert result.closed_trades == []
        assert result.unpriced_legs == 1

    def test_partial_sell_leaves_open_position(self):
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 4.0, "0xsell")]
        prices = {_dt(0): 1.0, _dt(1): 1.5}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts, _h: prices.get(ts))

        assert result.closed_trades[0].token_amount == pytest.approx(4.0)
        assert result.open_position_amount == pytest.approx(6.0)

    def test_fifo_order_oldest_buy_matched_first(self):
        buys = [(_dt(0), 5.0, "0xbuy1"), (_dt(1), 5.0, "0xbuy2")]
        sells = [(_dt(2), 5.0, "0xsell")]
        prices = {_dt(0): 1.0, _dt(1): 3.0, _dt(2): 2.0}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts, _h: prices.get(ts))

        assert len(result.closed_trades) == 1
        # apparié avec l'achat à 1.0 (le plus ancien), pas celui à 3.0
        assert result.closed_trades[0].buy_price == pytest.approx(1.0)
        assert result.open_position_amount == pytest.approx(5.0)

    def test_sell_without_matching_buy_ignored_not_a_priced_leg(self):
        result = sm._fifo_match(TOKEN_X, [], [(_dt(0), 10.0, "0xsell")], lambda ts, _h: 1.0)
        assert result.closed_trades == []
        assert result.unpriced_legs == 0

    def test_price_lookup_receives_matching_tx_hash_per_leg(self):
        """Le hash transporté par chaque jambe est bien celui repassé à
        ``price_lookup`` -- pas seulement le timestamp (14/07, condition
        nécessaire pour que la résolution de prix par hash exact fonctionne)."""
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 10.0, "0xsell")]
        seen_hashes = []

        def _price_lookup(ts, tx_hash):
            seen_hashes.append(tx_hash)
            return 1.0

        sm._fifo_match(TOKEN_X, buys, sells, _price_lookup)
        assert seen_hashes == ["0xbuy", "0xsell"]

    def test_unmatched_sell_beyond_buy_queue_counted_never_credited(self):
        # 15/07, revue Gemini -- signal possible de rebase/rendement DeFi
        # (stETH, aTokens) : le solde augmente sans transfert entrant
        # équivalent. Le surplus vendu (12 - 10 = 2) ne doit jamais être
        # crédité comme profit -- juste compté.
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 12.0, "0xsell")]
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts, _h: 1.0)

        assert len(result.closed_trades) == 1
        assert result.closed_trades[0].token_amount == pytest.approx(10.0)  # jamais 12
        assert result.unmatched_sell_events == 1

    def test_exact_hashes_mark_confidence_per_leg(self):
        # 15/07, revue Gemini -- price_confirmation_ratio : chaque bord d'un
        # ClosedTrade porte sa propre confiance (achat exact, vente estimée,
        # ou l'inverse), indépendamment l'un de l'autre.
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 10.0, "0xsell")]
        result = sm._fifo_match(
            TOKEN_X, buys, sells, lambda ts, _h: 1.0, exact_hashes=frozenset({"0xbuy"}),
        )
        trade = result.closed_trades[0]
        assert trade.buy_price_exact is True
        assert trade.sell_price_exact is False

    def test_exact_hashes_default_empty_backward_compatible(self):
        # Rétrocompatibilité : un appelant qui ne fournit pas exact_hashes
        # (tout le code/tests existants avant #157 suite 15/07) obtient
        # buy_price_exact/sell_price_exact=False, jamais une erreur.
        buys = [(_dt(0), 10.0, "0xbuy")]
        sells = [(_dt(1), 10.0, "0xsell")]
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts, _h: 1.0)
        trade = result.closed_trades[0]
        assert trade.buy_price_exact is False
        assert trade.sell_price_exact is False


class TestSortinoRatio:
    def test_below_min_trades_unavailable(self):
        assert sm._sortino_ratio([0.1, 0.2]) is None

    def test_no_losses_unavailable_not_infinite(self):
        returns = [0.1, 0.2, 0.3, 0.1, 0.2]
        assert sm._sortino_ratio(returns) is None

    def test_mixed_returns_computed(self):
        returns = [0.2, -0.1, 0.3, -0.2, 0.1]
        result = sm._sortino_ratio(returns)
        assert result is not None
        # mean = 0.06 ; downside = [-0.1,-0.2] -> downside_dev = sqrt(mean([0.01,0.04])) = sqrt(0.025)
        import math
        expected = 0.06 / math.sqrt(0.025)
        assert result == pytest.approx(expected, rel=1e-6)


class TestMaxDrawdown:
    def test_no_trades_unavailable(self):
        assert sm._max_drawdown_pct([]) is None

    def test_monotonic_gains_zero_drawdown(self):
        trades = [
            sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 10.0, 1.0, 2.0),
            sm.ClosedTrade(TOKEN_X, _dt(1), _dt(2), 10.0, 1.0, 3.0),
        ]
        assert sm._max_drawdown_pct(trades) == pytest.approx(0.0)

    def test_known_drawdown_sequence(self):
        # cumulatif : +100 (peak 100) puis -40 (down to 60) -> dd = 40/100 = 0.4
        trades = [
            sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 100.0, 1.0, 2.0),  # pnl +100
            sm.ClosedTrade(TOKEN_X, _dt(1), _dt(2), 40.0, 2.0, 1.0),  # pnl -40
        ]
        assert sm._max_drawdown_pct(trades) == pytest.approx(0.4)


class TestAvgHoldingPeriod:
    def test_no_trades_unavailable(self):
        assert sm._avg_holding_period_days([]) is None

    def test_known_average(self):
        trades = [
            sm.ClosedTrade(TOKEN_X, _dt(0), _dt(2), 10.0, 1.0, 2.0),  # 2 jours
            sm.ClosedTrade(TOKEN_X, _dt(0), _dt(10), 10.0, 1.0, 2.0),  # 10 jours
        ]
        assert sm._avg_holding_period_days(trades) == pytest.approx(6.0)


class TestRecentWindowMetrics:
    """15/07, revue ChatGPT -- biais temporel : `_dt()` est relatif à une base
    FIXE (2026-01-01), pas à MAINTENANT -- les timestamps sont construits
    directement relatifs à `datetime.now()` (même patron que
    `test_age_measured_from_earliest_transfer_to_now`)."""

    def test_no_trades_in_window_unavailable(self):
        now = datetime.now(timezone.utc)
        old_trade = sm.ClosedTrade(TOKEN_X, now - timedelta(days=200), now - timedelta(days=199), 10.0, 1.0, 2.0)
        win_rate, pnl, count = sm._recent_window_metrics([old_trade], window_days=90)
        assert win_rate is None
        assert pnl is None
        assert count == 0

    def test_recent_trades_computed_independently_of_old_history(self):
        # Un wallet excellent il y a 200 jours (hors fenêtre) mais perdant
        # récemment (dans la fenêtre) -- la fenêtre récente ne doit refléter
        # QUE le trade récent, jamais diluée par l'historique ancien.
        now = datetime.now(timezone.utc)
        old_winner = sm.ClosedTrade(TOKEN_X, now - timedelta(days=200), now - timedelta(days=199), 10.0, 1.0, 100.0)
        recent_loser = sm.ClosedTrade(TOKEN_X, now - timedelta(days=10), now - timedelta(days=5), 10.0, 10.0, 1.0)
        win_rate, pnl, count = sm._recent_window_metrics([old_winner, recent_loser], window_days=90)
        assert count == 1
        assert win_rate == pytest.approx(0.0)
        assert pnl == pytest.approx(10.0 * (1.0 - 10.0))


class TestWalletAgeAndSwapCount:
    def test_no_transfers_unavailable(self):
        assert sm._wallet_age_days([]) is None

    def test_age_measured_from_earliest_transfer_to_now(self):
        old_ts = _dt(-100)
        old = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=old_ts)
        recent = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(-1))
        age = sm._wallet_age_days([recent, old])
        expected = (datetime.now(timezone.utc) - old_ts).total_seconds() / 86_400
        assert age == pytest.approx(expected, abs=0.01)

    def test_total_swaps_counts_both_directions(self):
        buy = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), tx_hash="0xb")
        sell = _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1), tx_hash="0xs")
        unrelated = _transfer(from_addr=FUNDER, to_addr=WALLET_B, token=TOKEN_X, ts=_dt(1), tx_hash="0xu")
        assert sm._count_total_swaps([buy, sell, unrelated], WALLET_A) == 2

    def test_wrap_unwrap_excluded_from_swap_count(self):
        # Exploit identifié (15/07, revue Gemini) : un script qui wrap/unwrap du
        # ETH<->WETH des centaines de fois débloquerait min_total_swaps sans
        # jamais prendre de risque de trading réel -- ces jambes (mint/burn
        # depuis/vers l'adresse zéro sur le WETH Base) ne doivent plus compter.
        weth_base = "0x4200000000000000000000000000000000000006"
        zero = "0x" + "0" * 40
        wrap = _transfer(from_addr=zero, to_addr=WALLET_A, token=weth_base, ts=_dt(0), tx_hash="0xw1")
        unwrap = _transfer(from_addr=WALLET_A, to_addr=zero, token=weth_base, ts=_dt(1), tx_hash="0xw2")
        real_buy = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(2), tx_hash="0xr")
        assert sm._count_total_swaps([wrap, unwrap, real_buy], WALLET_A) == 1

    def test_wrap_unwrap_of_unrelated_token_still_counted(self):
        # Un mint/burn depuis/vers l'adresse zéro sur un token QUELCONQUE (pas
        # le wrapped-native connu) n'est pas exclu -- seule l'adresse WETH/wrapped-
        # native enregistrée déclenche l'exclusion, jamais un token arbitraire.
        zero = "0x" + "0" * 40
        mint = _transfer(from_addr=zero, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), tx_hash="0xm")
        assert sm._count_total_swaps([mint], WALLET_A) == 1

    def test_stable_to_stable_peg_swap_excluded_from_swap_count(self):
        # 15/07, revue Gemini suite -- extension de l'exploit wrap/unwrap :
        # un swap stable<->stable (frais infimes, risque directionnel quasi
        # nul) permet le même padding de min_total_swaps. Réutilise le
        # registre stablecoin existant, aucun nouveau registre à maintenir.
        stable_a, stable_b = list(sm._STABLECOIN_ADDRESSES_BY_CHAIN["base"])[:2]
        out_leg = _transfer(from_addr=WALLET_A, to_addr=ROUTER, token=stable_a, ts=_dt(0), tx_hash="0xpeg")
        in_leg = _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=stable_b, ts=_dt(0), tx_hash="0xpeg")
        real_buy = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(1), tx_hash="0xr")
        assert sm._count_total_swaps([out_leg, in_leg, real_buy], WALLET_A) == 1

    def test_single_stablecoin_leg_not_treated_as_peg_swap(self):
        # Un achat de memecoin PAYÉ en stablecoin (une seule jambe stable dans
        # la tx, l'autre est le memecoin) n'est jamais un swap stable<->stable
        # -- exige au moins DEUX jambes stables dans la même transaction.
        stable_a = next(iter(sm._STABLECOIN_ADDRESSES_BY_CHAIN["base"]))
        stable_leg = _transfer(from_addr=WALLET_A, to_addr=ROUTER, token=stable_a, ts=_dt(0), tx_hash="0xbuy")
        memecoin_leg = _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), tx_hash="0xbuy")
        assert sm._count_total_swaps([stable_leg, memecoin_leg], WALLET_A) == 2


class TestRobustPnlCheck:
    def test_below_minimum_unavailable(self):
        trades = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 1.0, 2.0) for _ in range(5)]
        assert sm._robust_pnl_check(trades, trim_pct=0.10, min_required=30) is None

    def test_trims_both_tails_and_stays_positive(self):
        # 30 trades : 10 très négatifs, 10 neutres/légèrement positifs, 10 très positifs.
        # trim_pct=1/3 retire exactement 10 de chaque extrémité (même comportement
        # que l'ancien compte fixe de 10, pour ce cas précis) -> il ne reste que
        # les 10 neutres/positifs.
        losers = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 10.0, 1.0) for _ in range(10)]  # pnl -9 chacun
        middle = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 1.0, 1.1) for _ in range(10)]  # pnl +0.1 chacun
        winners = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 1.0, 100.0) for _ in range(10)]  # pnl +99 chacun
        result = sm._robust_pnl_check(losers + middle + winners, trim_pct=1 / 3, min_required=30)
        assert result is True

    def test_all_losses_remain_negative_after_trim(self):
        # sell < buy pour chaque trade -> pnl toujours négatif, peu importe la magnitude.
        # trim_pct=0.25 sur 40 trades retire exactement 10 de chaque extrémité.
        trades = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 10.0, 10.0 - i * 0.1) for i in range(1, 41)]
        result = sm._robust_pnl_check(trades, trim_pct=0.25, min_required=30)
        assert result is False

    def test_percentage_trim_closes_dilution_vector_fixed_count_missed(self):
        # Vecteur d'exploitation identifié par revue croisée externe (15/07,
        # Gemini/ChatGPT/Grok convergents) : 15 trades "chanceux" (+10000$
        # chacun) noyés dans 185 trades normaux légèrement perdants (-10$
        # chacun). L'ANCIEN compte fixe de 10 n'aurait retiré que 10 des 15
        # trades chanceux -- les 5 restants (+50000$) suffisaient à faire
        # paraître le reste "robuste" à tort (48250$ > 0). Le trim en
        # POURCENTAGE (10% de 200 = 20 >= 15) les retire TOUS -- révèle
        # correctement que le wallet n'est pas robuste sans ses coups de chance.
        lucky = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 1.0, 10_001.0) for _ in range(15)]
        normal = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 10.0, 0.0) for _ in range(185)]
        result = sm._robust_pnl_check(lucky + normal, trim_pct=0.10, min_required=30)
        assert result is False

    def test_trim_too_large_relative_to_sample_returns_none(self):
        trades = [sm.ClosedTrade(TOKEN_X, _dt(0), _dt(1), 1.0, 1.0, 2.0) for _ in range(30)]
        assert sm._robust_pnl_check(trades, trim_pct=0.6, min_required=30) is None


class TestHealthTrend:
    def test_below_minimum_unavailable(self):
        trades = [sm.ClosedTrade(TOKEN_X, _dt(i), _dt(i + 1), 1.0, 1.0, 1.1) for i in range(5)]
        assert sm._health_trend(trades, min_required=10, stable_band_pct=0.15) is None

    def test_clear_improvement_detected(self):
        first_half = [sm.ClosedTrade(TOKEN_X, _dt(i), _dt(i + 1), 1.0, 10.0, 9.0) for i in range(5)]  # pnl -1
        second_half = [sm.ClosedTrade(TOKEN_X, _dt(10 + i), _dt(11 + i), 1.0, 1.0, 11.0) for i in range(5)]  # pnl +10
        assert sm._health_trend(first_half + second_half, min_required=10, stable_band_pct=0.15) == "amélioration"

    def test_clear_degradation_detected(self):
        first_half = [sm.ClosedTrade(TOKEN_X, _dt(i), _dt(i + 1), 1.0, 1.0, 11.0) for i in range(5)]  # pnl +10
        second_half = [sm.ClosedTrade(TOKEN_X, _dt(10 + i), _dt(11 + i), 1.0, 10.0, 9.0) for i in range(5)]  # pnl -1
        assert sm._health_trend(first_half + second_half, min_required=10, stable_band_pct=0.15) == "dégradation"

    def test_similar_performance_is_stable(self):
        first_half = [sm.ClosedTrade(TOKEN_X, _dt(i), _dt(i + 1), 1.0, 1.0, 2.0) for i in range(5)]  # pnl +1
        second_half = [sm.ClosedTrade(TOKEN_X, _dt(10 + i), _dt(11 + i), 1.0, 1.0, 2.05) for i in range(5)]  # pnl +1.05
        assert sm._health_trend(first_half + second_half, min_required=10, stable_band_pct=0.15) == "stable"


# ---------------------------------------------------------------------------
# Groupement / sélection avec plafond (couche 2, décision opérateur N=20)
# ---------------------------------------------------------------------------

class TestSelectTokensForDeepAnalysis:
    def test_under_cap_all_selected_none_skipped(self):
        grouped = {f"0x{i}": [_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=f"0x{i}", ts=_dt(i))] for i in range(5)}
        selected, found, skipped = sm._select_tokens_for_deep_analysis(grouped, cap=20)
        assert found == 5
        assert skipped == 0
        assert len(selected) == 5

    def test_over_cap_truncates_and_reports_skipped_count(self):
        grouped = {f"0x{i}": [_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=f"0x{i}", ts=_dt(i))] for i in range(25)}
        selected, found, skipped = sm._select_tokens_for_deep_analysis(grouped, cap=20)
        assert found == 25
        assert len(selected) == 20
        assert skipped == 5

    def test_default_cap_matches_operator_decision_n50(self):
        # 20->50 puis ramené à 10 le 14/07, remonté à 50 le 15/07 une fois la
        # file d'attente en arrière-plan (wallet_scan_queue.py) construite --
        # un passage plus lourd est acceptable, les scans répétés ne bloquent
        # plus une réponse Telegram synchrone.
        assert sm.WEIGHTS.max_tokens_analyzed == 50

    def test_most_recent_token_selected_first(self):
        old_token = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xold", ts=_dt(0))
        new_token = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xnew", ts=_dt(10))
        grouped = {"0xold": [old_token], "0xnew": [new_token]}
        selected, _, _ = sm._select_tokens_for_deep_analysis(grouped, cap=1)
        assert selected == ["0xnew"]

    def test_without_wallet_preserves_historical_recency_only_behavior(self):
        """Rétrocompatibilité : sans wallet=, aucun round-trip n'est jamais détecté,
        le tri retombe exactement sur le comportement historique (récence pure)."""
        old_round_trip = [
            _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xold", ts=_dt(0), tx_hash="0xb1"),
            _transfer(from_addr=WALLET_A, to_addr=FUNDER, token="0xold", ts=_dt(1), tx_hash="0xs1"),
        ]
        new_open = [_transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xnew", ts=_dt(10), tx_hash="0xb2")]
        grouped = {"0xold": old_round_trip, "0xnew": new_open}
        selected, _, _ = sm._select_tokens_for_deep_analysis(grouped, cap=1)
        assert selected == ["0xnew"]  # récence pure, round-trip ignoré sans wallet

    def test_round_trip_token_beats_more_recent_open_position(self):
        """15/07 -- correctif réel : un token plus ANCIEN mais avec un round-trip
        achat+vente complet doit passer avant un token plus récent mais encore
        ouvert (jamais clôturable en FIFO -> jamais de PnL/win-rate mesurable)."""
        old_round_trip = [
            _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xclosed", ts=_dt(0), tx_hash="0xb1"),
            _transfer(from_addr=WALLET_A, to_addr=FUNDER, token="0xclosed", ts=_dt(1), tx_hash="0xs1"),
        ]
        new_open = [_transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xopen", ts=_dt(10), tx_hash="0xb2")]
        grouped = {"0xclosed": old_round_trip, "0xopen": new_open}
        selected, _, _ = sm._select_tokens_for_deep_analysis(grouped, wallet=WALLET_A, cap=1)
        assert selected == ["0xclosed"]

    def test_recency_still_breaks_ties_among_round_trip_tokens(self):
        """La récence/fréquence continue de départager -- seulement APRÈS le
        critère round-trip, jamais remplacée."""
        older_closed = [
            _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xolder", ts=_dt(0), tx_hash="0xb1"),
            _transfer(from_addr=WALLET_A, to_addr=FUNDER, token="0xolder", ts=_dt(1), tx_hash="0xs1"),
        ]
        newer_closed = [
            _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xnewer", ts=_dt(5), tx_hash="0xb2"),
            _transfer(from_addr=WALLET_A, to_addr=FUNDER, token="0xnewer", ts=_dt(6), tx_hash="0xs2"),
        ]
        grouped = {"0xolder": older_closed, "0xnewer": newer_closed}
        selected, _, _ = sm._select_tokens_for_deep_analysis(grouped, wallet=WALLET_A, cap=1)
        assert selected == ["0xnewer"]


# ---------------------------------------------------------------------------
# Anti-faux-positif wash-trading multi-token (#157, correction 14/07)
# ---------------------------------------------------------------------------

class TestDominantCounterpartyShareExtraExclusions:
    def test_extra_exclusion_prevents_false_positive(self):
        # Le wallet échange presque exclusivement avec ROUTER -- sans exclusion,
        # ce serait détecté comme wash-trading.
        transfers = [
            _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0)),
            _transfer(from_addr=WALLET_A, to_addr=ROUTER, token=TOKEN_X, ts=_dt(0.1)),
            _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0.2)),
        ]
        without_exclusion = sm._dominant_counterparty_share(transfers, WALLET_A, lp_address=None)
        with_exclusion = sm._dominant_counterparty_share(
            transfers, WALLET_A, lp_address=None, extra_exclusions={ROUTER},
        )
        assert without_exclusion == pytest.approx(1.0)
        assert with_exclusion == pytest.approx(0.0)  # plus assez de contreparties non exclues

    def test_lp_address_and_extra_exclusions_combine(self):
        transfers = [
            _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0)),
            _transfer(from_addr="0xlp", to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0.1)),
            _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0.2)),
        ]
        share = sm._dominant_counterparty_share(
            transfers, WALLET_A, lp_address="0xlp", extra_exclusions={ROUTER},
        )
        assert share == 0.0


class TestBuildDexInfrastructureExclusions:
    def test_counterparty_recurring_across_distinct_tokens_excluded(self):
        """Le bug réel corrigé (14/07) : un wallet actif sur plusieurs tokens via
        le même routeur/pool ne doit PAS être disqualifié -- le routeur revient
        structurellement sur chaque token, ce n'est pas un partenaire de
        wash-trading (typiquement lié à UN seul token/schéma)."""
        grouped = {
            TOKEN_X: [
                _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0)),
                _transfer(from_addr=WALLET_A, to_addr=ROUTER, token=TOKEN_X, ts=_dt(1)),
            ],
            TOKEN_Y: [
                _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_Y, ts=_dt(2)),
            ],
        }
        exclusions = sm._build_dex_infrastructure_exclusions(grouped, WALLET_A)
        assert ROUTER.lower() in exclusions

    def test_counterparty_on_single_token_only_not_excluded(self):
        """Une contrepartie liée à UN SEUL token (pas de récurrence) n'est pas
        traitée comme infra -- reste éligible à une suspicion de wash-trading."""
        grouped = {
            TOKEN_X: [
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0)),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1)),
            ],
        }
        exclusions = sm._build_dex_infrastructure_exclusions(grouped, WALLET_A)
        assert FUNDER.lower() not in exclusions

    def test_wallet_itself_never_in_exclusions(self):
        grouped = {TOKEN_X: [_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0))]}
        exclusions = sm._build_dex_infrastructure_exclusions(grouped, WALLET_A)
        assert WALLET_A.lower() not in exclusions


# ---------------------------------------------------------------------------
# Disqualifiants durs (couche 1)
# ---------------------------------------------------------------------------

class FakeGoPlusClient:
    def __init__(self, *, security=None):
        self._security = security or {}
        self.calls: list[tuple[str, str | None]] = []  # (address, chain_id reçu) -- #157, 14/07

    async def get_address_security(self, address, **kwargs):
        self.calls.append((address, kwargs.get("chain_id")))
        return self._security.get(
            address, AddressSecurity(address=address, flags={}, is_malicious=False, available=True),
        )


class TestHardDisqualifiers:
    @pytest.mark.asyncio
    async def test_contract_wallet_disqualified(self):
        info = AddressInfo(address=WALLET_A, is_contract=True, available=True)
        result = await sm._hard_disqualifiers(WALLET_A, info, [], None)
        assert result.disqualified is True
        assert any("contrat" in r for r in result.reasons)

    @pytest.mark.asyncio
    async def test_wash_trading_disqualified(self):
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        transfers = [
            _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0)),
            _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(0.1)),
            _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0.2)),
            _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(0.3)),
        ]
        result = await sm._hard_disqualifiers(WALLET_A, info, transfers, None)
        assert result.wash_trading_suspected is True
        assert result.disqualified is True

    @pytest.mark.asyncio
    async def test_wash_trading_not_triggered_when_counterparty_excluded(self):
        """Reproduit le scénario exact du bug : contrepartie dominante mais
        excluse (résolue comme infra DEX) -- ne doit PAS disqualifier."""
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        transfers = [
            _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0)),
            _transfer(from_addr=WALLET_A, to_addr=ROUTER, token=TOKEN_X, ts=_dt(0.1)),
            _transfer(from_addr=ROUTER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0.2)),
        ]
        result = await sm._hard_disqualifiers(WALLET_A, info, transfers, None, extra_exclusions={ROUTER})
        assert result.wash_trading_suspected is False
        assert result.disqualified is False

    @pytest.mark.asyncio
    async def test_clean_wallet_not_disqualified(self):
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        transfers = [_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0))]
        result = await sm._hard_disqualifiers(WALLET_A, info, transfers, None)
        assert result.disqualified is False
        assert result.reasons == []

    @pytest.mark.asyncio
    async def test_financed_by_goplus_flagged_wallet_disqualified(self):
        goplus = FakeGoPlusClient(
            security={
                FUNDER: AddressSecurity(
                    address=FUNDER, flags={"sanctioned": True}, is_malicious=True, available=True,
                ),
            },
        )
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        result = await sm._hard_disqualifiers(WALLET_A, info, [], FUNDER, goplus_client=goplus)
        assert result.financed_by_known_malicious is True
        assert result.disqualified is True
        assert any("GoPlus" in r for r in result.reasons)

    @pytest.mark.asyncio
    async def test_goplus_clean_result_not_disqualified(self):
        goplus = FakeGoPlusClient(
            security={FUNDER: AddressSecurity(address=FUNDER, flags={}, is_malicious=False, available=True)},
        )
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        result = await sm._hard_disqualifiers(WALLET_A, info, [], FUNDER, goplus_client=goplus)
        assert result.financed_by_known_malicious is False
        assert result.disqualified is False

    @pytest.mark.asyncio
    async def test_goplus_unavailable_is_a_note_not_a_disqualification(self):
        """Doctrine fail-closed (#157) : une vérification GoPlus indisponible ne
        disqualifie JAMAIS -- et ne doit pas non plus silencieusement valoir
        "non malveillant" sans le dire (financing_check_note porte l'info)."""
        goplus = FakeGoPlusClient(
            security={FUNDER: AddressSecurity(address=FUNDER, available=False, error="timeout GoPlus")},
        )
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        result = await sm._hard_disqualifiers(WALLET_A, info, [], FUNDER, goplus_client=goplus)
        assert result.financed_by_known_malicious is False
        assert result.disqualified is False
        assert result.financing_check_note is not None
        assert "indisponible" in result.financing_check_note

    @pytest.mark.asyncio
    async def test_no_funding_source_no_goplus_call_needed(self):
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        result = await sm._hard_disqualifiers(WALLET_A, info, [], None)
        assert result.financed_by_known_malicious is False
        assert result.financing_check_note is None

    @pytest.mark.asyncio
    async def test_funding_source_chain_forwarded_to_goplus(self):
        # #157, correction 14/07 : la chaîne RÉELLE où funding_source a été
        # trouvé doit atteindre GoPlus -- jamais Base par défaut désormais que
        # le scan couvre 13 chaînes.
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        goplus = FakeGoPlusClient()

        await sm._hard_disqualifiers(
            WALLET_A, info, [], FUNDER, goplus_client=goplus, funding_source_chain="celo",
        )

        assert goplus.calls == [(FUNDER, "42220")]

    @pytest.mark.asyncio
    async def test_no_funding_source_chain_falls_back_to_goplus_default(self):
        # Comportement mono-chaîne historique inchangé : pas de chain_id
        # explicite -> get_address_security retombe sur son propre défaut
        # (Base), _hard_disqualifiers n'invente jamais un chain_id.
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        goplus = FakeGoPlusClient()

        await sm._hard_disqualifiers(WALLET_A, info, [], FUNDER, goplus_client=goplus)

        assert goplus.calls == [(FUNDER, None)]

    @pytest.mark.asyncio
    async def test_unknown_funding_source_chain_falls_back_never_crashes(self):
        info = AddressInfo(address=WALLET_A, is_contract=False, available=True)
        goplus = FakeGoPlusClient()

        await sm._hard_disqualifiers(
            WALLET_A, info, [], FUNDER, goplus_client=goplus, funding_source_chain="not_a_real_chain",
        )

        assert goplus.calls == [(FUNDER, None)]


class TestFundingSourceChainThreading:
    """#157, correction 14/07 -- bout-en-bout via score_wallets : la chaîne où
    funding_source est RÉELLEMENT trouvée doit atteindre GoPlus, pas Base par
    défaut, quand cette chaîne n'est pas la première scannée."""

    @pytest.mark.asyncio
    async def test_funding_source_found_on_non_base_chain_reaches_goplus(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        base_client = FakeBlockscoutClient()  # aucune transaction -- _funding_source échoue sur "base"
        celo_client = FakeBlockscoutClient(
            transactions={
                WALLET_A: BoundedTransactionsResult(
                    transactions=[
                        Transaction(
                            tx_hash="0x1", from_address=FUNDER, to_address=WALLET_A,
                            value_native=1.0, status="ok", method=None, timestamp=_iso(_dt(0)),
                        )
                    ],
                    available=True, truncated=False,
                ),
            },
        )
        goplus = FakeGoPlusClient()

        report = await sm.score_wallets(
            [WALLET_A],
            chains={"base": base_client, "celo": celo_client},  # ordre = base tenté avant celo
            gecko=FakeGeckoTerminalClient(),
            llm=_fake_llm,
            goplus=goplus,
        )

        card = report.wallets[0]
        assert card.funding_source == FUNDER
        assert goplus.calls == [(FUNDER, "42220")]  # 42220 = chain_id Celo, jamais 8453 (Base) par défaut


# ---------------------------------------------------------------------------
# Convergence pairwise (couche 1, wallets soumis ensemble)
# ---------------------------------------------------------------------------

class TestPairwiseConvergence:
    def test_shared_funding_source_flagged(self):
        pairs = sm._pairwise_convergence(
            [WALLET_A, WALLET_B], {WALLET_A.lower(): FUNDER, WALLET_B.lower(): FUNDER},
        )
        assert pairs == [(WALLET_A, WALLET_B)]

    def test_different_funding_sources_no_pair(self):
        pairs = sm._pairwise_convergence(
            [WALLET_A, WALLET_B], {WALLET_A.lower(): FUNDER, WALLET_B.lower(): "0x" + "9" * 40},
        )
        assert pairs == []

    def test_missing_funding_source_no_false_pair(self):
        pairs = sm._pairwise_convergence([WALLET_A, WALLET_B], {WALLET_A.lower(): FUNDER})
        assert pairs == []


# ---------------------------------------------------------------------------
# Drapeau "suspect positif" (couche 3, séparé du score)
# ---------------------------------------------------------------------------

class TestSuspectPositiveFlag:
    def test_below_threshold_on_all_axes_not_flagged(self):
        card = sm.WalletScoreCard(address=WALLET_A, win_rate=0.5, sortino=0.5)
        assert sm._suspect_positive_flag(card) is False

    def test_three_axes_exceeded_flagged(self):
        card = sm.WalletScoreCard(
            address=WALLET_A,
            win_rate=0.8,
            sortino=2.0,
            diversification_total_tokens=4,
            diversification_profitable_tokens=3,
            early_entry_recurrence_count=0,
        )
        assert sm._suspect_positive_flag(card) is True

    def test_only_two_axes_exceeded_not_flagged(self):
        card = sm.WalletScoreCard(address=WALLET_A, win_rate=0.8, sortino=2.0)
        assert sm._suspect_positive_flag(card) is False


# ---------------------------------------------------------------------------
# Prix par tx_hash exact (14/07, complément pool+OHLCV -- cf. rapport
# [VPS Secondaire] 14/07 : preuve de faisabilité + cas réel WETH/USDC
# tx 0x9e7307776cc89b087a6c025c5e0c72deaab78e2e5b85b5179b00dfd6f4d165cc,
# wallet 0x28ce8143bF18b23f7F089e28D5A89CEbFd9A4B3d, swap direct sans
# agrégateur -- vérifié en direct contre Blockscout/GeckoTerminal ce soir).
# ---------------------------------------------------------------------------

_TX = "0xswaptx"
_STABLE = next(iter(sm._STABLECOIN_ADDRESSES_BY_CHAIN["base"]))


class TestHashBasedPrice:
    @pytest.mark.asyncio
    async def test_direct_stable_leg_returns_ratio_price(self):
        client = FakeBlockscoutClient(
            tx_token_transfers={
                _TX: TokenTransfersResult(
                    transfers=[
                        _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=TOKEN_X, ts=_dt(0), amount=2.0, tx_hash=_TX),
                        _transfer(from_addr=POOL_X, to_addr=WALLET_A, token=_STABLE, ts=_dt(0), amount=5000.0, tx_hash=_TX),
                    ],
                    available=True,
                ),
            },
        )
        price = await sm._hash_based_price(client, _TX, TOKEN_X, WALLET_A, chain="base")
        assert price == pytest.approx(2500.0)  # 5000 stable / 2 token

    @pytest.mark.asyncio
    async def test_multihop_no_stable_leg_falls_back_to_none(self):
        """Sortie multi-hop non-stable (ex. token -> autre token) -- repli
        attendu pour la majorité des jambes, pas un cas d'erreur."""
        client = FakeBlockscoutClient(
            tx_token_transfers={
                _TX: TokenTransfersResult(
                    transfers=[
                        _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=TOKEN_X, ts=_dt(0), amount=2.0, tx_hash=_TX),
                        _transfer(from_addr=POOL_X, to_addr=WALLET_A, token=TOKEN_Y, ts=_dt(0), amount=10.0, tx_hash=_TX),
                    ],
                    available=True,
                ),
            },
        )
        price = await sm._hash_based_price(client, _TX, TOKEN_X, WALLET_A, chain="base")
        assert price is None

    @pytest.mark.asyncio
    async def test_wallet_not_party_falls_back_to_none(self):
        """Pattern agrégateur-redirect réel constaté le 14/07 (wallet
        0xbae88c80..., tx 0x9ef4f224...) : ni la jambe token ni la jambe
        stable ne touchent le wallet directement -- jamais deviné."""
        client = FakeBlockscoutClient(
            tx_token_transfers={
                _TX: TokenTransfersResult(
                    transfers=[
                        _transfer(from_addr=ROUTER, to_addr=POOL_X, token=TOKEN_X, ts=_dt(0), amount=2.0, tx_hash=_TX),
                        _transfer(from_addr=POOL_X, to_addr="0xelsewhere", token=_STABLE, ts=_dt(0), amount=5000.0, tx_hash=_TX),
                    ],
                    available=True,
                ),
            },
        )
        price = await sm._hash_based_price(client, _TX, TOKEN_X, WALLET_A, chain="base")
        assert price is None

    @pytest.mark.asyncio
    async def test_ambiguous_double_token_leg_never_guesses(self):
        client = FakeBlockscoutClient(
            tx_token_transfers={
                _TX: TokenTransfersResult(
                    transfers=[
                        _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=TOKEN_X, ts=_dt(0), amount=1.0, tx_hash=_TX),
                        _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=TOKEN_X, ts=_dt(0), amount=1.0, tx_hash=_TX),
                        _transfer(from_addr=POOL_X, to_addr=WALLET_A, token=_STABLE, ts=_dt(0), amount=5000.0, tx_hash=_TX),
                    ],
                    available=True,
                ),
            },
        )
        price = await sm._hash_based_price(client, _TX, TOKEN_X, WALLET_A, chain="base")
        assert price is None

    @pytest.mark.asyncio
    async def test_ambiguous_double_stable_leg_never_guesses(self):
        stable_2 = sorted(sm._STABLECOIN_ADDRESSES_BY_CHAIN["base"])[1]
        client = FakeBlockscoutClient(
            tx_token_transfers={
                _TX: TokenTransfersResult(
                    transfers=[
                        _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=TOKEN_X, ts=_dt(0), amount=2.0, tx_hash=_TX),
                        _transfer(from_addr=POOL_X, to_addr=WALLET_A, token=_STABLE, ts=_dt(0), amount=2500.0, tx_hash=_TX),
                        _transfer(from_addr=POOL_X, to_addr=WALLET_A, token=stable_2, ts=_dt(0), amount=2500.0, tx_hash=_TX),
                    ],
                    available=True,
                ),
            },
        )
        price = await sm._hash_based_price(client, _TX, TOKEN_X, WALLET_A, chain="base")
        assert price is None

    @pytest.mark.asyncio
    async def test_blockscout_unavailable_returns_none_never_raises(self):
        client = FakeBlockscoutClient(
            tx_token_transfers={_TX: TokenTransfersResult(available=False, error="timeout")},
        )
        price = await sm._hash_based_price(client, _TX, TOKEN_X, WALLET_A, chain="base")
        assert price is None

    @pytest.mark.asyncio
    async def test_unsupported_chain_returns_none_even_with_a_clean_stable_leg(self):
        """Registre stablecoin vide pour une chaîne (ethereum, pas encore
        couvert par ce chantier) -- repli systématique, pas un manque
        silencieux (cf. docstring de _STABLECOIN_ADDRESSES_BY_CHAIN)."""
        client = FakeBlockscoutClient(
            tx_token_transfers={
                _TX: TokenTransfersResult(
                    transfers=[
                        _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=TOKEN_X, ts=_dt(0), amount=2.0, tx_hash=_TX),
                        _transfer(from_addr=POOL_X, to_addr=WALLET_A, token=_STABLE, ts=_dt(0), amount=5000.0, tx_hash=_TX),
                    ],
                    available=True,
                ),
            },
        )
        price = await sm._hash_based_price(client, _TX, TOKEN_X, WALLET_A, chain="ethereum")
        assert price is None

    @pytest.mark.asyncio
    async def test_client_none_returns_none(self):
        price = await sm._hash_based_price(None, _TX, TOKEN_X, WALLET_A, chain="base")
        assert price is None


# ---------------------------------------------------------------------------
# Bout-en-bout mocké : score_wallets
# ---------------------------------------------------------------------------

class FakeBlockscoutClient:
    def __init__(self, *, address_infos=None, transfers=None, transactions=None, tx_token_transfers=None):
        self._address_infos = address_infos or {}
        self._transfers = transfers or {}
        self._transactions = transactions or {}
        # tx_hash -> TokenTransfersResult (14/07, prix par hash exact) -- vide
        # par défaut : `_hash_based_price` retombe alors systématiquement sur
        # pool+OHLCV pour tous les tests qui ne mockent pas cette méthode
        # explicitement, comportement identique à avant ce chantier.
        self._tx_token_transfers = tx_token_transfers or {}
        self.tx_token_transfers_calls: list[str] = []

    async def get_address_info(self, address):
        return self._address_infos.get(
            address, AddressInfo(address=address, is_contract=False, available=True)
        )

    async def get_token_transfers(self, address, limit=50, *, max_pages=1, token_type=None):
        return self._transfers.get(address, TokenTransfersResult(transfers=[], available=True))

    async def get_transactions_bounded(self, address, *, max_pages=5):
        return self._transactions.get(
            address, BoundedTransactionsResult(transactions=[], available=True, truncated=False)
        )

    async def get_transaction_token_transfers(self, tx_hash):
        self.tx_token_transfers_calls.append(tx_hash)
        return self._tx_token_transfers.get(
            tx_hash, TokenTransfersResult(available=False, error="non mocké dans ce test")
        )


class FakeGeckoTerminalClient:
    """``pool_for_token`` mappe token -> pool résolu (peut différer, comme en
    réalité) ; ``ohlcv`` est keyé par POOL address (jamais le token), reflétant
    le comportement réel post-correctif de `resolve_primary_pool`. ``reserve_usd_for_token``
    (15/07, défense anti-dust/scam-pool) : liquidité confirmée du pool résolu --
    absente/``None`` par défaut (fail-open, comportement historique inchangé)."""

    def __init__(
        self, *, pool_for_token=None, pool_created_at=None, ohlcv=None, reserve_usd_for_token=None,
        pool_error_for_token=None,
    ):
        self._pool_for_token = pool_for_token or {}
        self._pool_created_at = pool_created_at or {}
        self._ohlcv = ohlcv or {}
        self._reserve_usd_for_token = reserve_usd_for_token or {}
        # 15/07, revue Gemini -- gel des erreurs transitoires : simule une panne
        # D'INFRASTRUCTURE GeckoTerminal (timeout/429/erreur serveur) plutôt que
        # le verdict de donnée par défaut "aucun pool trouvé pour ce token".
        self._pool_error_for_token = pool_error_for_token or {}
        self.get_ohlcv_calls: list[tuple[str, dict]] = []  # (pool_address, kwargs reçus) -- #182, 15/07

    async def resolve_primary_pool(self, token_address, **kwargs):
        pool_address = self._pool_for_token.get(token_address)
        if pool_address is None:
            error = self._pool_error_for_token.get(token_address, "aucun pool trouvé pour ce token")
            return PoolMetadata(pool_address=token_address, available=False, error=error)
        return PoolMetadata(
            pool_address=pool_address,
            created_at=self._pool_created_at.get(token_address),
            reserve_usd=self._reserve_usd_for_token.get(token_address),
            available=True,
        )

    async def get_ohlcv(self, pool_address, **kwargs):
        self.get_ohlcv_calls.append((pool_address, kwargs))  # #182, 15/07 -- correctif de vitesse
        return self._ohlcv.get(pool_address, OHLCVResult(candles=[], available=False, error="indisponible"))


def _flat_ohlcv(price: float, *, n: int = 30, start: datetime | None = None) -> OHLCVResult:
    start = start or _dt(-5)
    candles = [
        Candle(ts=int((start + timedelta(hours=i)).timestamp()), open=price, high=price, low=price, close=price, volume=1000.0)
        for i in range(n)
    ]
    return OHLCVResult(candles=candles, available=True)


async def _fake_llm(prompt, system, *, max_tokens=800, model=None, depth=None, **kwargs):
    return '{"wallets": [{"address": "%s", "thesis": "thèse test"}], "synthesis": "synthèse test"}' % WALLET_A


def _clean_goplus() -> FakeGoPlusClient:
    return FakeGoPlusClient()


@pytest.fixture(autouse=True)
def _cmc_unavailable_by_default(monkeypatch):
    """#157, 14/07 : CoinMarketCap (3e couche) est tenté à chaque échec
    GeckoTerminal, INDÉPENDAMMENT du résultat DexScreener (cf.
    `TestCmcPricingRecovery`) -- sans ce défaut, tous les tests existants qui
    font échouer GeckoTerminal déclencheraient un VRAI appel réseau CMC non
    mocké. Par défaut CMC échoue aussi (`available=False`), comme s'il n'était
    pas configuré -- les tests qui veulent vérifier la récupération CMC
    surchargent ce défaut localement via `monkeypatch.setattr`."""
    from aria_core.services.coinmarketcap import OHLCVResult as CmcOHLCVResult
    from aria_core.services.coinmarketcap import PoolMetadata as CmcPoolMetadata

    async def _unavailable_pool(token_address, *, network_slug="base"):
        return CmcPoolMetadata(pool_address=token_address, available=False, error="CMC non mocké dans ce test")

    async def _unavailable_ohlcv(pool_address, *, network_slug="base"):
        return CmcOHLCVResult(candles=[], available=False, error="CMC non mocké dans ce test")

    monkeypatch.setattr("aria_core.services.coinmarketcap.resolve_primary_pool", _unavailable_pool)
    monkeypatch.setattr("aria_core.services.coinmarketcap.get_ohlcv", _unavailable_ohlcv)


class TestScoreWalletsValidation:
    @pytest.mark.asyncio
    async def test_no_addresses(self):
        report = await sm.score_wallets([], client=FakeBlockscoutClient(), gecko=FakeGeckoTerminalClient())
        assert report.available is False

    @pytest.mark.asyncio
    async def test_more_than_three_addresses(self):
        report = await sm.score_wallets(
            [WALLET_A, WALLET_B, WALLET_C, "0x" + "d" * 40],
            client=FakeBlockscoutClient(), gecko=FakeGeckoTerminalClient(),
        )
        assert report.available is False
        assert "3" in report.error

    @pytest.mark.asyncio
    async def test_invalid_address_format(self):
        report = await sm.score_wallets(["not-an-address"], client=FakeBlockscoutClient(), gecko=FakeGeckoTerminalClient())
        assert report.available is False


class TestScoreWalletsEndToEnd:
    @pytest.mark.asyncio
    async def test_transfers_unavailable_marks_wallet_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        client = FakeBlockscoutClient(
            transfers={WALLET_A: TokenTransfersResult(available=False, error="donnée on-chain indisponible")}
        )
        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=FakeGeckoTerminalClient(), llm=_fake_llm, goplus=_clean_goplus(),
        )
        assert report.available is True
        assert report.wallets[0].available is False

    @pytest.mark.asyncio
    async def test_profitable_wallet_scored_and_thesis_attached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        pool_created = _dt(-1)
        buy_ts = _dt(0)
        sell_ts = _dt(2)

        transfers = TokenTransfersResult(
            transfers=[
                # Deux achats (pas un seul apport massif) -> largest_share=0.5 <=
                # _LARGEST_BUY_SHARE_MAX (0.7), "taille contrôlée" au sens existant.
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0),
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=sell_ts, amount=10.0),
            ],
            available=True,
        )
        # Prix en escalier : 1.0 avant/à l'achat, 2.0 après -- assure une jambe
        # gagnante réelle (buy_price=1.0, sell_price=2.0), pas un prix plat.
        step_candles = [
            Candle(ts=int(_dt(-2).timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
            Candle(ts=int(buy_ts.timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
            Candle(ts=int(_dt(1).timestamp()), open=2.0, high=2.0, low=2.0, close=2.0, volume=1000.0),
            Candle(ts=int(sell_ts.timestamp()), open=2.0, high=2.0, low=2.0, close=2.0, volume=1000.0),
        ]
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: pool_created},
            ohlcv={POOL_X: OHLCVResult(candles=step_candles, available=True)},
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.available is True
        assert card.closed_trades_count == 2  # FIFO : la vente de 10 consomme les 2 achats de 5
        assert card.win_rate == pytest.approx(1.0)
        assert card.realized_pnl_usd == pytest.approx(10.0)  # (5+5) * (2.0 - 1.0)
        assert card.tokens_found == 1
        assert card.tokens_analyzed == 1
        assert card.tokens_skipped_capped is False
        assert card.early_entry_recurrence_count == 1  # achat dans la fenêtre de 3j après création
        assert card.thesis == "thèse test"
        assert report.synthesis == "synthèse test"

    @pytest.mark.asyncio
    async def test_pool_lookup_errors_surfaced_on_card(self, tmp_path, monkeypatch):
        # #157, 14/07 : distingue "pool jamais trouvé sur GeckoTerminal" (token
        # trop obscur/mort) d'un autre problème de valorisation -- jamais un
        # diagnostic perdu en silence.
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        buy_ts = _dt(0)
        transfers = TokenTransfersResult(
            transfers=[_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0)],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        # Aucune entrée dans pool_for_token -> resolve_primary_pool échoue pour TOKEN_X.
        gecko = FakeGeckoTerminalClient()

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.available is True
        assert card.pool_lookup_errors == 1
        assert card.unpriced_legs == 1

    @pytest.mark.asyncio
    async def test_dexscreener_gap_flagged_when_gecko_misses_a_real_pair(self, tmp_path, monkeypatch):
        # #157, 14/07 : triangulation -- GeckoTerminal ne résout aucun pool mais
        # DexScreener confirme une paire réelle -> écart de source signalé
        # explicitement, distinct d'un simple "token illiquide".
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return True

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)

        buy_ts = _dt(0)
        transfers = TokenTransfersResult(
            transfers=[_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0)],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()  # aucun pool connu -> resolve_primary_pool échoue

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.pool_lookup_errors == 1
        assert card.gecko_dexscreener_gap_count == 1

    @pytest.mark.asyncio
    async def test_no_dexscreener_gap_when_both_sources_agree_no_pool(self, tmp_path, monkeypatch):
        # Les deux sources d'accord (aucune paire nulle part) -> pas de faux
        # signal d'écart, jamais confondu avec le cas ci-dessus.
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return False

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)

        buy_ts = _dt(0)
        transfers = TokenTransfersResult(
            transfers=[_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0)],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.pool_lookup_errors == 1
        assert card.gecko_dexscreener_gap_count == 0

    @pytest.mark.asyncio
    async def test_no_dexscreener_gap_when_verification_itself_unavailable(self, tmp_path, monkeypatch):
        # DexScreener indisponible (None) -- jamais confondu avec un écart
        # confirmé (True) : on ne peut simplement pas trancher, donc pas de gap.
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return None

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)

        buy_ts = _dt(0)
        transfers = TokenTransfersResult(
            transfers=[_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0)],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.pool_lookup_errors == 1
        assert card.gecko_dexscreener_gap_count == 0

    @pytest.mark.asyncio
    async def test_cap_reached_logs_explicitly_never_silent(self, tmp_path, monkeypatch, caplog):
        """#157 -- au-delà du plafond, log EXPLICITE, jamais une troncature
        silencieuse. Utilise ``max_tokens`` explicite (#157, 14/07 : override
        pour des re-scans plus rapides une fois les bons wallets identifiés)
        plutôt que le défaut (relevé 20->50 le même jour) pour garder ce test
        rapide et lisible."""
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=f"0x{i:040d}", ts=_dt(i))
                for i in range(25)
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()

        with caplog.at_level("INFO"):
            report = await sm.score_wallets(
                [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(), max_tokens=20,
            )

        card = report.wallets[0]
        assert card.tokens_found == 25
        assert card.tokens_analyzed == 20
        assert card.tokens_skipped_capped is True
        assert any("plafond" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_tokens_found_total_never_regresses_across_scans(self, tmp_path, monkeypatch):
        """16/07 : `get_token_transfers` est plafonné (2000 transferts/10 pages) --
        pour un wallet très actif, la fenêtre des N derniers transferts capturée à
        un passage peut différer du passage précédent (nouvelle activité qui pousse
        d'anciens tokens hors de la fenêtre), faisant apparaître MOINS de tokens
        distincts qu'avant. Le total affiché ne doit jamais redescendre -- sinon la
        progression semble incohérente ET une fausse "couverture 100%" pourrait se
        déclencher à tort (cf. commentaire dans smart_money.py)."""
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        # 25 tokens, timestamps croissants avec l'indice (_dt(i) = 1er jan 2026 + i
        # jours) -- donc plus l'indice est haut, plus c'est RÉCENT. Sans round-trip
        # (achat seul), le tri de sélection ne départage que par récence : avec
        # cap=10, le round 1 sélectionne les 10 plus récents (indices 15-24),
        # laissant les 15 plus anciens (0-14) non scannés.
        client = FakeBlockscoutClient(transfers={
            WALLET_A: TokenTransfersResult(
                transfers=[
                    _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=f"0x{i:040d}", ts=_dt(i))
                    for i in range(25)
                ],
                available=True,
            )
        })
        gecko = FakeGeckoTerminalClient()

        first = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(), max_tokens=10,
        )
        assert first.wallets[0].tokens_found == 25
        assert first.wallets[0].full_coverage is False  # 10/25 scannés, pas encore complet

        # Passage suivant : la fenêtre de pagination "recule" et ne montre plus que
        # les 10 tokens les PLUS RÉCENTS (15-24) -- exactement ceux déjà scannés au
        # round 1. Les 15 plus anciens (0-14, jamais scannés) sont désormais
        # invisibles dans TOUTE fenêtre, passée ou présente -- ils ne seront plus
        # jamais retentés. Sans le correctif, `total_found` serait écrasé à 10 (ce
        # que CE round voit) -- et comme ces 10 sont déjà dans `scanned_tokens`,
        # 10 >= 10 déclencherait à tort une couverture 100%, alors qu'il manque
        # réellement 15 tokens jamais vus.
        client._transfers[WALLET_A] = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=f"0x{i:040d}", ts=_dt(i))
                for i in range(15, 25)
            ],
            available=True,
        )
        second = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(), max_tokens=10,
        )
        assert second.wallets[0].tokens_found == 25
        assert second.wallets[0].full_coverage is False

    @pytest.mark.asyncio
    async def test_active_multi_token_wallet_via_shared_routers_not_falsely_disqualified(self, tmp_path, monkeypatch):
        """Test dédié demandé par l'opérateur (14/07) : un wallet actif sur
        plusieurs tokens (jusqu'au plafond) via les MÊMES 1-2 routeurs/pools DEX
        ne doit PAS être disqualifié à tort pour wash-trading -- c'est la
        mécanique normale d'un DEX, pas un signal de manipulation."""
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        n_tokens = 10
        tokens = [f"0x{i:040d}" for i in range(n_tokens)]
        # Chaque token est tradé via l'un des DEUX routeurs partagés (alternance) --
        # aucune contrepartie individuelle ne domine un SEUL token, mais ROUTER_1/
        # ROUTER_2 reviennent chacun sur plusieurs tokens distincts.
        router_1, router_2 = "0x" + "d" * 40, "0x" + "e" * 40
        transfers = []
        pool_for_token = {}
        for i, token in enumerate(tokens):
            router = router_1 if i % 2 == 0 else router_2
            transfers.append(_transfer(from_addr=router, to_addr=WALLET_A, token=token, ts=_dt(i)))
            transfers.append(_transfer(from_addr=WALLET_A, to_addr=router, token=token, ts=_dt(i + 0.5)))
            pool_for_token[token] = f"pool-{i}"  # pas de pool GeckoTerminal résolu -> teste juste la couche 1

        client = FakeBlockscoutClient(transfers={WALLET_A: TokenTransfersResult(transfers=transfers, available=True)})
        gecko = FakeGeckoTerminalClient()  # aucun pool résolu -- isole le fix couche 1 (récurrence), pas la couche 2

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.available is True
        assert card.disqualified is False, f"faussement disqualifié : {card.disqualification_reasons}"
        assert card.disqualification_reasons == []

    @pytest.mark.asyncio
    async def test_two_wallets_sharing_funding_source_flagged_as_convergent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        client = FakeBlockscoutClient(
            transactions={
                WALLET_A: BoundedTransactionsResult(
                    transactions=[
                        Transaction(
                            tx_hash="0x1", from_address=FUNDER, to_address=WALLET_A,
                            value_native=1.0, status="ok", method=None, timestamp=_iso(_dt(0)),
                        )
                    ],
                    available=True, truncated=False,
                ),
                WALLET_B: BoundedTransactionsResult(
                    transactions=[
                        Transaction(
                            tx_hash="0x2", from_address=FUNDER, to_address=WALLET_B,
                            value_native=1.0, status="ok", method=None, timestamp=_iso(_dt(0)),
                        )
                    ],
                    available=True, truncated=False,
                ),
            },
        )
        report = await sm.score_wallets(
            [WALLET_A, WALLET_B], client=client, gecko=FakeGeckoTerminalClient(), llm=_fake_llm, goplus=_clean_goplus(),
        )

        assert report.convergence_pairs == [(WALLET_A, WALLET_B)]

    @pytest.mark.asyncio
    async def test_ens_name_cosmetic_never_affects_score(self, tmp_path, monkeypatch):
        """Doctrine explicite (#157) : un wallet nommé (ENS/Basename) n'est ni
        meilleur ni pire qu'un wallet anonyme -- vérifie que deux wallets
        identiques en tout, sauf le nom, obtiennent le MÊME résultat de score."""
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=5.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1), amount=5.0),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(
            address_infos={
                WALLET_A: AddressInfo(address=WALLET_A, is_contract=False, ens_domain_name="named.base.eth", available=True),
            },
            transfers={WALLET_A: transfers},
        )
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        card = report.wallets[0]

        assert card.display_name == "named.base.eth"
        # le nom n'apparaît dans AUCUN champ de score -- juste display_name
        assert "named.base.eth" not in str(card.win_rate)
        assert card.suspect_positive == sm._suspect_positive_flag(card)  # calcul indépendant du nom


class TestComparativeRanking:
    """15/07, décision opérateur : classement percentile parmi les AUTRES
    wallets déjà notés (`wallet_score_log`), jamais un pourcentage inventé sur
    une population vide/unitaire."""

    @staticmethod
    def _mk_transfers(wallet: str) -> TokenTransfersResult:
        return TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=wallet, token=TOKEN_X, ts=_dt(0), amount=10.0),
                _transfer(from_addr=wallet, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0),
            ],
            available=True,
        )

    @staticmethod
    def _candles(sell_price: float) -> list:
        return [
            Candle(ts=int(_dt(-2).timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
            Candle(ts=int(_dt(0).timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
            Candle(ts=int(_dt(2).timestamp()), open=sell_price, high=sell_price, low=sell_price, close=sell_price, volume=1000.0),
        ]

    @pytest.mark.asyncio
    async def test_first_wallet_ever_scored_has_no_comparison_population(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        client = FakeBlockscoutClient(transfers={WALLET_A: self._mk_transfers(WALLET_A)})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=self._candles(2.0), available=True)},
        )
        report = await sm.score_wallets([WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus())
        card = report.wallets[0]
        assert card.compared_against_n_wallets == 0
        assert card.composite_percentile is None
        assert card.percentile_win_rate is None
        assert card.percentile_pnl is None

    @staticmethod
    def _mk_hash_priced_transfers(wallet: str) -> TokenTransfersResult:
        # 15/07, suite du correctif #175 (comparabilité percentile) : jambes
        # hash-exactes (tx_hash + jambe stablecoin) pour que ce wallet soit
        # `price_confidence_low=False` et reste éligible à la population de
        # comparaison -- sinon le nouveau filtre l'exclurait silencieusement
        # et le test ne mesurerait plus le mécanisme de percentile qu'il vise.
        return TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=wallet, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xbuy"),
                _transfer(from_addr=wallet, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0, tx_hash="0xsell"),
            ],
            available=True,
        )

    @staticmethod
    def _hash_priced_tx_transfers(wallet: str, *, sell_price: float) -> dict:
        return {
            "0xbuy": TokenTransfersResult(
                transfers=[
                    _transfer(from_addr=FUNDER, to_addr=wallet, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xbuy"),
                    _transfer(from_addr=wallet, to_addr=FUNDER, token=_STABLE, ts=_dt(0), amount=10.0, tx_hash="0xbuy"),
                ],
                available=True,
            ),
            "0xsell": TokenTransfersResult(
                transfers=[
                    _transfer(from_addr=wallet, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0, tx_hash="0xsell"),
                    _transfer(from_addr=FUNDER, to_addr=wallet, token=_STABLE, ts=_dt(2), amount=10.0 * sell_price, tx_hash="0xsell"),
                ],
                available=True,
            ),
        }

    @pytest.mark.asyncio
    async def test_winning_wallet_ranks_above_a_previously_scored_loser(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        loser_client = FakeBlockscoutClient(
            transfers={WALLET_A: self._mk_hash_priced_transfers(WALLET_A)},
            tx_token_transfers=self._hash_priced_tx_transfers(WALLET_A, sell_price=0.5),
        )
        loser_gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=self._candles(0.5), available=True)},  # buy 1.0 -> sell 0.5 : perte
        )
        await sm.score_wallets([WALLET_A], client=loser_client, gecko=loser_gecko, llm=_fake_llm, goplus=_clean_goplus())

        winner_client = FakeBlockscoutClient(
            transfers={WALLET_B: self._mk_hash_priced_transfers(WALLET_B)},
            tx_token_transfers=self._hash_priced_tx_transfers(WALLET_B, sell_price=2.0),
        )
        winner_gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=self._candles(2.0), available=True)},  # buy 1.0 -> sell 2.0 : gain
        )
        report_b = await sm.score_wallets(
            [WALLET_B], client=winner_client, gecko=winner_gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        card_b = report_b.wallets[0]
        assert card_b.price_confidence_low is False  # confirme la prémisse (hash-exact, pas exclu)

        assert card_b.compared_against_n_wallets == 1
        assert card_b.percentile_win_rate == pytest.approx(100.0)
        assert card_b.percentile_pnl == pytest.approx(100.0)
        assert card_b.percentile_diversification == pytest.approx(100.0)
        # Sortino indisponible des deux côtés (1 seul trade clôturé chacun,
        # sous min_closed_trades_for_sortino) -- exclu du composite, pas un 0 inventé.
        assert card_b.percentile_sortino is None
        assert card_b.composite_percentile == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_wallet_never_compared_against_its_own_previous_score(self, tmp_path, monkeypatch):
        """Re-scorer LE MÊME wallet ne doit jamais le comparer à sa propre
        entrée précédente dans wallet_score_log (auto-comparaison exclue)."""
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        client = FakeBlockscoutClient(transfers={WALLET_A: self._mk_transfers(WALLET_A)})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=self._candles(2.0), available=True)},
        )
        await sm.score_wallets([WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus())
        report_again = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        assert report_again.wallets[0].compared_against_n_wallets == 0

    @pytest.mark.asyncio
    async def test_partially_covered_wallet_excluded_from_comparison_population(self, tmp_path, monkeypatch):
        # 15/07, revue Gemini -- pollution asymétrique du percentile : un
        # wallet scanné une seule fois (full_coverage=False, seuls quelques
        # tokens prioritaires analysés) ne doit jamais servir de référence
        # dans le classement comparatif d'un AUTRE wallet -- son score est
        # temporairement biaisé, pas une mesure fiable de sa performance
        # globale.
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        # WALLET_A a 2 tokens mais un plafond d'analyse de 1 -> full_coverage=False.
        partial_transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0),
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_Y, ts=_dt(0), amount=10.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_Y, ts=_dt(2), amount=10.0),
            ],
            available=True,
        )
        partial_client = FakeBlockscoutClient(transfers={WALLET_A: partial_transfers})
        partial_gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X, TOKEN_Y: POOL_Y},
            pool_created_at={TOKEN_X: _dt(-1), TOKEN_Y: _dt(-1)},
            ohlcv={
                POOL_X: OHLCVResult(candles=self._candles(2.0), available=True),
                POOL_Y: OHLCVResult(candles=self._candles(2.0), available=True),
            },
        )
        partial_report = await sm.score_wallets(
            [WALLET_A], client=partial_client, gecko=partial_gecko, llm=_fake_llm, goplus=_clean_goplus(), max_tokens=1,
        )
        assert partial_report.wallets[0].full_coverage is False  # confirme la prémisse du test

        winner_client = FakeBlockscoutClient(transfers={WALLET_B: self._mk_transfers(WALLET_B)})
        winner_gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=self._candles(2.0), available=True)},
        )
        report_b = await sm.score_wallets(
            [WALLET_B], client=winner_client, gecko=winner_gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        # WALLET_A (partiel) exclu -- aucune population de comparaison valide.
        assert report_b.wallets[0].compared_against_n_wallets == 0


class TestCmcPricingRecovery:
    """#157, 14/07 : CoinMarketCap comme 3e couche de PRICING (pas un 3e
    diagnostic comme DexScreener) -- tentée à chaque échec GeckoTerminal,
    INDÉPENDAMMENT du résultat DexScreener (corrigé en review : un if/else
    aurait empêché CMC de tourner dès que DexScreener confirmait une paire,
    alors que DexScreener ne fournit aucun prix historique lui-même)."""

    def _mock_cmc_success(self, monkeypatch, *, token_address, pool_address, price: float = 1.0):
        from aria_core.services.coinmarketcap import OHLCVResult as CmcOHLCVResult
        from aria_core.services.coinmarketcap import PoolMetadata as CmcPoolMetadata

        async def _resolve(addr, *, network_slug="base"):
            if addr == token_address:
                return CmcPoolMetadata(pool_address=pool_address, available=True)
            return CmcPoolMetadata(pool_address=addr, available=False, error="pas ce token")

        async def _ohlcv(pool_addr, *, network_slug="base"):
            if pool_addr == pool_address:
                return _flat_ohlcv(price, start=_dt(-2))
            return CmcOHLCVResult(candles=[], available=False, error="pas ce pool")

        monkeypatch.setattr("aria_core.services.coinmarketcap.resolve_primary_pool", _resolve)
        monkeypatch.setattr("aria_core.services.coinmarketcap.get_ohlcv", _ohlcv)

    @pytest.mark.asyncio
    async def test_cmc_recovers_pricing_when_gecko_and_dexscreener_both_fail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return False  # DexScreener non plus n'a rien trouvé

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)
        self._mock_cmc_success(monkeypatch, token_address=TOKEN_X, pool_address=POOL_X)

        buy_ts = _dt(0)
        sell_ts = _dt(1)
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=sell_ts, amount=5.0),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()  # aucun pool connu -> échoue pour TOKEN_X

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.cmc_price_recovery_count == 1
        assert card.closed_trades_count == 1
        assert card.unpriced_legs == 0

    @pytest.mark.asyncio
    async def test_cmc_still_attempted_even_when_dexscreener_confirms_a_pair(self, tmp_path, monkeypatch):
        # Correction demandée en review (14/07) : `has_any_pair == True` ne
        # doit JAMAIS empêcher CMC de tenter sa propre résolution -- DexScreener
        # confirme qu'une paire EXISTE, mais ne la price pas lui-même. Les deux
        # compteurs (diagnostic gap + récupération de prix) doivent être vrais
        # simultanément sur ce même token.
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return True  # DexScreener CONFIRME une paire

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)
        self._mock_cmc_success(monkeypatch, token_address=TOKEN_X, pool_address=POOL_X)

        buy_ts = _dt(0)
        sell_ts = _dt(1)
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=sell_ts, amount=5.0),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.gecko_dexscreener_gap_count == 1
        assert card.cmc_price_recovery_count == 1
        assert card.closed_trades_count == 1

    @pytest.mark.asyncio
    async def test_cmc_never_called_when_gecko_already_succeeded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fail_if_called(*args, **kwargs):
            raise AssertionError("CMC ne doit jamais être appelé quand GeckoTerminal a déjà résolu le pool")

        monkeypatch.setattr("aria_core.services.coinmarketcap.resolve_primary_pool", _fail_if_called)
        monkeypatch.setattr("aria_core.services.coinmarketcap.get_ohlcv", _fail_if_called)

        buy_ts = _dt(0)
        sell_ts = _dt(1)
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=sell_ts, amount=5.0),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.cmc_price_recovery_count == 0
        assert card.closed_trades_count == 1

    @pytest.mark.asyncio
    async def test_all_three_sources_fail_still_degrades_softly(self, tmp_path, monkeypatch):
        # Gecko, DexScreener ET CMC échouent tous -- comportement inchangé
        # (unpriced_legs incrémenté), jamais une exception qui remonte.
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return None  # vérification elle-même indisponible

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)
        # CMC reste sur le défaut autouse (_cmc_unavailable_by_default) -- échoue aussi.

        buy_ts = _dt(0)
        transfers = TokenTransfersResult(
            transfers=[_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0)],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.available is True
        assert card.cmc_price_recovery_count == 0
        assert card.gecko_dexscreener_gap_count == 0
        assert card.unpriced_legs == 1


class TestMaxHashPricedLegsPerToken:
    """Plafond (correction opérateur, 14/07) : un wallet actif peut avoir des
    dizaines/centaines de tx_hash distincts sur UN token -- sans plafond,
    autant d'appels Blockscout séquentiels supplémentaires par requête. Vérifie
    que le nombre d'appels à ``get_transaction_token_transfers`` ne dépasse
    JAMAIS ``WEIGHTS.max_hash_priced_legs_per_token``, et que les jambes
    au-delà du plafond restent valorisées via pool+OHLCV (aucun abandon
    silencieux)."""

    @pytest.mark.asyncio
    async def test_hash_lookups_capped_legs_beyond_fall_back_to_ohlcv(self, tmp_path, monkeypatch):
        from dataclasses import replace

        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        monkeypatch.setattr(sm, "WEIGHTS", replace(sm.WEIGHTS, max_hash_priced_legs_per_token=3))

        n_buys = 6
        buys = [
            _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(i), amount=1.0, tx_hash=f"0xbuy{i}")
            for i in range(n_buys)
        ]
        sell = _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(n_buys), amount=float(n_buys), tx_hash="0xsell")
        transfers = TokenTransfersResult(transfers=[*buys, sell], available=True)
        # Aucun hash mocké dans tx_token_transfers -- toutes les jambes
        # retombent sur pool+OHLCV, ce qui isole précisément le comportement
        # du plafond (nombre d'appels), pas la résolution de prix elle-même.
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.available is True
        # 7 tx_hash distincts au total (6 achats + 1 vente) -- plafonné à 3,
        # jamais 7 (un par jambe).
        assert len(client.tx_token_transfers_calls) == 3
        assert client.tx_token_transfers_calls == ["0xbuy0", "0xbuy1", "0xbuy2"]  # ordre chronologique
        # Toutes les jambes restent néanmoins valorisées via pool+OHLCV --
        # aucun abandon silencieux au-delà du plafond.
        assert card.unpriced_legs == 0
        assert card.closed_trades_count >= 1


class TestHashPriceRealSwapRegression:
    """Cas réel capturé en direct le 14/07 -- swap WETH->USDC direct (Uniswap
    V3 SwapRouter02 `exactInputSingle`, sans agrégateur/redirection),
    wallet 0x28ce8143bF18b23f7F089e28D5A89CEbFd9A4B3d, tx
    0x9e7307776cc89b087a6c025c5e0c72deaab78e2e5b85b5179b00dfd6f4d165cc :
    - jambe WETH : wallet -> pool, 0.000066281897035081 WETH (vente).
    - jambe USDC : pool -> wallet, 0.123906 USDC (réception directe).
    - ratio implicite : ~1869.38 USDC/WETH (cohérent avec le exchange_rate
      spot WETH ~1878.39 relevé au même moment sur Blockscout).

    Contrairement au premier cas trouvé le 14/07 (tx 0x9ef4f224..., wallet
    0xbae88c80... -- pattern agrégateur `strictlySwapAndCall`, aucune jambe ne
    touche le wallet directement, DONC INUTILISABLE pour ce test), celui-ci
    est un swap direct : jambe token ET jambe stable touchent bien le wallet.

    Le pool+OHLCV mocké renvoie volontairement un prix TRÈS différent (500.0,
    contre ~1869 réel) aux deux timestamps -- si le prix par hash n'était pas
    réellement utilisé pour la jambe de vente, ce test échouerait en
    détectant 500.0 au lieu de ~1869.38."""

    _WALLET = "0x28ce8143bF18b23f7F089e28D5A89CEbFd9A4B3d"
    _WETH = "0x4200000000000000000000000000000000000006"
    _TX = "0x9e7307776cc89b087a6c025c5e0c72deaab78e2e5b85b5179b00dfd6f4d165cc"
    _POOL = "0x6c561b446416e1a00e8e93e221854d6ea4171372"  # WETH/USDC 0.3%, la plus forte liquidité/volume
    _WRONG_OHLCV_PRICE = 500.0
    _REAL_RATIO_PRICE = 0.123906 / 0.000066281897035081  # ~1869.379

    @pytest.mark.asyncio
    async def test_sell_leg_priced_from_real_tx_hash_not_from_mocked_pool_price(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        buy_ts = _dt(-1)
        sell_ts = datetime(2026, 7, 14, 19, 24, 55, tzinfo=timezone.utc)

        transfers = TokenTransfersResult(
            transfers=[
                # Achat synthétique antérieur -- AUCUN hash mocké dans
                # tx_token_transfers, retombe volontairement sur pool+OHLCV
                # (prouve que les deux mécanismes coexistent sur le même token).
                _transfer(
                    from_addr=FUNDER, to_addr=self._WALLET, token=self._WETH,
                    ts=buy_ts, amount=0.001, tx_hash="0xbuy-not-mocked",
                ),
                # Vente réelle -- hash mocké ci-dessous avec les VRAIS transferts.
                _transfer(
                    from_addr=self._WALLET, to_addr=self._POOL, token=self._WETH,
                    ts=sell_ts, amount=0.000066281897035081, tx_hash=self._TX,
                ),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(
            transfers={self._WALLET: transfers},
            tx_token_transfers={
                self._TX: TokenTransfersResult(
                    transfers=[
                        _transfer(
                            from_addr=self._WALLET, to_addr=self._POOL, token=self._WETH,
                            ts=sell_ts, amount=0.000066281897035081, tx_hash=self._TX,
                        ),
                        _transfer(
                            from_addr=self._POOL, to_addr=self._WALLET, token=_STABLE,
                            ts=sell_ts, amount=0.123906, tx_hash=self._TX,
                        ),
                    ],
                    available=True,
                ),
            },
        )
        gecko = FakeGeckoTerminalClient(
            pool_for_token={self._WETH: self._POOL},
            pool_created_at={self._WETH: _dt(-5)},
            ohlcv={self._POOL: _flat_ohlcv(self._WRONG_OHLCV_PRICE, start=_dt(-6))},
        )

        report = await sm.score_wallets(
            [self._WALLET], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.available is True
        assert card.closed_trades_count == 1
        assert card.unpriced_legs == 0

        # Prix RÉEL par hash pour la jambe de vente -- pas le prix pool mocké.
        pnl = card.realized_pnl_usd
        assert pnl is not None
        # buy_price = 500.0 (OHLCV mocké, hash non fourni pour cet achat) ;
        # sell_price doit être ~1869.38 (hash réel), PAS 500.0 (sinon pnl≈0).
        # FIFO n'apparie que le plus petit des deux montants (vente 0.0000663
        # < achat 0.001) -- la position d'achat restante reste ouverte.
        matched_amount = 0.000066281897035081
        expected_pnl = matched_amount * (self._REAL_RATIO_PRICE - self._WRONG_OHLCV_PRICE)
        assert pnl == pytest.approx(expected_pnl, rel=1e-4)
        assert pnl != pytest.approx(0.0, abs=0.01)  # écarterait un repli silencieux sur le prix pool mocké
        # tx du buy_leg jamais interrogée (non mockée) sans faire planter le test --
        # confirme le repli silencieux OHLCV pour ce hash précis.
        assert "0xbuy-not-mocked" in client.tx_token_transfers_calls
        assert self._TX in client.tx_token_transfers_calls


class TestLiquidityFloorForPricing:
    """15/07, revue Gemini -- défense anti-dust/scam-pool : un pool résolu mais
    dont la liquidité confirmée est sous le plancher ne doit pas valoriser de
    PnL (pool trivialement manipulable, ex. dust envoyé par un scammeur)."""

    @pytest.mark.asyncio
    async def test_thin_pool_skips_pricing_and_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return False

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)

        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=100.0, tx_hash="0xbuy"),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1), amount=100.0, tx_hash="0xsell"),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
            reserve_usd_for_token={TOKEN_X: 1_500.0},  # sous le plancher par défaut (30 000$)
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.available is True
        # L'achat reste bloqué (pool confirmé trop peu liquide) -- sans achat
        # valorisé, le trade ne peut pas se clôturer (FIFO exige les deux
        # bords), même si la vente elle-même serait valorisable (15/07,
        # plancher désormais asymétrique -- cf. TestRugPullAsymmetricFloor).
        assert card.closed_trades_count == 0
        assert card.unpriced_legs == 1  # un seul échec d'appariement (achat bloqué), pas 2 jambes comptées en vrac
        assert card.thin_liquidity_pricing_skipped_count == 1
        assert card.pool_lookup_errors == 0  # le pool EST résolu -- ce n'est pas un échec de résolution

    @pytest.mark.asyncio
    async def test_liquid_pool_prices_normally(self, tmp_path, monkeypatch):
        # Régression : une liquidité confirmée AU-DESSUS du plancher continue
        # de valoriser normalement (comportement historique inchangé).
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=100.0, tx_hash="0xbuy"),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1), amount=100.0, tx_hash="0xsell"),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
            reserve_usd_for_token={TOKEN_X: 50_000.0},  # au-dessus du plancher
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.closed_trades_count == 1
        assert card.thin_liquidity_pricing_skipped_count == 0


class TestRugPullAsymmetricFloor:
    """15/07, revue Gemini -- bug réel confirmé dans le correctif #160 (pas
    une simple limite résiduelle) : le plancher de liquidité, pensé pour
    bloquer le dust à l'ACHAT, bloquait aussi la valorisation d'une VENTE --
    un rug pull (pool effondré au moment du scan) faisait donc disparaître la
    perte réelle des statistiques au lieu de la comptabiliser. Corrigé : le
    plancher ne gate désormais que les jambes d'achat, jamais les ventes."""

    @pytest.mark.asyncio
    async def test_confirmed_entry_then_rug_pull_exit_captures_real_loss(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        # Achat confirmé par prix d'exécution exact (100$/token) -- établi
        # indépendamment de la liquidité ACTUELLE du pool, donc jamais bloqué
        # par le plancher (le hash-pricing est vérifié avant tout gate de
        # liquidité).
        buy_leg = _transfer(from_addr=POOL_X, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=100.0, tx_hash="0xbuy")
        stable_leg = _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=_STABLE, ts=_dt(0), amount=10_000.0, tx_hash="0xbuy")
        # Sortie après rug pull -- aucun hash mocké, retombe sur l'OHLCV du
        # pool, dont la liquidité CONFIRMÉE au moment du scan est très sous le
        # plancher (rug pull déjà survenu).
        sell_leg = _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1), amount=100.0, tx_hash="0xsell")

        transfers = TokenTransfersResult(transfers=[buy_leg, sell_leg], available=True)
        client = FakeBlockscoutClient(
            transfers={WALLET_A: transfers},
            tx_token_transfers={"0xbuy": TokenTransfersResult(transfers=[buy_leg, stable_leg], available=True)},
        )
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},  # prix crashé post-rug
            reserve_usd_for_token={TOKEN_X: 500.0},  # rug pull confirmé -- très sous le plancher
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        # La perte réelle du rug pull EST capturée -- pas d'immunité.
        assert card.closed_trades_count == 1
        assert card.realized_pnl_usd == pytest.approx(100.0 * (1.0 - 100.0))  # -9900$, perte réelle

    @pytest.mark.asyncio
    async def test_cmc_recovered_price_never_blocked_by_thin_liquidity_gate(self, tmp_path, monkeypatch):
        # Régression du bug trouvé en construisant le correctif ci-dessus :
        # quand GeckoTerminal ne résout AUCUN pool (pas "confirmé trop peu
        # liquide", juste absent) mais CMC recouvre un prix valide, l'achat ne
        # doit JAMAIS être bloqué -- `pool_meta.available=False` ne doit pas
        # se confondre avec "pool résolu mais trop peu liquide".
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        async def _fake_has_any_pair(contract, *, chain="base"):
            return False

        monkeypatch.setattr("aria_core.services.dexscreener.has_any_pair", _fake_has_any_pair)

        from aria_core.services.coinmarketcap import OHLCVResult as CmcOHLCVResult
        from aria_core.services.coinmarketcap import PoolMetadata as CmcPoolMetadata

        async def _cmc_resolve(addr, *, network_slug="base"):
            return CmcPoolMetadata(pool_address=POOL_X, available=True)

        async def _cmc_ohlcv(pool_addr, *, network_slug="base"):
            return CmcOHLCVResult(candles=_flat_ohlcv(2.0, start=_dt(-2)).candles, available=True)

        monkeypatch.setattr("aria_core.services.coinmarketcap.resolve_primary_pool", _cmc_resolve)
        monkeypatch.setattr("aria_core.services.coinmarketcap.get_ohlcv", _cmc_ohlcv)

        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xbuy"),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1), amount=10.0, tx_hash="0xsell"),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()  # aucun pool_for_token -- GeckoTerminal ne résout rien

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.cmc_price_recovery_count == 1
        assert card.closed_trades_count == 1  # achat NON bloqué malgré pool_meta.available=False


class TestPercentileComparabilityCaveat:
    """15/07, revue ChatGPT -- angle mort de comparabilité : le drapeau de
    confiance basse existait déjà mais n'était jamais rattaché au chiffre du
    percentile lui-même, ni utilisé pour filtrer la population de comparaison."""

    def test_caveat_attached_directly_to_percentile_line(self):
        card = sm.WalletScoreCard(
            address=WALLET_A,
            compared_against_n_wallets=5,
            composite_percentile=90.0,
            price_confirmation_ratio=0.10,
            price_confidence_low=True,
        )
        text = sm._format_card_for_prompt(card)
        assert "percentile composite 90e" in text
        assert "ATTENTION" in text
        assert "10%" in text  # le ratio réel, pas juste un renvoi générique

    def test_no_caveat_when_confidence_is_high(self):
        card = sm.WalletScoreCard(
            address=WALLET_A,
            compared_against_n_wallets=5,
            composite_percentile=90.0,
            price_confirmation_ratio=0.80,
            price_confidence_low=False,
        )
        text = sm._format_card_for_prompt(card)
        assert "ATTENTION" not in text

    @pytest.mark.asyncio
    async def test_low_confidence_wallet_excluded_from_comparison_population(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        # WALLET_A : PnL entièrement estimé (OHLCV, aucun hash mocké) -> price_confidence_low=True.
        low_conf_transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xbuy"),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0, tx_hash="0xsell"),
            ],
            available=True,
        )
        low_conf_client = FakeBlockscoutClient(transfers={WALLET_A: low_conf_transfers})
        low_conf_gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(
                candles=[
                    Candle(ts=int(_dt(-2).timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
                    Candle(ts=int(_dt(0).timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
                    Candle(ts=int(_dt(2).timestamp()), open=2.0, high=2.0, low=2.0, close=2.0, volume=1000.0),
                ],
                available=True,
            )},
        )
        low_conf_report = await sm.score_wallets(
            [WALLET_A], client=low_conf_client, gecko=low_conf_gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        assert low_conf_report.wallets[0].price_confidence_low is True  # confirme la prémisse

        winner_client = FakeBlockscoutClient(transfers={WALLET_B: TestComparativeRanking._mk_transfers(WALLET_B)})
        winner_gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=TestComparativeRanking._candles(2.0), available=True)},
        )
        report_b = await sm.score_wallets(
            [WALLET_B], client=winner_client, gecko=winner_gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        # WALLET_A (confiance basse) exclu -- aucune population de comparaison valide.
        assert report_b.wallets[0].compared_against_n_wallets == 0


class TestPriceConfirmationRatio:
    """15/07, revue Gemini -- transparence sur la confiance du cost-basis :
    part des jambes valorisées par un prix d'exécution EXACT (tx_hash +
    stablecoin) plutôt que par le repli marché OHLCV."""

    def _hash_leg_pair(self, *, wallet, pool, token, token_amount, stable_amount, is_buy: bool):
        if is_buy:
            token_leg = _transfer(from_addr=pool, to_addr=wallet, token=token, ts=_dt(0), amount=token_amount)
            stable_leg = _transfer(from_addr=wallet, to_addr=pool, token=_STABLE, ts=_dt(0), amount=stable_amount)
        else:
            token_leg = _transfer(from_addr=wallet, to_addr=pool, token=token, ts=_dt(0), amount=token_amount)
            stable_leg = _transfer(from_addr=pool, to_addr=wallet, token=_STABLE, ts=_dt(0), amount=stable_amount)
        return TokenTransfersResult(transfers=[token_leg, stable_leg], available=True)

    @pytest.mark.asyncio
    async def test_mixed_exact_and_estimated_legs_computes_ratio(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        transfers = TokenTransfersResult(
            transfers=[
                # Achat -- AUCUN hash mocké -> retombe sur pool+OHLCV (estimé).
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xbuy"),
                # Vente -- hash mocké avec jambe stablecoin -> prix exact.
                _transfer(from_addr=WALLET_A, to_addr=POOL_X, token=TOKEN_X, ts=_dt(1), amount=10.0, tx_hash="0xsell"),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(
            transfers={WALLET_A: transfers},
            tx_token_transfers={
                "0xsell": self._hash_leg_pair(
                    wallet=WALLET_A, pool=POOL_X, token=TOKEN_X, token_amount=10.0, stable_amount=110.0, is_buy=False,
                ),
            },
        )
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.closed_trades_count == 1
        # 1 jambe exacte (vente) sur 2 jambes au total (achat estimé + vente exacte).
        assert card.price_confirmation_ratio == pytest.approx(0.5)
        assert card.price_confidence_low is False  # 50% >= seuil par défaut (30%)

    @pytest.mark.asyncio
    async def test_fully_estimated_pricing_flags_low_confidence(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xbuy"),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(1), amount=10.0, tx_hash="0xsell"),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})  # aucun hash mocké
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.price_confirmation_ratio == pytest.approx(0.0)
        assert card.price_confidence_low is True


class TestCapitalWeightedDiversification:
    """15/07, revue ChatGPT -- complète (remplace pas) le ratio par comptage :
    mesure la concentration réelle du capital plutôt que la largeur des paris."""

    @pytest.mark.asyncio
    async def test_capital_weighted_ratio_diverges_from_count_ratio(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        def _pair(token, pool, *, token_amount, buy_stable, sell_stable, buy_hash, sell_hash):
            return [
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=token, ts=_dt(0), amount=token_amount, tx_hash=buy_hash),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=token, ts=_dt(1), amount=token_amount, tx_hash=sell_hash),
            ], {
                buy_hash: self._pair_result(pool, token, token_amount, buy_stable, is_buy=True),
                sell_hash: self._pair_result(pool, token, token_amount, sell_stable, is_buy=False),
            }

        # TOKEN_X : grosse capitalisation (1000$ engagés), profitable (+100$).
        x_transfers, x_hashes = _pair(
            TOKEN_X, POOL_X, token_amount=100.0, buy_stable=1000.0, sell_stable=1100.0,
            buy_hash="0xbuyX", sell_hash="0xsellX",
        )
        # TOKEN_Y : capital minuscule (1$ engagé), perdant (-0.5$).
        y_transfers, y_hashes = _pair(
            TOKEN_Y, POOL_Y, token_amount=1.0, buy_stable=1.0, sell_stable=0.5,
            buy_hash="0xbuyY", sell_hash="0xsellY",
        )

        transfers = TokenTransfersResult(transfers=[*x_transfers, *y_transfers], available=True)
        client = FakeBlockscoutClient(
            transfers={WALLET_A: transfers}, tx_token_transfers={**x_hashes, **y_hashes},
        )
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X, TOKEN_Y: POOL_Y},
            pool_created_at={TOKEN_X: _dt(-1), TOKEN_Y: _dt(-1)},
            ohlcv={},  # OHLCV vide -- force le pricing par hash exact sur les deux tokens
        )

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )

        card = report.wallets[0]
        assert card.closed_trades_count == 2
        # Comptage : 1 profitable / 2 tokens = 50%.
        assert card.diversification_profitable_tokens == 1
        assert card.diversification_total_tokens == 2
        # Pondéré par capital : 1000$ profitables / 1001$ engagés au total ~= 99.9%.
        assert card.diversification_capital_weighted_ratio == pytest.approx(1000.0 / 1001.0, rel=1e-4)

    @staticmethod
    def _pair_result(pool, token, token_amount, stable_amount, *, is_buy):
        if is_buy:
            token_leg = _transfer(from_addr=pool, to_addr=WALLET_A, token=token, ts=_dt(0), amount=token_amount)
            stable_leg = _transfer(from_addr=WALLET_A, to_addr=pool, token=_STABLE, ts=_dt(0), amount=stable_amount)
        else:
            token_leg = _transfer(from_addr=WALLET_A, to_addr=pool, token=token, ts=_dt(1), amount=token_amount)
            stable_leg = _transfer(from_addr=pool, to_addr=WALLET_A, token=_STABLE, ts=_dt(1), amount=stable_amount)
        return TokenTransfersResult(transfers=[token_leg, stable_leg], available=True)


class TestTransientPricingErrorRetry:
    """15/07, revue Gemini -- gel des erreurs transitoires : une panne
    D'INFRASTRUCTURE GeckoTerminal (timeout/429/erreur serveur, déjà retentée
    par `_get_json` avant d'abandonner) ne doit JAMAIS figer un token comme
    "scanné" dans le checkpoint incrémental -- sinon une coupure réseau
    ponctuelle se transforme en cicatrice permanente sur le score du wallet."""

    @pytest.mark.asyncio
    async def test_transient_error_token_retried_on_next_pass_without_new_activity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        buy_ts = _dt(0)
        sell_ts = _dt(2)
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=10.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=sell_ts, amount=10.0),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})

        # Passage 1 : GeckoTerminal renvoie une panne D'INFRASTRUCTURE (pas le
        # verdict de donnée "aucun pool trouvé pour ce token").
        failing_gecko = FakeGeckoTerminalClient(
            pool_error_for_token={TOKEN_X: "donnée GeckoTerminal indisponible (timeout GeckoTerminal)"},
        )
        report1 = await sm.score_wallets(
            [WALLET_A], client=client, gecko=failing_gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        card1 = report1.wallets[0]
        assert card1.transient_pricing_errors == 1
        assert card1.full_coverage is False  # le token n'est PAS marqué "scanné"

        checkpoint = await wallet_scan_state.get_checkpoint(WALLET_A)
        assert len(checkpoint.scanned_tokens) == 0  # rien de figé malgré la tentative

        # Passage 2 : MÊME wallet, MÊMES transferts (aucune nouvelle activité
        # on-chain) -- GeckoTerminal a récupéré, le token doit être retenté et
        # valorisé sans qu'aucun nouveau transfert ne l'ait déclenché.
        recovered_gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(
                candles=[
                    Candle(ts=int(_dt(-2).timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
                    Candle(ts=int(buy_ts.timestamp()), open=1.0, high=1.0, low=1.0, close=1.0, volume=1000.0),
                    Candle(ts=int(sell_ts.timestamp()), open=2.0, high=2.0, low=2.0, close=2.0, volume=1000.0),
                ],
                available=True,
            )},
        )
        report2 = await sm.score_wallets(
            [WALLET_A], client=client, gecko=recovered_gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        card2 = report2.wallets[0]
        assert card2.closed_trades_count == 1  # valorisé au 2e passage, sans nouvelle activité
        assert card2.transient_pricing_errors == 0
        assert card2.full_coverage is True

    @pytest.mark.asyncio
    async def test_genuine_no_pool_token_marked_scanned_not_flagged_transient(self, tmp_path, monkeypatch):
        # Contraste : un token sans AUCUN pool (verdict de donnée légitime, pas
        # une panne) reste marqué "scanné" -- comportement HISTORIQUE inchangé,
        # jamais un compteur d'erreur transitoire pour ce cas.
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        transfers = TokenTransfersResult(
            transfers=[_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0)],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient()  # aucune entrée -> "aucun pool trouvé pour ce token"

        report = await sm.score_wallets(
            [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
        )
        card = report.wallets[0]
        assert card.transient_pricing_errors == 0
        assert card.pool_lookup_errors == 1
        assert card.full_coverage is True  # marqué "scanné" malgré l'échec (verdict définitif)

        checkpoint = await wallet_scan_state.get_checkpoint(WALLET_A)
        assert len(checkpoint.scanned_tokens) == 1


class TestPercentileTieSmoothing:
    """15/07, revue externe -- lissage des ex-æquo : un wallet dont la valeur
    est EX-ÆQUO avec la majorité de la population (pas strictement pire) ne
    doit jamais tomber au 0e percentile comme s'il était pire que tout le
    monde."""

    def test_tied_value_gets_half_credit_not_zero(self):
        # 4 wallets à 0.5 pile dans la population -> un wallet ÉGALEMENT à 0.5
        # doit être crédité à 50% (ex-æquo avec 100% de la population), jamais 0%.
        population = [0.5, 0.5, 0.5, 0.5]

        def _percentile(value, pop):
            below = sum(1 for p in pop if p < value)
            tied = sum(1 for p in pop if p == value)
            return round(100.0 * (below + 0.5 * tied) / len(pop), 1)

        assert _percentile(0.5, population) == pytest.approx(50.0)

    def test_strictly_greater_still_gets_full_credit(self):
        population = [0.1, 0.2, 0.3]

        def _percentile(value, pop):
            below = sum(1 for p in pop if p < value)
            tied = sum(1 for p in pop if p == value)
            return round(100.0 * (below + 0.5 * tied) / len(pop), 1)

        assert _percentile(0.4, population) == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_tied_win_rate_scored_at_fifty_not_zero_end_to_end(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        # WALLET_A (déjà noté) : win_rate exactement 0.5 (1 gagnant / 1 perdant).
        # Jambes hash-exactes (tx_hash + jambe stablecoin) -- sinon WALLET_A serait
        # `price_confidence_low=True` (100% estimé OHLCV) et exclu de la population
        # de comparaison (#175), ce qui n'est PAS ce que ce test veut vérifier.
        loser_leg = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xlbuy")
        loser_sell = _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0, tx_hash="0xlsell")
        winner_leg = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_Y, ts=_dt(0), amount=10.0, tx_hash="0xwbuy")
        winner_sell = _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_Y, ts=_dt(2), amount=10.0, tx_hash="0xwsell")
        transfers_a = TokenTransfersResult(transfers=[loser_leg, loser_sell, winner_leg, winner_sell], available=True)
        client_a = FakeBlockscoutClient(
            transfers={WALLET_A: transfers_a},
            tx_token_transfers={
                "0xlbuy": TokenTransfersResult(transfers=[
                    _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0, tx_hash="0xlbuy"),
                    _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=_STABLE, ts=_dt(0), amount=10.0, tx_hash="0xlbuy"),
                ], available=True),
                "0xlsell": TokenTransfersResult(transfers=[
                    _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0, tx_hash="0xlsell"),
                    _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=_STABLE, ts=_dt(2), amount=5.0, tx_hash="0xlsell"),
                ], available=True),
                "0xwbuy": TokenTransfersResult(transfers=[
                    _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_Y, ts=_dt(0), amount=10.0, tx_hash="0xwbuy"),
                    _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=_STABLE, ts=_dt(0), amount=10.0, tx_hash="0xwbuy"),
                ], available=True),
                "0xwsell": TokenTransfersResult(transfers=[
                    _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_Y, ts=_dt(2), amount=10.0, tx_hash="0xwsell"),
                    _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=_STABLE, ts=_dt(2), amount=20.0, tx_hash="0xwsell"),
                ], available=True),
            },
        )
        gecko_a = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X, TOKEN_Y: POOL_Y},
            pool_created_at={TOKEN_X: _dt(-1), TOKEN_Y: _dt(-1)},
            ohlcv={
                POOL_X: OHLCVResult(candles=TestComparativeRanking._candles(0.5), available=True),  # perte
                POOL_Y: OHLCVResult(candles=TestComparativeRanking._candles(2.0), available=True),  # gain
            },
        )
        await sm.score_wallets([WALLET_A], client=client_a, gecko=gecko_a, llm=_fake_llm, goplus=_clean_goplus())

        # WALLET_B : EXACTEMENT le même win_rate (0.5) -- ex-æquo, pas pire.
        transfers_b = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_B, token=TOKEN_X, ts=_dt(0), amount=10.0),
                _transfer(from_addr=WALLET_B, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0),
                _transfer(from_addr=FUNDER, to_addr=WALLET_B, token=TOKEN_Y, ts=_dt(0), amount=10.0),
                _transfer(from_addr=WALLET_B, to_addr=FUNDER, token=TOKEN_Y, ts=_dt(2), amount=10.0),
            ],
            available=True,
        )
        client_b = FakeBlockscoutClient(transfers={WALLET_B: transfers_b})
        gecko_b = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X, TOKEN_Y: POOL_Y},
            pool_created_at={TOKEN_X: _dt(-1), TOKEN_Y: _dt(-1)},
            ohlcv={
                POOL_X: OHLCVResult(candles=TestComparativeRanking._candles(0.5), available=True),
                POOL_Y: OHLCVResult(candles=TestComparativeRanking._candles(2.0), available=True),
            },
        )
        report_b = await sm.score_wallets(
            [WALLET_B], client=client_b, gecko=gecko_b, llm=_fake_llm, goplus=_clean_goplus(),
        )
        card_b = report_b.wallets[0]
        assert card_b.win_rate == pytest.approx(0.5)
        # Ex-æquo avec WALLET_A (0.5 pile) -> 50%, jamais 0%.
        assert card_b.percentile_win_rate == pytest.approx(50.0)


class TestSortinoPnlContradiction:
    """15/07, revue externe -- biais d'asymétrie de taille : `sortino` se
    calcule sur le rendement EN % par trade, jamais pondéré par le capital
    engagé -- un wallet peut afficher un Sortino positif alors que son PnL
    réalisé en DOLLARS est négatif (beaucoup de petits gains en % sur des
    mises minuscules, une grosse perte en % plus faible mais en $ dominante)."""

    @pytest.mark.asyncio
    async def test_flagged_when_sortino_positive_but_pnl_negative(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))

        # 4 micro-trades gagnants (+100% sur une mise de 1$ chacun, +4$ au total)
        # + 1 trade majeur perdant (-50% sur une mise de 1000$, -500$) :
        # mean(return) = (4*1.0 - 0.5) / 5 = 0.7 ; downside_dev = sqrt(mean([0.25])) = 0.5
        # -> Sortino = 0.7/0.5 = 1.4 (positif) alors que PnL réel = 4 - 500 = -496$ (négatif).
        transfers = []
        gecko_pools = {}
        gecko_ohlcv = {}
        for i in range(4):
            token = "0x" + f"{i+10}".rjust(40, "0")
            pool = "0x" + f"{i+50}".rjust(40, "0")
            transfers.append(_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=token, ts=_dt(0), amount=1.0))
            transfers.append(_transfer(from_addr=WALLET_A, to_addr=FUNDER, token=token, ts=_dt(2), amount=1.0))
            gecko_pools[token] = pool
            gecko_ohlcv[pool] = OHLCVResult(candles=TestComparativeRanking._candles(2.0), available=True)  # buy 1 -> sell 2

        major_token = "0x" + "99".rjust(40, "0")
        major_pool = "0x" + "88".rjust(40, "0")
        transfers.append(_transfer(from_addr=FUNDER, to_addr=WALLET_A, token=major_token, ts=_dt(0), amount=1000.0))
        transfers.append(_transfer(from_addr=WALLET_A, to_addr=FUNDER, token=major_token, ts=_dt(2), amount=1000.0))
        gecko_pools[major_token] = major_pool
        # buy 1.0 -> sell 0.5 : perte de 50% sur une mise de 1000$ (candles bâties pour un prix
        # UNITAIRE de 1.0 puis 0.5 -- l'amount=1000 porte la taille de la mise, pas le prix).
        gecko_ohlcv[major_pool] = OHLCVResult(candles=TestComparativeRanking._candles(0.5), available=True)

        client = FakeBlockscoutClient(transfers={WALLET_A: TokenTransfersResult(transfers=transfers, available=True)})
        gecko = FakeGeckoTerminalClient(
            pool_for_token=gecko_pools,
            pool_created_at={t: _dt(-1) for t in gecko_pools},
            ohlcv=gecko_ohlcv,
        )

        report = await sm.score_wallets([WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus())
        card = report.wallets[0]

        assert card.closed_trades_count == 5
        assert card.realized_pnl_usd == pytest.approx(4.0 - 500.0)  # -496$, réellement négatif
        assert card.sortino is not None and card.sortino > 0  # "honorable" en apparence
        assert card.sortino_pnl_contradiction is True

        text = sm._format_card_for_prompt(card)
        assert "ATTENTION" in text
        assert "Sortino positif mais PnL réalisé négatif" in text

    @pytest.mark.asyncio
    async def test_not_flagged_when_sortino_and_pnl_agree(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=TestComparativeRanking._candles(2.0), available=True)},
        )
        report = await sm.score_wallets([WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus())
        card = report.wallets[0]
        assert card.sortino_pnl_contradiction is False  # sortino indisponible ici (1 seul trade, sous le seuil)


def test_wallet_scoring_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_WALLET_SCORING_ENABLED", raising=False)
    assert sm.wallet_scoring_enabled() is False


def test_wallet_scoring_gate_on_when_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "true")
    assert sm.wallet_scoring_enabled() is True


class TestTransferHistoryTruncated:
    """15/07, revue externe -- le plafond de pagination Blockscout (2000
    transferts/10 pages) peut tronquer l'historique d'un wallet très actif
    sans qu'aucun signal ne le dise -- risque de biais silencieux sur TOUS
    les axes (W/PnL/S/D) puisque des achats/ventes anciens manqueraient."""

    @pytest.mark.asyncio
    async def test_surfaced_on_card_and_displayed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0),
            ],
            available=True,
            truncated=True,  # plafond de pagination atteint, historique pas réellement épuisé
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=TestComparativeRanking._candles(2.0), available=True)},
        )
        report = await sm.score_wallets([WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus())
        card = report.wallets[0]

        assert card.transfer_history_truncated is True
        text = sm._format_card_for_prompt(card)
        assert "ATTENTION" in text
        assert "historique de transferts tronqué" in text

    @pytest.mark.asyncio
    async def test_not_flagged_when_history_genuinely_exhausted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=_dt(0), amount=10.0),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=_dt(2), amount=10.0),
            ],
            available=True,
            truncated=False,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X}, pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: OHLCVResult(candles=TestComparativeRanking._candles(2.0), available=True)},
        )
        report = await sm.score_wallets([WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus())
        card = report.wallets[0]

        assert card.transfer_history_truncated is False
        assert "historique de transferts tronqué" not in sm._format_card_for_prompt(card)


class TestFormatWalletScoreCardLinesCumulative:
    """15/07, constat opérateur -- la carte Telegram affichait "Tokens analysés :
    X/Y" (cette passe seulement) sans jamais montrer le cumul, alors que la thèse
    LLM (prompt) reçoit et mentionne, elle, le cumul réel -- deux chiffres
    différents dans le même message pour un humain qui lit les deux. Corrigé :
    la carte affiche désormais aussi la couverture cumulée."""

    def _card(self, **overrides) -> sm.WalletScoreCard:
        card = sm.WalletScoreCard(address=WALLET_A)
        card.tokens_found = 806
        card.tokens_analyzed = 50
        card.tokens_skipped_capped = True
        card.tokens_scanned_cumulative = 118
        card.full_coverage = False
        for key, value in overrides.items():
            setattr(card, key, value)
        return card

    def test_shows_this_pass_count_distinctly(self):
        lines = sm.format_wallet_score_card_lines(self._card())
        text = "\n".join(lines)
        assert "Tokens analysés cette passe : 50/806" in text

    def test_shows_cumulative_coverage(self):
        lines = sm.format_wallet_score_card_lines(self._card())
        text = "\n".join(lines)
        assert "Couverture cumulée : 118/806" in text

    def test_marks_complete_when_full_coverage(self):
        lines = sm.format_wallet_score_card_lines(
            self._card(tokens_scanned_cumulative=806, full_coverage=True)
        )
        text = "\n".join(lines)
        assert "Couverture cumulée : 806/806 (complète)" in text

    def test_cumulative_shown_even_when_not_capped(self):
        lines = sm.format_wallet_score_card_lines(
            self._card(tokens_analyzed=10, tokens_skipped_capped=False, tokens_scanned_cumulative=10)
        )
        text = "\n".join(lines)
        assert "Couverture cumulée : 10/806" in text


class TestOhlcvSingleTierSpeedFix:
    """#182, 15/07 -- correctif de vitesse : le wallet-scoring n'utilise
    price_at() (une seule bougie) et n'a jamais besoin du seuil de 20 bougies
    pensé pour /vc -- min_useful_candles=1 doit être passé à gecko.get_ohlcv
    au point d'appel wallet-scoring, économisant jusqu'à 2 appels
    GeckoTerminal par token jeune/microcap."""

    @pytest.mark.asyncio
    async def test_wallet_scoring_requests_single_tier_ohlcv(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "DB_PATH", str(tmp_path / "wallet_scoring.db"))
        buy_ts = _dt(0)
        sell_ts = _dt(1)
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=FUNDER, to_addr=WALLET_A, token=TOKEN_X, ts=buy_ts, amount=5.0, tx_hash="0xbuy"),
                _transfer(from_addr=WALLET_A, to_addr=FUNDER, token=TOKEN_X, ts=sell_ts, amount=5.0, tx_hash="0xsell"),
            ],
            available=True,
        )
        client = FakeBlockscoutClient(transfers={WALLET_A: transfers})
        gecko = FakeGeckoTerminalClient(
            pool_for_token={TOKEN_X: POOL_X},
            pool_created_at={TOKEN_X: _dt(-1)},
            ohlcv={POOL_X: _flat_ohlcv(1.0, start=_dt(-2))},
        )

        await sm.score_wallets([WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus())

        assert gecko.get_ohlcv_calls, "gecko.get_ohlcv jamais appelé -- test mal posé"
        pool_address, kwargs = gecko.get_ohlcv_calls[0]
        assert pool_address == POOL_X
        assert kwargs.get("min_useful_candles") == 1
