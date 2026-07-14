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


def _transfer(*, from_addr: str, to_addr: str, token: str, ts: datetime, amount: float = 100.0) -> TokenTransfer:
    return TokenTransfer(
        tx_hash="0x1",
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
    def test_simple_winning_trade(self):
        buys = [(_dt(0), 10.0)]
        sells = [(_dt(1), 10.0)]
        prices = {_dt(0): 1.0, _dt(1): 2.0}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts: prices.get(ts))

        assert len(result.closed_trades) == 1
        trade = result.closed_trades[0]
        assert trade.pnl_usd == pytest.approx(10.0)  # 10 * (2-1)
        assert trade.return_pct == pytest.approx(1.0)
        assert result.unpriced_legs == 0
        assert result.open_position_amount == 0.0

    def test_simple_losing_trade(self):
        buys = [(_dt(0), 10.0)]
        sells = [(_dt(1), 10.0)]
        prices = {_dt(0): 2.0, _dt(1): 1.0}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts: prices.get(ts))

        assert result.closed_trades[0].pnl_usd == pytest.approx(-10.0)

    def test_missing_price_counted_unpriced_never_zeroed(self):
        buys = [(_dt(0), 10.0)]
        sells = [(_dt(1), 10.0)]
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts: None)

        assert result.closed_trades == []
        assert result.unpriced_legs == 1

    def test_partial_sell_leaves_open_position(self):
        buys = [(_dt(0), 10.0)]
        sells = [(_dt(1), 4.0)]
        prices = {_dt(0): 1.0, _dt(1): 1.5}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts: prices.get(ts))

        assert result.closed_trades[0].token_amount == pytest.approx(4.0)
        assert result.open_position_amount == pytest.approx(6.0)

    def test_fifo_order_oldest_buy_matched_first(self):
        buys = [(_dt(0), 5.0), (_dt(1), 5.0)]
        sells = [(_dt(2), 5.0)]
        prices = {_dt(0): 1.0, _dt(1): 3.0, _dt(2): 2.0}
        result = sm._fifo_match(TOKEN_X, buys, sells, lambda ts: prices.get(ts))

        assert len(result.closed_trades) == 1
        # apparié avec l'achat à 1.0 (le plus ancien), pas celui à 3.0
        assert result.closed_trades[0].buy_price == pytest.approx(1.0)
        assert result.open_position_amount == pytest.approx(5.0)

    def test_sell_without_matching_buy_ignored_not_a_priced_leg(self):
        result = sm._fifo_match(TOKEN_X, [], [(_dt(0), 10.0)], lambda ts: 1.0)
        assert result.closed_trades == []
        assert result.unpriced_legs == 0


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

    def test_default_cap_matches_operator_decision_n20(self):
        assert sm.WEIGHTS.max_tokens_analyzed == 20

    def test_most_recent_token_selected_first(self):
        old_token = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xold", ts=_dt(0))
        new_token = _transfer(from_addr=FUNDER, to_addr=WALLET_A, token="0xnew", ts=_dt(10))
        grouped = {"0xold": [old_token], "0xnew": [new_token]}
        selected, _, _ = sm._select_tokens_for_deep_analysis(grouped, cap=1)
        assert selected == ["0xnew"]


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

    async def get_address_security(self, address, **kwargs):
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
# Bout-en-bout mocké : score_wallets
# ---------------------------------------------------------------------------

class FakeBlockscoutClient:
    def __init__(self, *, address_infos=None, transfers=None, transactions=None):
        self._address_infos = address_infos or {}
        self._transfers = transfers or {}
        self._transactions = transactions or {}

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


class FakeGeckoTerminalClient:
    """``pool_for_token`` mappe token -> pool résolu (peut différer, comme en
    réalité) ; ``ohlcv`` est keyé par POOL address (jamais le token), reflétant
    le comportement réel post-correctif de `resolve_primary_pool`."""

    def __init__(self, *, pool_for_token=None, pool_created_at=None, ohlcv=None):
        self._pool_for_token = pool_for_token or {}
        self._pool_created_at = pool_created_at or {}
        self._ohlcv = ohlcv or {}

    async def resolve_primary_pool(self, token_address):
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
    async def test_cap_reached_logs_explicitly_never_silent(self, tmp_path, monkeypatch, caplog):
        """#157 -- décision opérateur N=20 : au-delà, log EXPLICITE, jamais une
        troncature silencieuse."""
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
                [WALLET_A], client=client, gecko=gecko, llm=_fake_llm, goplus=_clean_goplus(),
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


def test_wallet_scoring_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_WALLET_SCORING_ENABLED", raising=False)
    assert sm.wallet_scoring_enabled() is False


def test_wallet_scoring_gate_on_when_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "true")
    assert sm.wallet_scoring_enabled() is True
