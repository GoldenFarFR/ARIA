"""performance_breakdown.py -- winrate/PnL/expectancy segmentation of every
closed paper-trading trade (07/23, operator request: "balance toutes tes idee
qui permettrons daffirmer les mauvaise et les meilleurs resultat")."""
from __future__ import annotations

import pytest

from aria_core import paper_trader
from aria_core import performance_breakdown as pb


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "perf_test.db"))
    yield


# ── compute_metrics ──────────────────────────────────────────────────────────


def test_compute_metrics_empty_input():
    m = pb.compute_metrics([])
    assert m == {
        "n_trades": 0, "winrate": 0.0, "pnl_total": 0.0, "profit_factor": None,
        "avg_win": None, "avg_loss": None, "expectancy": None, "max_drawdown_usd": 0.0,
    }


def test_compute_metrics_simple_mix():
    trades = [
        {"pnl_usd": 100.0, "closed_at": "2026-07-20T10:00:00"},
        {"pnl_usd": -50.0, "closed_at": "2026-07-21T10:00:00"},
    ]
    m = pb.compute_metrics(trades)
    assert m["n_trades"] == 2
    assert m["winrate"] == pytest.approx(0.5)
    assert m["pnl_total"] == pytest.approx(50.0)
    assert m["profit_factor"] == pytest.approx(2.0)
    assert m["avg_win"] == pytest.approx(100.0)
    assert m["avg_loss"] == pytest.approx(50.0)
    assert m["expectancy"] == pytest.approx(25.0)
    assert m["max_drawdown_usd"] == pytest.approx(50.0)


def test_compute_metrics_operator_example_higher_expectancy_at_lower_winrate():
    """Le point exact soulevé par l'opérateur : 75% winrate + 3000$ doit
    ressortir avec une meilleure espérance que 100% winrate + 1000$."""
    high_winrate_low_pnl = [
        {"pnl_usd": 250.0, "closed_at": f"2026-07-{i:02d}T00:00:00"} for i in range(1, 5)
    ]  # 4/4 gagnants, total 1000$
    lower_winrate_higher_pnl = [
        {"pnl_usd": 1200.0, "closed_at": f"2026-07-{i:02d}T00:00:00"} for i in range(1, 4)
    ] + [
        {"pnl_usd": -600.0, "closed_at": "2026-07-04T00:00:00"}
    ]  # 3/4 gagnants, total 3000$
    m_high_wr = pb.compute_metrics(high_winrate_low_pnl)
    m_lower_wr = pb.compute_metrics(lower_winrate_higher_pnl)
    assert m_high_wr["winrate"] == pytest.approx(1.0)
    assert m_lower_wr["winrate"] == pytest.approx(0.75)
    assert m_lower_wr["pnl_total"] > m_high_wr["pnl_total"]
    assert m_lower_wr["expectancy"] > m_high_wr["expectancy"]


def test_compute_metrics_no_losses_profit_factor_is_none():
    m = pb.compute_metrics([{"pnl_usd": 100.0, "closed_at": "2026-07-20T00:00:00"}])
    assert m["profit_factor"] is None  # jamais "infini" pour une division par zero
    assert m["avg_loss"] is None


def test_compute_metrics_no_wins_avg_win_is_none():
    m = pb.compute_metrics([{"pnl_usd": -100.0, "closed_at": "2026-07-20T00:00:00"}])
    assert m["avg_win"] is None
    # 0% winrate reste bien defini : le poids du cote gagnant (winrate) est
    # exactement 0, expectancy = -avg_loss, jamais "indisponible"
    assert m["expectancy"] == pytest.approx(-100.0)


def test_compute_metrics_all_wins_expectancy_equals_avg_win():
    """100% winrate : le poids du cote perdant (1-winrate) est exactement 0,
    expectancy doit rester calculable (= avg_win), jamais None."""
    m = pb.compute_metrics([{"pnl_usd": 100.0, "closed_at": "2026-07-20T00:00:00"}])
    assert m["avg_loss"] is None
    assert m["expectancy"] == pytest.approx(100.0)


def test_compute_metrics_trades_missing_pnl_are_excluded():
    trades = [
        {"pnl_usd": 100.0, "closed_at": "2026-07-20T00:00:00"},
        {"pnl_usd": None, "closed_at": "2026-07-21T00:00:00"},  # donnee manquante
    ]
    m = pb.compute_metrics(trades)
    assert m["n_trades"] == 2  # compte les 2 positions...
    assert m["winrate"] == pytest.approx(1.0)  # ...mais seule celle avec pnl entre dans le taux


def test_compute_metrics_drawdown_sorts_by_closed_at_not_input_order():
    """Les trades sont fournis dans le DESORDRE temporel -- le drawdown doit
    quand meme etre calcule sur la vraie sequence chronologique."""
    trades = [
        {"pnl_usd": -50.0, "closed_at": "2026-07-22T00:00:00"},  # 3e dans le temps
        {"pnl_usd": 100.0, "closed_at": "2026-07-20T00:00:00"},  # 1er dans le temps
        {"pnl_usd": -80.0, "closed_at": "2026-07-21T00:00:00"},  # 2e dans le temps
    ]
    m = pb.compute_metrics(trades)
    # sequence chronologique : +100 (cum=100, peak=100) -> -80 (cum=20, dd=80)
    # -> -50 (cum=-30, dd=130)
    assert m["max_drawdown_usd"] == pytest.approx(130.0)


# ── breakdown_by ─────────────────────────────────────────────────────────────


def test_breakdown_by_groups_correctly():
    trades = [
        {"pnl_usd": 100.0, "closed_at": "2026-07-20T00:00:00", "chain": "base"},
        {"pnl_usd": -50.0, "closed_at": "2026-07-21T00:00:00", "chain": "base"},
        {"pnl_usd": 200.0, "closed_at": "2026-07-22T00:00:00", "chain": "solana"},
    ]
    groups = pb.breakdown_by(trades, pb.key_chain)
    assert set(groups.keys()) == {"base", "solana"}
    assert groups["base"]["n_trades"] == 2
    assert groups["solana"]["n_trades"] == 1
    assert groups["solana"]["pnl_total"] == pytest.approx(200.0)


def test_breakdown_by_drops_trades_with_none_key():
    def _key_returns_none_for_first(t):
        return None if t.get("pnl_usd") == 100.0 else "group"

    trades = [{"pnl_usd": 100.0, "closed_at": "2026-07-20T00:00:00"},
              {"pnl_usd": 200.0, "closed_at": "2026-07-21T00:00:00"}]
    groups = pb.breakdown_by(trades, _key_returns_none_for_first)
    assert list(groups.keys()) == ["group"]
    assert groups["group"]["n_trades"] == 1


# ── ready-made segmentation keys ─────────────────────────────────────────────


def test_key_conviction_tier_unknown_when_missing():
    assert pb.key_conviction_tier({}) == "unknown"
    assert pb.key_conviction_tier({"conviction_tier": "strong"}) == "strong"


def test_key_chain_unknown_when_missing():
    assert pb.key_chain({}) == "unknown"


def test_key_exit_reason():
    assert pb.key_exit_reason({"close_reason": "trailing_stop"}) == "trailing_stop"
    assert pb.key_exit_reason({}) == "unknown"


def test_key_discovery_channel():
    assert pb.key_discovery_channel({"discovery_channel": "websocket"}) == "websocket"
    assert pb.key_discovery_channel({}) == "unknown"


def test_key_rr_buckets_use_rr_field_first():
    assert pb.key_rr({"rr": 1.5}) == "<2.0"
    assert pb.key_rr({"rr": 2.2}) == "2.0-2.5"
    assert pb.key_rr({"rr": 3.0}) == "2.5-3.5"
    assert pb.key_rr({"rr": 5.0}) == ">=3.5"


def test_key_rr_falls_back_to_recomputing_from_levels():
    """Ancien trade sans le champ rr persiste (07/23) -- doit recalculer
    depuis entry/target/invalidation plutot que de renvoyer 'unknown'."""
    t = {"rr": None, "entry_price": 1.5, "target_price": 2.5, "invalidation_price": 1.0}
    # rr = (2.5 - 1.5) / (1.5 - 1.0) = 1.0 / 0.5 = 2.0
    assert pb.key_rr(t) == "2.0-2.5"


def test_key_rr_unknown_when_nothing_available():
    assert pb.key_rr({}) == "unknown"


def test_key_rvol_buckets():
    assert pb.key_rvol({"rvol_multiple": 3.0}) == "<5x"
    assert pb.key_rvol({"rvol_multiple": 7.0}) == "5-10x"
    assert pb.key_rvol({"rvol_multiple": 15.0}) == "10-20x"
    assert pb.key_rvol({"rvol_multiple": 30.0}) == ">=20x"
    assert pb.key_rvol({}) == "unknown"


def test_key_align_score():
    assert pb.key_align_score({"align_score": 2}) == "2"
    assert pb.key_align_score({}) == "unknown"


def test_key_atr_buckets():
    assert pb.key_atr({"entry_atr_pct": 0.05}) == "<10%"
    assert pb.key_atr({"entry_atr_pct": 0.15}) == "10-20%"
    assert pb.key_atr({"entry_atr_pct": 0.25}) == "20-30%"
    assert pb.key_atr({"entry_atr_pct": 0.35}) == ">=30%"
    assert pb.key_atr({}) == "unknown"


def test_key_liquidity_buckets():
    assert pb.key_liquidity({"entry_liquidity_usd": 30_000}) == "<50k$"
    assert pb.key_liquidity({"entry_liquidity_usd": 500_000}) == ">=300k$"
    assert pb.key_liquidity({}) == "unknown"


def test_key_dev_sold_buckets():
    assert pb.key_dev_sold({"entry_dev_sold_pct": 0.05}) == "<10%"
    assert pb.key_dev_sold({"entry_dev_sold_pct": 0.80}) == ">=60%"
    assert pb.key_dev_sold({}) == "unknown"


def test_key_hour_of_day():
    assert pb.key_hour_of_day({"opened_at": "2026-07-20T14:30:00"}) == "14h-15h"
    assert pb.key_hour_of_day({}) == "unknown"
    assert pb.key_hour_of_day({"opened_at": "not-a-date"}) == "unknown"


def test_key_day_of_week():
    # 2026-07-20 est un lundi
    assert pb.key_day_of_week({"opened_at": "2026-07-20T00:00:00"}) == "Lundi"
    assert pb.key_day_of_week({}) == "unknown"


# ── format_breakdown_report ──────────────────────────────────────────────────


def test_format_breakdown_report_empty():
    text = pb.format_breakdown_report([])
    assert "Aucun trade" in text


def test_format_breakdown_report_includes_global_and_dimensions():
    trades = [
        {
            "pnl_usd": 300.0, "closed_at": "2026-07-20T10:00:00", "conviction_tier": "strong",
            "chain": "base", "rr": 3.0,
        },
        {
            "pnl_usd": -100.0, "closed_at": "2026-07-21T10:00:00", "conviction_tier": "weak",
            "chain": "base", "rr": 1.2,
        },
    ]
    text = pb.format_breakdown_report(trades)
    assert "Trades : 2" in text
    assert "Winrate : 50%" in text
    assert "Palier de conviction" in text
    assert "strong" in text and "weak" in text
    assert "R/R initial" in text


# ── get_all_closed_trades (DB integration) ──────────────────────────────────


@pytest.mark.asyncio
async def test_get_all_closed_trades_combines_current_and_archived():
    await paper_trader.reset_portfolio(1_000_000.0)
    await paper_trader.open_position("0xcurrent", "CUR", 1.0, target_price=2.0, invalidation_price=0.5)
    await paper_trader.close_position("0xcurrent", 1.5, reason="manuel")

    async with __import__("aiosqlite").connect(paper_trader.DB_PATH) as db:
        await db.execute(
            "INSERT INTO paper_position_archive (cycle_number, contract, symbol, status, pnl_usd, closed_at) "
            "VALUES (1, '0xarchived', 'ARC', 'closed', 42.0, '2026-07-01T00:00:00')"
        )
        await db.commit()

    trades = await pb.get_all_closed_trades()
    contracts = {t["contract"] for t in trades}
    assert "0xcurrent" in contracts
    assert "0xarchived" in contracts
