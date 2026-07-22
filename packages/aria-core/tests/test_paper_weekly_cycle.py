"""Cycle d'entraînement hebdomadaire (18/07, remplace le protocole 30j/7j/14j) --
reset à 1M$ chaque semaine, objectif +10% validé chaque semaine, historique jamais
détruit (contrairement à ``reset_portfolio``)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import paper_trader as pt, risk_guard
from aria_core.paths import configure_data_dir
from aria_core.services.dexscreener import PairSnapshot
from aria_core.skills import market_sentiment
from aria_core.skills.ta_levels import Candle

A = "0x" + "a" * 40
B = "0x" + "b" * 40


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(pt, "_run_cycle_lock", asyncio.Lock())
    return tmp_path


async def _age_cycle(days: float) -> None:
    """Recule ``paper_state.created_at`` de ``days`` jours -- simule le temps écoulé
    sans dépendre d'un vrai sleep."""
    started = datetime.now(timezone.utc) - timedelta(days=days)
    async with aiosqlite.connect(pt.DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET created_at = ? WHERE id = 1", (started.isoformat(),),
        )
        await db.commit()


def test_weekly_target_equity_is_plus_10_pct():
    assert pt.weekly_target_equity(1_000_000.0) == 1_100_000.0


@pytest.mark.asyncio
async def test_weekly_cycle_not_due_when_fresh(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    assert await pt.weekly_cycle_due() is False


@pytest.mark.asyncio
async def test_weekly_cycle_due_after_7_days(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await _age_cycle(7.1)
    assert await pt.weekly_cycle_due() is True


@pytest.mark.asyncio
async def test_weekly_cycle_not_due_before_7_days(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await _age_cycle(6.9)
    assert await pt.weekly_cycle_due() is False


@pytest.mark.asyncio
async def test_run_weekly_reset_force_closes_open_position(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return 3.0  # +50 % latent au moment du reset

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["force_closed"] == 1
    assert (await pt.get_open_positions()) == []


@pytest.mark.asyncio
async def test_run_weekly_reset_validated_true_when_target_reached(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=500_000)

    async def price_lookup(contract):
        return 1.25  # 500k -> 625k, +125k latent -> équité 1 125 000 >= objectif 1,1M

    report = await pt.run_weekly_reset(price_lookup=price_lookup)
    assert report["validated"] is True
    assert round(report["end_equity"]) == 1_125_000


@pytest.mark.asyncio
async def test_run_weekly_reset_validated_false_when_below_target(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return 1.0  # flat -- équité reste 1M, sous l'objectif 1,1M

    report = await pt.run_weekly_reset(price_lookup=price_lookup)
    assert report["validated"] is False
    assert round(report["end_equity"]) == 1_000_000


@pytest.mark.asyncio
async def test_run_weekly_reset_price_unavailable_falls_back_to_entry_price(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return None  # prix indisponible -- jamais un prix inventé

    report = await pt.run_weekly_reset(price_lookup=price_lookup)
    # Fallback = entry_price -> pnl_usd == 0 exactement, équité inchangée.
    assert round(report["end_equity"]) == 1_000_000


@pytest.mark.asyncio
async def test_run_weekly_reset_never_destroys_history(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)
    await pt.close_position(A, 3.0, reason="cible")
    await pt.open_position(B, "BBB", 1.0, alloc_usd=20_000)

    async def price_lookup(contract):
        return 1.0

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    # Table live vidée...
    assert (await pt.get_open_positions()) == []
    assert (await pt.get_closed_positions()) == []

    # ...mais tout archivé sous le bon numéro de cycle, rien perdu.
    async with aiosqlite.connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT contract, cycle_number FROM paper_position_archive ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 2
    assert all(cycle == report["cycle_number"] for _, cycle in rows)
    assert {r[0] for r in rows} == {A, B}


@pytest.mark.asyncio
async def test_run_weekly_reset_resets_capital_and_increments_cycle(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    start_cycle = await pt.get_current_cycle_number()
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)

    async def price_lookup(contract):
        return 2.0

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["next_cycle_number"] == start_cycle + 1
    assert await pt.get_current_cycle_number() == start_cycle + 1
    assert await pt.starting_capital() == pt.STARTING_CAPITAL_USD
    assert await pt.cash_available() == pt.STARTING_CAPITAL_USD
    assert await pt.get_equity_high_water_mark() == pt.STARTING_CAPITAL_USD


@pytest.mark.asyncio
async def test_run_weekly_reset_records_permanent_cycle_row(tmp_db):
    await pt.reset_portfolio(1_000_000.0)

    async def price_lookup(contract):
        return 2.0

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    async with aiosqlite.connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT started_at, ended_at, target_equity, start_capital, end_equity, "
            "return_pct, validated, closed_trades, win_rate FROM paper_weekly_cycle "
            "WHERE cycle_number = ?",
            (report["cycle_number"],),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[2] == 1_100_000.0  # target_equity
    assert row[3] == 1_000_000.0  # start_capital
    assert row[6] == 0  # validated=False (flat, en dessous de l'objectif)


@pytest.mark.asyncio
async def test_run_weekly_reset_clears_circuit_breaker_for_fresh_week(tmp_db):
    await pt.reset_portfolio(1_000_000.0)
    risk_guard.block_new_entries("test -- palier dur simulé", by="test")
    blocked_before, _ = risk_guard.blocks_new_entries()
    assert blocked_before is True

    async def price_lookup(contract):
        return 2.0

    await pt.run_weekly_reset(price_lookup=price_lookup)

    blocked_after, reason_after = risk_guard.blocks_new_entries()
    assert blocked_after is False, reason_after


@pytest.mark.asyncio
async def test_run_weekly_reset_default_price_lookup_signature(tmp_db, monkeypatch):
    """Le mécanisme de prix PAR DÉFAUT (#173 -- ``_default_pair_lookup``, plus
    ``_default_price_lookup`` directement depuis le 20/07) est appelé avec ``chain=``
    -- un price_lookup INJECTÉ garde le contrat d'appel historique à un seul argument
    (même convention que run_paper_cycle, cf. paper_trader.py)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000, chain="solana")

    calls = []

    async def fake_pair_lookup(contract, *, chain="base"):
        calls.append((contract, chain))
        return PairSnapshot(pair_address="0xpool", price_usd=2.0, liquidity_usd=100_000.0, base_symbol="AAA")

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    await pt.run_weekly_reset()

    assert calls == [(A, "solana")]


# ── #173 (20/07) : prix de clôture ROBUSTE (médiane bougies) au reset hebdomadaire ──

def _candles(*closes: float) -> list[Candle]:
    return [Candle(ts=i, open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]


class TestRobustClosePrice:
    """_robust_close_price -- fonction quasi pure (un seul appel réseau mocké),
    testée directement sans passer par tout le cycle de reset."""

    @pytest.mark.asyncio
    async def test_odd_count_uses_middle_value(self, monkeypatch):
        async def fake_fetch(pool_address, chain, *, contract="", pair=None):
            return _candles(1.0, 1.1, 5.0)  # 5.0 = mèche isolée -- médiane ignore l'extrême

        monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)
        pair = PairSnapshot(pair_address="0xpool", price_usd=5.0)
        price = await pt._robust_close_price(A, "base", pair)
        assert price == pytest.approx(1.1)

    @pytest.mark.asyncio
    async def test_even_count_averages_two_middle_values(self, monkeypatch):
        async def fake_fetch(pool_address, chain, *, contract="", pair=None):
            return _candles(1.0, 1.2, 1.4, 1.6)

        monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)
        pair = PairSnapshot(pair_address="0xpool", price_usd=1.6)
        price = await pt._robust_close_price(A, "base", pair)
        assert price == pytest.approx((1.2 + 1.4) / 2.0)

    @pytest.mark.asyncio
    async def test_too_few_candles_returns_none(self, monkeypatch):
        async def fake_fetch(pool_address, chain, *, contract="", pair=None):
            return _candles(1.0, 1.1)  # sous _RESET_PRICE_MIN_CANDLES (3)

        monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)
        pair = PairSnapshot(pair_address="0xpool", price_usd=1.1)
        assert await pt._robust_close_price(A, "base", pair) is None

    @pytest.mark.asyncio
    async def test_invalid_closes_filtered_before_counting(self, monkeypatch):
        async def fake_fetch(pool_address, chain, *, contract="", pair=None):
            return _candles(1.0, 0.0, 1.2, -1.0, 1.4)  # 2 invalides -> seulement 3 valides

        monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)
        pair = PairSnapshot(pair_address="0xpool", price_usd=1.4)
        price = await pt._robust_close_price(A, "base", pair)
        assert price == pytest.approx(1.2)

    @pytest.mark.asyncio
    async def test_empty_candles_returns_none(self, monkeypatch):
        async def fake_fetch(pool_address, chain, *, contract="", pair=None):
            return []

        monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)
        pair = PairSnapshot(pair_address="0xpool", price_usd=1.0)
        assert await pt._robust_close_price(A, "base", pair) is None

    @pytest.mark.asyncio
    async def test_fetch_exception_degrades_to_none_never_raises(self, monkeypatch):
        async def fake_fetch(pool_address, chain, *, contract="", pair=None):
            raise RuntimeError("boom")

        monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)
        pair = PairSnapshot(pair_address="0xpool", price_usd=1.0)
        assert await pt._robust_close_price(A, "base", pair) is None

    @pytest.mark.asyncio
    async def test_none_pair_returns_none(self):
        assert await pt._robust_close_price(A, "base", None) is None

    @pytest.mark.asyncio
    async def test_pair_without_address_returns_none(self):
        pair = PairSnapshot(pair_address="", price_usd=1.0)
        assert await pt._robust_close_price(A, "base", pair) is None

    @pytest.mark.asyncio
    async def test_only_last_window_considered(self, monkeypatch):
        """Une vieille mèche hors fenêtre ne doit jamais influencer la médiane."""
        async def fake_fetch(pool_address, chain, *, contract="", pair=None):
            return _candles(50.0, 50.0, 1.0, 1.1, 1.2)  # fenêtre par défaut = 5 dernières

        monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)
        pair = PairSnapshot(pair_address="0xpool", price_usd=1.2)
        price = await pt._robust_close_price(A, "base", pair)
        # Toutes les 5 sont dans la fenêtre par défaut (exactement 5) -- médiane sur les 5.
        assert price == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_run_weekly_reset_uses_robust_median_over_raw_spot(tmp_db, monkeypatch):
    """Cas central (#173) : le spot instantané reflète une mèche (5.0$) mais les
    bougies récentes montrent un prix stable (~1.1$) -- le reset doit clôturer sur la
    médiane robuste, pas sur le tick de mèche."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)

    async def fake_pair_lookup(contract, *, chain="base"):
        return PairSnapshot(pair_address="0xpool", price_usd=5.0, liquidity_usd=100_000.0, base_symbol="AAA")

    async def fake_fetch(pool_address, chain, *, contract="", pair=None):
        return _candles(1.0, 1.1, 1.05)  # aucune trace de la mèche à 5.0$ dans l'historique

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)

    report = await pt.run_weekly_reset()
    async with aiosqlite.connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT exit_price, close_notes FROM paper_position_archive WHERE contract = ?", (A,),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == pytest.approx(1.05)  # médiane, jamais le spot à 5.0$
    assert "médiane" in row[1].lower() or "anti-mèche" in row[1].lower()
    assert report["force_closed"] == 1


@pytest.mark.asyncio
async def test_run_weekly_reset_falls_back_to_spot_when_no_candles(tmp_db, monkeypatch):
    """Non-régression : sans bougies exploitables, le reset retombe sur le prix spot
    déjà en main (comportement historique), jamais un échec bloquant."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 1.0, alloc_usd=50_000)

    async def fake_pair_lookup(contract, *, chain="base"):
        return PairSnapshot(pair_address="0xpool", price_usd=1.3, liquidity_usd=100_000.0, base_symbol="AAA")

    async def fake_fetch(pool_address, chain, *, contract="", pair=None):
        return []

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)
    monkeypatch.setattr("aria_core.momentum_entry._fetch_candles", fake_fetch)

    await pt.run_weekly_reset()
    async with aiosqlite.connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT exit_price FROM paper_position_archive WHERE contract = ?", (A,),
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == pytest.approx(1.3)


@pytest.mark.asyncio
async def test_run_weekly_reset_falls_back_to_entry_price_when_pair_unavailable(tmp_db, monkeypatch):
    """Non-régression totale : aucune paire trouvée du tout -> comportement historique
    inchangé, repli sur le prix d'entrée (jamais un prix inventé)."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(A, "AAA", 2.0, alloc_usd=50_000)

    async def fake_pair_lookup(contract, *, chain="base"):
        return None

    monkeypatch.setattr(pt, "_default_pair_lookup", fake_pair_lookup)

    report = await pt.run_weekly_reset()
    assert round(report["end_equity"]) == 1_000_000  # pnl == 0, valorisé au coût d'entrée


def test_format_weekly_cycle_report_shows_verdict():
    report = {
        "cycle_number": 3, "start_capital": 1_000_000.0, "target_equity": 1_100_000.0,
        "end_equity": 1_050_000.0, "return_pct": 5.0, "closed_trades": 4, "win_rate": 75.0,
        "validated": False, "force_closed": 2, "next_cycle_number": 4,
    }
    text = pt.format_weekly_cycle_report(report)
    assert "SIMULATION" in text
    assert "non atteint" in text
    assert "1 000 000" in text or "1,000,000" in text or "1000000" in text
    assert "cycle #4" in text or "#4" in text
    assert "Aucun argent réel" in text


# ── Tâche 2 (22/07, poche satellite, option 3 confirmée par l'opérateur) ──────
# Une position au potentiel encore intact (régime ratchet Euphorie, stop ATR pas
# touché, R/R restant >= 1.5) est promue dans une poche séparée et plafonnée au
# lieu d'être force-clôturée -- exclue du verdict hebdomadaire principal, jamais
# wipée par l'archivage.

def _pos(**over) -> dict:
    """Position minimale pour les tests purs de ``_satellite_pocket_eligible`` --
    par défaut, un candidat qui DOIT être éligible (sert de point de départ, chaque
    test ne casse qu'UN seul critère à la fois)."""
    base = dict(
        strategy="momentum", entry_price=1.0, entry_atr_pct=0.05,
        high_water_price=1.0, invalidation_price=0.7, breakeven_locked=0,
        target_price=3.0, entry_regime="euphorie",
    )
    base.update(over)
    return base


class TestRemainingRewardRisk:
    def test_none_without_target(self):
        assert pt._remaining_reward_risk(price=1.3, target_price=None, active_stop=0.875) is None

    def test_none_when_target_at_or_below_price(self):
        assert pt._remaining_reward_risk(price=1.3, target_price=1.2, active_stop=0.875) is None

    def test_none_when_stop_already_touched(self):
        assert pt._remaining_reward_risk(price=1.3, target_price=3.0, active_stop=1.3) is None

    def test_computes_ratio(self):
        rr = pt._remaining_reward_risk(price=1.3, target_price=3.0, active_stop=0.875)
        assert rr == pytest.approx(1.7 / 0.425)


class TestSatellitePocketEligible:
    """Stop actif du candidat par défaut (_pos()) : trail_pct = 2.5*0.05 = 0.125,
    high_water=1.0 -> stop suiveur 0.875 ; invalidation 0.7 < 0.875 -> ignorée.
    Prix 1.3, cible 3.0 -> R/R restant = 1.7/0.425 = 4.0 (largement >= 1.5)."""

    def test_eligible_momentum_euphorie_good_rr(self):
        eligible, rr = pt._satellite_pocket_eligible(_pos(), 1.3, market_sentiment.META_REGIME_EUPHORIA)
        assert eligible is True
        assert rr == pytest.approx(4.0)

    def test_not_eligible_vc_thesis_strategy(self):
        eligible, _ = pt._satellite_pocket_eligible(
            _pos(strategy="vc_thesis"), 1.3, market_sentiment.META_REGIME_EUPHORIA,
        )
        assert eligible is False

    def test_not_eligible_regime_ratchet_below_euphorie(self):
        # Régime observé à l'entrée Euphorie, mais maintenant Neutre -- le RATCHET
        # (le plus prudent des deux) retombe à Neutre -- jamais éligible.
        eligible, _ = pt._satellite_pocket_eligible(
            _pos(entry_regime="euphorie"), 1.3, market_sentiment.META_REGIME_NEUTRAL,
        )
        assert eligible is False

    def test_not_eligible_stop_touched(self):
        eligible, rr = pt._satellite_pocket_eligible(_pos(), 0.80, market_sentiment.META_REGIME_EUPHORIA)
        assert eligible is False
        assert rr is None

    def test_not_eligible_low_remaining_rr(self):
        eligible, rr = pt._satellite_pocket_eligible(
            _pos(target_price=1.5), 1.3, market_sentiment.META_REGIME_EUPHORIA,
        )
        assert eligible is False
        assert rr is not None and rr < pt.SATELLITE_POCKET_MIN_RR

    def test_not_eligible_missing_price(self):
        eligible, _ = pt._satellite_pocket_eligible(_pos(), None, market_sentiment.META_REGIME_EUPHORIA)
        assert eligible is False


@pytest.mark.asyncio
async def test_satellite_eligible_position_promoted_and_survives_reset(tmp_db, monkeypatch):
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=3.0, invalidation_price=0.7,
        alloc_usd=30_000, entry_atr_pct=0.05, strategy="momentum", entry_regime="euphorie",
    )

    async def fake_resolve():
        return market_sentiment.META_REGIME_EUPHORIA

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    async def price_lookup(contract):
        return 1.3  # R/R restant = 4.0 -- largement éligible

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["force_closed"] == 0
    assert report["satellite_open_positions"] == 1
    assert len(report["satellite_added_this_cycle"]) == 1
    assert report["satellite_reserved_usd"] == pytest.approx(30_000.0)

    open_positions = await pt.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0]["contract"] == A
    assert open_positions[0]["pocket"] == "satellite"

    # Jamais archivée, puisque toujours ouverte (contrairement à toute position
    # force-clôturée, qui elle atterrit dans paper_position_archive).
    async with aiosqlite.connect(pt.DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM paper_position_archive WHERE contract = ?", (A,),
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 0


@pytest.mark.asyncio
async def test_satellite_position_excluded_from_weekly_verdict(tmp_db, monkeypatch):
    """Une poche satellite très profitable (mark-to-market) ne doit ni gonfler ni
    dégonfler le verdict de la semaine -- son coût est neutralisé (jamais compté
    comme une perte de capital pour la poche principale), sa valeur flottante
    jamais additionnée."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=20.0, invalidation_price=0.5,
        alloc_usd=30_000, entry_atr_pct=0.05, strategy="momentum", entry_regime="euphorie",
    )

    async def fake_resolve():
        return market_sentiment.META_REGIME_EUPHORIA

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    async def price_lookup(contract):
        return 5.0  # +400% latent -- éligible (R/R restant encore > 1.5) et très profitable

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["satellite_open_positions"] == 1
    # Aucune autre position -> poche principale reste EXACTEMENT à 1M$, peu importe
    # la performance flottante de la poche satellite (qui vaut ici 5x son coût).
    assert round(report["end_equity"]) == 1_000_000
    assert report["validated"] is False  # 1M$ < objectif 1,1M$


@pytest.mark.asyncio
async def test_satellite_pocket_capped_admits_best_rr_first(tmp_db, monkeypatch):
    """Deux candidats éligibles dont le coût cumulé dépasse le plafond (5% de 1M$ =
    50 000$) -- seul le MEILLEUR R/R restant est admis, l'autre est force-clôturé."""
    await pt.reset_portfolio(1_000_000.0)
    # A : R/R restant = 4.0 (cf. TestSatellitePocketEligible).
    await pt.open_position(
        A, "AAA", 1.0, target_price=3.0, invalidation_price=0.7,
        alloc_usd=30_000, entry_atr_pct=0.05, strategy="momentum", entry_regime="euphorie",
    )
    # B : même profil mais cible plus proche -> R/R restant = 1.2/0.425 ≈ 2.82 (encore
    # éligible >= 1.5, mais moins bon que A=4.0). cost_usd 30_000 + 30_000 = 60_000 >
    # plafond 50_000 -- B doit être le rejeté (pas parce qu'inéligible, mais faute de place).
    await pt.open_position(
        B, "BBB", 1.0, target_price=2.5, invalidation_price=0.7,
        alloc_usd=30_000, entry_atr_pct=0.05, strategy="momentum", entry_regime="euphorie",
    )

    async def fake_resolve():
        return market_sentiment.META_REGIME_EUPHORIA

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    async def price_lookup(contract):
        return 1.3

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["satellite_open_positions"] == 1
    assert report["satellite_rejected_no_room"] == 1
    assert report["force_closed"] == 1

    open_positions = await pt.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0]["contract"] == A  # meilleur R/R restant, admis en premier
    assert open_positions[0]["pocket"] == "satellite"


@pytest.mark.asyncio
async def test_satellite_carried_over_position_never_reevaluated(tmp_db, monkeypatch):
    """Une position déjà 'satellite' depuis une semaine précédente survit telle
    quelle -- jamais réévaluée ni reclôturée, même si son régime/R-R ne qualifierait
    plus aujourd'hui."""
    await pt.reset_portfolio(1_000_000.0)
    pos = await pt.open_position(A, "AAA", 1.0, alloc_usd=30_000, strategy="momentum")
    async with aiosqlite.connect(pt.DB_PATH) as db:
        await db.execute("UPDATE paper_position SET pocket = 'satellite' WHERE id = ?", (pos["id"],))
        await db.commit()

    async def fake_resolve():
        return market_sentiment.META_REGIME_NEUTRAL  # ne qualifierait plus aujourd'hui

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    async def price_lookup(contract):
        return 0.1  # prix effondré -- n'a aucune importance, jamais réévaluée

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["force_closed"] == 0
    assert report["satellite_open_positions"] == 1
    assert len(report["satellite_added_this_cycle"]) == 0  # pas NOUVELLE cette semaine

    open_positions = await pt.get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0]["pocket"] == "satellite"


@pytest.mark.asyncio
async def test_satellite_ineligible_position_force_closed_as_before(tmp_db, monkeypatch):
    """Régime Neutre (pas Euphorie) -> comportement historique inchangé, la position
    est force-clôturée comme n'importe quelle autre semaine."""
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        A, "AAA", 1.0, target_price=3.0, invalidation_price=0.7,
        alloc_usd=30_000, entry_atr_pct=0.05, strategy="momentum", entry_regime="euphorie",
    )

    async def fake_resolve():
        return market_sentiment.META_REGIME_NEUTRAL

    monkeypatch.setattr(market_sentiment, "resolve_meta_regime", fake_resolve)

    async def price_lookup(contract):
        return 1.3

    report = await pt.run_weekly_reset(price_lookup=price_lookup)

    assert report["force_closed"] == 1
    assert report["satellite_open_positions"] == 0
    assert (await pt.get_open_positions()) == []


def test_format_weekly_cycle_report_shows_satellite_pocket():
    report = {
        "cycle_number": 3, "start_capital": 1_000_000.0, "target_equity": 1_100_000.0,
        "end_equity": 1_000_000.0, "return_pct": 0.0, "closed_trades": 0, "win_rate": None,
        "validated": False, "force_closed": 1, "next_cycle_number": 4,
        "satellite_added_this_cycle": [{"contract": A, "cost_usd": 30_000.0, "remaining_rr": 4.0}],
        "satellite_open_positions": 1, "satellite_reserved_usd": 30_000.0,
        "satellite_rejected_no_room": 1,
    }
    text = pt.format_weekly_cycle_report(report)
    assert "satellite" in text.lower()
    assert "1" in text  # count(s) présents
    assert "30 000" in text or "30,000" in text
