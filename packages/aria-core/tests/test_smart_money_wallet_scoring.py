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

    def test_default_cap_matches_operator_decision_n10(self):
        # 20->50 puis ramené à 10 le 14/07 (décision opérateur explicite) :
        # scans plus rapides par défaut.
        assert sm.WEIGHTS.max_tokens_analyzed == 10

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
    le comportement réel post-correctif de `resolve_primary_pool`."""

    def __init__(self, *, pool_for_token=None, pool_created_at=None, ohlcv=None):
        self._pool_for_token = pool_for_token or {}
        self._pool_created_at = pool_created_at or {}
        self._ohlcv = ohlcv or {}

    async def resolve_primary_pool(self, token_address, **kwargs):
        pool_address = self._pool_for_token.get(token_address)
        if pool_address is None:
            return PoolMetadata(pool_address=token_address, available=False, error="aucun pool trouvé pour ce token")
        return PoolMetadata(
            pool_address=pool_address,
            created_at=self._pool_created_at.get(token_address),
            available=True,
        )

    async def get_ohlcv(self, pool_address, **kwargs):
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


def test_wallet_scoring_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_WALLET_SCORING_ENABLED", raising=False)
    assert sm.wallet_scoring_enabled() is False


def test_wallet_scoring_gate_on_when_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "true")
    assert sm.wallet_scoring_enabled() is True
